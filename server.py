import os
import re
import uuid
import sys
import io
import threading
from pathlib import Path
from typing import List
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent
)

import db
import ebay_poster

load_dotenv()

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("กรุณากำหนด LINE_CHANNEL_SECRET และ LINE_CHANNEL_ACCESS_TOKEN")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

LIFF_URL = "https://liff.line.me/2012509962-djqQR7a0"
BASE_URL = "https://ebay-poster.onrender.com"

db.init_db()

app = FastAPI(title="eBay Auto Poster LINE Bot")

# เปิดให้ eBay เข้ามาดึงรูปภาพได้
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def get_form_path():
    if os.path.exists("form.html"):
        return "form.html"
    elif os.path.exists("templates/form.html"):
        return "templates/form.html"
    return "form.html"

# เริ่มระบบ eBay Auto Poster อัตโนมัติในเบื้องหลัง
@app.on_event("startup")
def startup_event():
    def worker_loop():
        ebay_poster.run_queue_worker(interval_minutes=5)
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    print("[*] eBay Auto Poster Background Worker started on Cloud!")

@app.get("/")
def home():
    return FileResponse(get_form_path())

@app.get("/form")
def get_form():
    return FileResponse(get_form_path())

@app.post("/api/submit-item")
async def submit_item(
    title: str = Form(...),
    price: float = Form(...),
    currency: str = Form("USD"),
    condition: str = Form("Near Mint or Better (NM)"),
    files: List[UploadFile] = File(...)
):
    saved_paths = []
    for file in files:
        ext = os.path.splitext(file.filename)[1] or ".jpg"
        unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
        target_path = UPLOAD_DIR / unique_name
        
        content = await file.read()
        with open(target_path, "wb") as f:
            f.write(content)
        saved_paths.append(str(target_path))

    # ดึง User ID ล่าสุด
    conn = db.sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM items WHERE user_id != 'web_form' ORDER BY id DESC LIMIT 1")
    user_row = c.fetchone()
    conn.close()
    
    line_user_id = user_row[0] if user_row else "web_form"

    item_id = db.add_queued_item(
        user_id=line_user_id,
        title=title,
        price=price,
        image_paths=saved_paths,
        currency=currency,
        condition=condition
    )

    if line_user_id and line_user_id != "web_form":
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                msg = (
                    "🎉 ได้รับการ์ดใหม่เข้าคิวเรียบร้อยครับ!\n\n"
                    f"🏷️ ชื่อ: {title}\n"
                    f"💰 ราคา: ${price:.2f} {currency}\n"
                    f"🔍 สภาพ: {condition}\n"
                    f"📸 จำนวนรูป: {len(saved_paths)} รูป\n"
                    f"🆔 คิวที่: #{item_id}\n\n"
                    "⏰ ระบบกำลังเตรียมส่งข้อมูลขึ้น eBay ให้ทันทีครับ!"
                )
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=line_user_id,
                        messages=[TextMessage(text=msg)]
                    )
                )
        except Exception as e:
            print(f"[!] Push notification error: {e}")

    return {
        "success": True,
        "message": "บันทึกเข้าคิวเรียบร้อย",
        "item_id": item_id,
        "title": title,
        "price": price,
        "images_count": len(saved_paths)
    }

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature")

    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        handler.handle(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return JSONResponse(content={"status": "OK"})

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event: MessageEvent):
    user_id = event.source.user_id
    message_id = event.message.id

    with ApiClient(configuration) as api_client:
        line_bot_blob = MessagingApiBlob(api_client)
        image_content = line_bot_blob.get_message_content(message_id)

        filename = f"{message_id}.jpg"
        file_path = str(UPLOAD_DIR / filename)
        with open(file_path, "wb") as f:
            f.write(image_content)

        db.save_draft_image(user_id, file_path)

        line_bot_api = MessagingApi(api_client)
        reply_text = (
            "📸 ได้รับรูปภาพเรียบร้อยครับ!\n\n"
            f"👉 แตะเปิดหน้าต่างลงขายการ์ดจบในที่เดียวได้ที่นี่ครับ:\n{LIFF_URL}"
        )
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    user_id = event.source.user_id
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        if text.lower() in ["คิว", "ดูคิว", "queue", "status"]:
            queued = db.get_queued_items()
            if not queued:
                msg = f"📭 ยังไม่มีรายการที่รอโพสต์ในคิวครับ\n\nแตะที่นี่เพื่อลงขายการ์ด:\n{LIFF_URL}"
            else:
                msg = f"📋 รายการในคิวรอโพสต์ ({len(queued)} รายการ):\n"
                for idx, item in enumerate(queued, 1):
                    item_id, title, price, currency, images, status = item
                    img_count = len(images.split(";")) if images else 0
                    msg += f"\n{idx}. {title}\n   💰 ${price} {currency} ({img_count} รูป)"
                msg += f"\n\n➕ เพิ่มการ์ดใหม่ได้ที่:\n{LIFF_URL}"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=msg)]
                )
            )
            return

        # คำสั่งลงสินค้า
        if text.lower() in ["ลงขาย", "ลงสินค้า", "ขาย", "เพิ่ม", "เริ่ม", "add", "new", "form", "1"]:
            msg = (
                "🃏 แตะที่ลิงก์ด้านล่างเพื่อเปิดหน้าลงขายใน LINE ได้เลยครับ:\n\n"
                f"👉 {LIFF_URL}\n\n"
                "📸 เลือกรูปหน้า-หลังการ์ด พร้อมระบุสภาพและราคาในหน้าเดียวได้เลยครับ!"
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=msg)]
                )
            )
            return

        # ค่าเริ่มต้น
        msg = (
            "👋 ยินดีต้อนรับสู่ระบบลงขายสินค้า eBay อัตโนมัติ (Online 24 ชม.)!\n\n"
            "✨ แตะเพื่อเปิดหน้าลงขายการ์ดใน LINE ได้เลยครับ:\n"
            f"👉 {LIFF_URL}\n\n"
            "📌 หรือพิมพ์ **ดูคิว** เพื่อตรวจดูรายการที่รอโพสต์"
        )
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=msg)]
            )
        )
