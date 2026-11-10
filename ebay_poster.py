import os
import re
import time
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage

import db

load_dotenv()

EBAY_TOKEN = os.getenv("EBAY_USER_TOKEN")
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

line_config = Configuration(access_token=LINE_TOKEN)

def get_base_url():
    try:
        with open("webhook_url.txt", "r", encoding="utf-8") as f:
            url = f.read().strip()
            return url.replace("/callback", "")
    except Exception:
        return "http://localhost:8000"

def get_game_and_specifics(title: str) -> dict:
    """
    วิเคราะห์ชื่อสินค้าเพื่อสร้าง Item Specifics สำหรับการ์ดใบเดี่ยว (Category 183454)
    """
    t = title.lower()
    
    # ค่าเริ่มต้นสำหรับ One Piece Card Game (ยอดนิยมสูงสุด)
    specifics = {
        "Game": "One Piece CCG",
        "Franchise": "One Piece",
        "Manufacturer": "Bandai",
        "Language": "Japanese",
        "Country/Region of Manufacture": "Japan",
        "Age Level": "14+",
        "Autographed": "No",
        "Customized": "No",
        "Vintage": "No"
    }

    if any(k in t for k in ["pokemon", "pikachu", "charizard", "โปเกมอน"]):
        specifics["Game"] = "Pokémon TCG"
        specifics["Franchise"] = "Pokémon"
        specifics["Manufacturer"] = "Nintendo"
    elif any(k in t for k in ["yugioh", "yu-gi-oh", "blue-eyes", "ยูกิ"]):
        specifics["Game"] = "Yu-Gi-Oh! TCG"
        specifics["Franchise"] = "Yu-Gi-Oh!"
        specifics["Manufacturer"] = "Konami"
    elif any(k in t for k in ["dragon ball", "goku"]):
        specifics["Game"] = "Dragon Ball Super"
        specifics["Franchise"] = "Dragon Ball"
        specifics["Manufacturer"] = "Bandai"

    # ถ้ามีคำว่า English ในชื่อ
    if "english" in t or "eng" in t:
        specifics["Language"] = "English"

    return specifics

def map_condition(condition_str: str):
    """
    แปลงสภาพการ์ดเป็น ConditionID และ ConditionDescriptors สำหรับ eBay
    """
    c = condition_str.lower()
    
    # การ์ดเกรด
    if "graded" in c:
        descriptors = [("40002", "400020")] # Graded
        if "psa" in c:
            descriptors.append(("40003", "400030")) # PSA
        elif "bgs" in c or "beckett" in c:
            descriptors.append(("40003", "400031")) # BGS
        elif "cgc" in c:
            descriptors.append(("40003", "400032")) # CGC

        grade_match = re.search(r"(\d+(\.\d+)?)", condition_str)
        if grade_match:
            descriptors.append(("40004", grade_match.group(1)))
        return 2750, descriptors

    # การ์ดดิบ (Ungraded)
    descriptors = [("40002", "400010")] # Ungraded
    if "lightly" in c or "lp" in c or "excellent" in c:
        descriptors.append(("40001", "400011")) # Lightly Played (Excellent)
    elif "moderately" in c or "mp" in c:
        descriptors.append(("40001", "400012")) # Moderately Played (Very Good)
    elif "heavily" in c or "hp" in c:
        descriptors.append(("40001", "400013")) # Heavily Played (Poor)
    elif "damaged" in c or "dmg" in c:
        descriptors.append(("40001", "400014")) # Damaged
    else:
        # Near Mint or Better (400010)
        descriptors.append(("40001", "400010"))

    return 4000, descriptors

def notify_line(user_id: str, message: str):
    target_id = user_id if (user_id and user_id != "web_form") else "U65d1bc34fbca88c47dfb4be576cb5218"
    try:
        with ApiClient(line_config) as api:
            bot = MessagingApi(api)
            bot.push_message(PushMessageRequest(
                to=target_id,
                messages=[TextMessage(text=message)]
            ))
    except Exception as e:
        print(f"[!] LINE notify error: {e}")

def post_item_to_ebay(item_data) -> dict:
    item_id, user_id, title, price, currency, condition, image_paths, status, created_at, post_time = item_data
    base_url = get_base_url()

    # รูปภาพ URLs
    images = [img.strip().replace("\\", "/") for img in image_paths.split(";") if img.strip()]
    picture_xml = ""
    for img in images:
        full_img_url = f"{base_url}/{img}"
        picture_xml += f"<PictureURL>{full_img_url}</PictureURL>\n"

    # Specifics & Descriptors
    specifics_dict = get_game_and_specifics(title)
    game_name = specifics_dict["Game"]
    cond_id, descriptors = map_condition(condition or "Near Mint")

    descriptors_xml = ""
    for d_name, d_val in descriptors:
        descriptors_xml += f"""
        <ConditionDescriptor>
          <Name>{d_name}</Name>
          <Value>{d_val}</Value>
        </ConditionDescriptor>"""

    specifics_xml = ""
    for k, v in specifics_dict.items():
        specifics_xml += f"""
        <NameValueList>
          <Name>{k}</Name>
          <Value>{v}</Value>
        </NameValueList>"""

    # ปรับแต่งชื่อสำหรับ eBay US
    has_thai = any('\u0e00' <= char <= '\u0e7f' for char in title)
    if has_thai:
        ebay_title = f"{game_name} - {title} Card Japanese"
    else:
        ebay_title = title if game_name.lower() in title.lower() else f"{title} {game_name}"
    ebay_title = ebay_title[:80]

    # Description สวยงาม
    description = f"""
    <h2>{ebay_title}</h2>
    <p><strong>Game:</strong> {specifics_dict['Game']}</p>
    <p><strong>Condition:</strong> {condition}</p>
    <p><strong>Language:</strong> {specifics_dict['Language']}</p>
    <p><strong>Manufacturer:</strong> {specifics_dict['Manufacturer']} (100% Authentic Guaranteed)</p>
    <hr/>
    <p><strong>Packaging & Shipping:</strong></p>
    <ul>
      <li>Card is sleeved in soft inner sleeve and rigid toploader.</li>
      <li>Shipped in padded bubble mailer with cardboard reinforcement.</li>
      <li>Fast international shipping with tracking number.</li>
    </ul>
    """

    xml_req = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <Item>
    <Title>{ebay_title}</Title>
    <Description><![CDATA[{description}]]></Description>
    <PrimaryCategory>
      <CategoryID>183454</CategoryID>
    </PrimaryCategory>
    <StartPrice currencyID="{currency}">{price:.2f}</StartPrice>
    <ConditionID>{cond_id}</ConditionID>
    <ConditionDescriptors>
      {descriptors_xml}
    </ConditionDescriptors>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <Country>TH</Country>
    <Currency>{currency}</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Location>Bangkok, Thailand</Location>
    <PictureDetails>
      {picture_xml}
    </PictureDetails>
    <ItemSpecifics>
      {specifics_xml}
    </ItemSpecifics>
    <PostalCode>10110</PostalCode>
    <Quantity>1</Quantity>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsNotAccepted</ReturnsAcceptedOption>
    </ReturnPolicy>
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>StandardShippingFromOutsideUS</ShippingService>
        <ShippingServiceCost currencyID="{currency}">5.00</ShippingServiceCost>
      </ShippingServiceOptions>
    </ShippingDetails>
  </Item>
</AddItemRequest>"""

    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": "1149",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-CALL-NAME": "AddItem",
        "X-EBAY-API-IAF-TOKEN": EBAY_TOKEN,
        "Content-Type": "text/xml"
    }

    try:
        r = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_req.encode("utf-8"), timeout=60)
        root = ET.fromstring(r.text)
        ack = root.find("{urn:ebay:apis:eBLBaseComponents}Ack")
        ack_text = ack.text if ack is not None else "Failure"

        if ack_text in ["Success", "Warning"]:
            ebay_item_id_elem = root.find(".//{urn:ebay:apis:eBLBaseComponents}ItemID")
            ebay_item_id = ebay_item_id_elem.text if ebay_item_id_elem is not None else "Unknown"
            ebay_url = f"https://www.ebay.com/itm/{ebay_item_id}"

            conn = db.sqlite3.connect(db.DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE items SET status = 'POSTED', post_time = CURRENT_TIMESTAMP WHERE id = ?", (item_id,))
            conn.commit()
            conn.close()

            return {"success": True, "ebay_id": ebay_item_id, "url": ebay_url}
        else:
            errors = []
            for err in root.findall(".//{urn:ebay:apis:eBLBaseComponents}Errors"):
                long_msg = err.find("{urn:ebay:apis:eBLBaseComponents}LongMessage")
                if long_msg is not None:
                    errors.append(long_msg.text)
            return {"success": False, "errors": errors}
    except Exception as e:
        return {"success": False, "errors": [str(e)]}

def run_queue_worker(interval_minutes: int = 5):
    print(f"[*] เริ่มต้นระบบ eBay Auto Poster Worker (โพสต์ทุกๆ {interval_minutes} นาที)...")
    
    while True:
        conn = db.sqlite3.connect(db.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, user_id, title, price, currency, condition, image_paths, status, created_at, post_time FROM items WHERE status = 'QUEUED' ORDER BY id ASC LIMIT 1")
        row = c.fetchone()
        conn.close()

        if row:
            item_id, user_id, title, price, currency, condition, image_paths, status, created_at, post_time = row
            print(f"\n[+] กำลังโพสต์การ์ด #{item_id}: {title} (${price:.2f})...")

            res = post_item_to_ebay(row)
            if res["success"]:
                print(f"[✓] โพสต์สำเร็จ! eBay Item ID: {res['ebay_id']} | Link: {res['url']}")
                msg = (
                    "🎉 โพสต์การ์ดขึ้น eBay สำเร็จแล้วครับ! 🚀\n\n"
                    f"🏷️ {title}\n"
                    f"💰 ราคา: ${price:.2f} {currency}\n"
                    f"🔍 สภาพ: {condition}\n"
                    f"🔗 ลิงก์สินค้า: {res['url']}\n\n"
                    f"⏰ รายการถัดไปจะโพสต์ในอีก {interval_minutes} นาทีครับ"
                )
                notify_line(user_id, msg)
            else:
                print(f"[x] โพสต์ไม่สำเร็จ: {res['errors']}")
                conn = db.sqlite3.connect(db.DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE items SET status = 'FAILED' WHERE id = ?", (item_id,))
                conn.commit()
                conn.close()

                err_summary = "\n".join(res["errors"][:2])
                msg = f"⚠️ โพสต์การ์ด #{item_id} ({title}) ไม่สำเร็จ:\n{err_summary}"
                notify_line(user_id, msg)

            print(f"[*] รอ {interval_minutes} นาที ก่อนโพสต์ใบถัดไป...")
            time.sleep(interval_minutes * 60)
        else:
            time.sleep(10)

if __name__ == "__main__":
    import sys
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    run_queue_worker(interval_minutes=minutes)
