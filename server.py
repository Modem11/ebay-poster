import os
import re
import uuid
import sys
import io
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
    TextMessage,
    FlexMessage,
    FlexContainer
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent
)

import db

# โหลดค่าคอนฟิกจาก .env
load_dotenv()

CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("กรุณากำหนด LINE_CHANNEL_SECRET และ LINE_CHANNEL_ACCESS_TOKEN ในไฟล์ .env")

# ตั้งค่า LINE SDK
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# โฟลเดอร์เก็บรูป
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

LIFF_URL = "https://liff.line.me/2012509962-djqQR7a0"

# เริ่มต้นฐานข้อมูล
db.init_db()

app = FastAPI(title="eBay Auto Poster LINE Bot")

# เปิดให้อินเทอร์เน็ตเข้าถึงโฟลเดอร์รูปภาพได้สำหรับ eBay ดึงรูปไปแสดง
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# เสิร์ฟหน้าเว็บฟอร์ม
@app.get("/")
def home():
    return FileResponse("templates/form.html")

@app.get("/form")
def get_form():
    return FileResponse("templates/form.html")

# API รับข้อมูลจากหน้าฟอร์ม (รูปภาพพร้อมกันหลายรูป + ชื่อ + ราคา + สภาพ)
@app.post("/api/submit-item")
async def submit_item(
    title: str = Form(...),
    price: float = Form(...),
    currency: str = Form("USD"),
    condition: str = Form("Brand New"),
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

    # บันทึกเข้าฐานข้อมูลสถานะ QUEUED ทันที
    # ดึง User ID ล่าสุดที่มีในระบบ
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

    # ส่งการแจ้งเตือนเข้าไปในห้องแชท LINE ของคุณทันที!
    if line_user_id and line_user_id != "web_form":
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                msg = (
                    "🎉 ได้รับการ์ดใหม่เข้าคิวเรียบร้อยครับ!\n\n"
                    f"🏷️ ชื่อ: {title}\n"
                    f"💰 ราคา: {price} {currency}\n"
                    f"🔍 สภาพ: {condition}\n"
                    f"📸 จำนวนรูป: {len(saved_paths)} รูป\n"
                    f"🆔 คิวที่: #{item_id}\n\n"
                    "⏰ พร้อมสำหรับการโพสต์ขายตามคิวเวลาครับ!"
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

# Webhook สำหรับ LINE
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


# ดึง URL ปัจจุบัน
def get_base_url():
    try:
        with open("webhook_url.txt", "r", encoding="utf-8") as f:
            url = f.read().strip()
            return url.replace("/callback", "")
    except Exception:
        return "http://localhost:8000"


# จัดการเมื่อผู้ใช้ส่งรูปภาพทางแชท
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event: MessageEvent):
    user_id = event.source.user_id
    message_id = event.message.id
    base_url = get_base_url()

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
            "👉 พิมพ์: `ชื่อสินค้า / ราคา` เพื่อบันทึกเข้าคิว\n"
            f"💡 หรือใช้ฟอร์มใส่รูป+รายละเอียดพร้อมกันที่นี่:\n{base_url}/form"
        )
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


# จัดการเมื่อผู้ใช้ส่งข้อความทางแชท
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    user_id = event.source.user_id
    text = event.message.text.strip()
    base_url = get_base_url()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # ดูคิว
        if text.lower() in ["คิว", "ดูคิว", "queue", "status"]:
            queued = db.get_queued_items()
            if not queued:
                msg = f"📭 ยังไม่มีรายการที่รอโพสต์ในคิวครับ\n\nแตะที่นี่เพื่อลงสินค้าใหม่:\n{base_url}/form"
            else:
                msg = f"📋 รายการในคิวรอโพสต์ ({len(queued)} รายการ):\n"
                for idx, item in enumerate(queued, 1):
                    item_id, title, price, currency, images, status = item
                    img_count = len(images.split(";")) if images else 0
                    msg += f"\n{idx}. {title}\n   💰 {price} {currency} ({img_count} รูป)"
                msg += f"\n\n➕ เพิ่มสินค้าใหม่ได้ที่:\n{base_url}/form"
            
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=msg)]
                )
            )
            return

        # คำสั่งลงสินค้า หรือเริ่มต้น
        if text.lower() in ["ลงขาย", "ลงสินค้า", "ขาย", "เพิ่ม", "เริ่ม", "add", "new", "form", "1"]:
            msg = (
                "🃏 แตะที่ลิงก์ด้านล่างเพื่อเปิดหน้าลงการ์ดใน LINE ได้เลยครับ:\n\n"
                "👉 https://liff.line.me/2012509962-djqQR7a0\n\n"
                "📸 เลือกรูปหน้า-หลังการ์ด พร้อมระบุสภาพและราคาในหน้าเดียวได้เลยครับ!"
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=msg)]
                )
            )
            return

        # ล้างดราฟต์
        if text.lower() in ["ล้าง", "ยกเลิก", "clear", "cancel"]:
            conn = db.sqlite3.connect(db.DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM items WHERE user_id = ? AND status = 'DRAFT'", (user_id,))
            conn.commit()
            conn.close()

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="🧹 ล้างรายการร่างเรียบร้อยครับ")]
                )
            )
            return

        # ตรวจสอบรูปแบบ ชื่อ / ราคา ทางแชท
        parts = [p.strip() for p in text.split("/")]
        if len(parts) >= 2:
            title = parts[0]
            price_raw = parts[1]
            match = re.search(r"(\d+(\.\d+)?)", price_raw)
            if match:
                price = float(match.group(1))
                currency = "USD"
                if "thb" in price_raw.lower() or "บาท" in price_raw:
                    currency = "THB"

                saved = db.update_draft_details(user_id, title, price, currency)
                if saved:
                    msg = (
                        "✅ บันทึกสินค้าเข้าคิวเรียบร้อยครับ!\n\n"
                        f"🏷️ {title}\n"
                        f"💰 {price} {currency} ({len(saved['images'])} รูป)\n"
                        f"🆔 คิวที่: #{saved['id']}\n\n"
                        f"➕ ลงสินค้าชิ้นถัดไปได้ที่:\n{base_url}/form"
                    )
                else:
                    msg = (
                        "⚠️ ยังไม่มีรูปภาพสินค้าครับ\n\n"
                        f"👉 แนะนำเปิดหน้าฟอร์มเพื่อใส่รูปพร้อมชื่อและราคาได้ทันทีที่นี่ครับ:\n{base_url}/form"
                    )
            else:
                msg = f"⚠️ ระบุราคาเป็นตัวเลขด้วยครับ หรือเปิดฟอร์มได้ที่:\n{base_url}/form"
        else:
            msg = (
                "👋 ยินดีต้อนรับสู่ระบบลงขายสินค้า eBay อัตโนมัติ!\n\n"
                "✨ ช่องทางที่สะดวกที่สุด (ใส่รูปพร้อมชื่อและราคาในหน้าเดียว):\n"
                f"👉 {base_url}/form\n\n"
                "📌 หรือพิมพ์ **ดูคิว** เพื่อตรวจดูรายการที่รอโพสต์"
            )

        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=msg)]
            )
        )
