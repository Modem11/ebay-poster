import sqlite3
import os
from datetime import datetime

DB_PATH = "items.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            title TEXT,
            price REAL,
            currency TEXT DEFAULT 'USD',
            condition TEXT DEFAULT 'Brand New',
            image_paths TEXT,
            status TEXT DEFAULT 'DRAFT', -- DRAFT, QUEUED, POSTED, FAILED
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            post_time TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_queued_item(user_id: str, title: str, price: float, image_paths: list[str], currency: str = 'USD', condition: str = 'Brand New'):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO items (user_id, title, price, currency, condition, image_paths, status)
        VALUES (?, ?, ?, ?, ?, ?, 'QUEUED')
    """, (user_id, title, price, currency, condition, ";".join(image_paths)))
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id

def save_draft_image(user_id: str, image_path: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, image_paths FROM items WHERE user_id = ? AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (user_id,))
    row = cursor.fetchone()
    if row:
        item_id, existing_images = row
        images = existing_images.split(";") if existing_images else []
        images.append(image_path)
        cursor.execute("UPDATE items SET image_paths = ? WHERE id = ?", (";".join(images), item_id))
    else:
        cursor.execute("INSERT INTO items (user_id, image_paths, status) VALUES (?, ?, 'DRAFT')", (user_id, image_path))
    conn.commit()
    conn.close()

def update_draft_details(user_id: str, title: str, price: float, currency: str = 'USD'):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, image_paths FROM items WHERE user_id = ? AND status = 'DRAFT' ORDER BY id DESC LIMIT 1", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None
    
    item_id, image_paths = row
    cursor.execute("""
        UPDATE items 
        SET title = ?, price = ?, currency = ?, status = 'QUEUED' 
        WHERE id = ?
    """, (title, price, currency, item_id))
    conn.commit()
    conn.close()
    return {"id": item_id, "title": title, "price": price, "currency": currency, "images": image_paths.split(";") if image_paths else []}

def get_queued_items():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, price, currency, image_paths, status FROM items WHERE status = 'QUEUED' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows
