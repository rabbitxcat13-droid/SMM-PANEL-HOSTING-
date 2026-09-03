# 𝗭ᴇᴇsʜᴀɴ × 𝗦ᴍᴍ 𝗛ᴜʙ

import logging
import sqlite3
import time
import random
import string
import re
import asyncio
from datetime import datetime

import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest, Forbidden

# ============================================================
# CONFIG — apna bot token aur owner id yaha daalo
# ============================================================
BOT_TOKEN = "8666643142:AAGeTnxcyPIbCUiCZJofy119X3obi6m6Fpc"
OWNER_ID = 8408439521  # ye admin/owner ka telegram user id (auto admin ban jayega)
DB_FILE = "smm_panel.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("smm_panel_bot")

# ============================================================
# BUTTON HELPERS (Bot API 9.4 native style — primary/success/danger)
# ============================================================
def ibtn(text, callback_data, style=None):
    kwargs = {}
    if style:
        # NOTE: "style" (button color, Bot API 9.4) is only a native constructor
        # kwarg in python-telegram-bot >= 22.7 (needs Python >= 3.10). On older
        # PTB/Python this host can actually install, we pass it through
        # api_kwargs instead — this has been supported since PTB v20 and gets
        # merged into the outgoing JSON exactly the same way, so the color still
        # works on Telegram's side even though the library doesn't "know" the field.
        kwargs["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(text, callback_data=callback_data, **kwargs)


def kbtn(text, style=None):
    kwargs = {}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return KeyboardButton(text, **kwargs)


# ============================================================
# DATABASE
# ============================================================
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            total_orders INTEGER DEFAULT 0,
            total_referrals INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_credited INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            joined_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            title TEXT,
            link TEXT,
            is_private INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            skey TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            unit_base INTEGER,
            service_id TEXT,
            min_qty INTEGER,
            max_qty INTEGER,
            platform TEXT DEFAULT 'instagram'
        )
    """)
    # crash-safe migration for older DBs created before "platform" column existed
    svc_cols = [r["name"] for r in cur.execute("PRAGMA table_info(services)").fetchall()]
    if "platform" not in svc_cols:
        cur.execute("ALTER TABLE services ADD COLUMN platform TEXT DEFAULT 'instagram'")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            skey TEXT,
            link TEXT,
            quantity INTEGER,
            charge REAL,
            status TEXT,
            api_order_id TEXT,
            created_at TEXT,
            refunded INTEGER DEFAULT 0,
            provider_status TEXT
        )
    """)
    # crash-safe migration for older DBs created before these columns existed
    order_cols = [r["name"] for r in cur.execute("PRAGMA table_info(orders)").fetchall()]
    if "refunded" not in order_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN refunded INTEGER DEFAULT 0")
    if "provider_status" not in order_cols:
        cur.execute("ALTER TABLE orders ADD COLUMN provider_status TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            deposit_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            status TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            balance REAL,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            code TEXT,
            user_id INTEGER,
            redeemed_at TEXT,
            UNIQUE(code, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            skey TEXT PRIMARY KEY,
            svalue TEXT
        )
    """)

    conn.commit()

    # seed default services if missing
    defaults = [
        ("followers", "𝗜𝗚 ꜰᴏʟʟᴏᴡᴇʀꜱ", 6.59, 100, None, 100, 10000, "instagram"),
        ("likes", "𝗜𝗚 𝐋ɪᴋᴇꜱ", 4.5, 100, None, 10, 100000, "instagram"),
        ("views", "𝗜𝗚 𝐕ɪᴇᴡꜱ", 7.0, 10000, None, 100, 100000, "instagram"),
        ("tg_subscribers", "𝗧ɢ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ", 6.0, 100, None, 10, 10000, "telegram"),
        ("tg_views", "𝗧ɢ 𝗩ɪᴇᴡꜱ", 3.5, 1000, None, 10, 1000000, "telegram"),
        ("tg_reactions", "𝗧ɢ 𝗥ᴇᴀᴄᴛɪᴏɴꜱ", 1.5, 100, None, 10, 100000, "telegram"),
    ]
    for skey, name, price, unit_base, sid, mn, mx, platform in defaults:
        cur.execute("SELECT 1 FROM services WHERE skey=?", (skey,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO services (skey,name,price,unit_base,service_id,min_qty,max_qty,platform) VALUES (?,?,?,?,?,?,?,?)",
                (skey, name, price, unit_base, sid, mn, mx, platform),
            )

    # seed default settings
    default_settings = {
        "bot_status": "on",
        "api_url": "",
        "api_key": "",
        "welcome_media_id": "",
        "welcome_media_type": "",
        "force_media_id": "",
        "force_media_type": "",
        "qr_photo_id": "",
        "payout_channel_id": "",
        "payout_channel_title": "",
    }
    for k, v in default_settings.items():
        cur.execute("SELECT 1 FROM settings WHERE skey=?", (k,))
        if not cur.fetchone():
            cur.execute("INSERT INTO settings (skey,svalue) VALUES (?,?)", (k, v))

    # seed owner as admin
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))

    conn.commit()
    conn.close()


# ---------- settings helpers ----------
def get_setting(key, default=""):
    conn = db()
    row = conn.execute("SELECT svalue FROM settings WHERE skey=?", (key,)).fetchone()
    conn.close()
    return row["svalue"] if row and row["svalue"] is not None else default


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings (skey,svalue) VALUES (?,?) ON CONFLICT(skey) DO UPDATE SET svalue=excluded.svalue",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------- user helpers ----------
def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def ensure_user(tg_user, referred_by=None):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (tg_user.username or "", tg_user.first_name or "", tg_user.id),
        )
        conn.commit()
        conn.close()
        return False  # not new
    conn.execute(
        "INSERT INTO users (user_id,username,first_name,balance,referred_by,joined_at) VALUES (?,?,?,?,?,?)",
        (tg_user.id, tg_user.username or "", tg_user.first_name or "", 0, referred_by, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return True  # new user


def is_admin(user_id):
    if user_id == OWNER_ID:
        return True
    conn = db()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return bool(row)


def is_banned(user_id):
    u = get_user(user_id)
    return bool(u and u["banned"])


def update_balance(user_id, delta):
    conn = db()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
    conn.commit()
    conn.close()


def gen_id(prefix):
    return f"{prefix}{int(time.time())}{random.randint(100,999)}"


# ---------- service helpers ----------
def get_service(skey):
    conn = db()
    row = conn.execute("SELECT * FROM services WHERE skey=?", (skey,)).fetchone()
    conn.close()
    return row


def get_all_services():
    conn = db()
    rows = conn.execute("SELECT * FROM services").fetchall()
    conn.close()
    return rows


# ============================================================
# STATE FLAGS (awaiting text input) — numeric range() pattern
# ============================================================
(
    ST_ORDER_LINK,
    ST_ORDER_QTY,
    ST_DEPOSIT_AMOUNT,
    ST_PROMO_INPUT,
    ST_ADMIN_ADD_ADMIN,
    ST_ADMIN_REMOVE_ADMIN,
    ST_ADMIN_ADD_CREDIT_ID,
    ST_ADMIN_ADD_CREDIT_AMT,
    ST_ADMIN_REMOVE_CREDIT_ID,
    ST_ADMIN_REMOVE_CREDIT_AMT,
    ST_ADMIN_BROADCAST,
    ST_ADMIN_PROMO_CODE,
    ST_ADMIN_PROMO_BAL,
    ST_ADMIN_PROMO_LIMIT,
    ST_ADMIN_BAN_ID,
    ST_ADMIN_UNBAN_ID,
    ST_ADMIN_TRACK_ID,
    ST_ADMIN_ADD_CHANNEL,
    ST_ADMIN_REMOVE_CHANNEL,
    ST_ADMIN_SET_API,
    ST_ADMIN_SET_APIKEY,
    ST_ADMIN_SET_PRICE_VALUE,
    ST_ADMIN_SET_PRICE_SID,
    ST_ADMIN_SET_PRICE_MIN,
    ST_ADMIN_SET_PRICE_MAX,
    ST_ADMIN_SET_WELCOME_MEDIA,
    ST_ADMIN_SET_FORCE_MEDIA,
    ST_ADMIN_SET_QR,
    ST_ADMIN_SET_PAYOUT_CHANNEL,
    ST_ADMIN_CHECK_ORDER,
) = range(30)

# in-memory temp per user_id for multi-step admin flows
TEMP = {}

# ============================================================
# TEXT TEMPLATES (kept EXACTLY as specified — only ${x}/{x} -> {x})
# ============================================================
WELCOME_MSG = """𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃

👋 <b>𝗛𝗲𝘆 — {first_name}</b>
🌸 <b>𝗪𝗘𝗟𝗖𝗢𝗠𝗘</b> • {bot_username}
━━━━━━━━━━━━━━━━━━━━
✨ <b>Sᴏᴄɪᴀʟ Mᴇᴅɪᴀ Gʀᴏᴡᴛʜ</b>
         Mᴀᴅᴇ Sɪᴍᴘʟᴇ.
╭─────────────────╮
    👥 Fᴏʟʟᴏᴡᴇʀs • ❤️ Lɪᴋᴇs
    📈 Rᴇᴀᴄʜ • 👁️ Vɪᴇᴡs  
╰─────────────────╯

━━━━━━━━━━━━━━━━━━━━
<blockquote>⚡ Iɴsᴛᴀɴᴛ Dᴇʟɪᴠᴇʀʏ
📞 24/7 Sᴜᴘᴘᴏʀᴛ — @S7U1R</blockquote>

𓂃🌸 <b>Cʜᴏᴏsᴇ Aɴ Oᴘᴛɪᴏɴ Bᴇʟᴏᴡ</b> 🌸𓂃"""

ORDER_PANEL_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🌿 <b>𝗪ᴇʟᴄᴏᴍᴇ 𝗧ᴏ</b> <b>𝗢ʀᴅᴇʀ 𝗣ᴀɴᴇʟ</b>
━━━━━━━━━━━━━━━━━━━━
🚀 <b>𝗖ʜᴏᴏꜱᴇ 𝗔 𝗣ʟᴀᴛғᴏʀᴍ</b>
<b>𝗧ᴏ 𝗖ᴏɴᴛɪɴᴜᴇ 𝗬ᴏᴜʀ 𝗢ʀᴅᴇʀ</b>
╭──────────────────╮
│✨ <b>Cʜᴏᴏsᴇ Yᴏᴜʀ Pʟᴀᴛғᴏʀᴍ</b>
╰──────────────────╯
━━━━━━━━━━━━━━━━━━━━
🔥 <b>𝗙ᴀsᴛ</b> • <b>𝗦ᴇᴄᴜʀᴇ</b> • <b>𝗥ᴇʟɪᴀʙʟᴇ</b>

𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

IG_SERVICES_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

╭━━━━━━━━━━━━━━━━━━╮
 📸 <b>𝗜ɴsᴛᴀɢʀᴀᴍ 𝗦ᴇʀᴠɪᴄᴇs</b>
 👇 <i>𝗦ᴇʟᴇᴄᴛ 𝗔 𝗦ᴇʀᴠɪᴄᴇ</i>
╰━━━━━━━━━━━━━━━━━━╯"""

TG_SERVICES_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

╭━━━━━━━━━━━━━━━━━━╮
 📸 <b>𝗧ᴇʟᴇɢʀᴀᴍ 𝗦ᴇʀᴠɪᴄᴇs</b>
 👇 <i>𝗦ᴇʟᴇᴄᴛ 𝗔 𝗦ᴇʀᴠɪᴄᴇ</i>
╰━━━━━━━━━━━━━━━━━━╯"""

SERVICE_DETAIL = {
    "followers": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
🍄 <b>𝗜ɢ 𝗙ᴏʟʟᴏᴡᴇʀ𝘀</b>

✨ Hɪɢʜ Qᴜᴀʟɪᴛʏ • Rᴇᴀʟ Aᴄᴄᴏᴜɴᴛs

<blockquote expandable>❤️ <b>Qᴜᴀʟɪᴛʏ</b> — Hɪɢʜ
👁️ <b>Tʏᴘᴇ</b> — Rᴇᴀʟ Aᴄᴄᴏᴜɴᴛs
⚡ <b>Sᴘᴇᴇᴅ</b> — Bᴜʟʟᴇᴛ
🩸 <b>Dʀᴏᴘ</b> — Lᴏᴡ Dʀᴏᴘ
🩸 <b>Rᴇғɪʟʟ</b> — Nᴏ Rᴇғɪʟʟ</blockquote>
━━━━━━━━━━━━━━━━━━━━
💰 <b>₹{price} / 100 Fᴏʟʟᴏᴡᴇʀs</b>

📊 Mɪɴ — {min_qty}
📈 Mᴀx — {max_qty}

<b>📎 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ
Pʀᴏғɪʟᴇ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝗙ᴏʟʟᴏᴡᴇʀs</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Pᴜʙʟɪᴄ Pʀᴏғɪʟᴇ Lɪɴᴋ",
    },
    "likes": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
❤️ <b>𝗜ɢ 𝗟ɪᴋᴇs 𝗦ᴇʀᴠɪᴄᴇ</b>

✨ Hɪɢʜ Sᴘᴇᴇᴅ • Pʀᴇᴍɪᴜᴍ Qᴜᴀʟɪᴛʏ

<blockquote expandable>🚀 <b>Sᴘᴇᴇᴅ</b> — Hɪɢʜ
🔥 <b>Qᴜᴀʟɪᴛʏ</b> — Pʀᴇᴍɪᴜᴍ
🎯 <b>Tᴀʀɢᴇᴛ</b> — HQ Aᴄᴄᴏᴜɴᴛs
⚡ <b>Dʀᴏᴘ</b> — Nᴏɴ Dʀᴏᴘ
⚠️ <b>Rᴇғɪʟʟ</b> — Nᴏ Rᴇғɪʟʟ</blockquote>
━━━━━━━━━━━━━━━━━━━━
💰 <b>₹{price} / 100 Lɪᴋᴇs</b>

📊 Mɪɴ — {min_qty}
📈 Mᴀx — {max_qty}

<b>📎 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ
Rᴇᴇʟ / Pᴏsᴛ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝗟ɪᴋᴇꜱ</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Rᴇᴇʟ / Pᴏsᴛ Lɪɴᴋ",
    },
    "views": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
❤️ <b>𝗜ɢ 𝐕ɪᴇᴡꜱ 𝗦ᴇʀᴠɪᴄᴇ</b>

✨ Hɪɢʜ Sᴘᴇᴇᴅ • Pʀᴇᴍɪᴜᴍ Qᴜᴀʟɪᴛʏ

<blockquote expandable>🚀 <b>Sᴘᴇᴇᴅ</b> — Hɪɢʜ
🔥 <b>Qᴜᴀʟɪᴛʏ</b> — Pʀᴇᴍɪᴜᴍ
🎯 <b>Tᴀʀɢᴇᴛ</b> — HQ Aᴄᴄᴏᴜɴᴛs
⚡ <b>Dʀᴏᴘ</b> — Nᴏɴ Dʀᴏᴘ
⚠️ <b>Rᴇғɪʟʟ</b> — Nᴏ Rᴇғɪʟʟ</blockquote>
━━━━━━━━━━━━━━━━━━━━
💰 <b>₹{price} / 10,000 ᴠɪᴇᴡꜱ</b>

📊 Mɪɴ — {min_qty}
📈 Mᴀx — {max_qty}

<b>📎 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ
Rᴇᴇʟ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃
        
🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝐕ɪᴇᴡꜱ</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Rᴇᴇʟ Lɪɴᴋ",
    },
    "tg_subscribers": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

📦 <b>𝗧ɢ ~ Mᴇᴍʙᴇʀ</b>

✨ Hɪɢʜ Qᴜᴀʟɪᴛʏ • Fᴀsᴛ Dᴇʟɪᴠᴇʀʏ
<blockquote expandable>💸 <b>Pʀɪᴄᴇ</b> — ₹{price} = 100 Sᴜʙs
🔰 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
⚡ <b>Sᴛᴀʀᴛ</b> — Iɴsᴛᴀɴᴛ
💧 <b>Dʀᴏᴘ</b> — Vᴇʀʏ Lᴏᴡ • Nᴏɴ Dʀᴏᴘ</blockquote>
━━━━━━━━━━━━━━━━━━━━
<b>🔗 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ Tɢ 
Cʜᴀɴɴᴇʟ & Gᴄ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝗦ᴜʙꜱᴄʀɪʙᴇʀꜱ</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Tɢ Cʜᴀɴɴᴇʟ / Gᴄ Lɪɴᴋ",
    },
    "tg_views": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

📦 <b>𝗧ɢ ~ Pᴏsᴛ Vɪᴇᴡs 🫧</b>

✨ Hɪɢʜ Qᴜᴀʟɪᴛʏ • Fᴀsᴛ Dᴇʟɪᴠᴇʀʏ
<blockquote expandable>💸 <b>Pʀɪᴄᴇ</b> — ₹{price} = 1000 Vɪᴇᴡs
🔰 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
⚡ <b>Sᴛᴀʀᴛ</b> — Iɴsᴛᴀɴᴛ
🥷 <b>Dʀᴏᴘ</b> — 100% Nᴏɴ Dʀᴏᴘ</blockquote>
━━━━━━━━━━━━━━━━━━━━
<b>🔗 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ
Tɢ Cʜᴀɴɴᴇʟ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝐕ɪᴇᴡꜱ</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Tɢ Cʜᴀɴɴᴇʟ Lɪɴᴋ",
    },
    "tg_reactions": {
        "detail": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

📦 <b>𝗧ɢ ~ Pᴏsᴛ Rᴇᴀᴄᴛɪᴏɴ</b>

✨ Rᴇᴀʟ • Mɪxᴇᴅ Qᴜᴀʟɪᴛʏ • Fᴀsᴛ
<blockquote expandable>💸 <b>Pʀɪᴄᴇ</b> — ₹{price} = 100 Rᴇᴀᴄᴛɪᴏɴs
🔰 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
⚡ <b>Sᴛᴀʀᴛ</b> — Fᴀsᴛ
💎 <b>Qᴜᴀʟɪᴛʏ</b> — Rᴇᴀʟ Mɪxᴇᴅ</blockquote>
━━━━━━━━━━━━━━━━━━━━
<b>🔗 Sᴇɴᴅ Yᴏᴜʀ Pᴜʙʟɪᴄ
Pᴏsᴛ Lɪɴᴋ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>""",
        "qty_prompt": """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🎯 <b>𝗦ᴇʟᴇᴄᴛ 𝗥ᴇᴀᴄᴛɪᴏɴ</b>

⚡ <i>ᴇɴᴛᴇʀ ᴍᴀɴᴜᴀʟ</i>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Mɪɴ</b> — {min_qty}
📈 <b>Mᴀx</b> — {max_qty}
━━━━━━━━━━━━━━━━━━━━
👇 <b>ᴇɴᴛᴇʀ Qᴜᴀɴᴛɪᴛʏ</b>""",
        "link_label": "Pᴏsᴛ Lɪɴᴋ",
    },
}

ORDER_PLACED_PENDING = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

✅ <b>𝗢ʀᴅᴇʀ 𝗣ʟᴀᴄᴇᴅ</b>

<blockquote>⏳ <b>Sᴛᴀᴛᴜs :</b> {status}</blockquote>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Uꜱᴇʀ Iᴅ</b> — {user_id}
🔗 <b>Lɪɴᴋ</b> — {link}
📦 <b>Qᴜᴀɴᴛɪᴛʏ</b> — {quantity}
💰 <b>Cʜᴀʀɢᴇ</b> — ₹{charge}
𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

ORDER_CONFIRMED = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

✅ <b>𝗢ʀᴅᴇʀ 𝗣ʟᴀᴄᴇᴅ</b> — <i>Sᴜᴄᴄᴇssғᴜʟ!</i>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Uꜱᴇʀ Iᴅ</b> — {user_id}
🔗 <b>Lɪɴᴋ</b> — {link}
📦 <b>Qᴜᴀɴᴛɪᴛʏ</b> — {quantity}
💰 <b>Cʜᴀʀɢᴇ</b> — ₹{charge}

🆔 <b>Oʀᴅᴇʀ Iᴅ</b> — {order_id}
📊 <b>Yᴏᴜʀ Tᴏᴛᴀʟ Oʀᴅᴇʀs</b> — {total_orders}
━━━━━━━━━━━━━━━━━━━━
<blockquote>🌷 Yᴏᴜʀ ᴏʀᴅᴇʀ ʜᴀs ʙᴇᴇɴ ᴘʟᴀᴄᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ.</blockquote>
𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

PAYOUT_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

✅ <b>𝗢ʀᴅᴇʀ 𝗣ʟᴀᴄᴇᴅ</b> — <i>Sᴜᴄᴄᴇssғᴜʟ!</i>

<blockquote>👤 <b>Uꜱᴇʀ Iᴅ</b> — <a href="tg://openmessage?user_id={user_id}">{user_id}</a>
📦 <b>Sᴇʀᴠɪᴄᴇ</b> — {service}
📊 <b>Qᴜᴀɴᴛɪᴛʏ</b> — {quantity}
💰 <b>Cʜᴀʀɢᴇ</b> — ₹{charge}</blockquote>

━━━━━━━━━━━━━━━━━━━━

𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

NEW_USER_NOTIFY_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🆕 <b>𝗡ᴇᴡ Uꜱᴇʀ Jᴏɪɴᴇᴅ!</b>

<blockquote expandable>👤 <b>Nᴀᴍᴇ</b> — {name}
🔖 <b>Uꜱᴇʀɴᴀᴍᴇ</b> — @{username}
🆔 <b>Cʜᴀᴛ ID</b> — {chat_id}</blockquote>
━━━━━━━━━━━━━━━━━━━━
👥 <b>Tᴏᴛᴀʟ Uꜱᴇʀs</b> — {total_users}

𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

DEPOSIT_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

💳 <b>𝗗ᴇᴘᴏꜱɪᴛ</b>

✨ Fᴀꜱᴛ • Eᴀꜱʏ • Sᴇᴄᴜʀᴇ
💰 <b>Aᴍᴏᴜɴᴛ</b> — Eɴᴛᴇʀ Yᴏᴜʀ Aᴍᴏᴜɴᴛ
<blockquote expandable>📌 <b>Mɪɴ</b> — ₹10
📌 <b>Mᴀx</b> — ₹10,000
✏️ <b>E𝘅ᴀᴍᴘʟᴇ</b> — 100, 250, 500</blockquote>
━━━━━━━━━━━━━━━━━━━━
💸 <b>Dᴇᴘᴏꜱɪᴛ Aᴍᴏᴜɴᴛ</b>
📊 Mɪɴ — ₹10
📈 Mᴀx — ₹10,000

<b>👇 Sᴇɴᴅ Yᴏᴜʀ Aᴍᴏᴜɴᴛ Tᴏ 
     Cᴏɴᴛɪɴᴜᴇ</b>"""

DEPOSIT_PAYMENT_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

💳 <b>𝗗ᴇᴘᴏꜱɪᴛ Pᴀʏᴍᴇɴᴛ</b>

✨ Fᴀꜱᴛ • Sɪᴍᴘʟᴇ • Sᴇᴄᴜʀᴇ

🆔 <b>Oʀᴅᴇʀ ID</b> — {order_id}
💰 <b>Aᴍᴏᴜɴᴛ</b> — ₹{amount}
📊 <b>Rᴀɴɢᴇ</b> — ₹10 - ₹10,000
━━━━━━━━━━━━━━━━━━━━
<blockquote expandable>📋 <b>Pᴀʏᴍᴇɴᴛ Sᴛᴇᴘs :- </b>

1️⃣ Sᴄᴀɴ QR Iɴ Aɴʏ UPI Aᴘᴘ
2️⃣ Cᴏᴍᴘʟᴇᴛᴇ Tʜᴇ Pᴀʏᴍᴇɴᴛ
3️⃣ Cʟɪᴄᴋ <b>I Hᴀᴠᴇ Pᴀɪᴅ</b></blockquote>
<b>👇 Cᴏᴍᴘʟᴇᴛᴇ Yᴏᴜʀ Pᴀʏᴍᴇɴᴛ Tᴏ Cᴏɴᴛɪɴᴜᴇ</b>"""

DEPOSIT_PAYOUT_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

✅ <b>𝗗ᴇᴘᴏꜱɪᴛ 𝗥ᴇᴄᴇɪᴠᴇᴅ</b> — <i>Sᴜᴄᴄᴇssғᴜʟ!</i>

<blockquote>👤 <b>Uꜱᴇʀ Iᴅ</b> — <a href="tg://openmessage?user_id={user_id}">{user_id}</a>
💰 <b>Aᴍᴏᴜɴᴛ</b> — ₹{amount}
🕐 <b>Dᴇᴘᴏꜱɪᴛᴇᴅ Aᴛ</b> — {deposit_time}</blockquote>
━━━━━━━━━━━━━━━━━━━━

𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

FORCE_JOIN_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🔒 <b>𝗙ᴏʀᴄᴇ Jᴏɪɴ Rᴇǫᴜɪʀᴇᴅ</b>

✨ Jᴏɪɴ • Vᴇʀɪғʏ • Cᴏɴᴛɪɴᴜᴇ
<blockquote expandable> • 📢 Pʟᴇᴀsᴇ Jᴏɪɴ Aʟʟ Cʜᴀɴɴᴇʟs Bᴇʟᴏᴡ
Tᴏ Uꜱᴇ Tʜɪꜱ Bᴏᴛ.
• ✅ Aғᴛᴇʀ Jᴏɪɴɪɴɢ Aʟʟ Cʜᴀɴɴᴇʟs,
Tᴀᴘ <b>I've Joined</b> Tᴏ Cᴏɴᴛɪɴᴜᴇ.</blockquote>
━━━━━━━━━━━━━━━━━━━━
🌷 <b>Jᴏɪɴ Aʟʟ Cʜᴀɴɴᴇʟs & Vᴇʀɪғʏ 
    Yᴏᴜʀ Jᴏɪɴ</b>

𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"""

PROMO_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🎟️ <b>𝗣ʀᴏᴍᴏ Cᴏᴅᴇ</b>

<blockquote expandable>🎫 <b>Pʀᴏᴍᴏ Cᴏᴅᴇ</b> — Eɴᴛᴇʀ Yᴏᴜʀ Cᴏᴅᴇ</blockquote>
━━━━━━━━━━━━━━━━━━━━
<b>📎 Sᴇɴᴅ Yᴏᴜʀ Pʀᴏᴍᴏ Cᴏᴅᴇ Tᴏ 
   Cᴏɴᴛɪɴᴜᴇ</b>"""

REFER_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

🎁 <b>𝗥ᴇғᴇʀ & Eᴀʀɴ</b>

✨ Sʜᴀʀᴇ • Rᴇғᴇʀ • Eᴀʀɴ

👥 <b>Tᴏᴛᴀʟ Rᴇғᴇʀʀᴀʟs</b> — {referrals}
💰 <b>Bᴏɴᴜs</b> — ₹{bonus} / Rᴇғᴇʀʀᴀʟ
🔗 <b>Yᴏᴜʀ Lɪɴᴋ</b> —
{referral_link}
━━━━━━━━━━━━━━━━━━━━
<b>📤 Sʜᴀʀᴇ Yᴏᴜʀ Lɪɴᴋ Aɴᴅ 
     Sᴛᴀʀᴛ Eᴀʀɴɪɴɢ 💸</b>"""

PROFILE_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

👤 <b>𝗨ꜱᴇʀ Pʀᴏғɪʟᴇ</b>

✨ Aᴄᴄᴏᴜɴᴛ Sᴛᴀᴛᴜꜱ • Aᴄᴛɪᴠᴇ
<blockquote expandable>👤 <b>Uꜱᴇʀ</b> — {name}
👋 <b>Uꜱᴇʀɴᴀᴍᴇ</b> — @{username}
🆔 <b>Uꜱᴇʀ ID</b> — {user_id}</blockquote>
━━━━━━━━━━━━━━━━━━━━
<blockquote expandable>💰 <b>Bᴀʟᴀɴᴄᴇ</b> — ₹{balance}
📦 <b>Tᴏᴛᴀʟ Oʀᴅᴇʀs</b> — {total_orders}
💳 <b>Tᴏᴛᴀʟ Rᴇғᴇʀʀᴀʟs</b> — {total_referrals}</blockquote>"""

PRICE_LIST_MSG = """𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃

📋 <b>𝗦ᴇʀᴠɪᴄᴇ Pʀɪᴄᴇ Lɪꜱᴛ</b>

✨ Iɴꜱᴛᴀɢʀᴀᴍ Sᴇʀᴠɪᴄᴇꜱ
<blockquote expandable>📸 <b>Fᴏʟʟᴏᴡᴇʀs</b> — ₹{followers_price} / 100
❤️ <b>Lɪᴋᴇs</b> — ₹{likes_price} / 100
👀 <b>Vɪᴇᴡs</b> — ₹{views_price} / 10,000</blockquote>

✨ Tᴇʟᴇɢʀᴀᴍ Sᴇʀᴠɪᴄᴇꜱ
<blockquote expandable>📦 <b>Sᴜʙsᴄʀɪʙᴇʀs</b> — ₹{tg_subscribers_price} / 100
👁️ <b>Vɪᴇᴡs</b> — ₹{tg_views_price} / 1,000
💎 <b>Rᴇᴀᴄᴛɪᴏɴs</b> — ₹{tg_reactions_price} / 100</blockquote>
━━━━━━━━━━━━━━━━━━━━"""

STATUS_MSG = """╭━━━✨ Smm Panel ✨━━━╮
┃ 📊 Bot Status
┣━━━━━━━━━━━━━━━━━━
┃ 🤖 Bot: {bot_state}
┃ 👥 Total Users: {total_users}
┃ ✅ Active Users: {active_users}
┃ 🚫 Banned Users: {banned_users}
┃ 🛡️ Admins: {total_admins}
┃ 📢 Channels: {total_channels}
┃ 🎟️ Promo Codes: {total_promos}
┣━━━━━━━━━━━━━━━━━━
┃ Full bot statistics
╰━━━━━━━━━━━━━━━━━━╯"""

REFERRAL_BONUS = 1  # ₹ credited to referrer when referred user's first deposit gets approved

# ============================================================
# KEYBOARDS
# ============================================================
def main_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [kbtn("𝐎ʀᴅᴇʀ 𝐏ᴀɴᴇʟ", style="primary"), kbtn("𝗗ᴇᴩᴏꜱɪᴛ", style="primary")],
            [kbtn("𝗣ʀᴏᴍᴏ 𝗖ᴏᴅᴇ", style="success"), kbtn("𝗥ᴇꜰᴇʀ & 𝗘ᴀʀɴ", style="success")],
            [kbtn("𝗠ʏ 𝗕ᴀʟᴀɴᴄᴇ", style="success"), kbtn("𝗣ʀɪᴄᴇ 𝗟ɪꜱᴛ", style="success")],
        ],
        resize_keyboard=True,
    )


def order_panel_kb():
    return InlineKeyboardMarkup(
        [[ibtn("𝗜ɴꜱᴛᴀɢʀᴀᴍ", "platform_instagram", "success"), ibtn("𝐓ᴇʟᴇɢʀᴀᴍ", "platform_telegram", "success")]]
    )


def ig_services_kb():
    return InlineKeyboardMarkup(
        [
            [ibtn("𝗜𝗚 ꜰᴏʟʟᴏᴡᴇʀꜱ", "svc_followers", "success"), ibtn("𝗜𝗚 𝐋ɪᴋᴇꜱ", "svc_likes", "success")],
            [ibtn("𝗜𝗚 𝐕ɪᴇᴡꜱ", "svc_views", "success")],
            [ibtn("🏠 Bᴀᴄᴋ Tᴏ Hᴏᴍᴇ", "back_home", "primary")],
        ]
    )


def tg_services_kb():
    return InlineKeyboardMarkup(
        [
            [ibtn("𝗧ɢ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ", "svc_tg_subscribers", "success"), ibtn("𝗧ɢ 𝗩ɪᴇᴡꜱ", "svc_tg_views", "success")],
            [ibtn("𝗧ɢ 𝗥ᴇᴀᴄᴛɪᴏɴꜱ", "svc_tg_reactions", "success")],
            [ibtn("🏠 Bᴀᴄᴋ Tᴏ Hᴏᴍᴇ", "back_home", "primary")],
        ]
    )


def cancel_service_kb():
    return InlineKeyboardMarkup([[ibtn("𝐂ᴀɴᴄᴇʟ 𝐒ᴇᴠɪᴄᴇ", "cancel_service", "danger")]])


def order_confirm_kb(order_id):
    return InlineKeyboardMarkup(
        [[ibtn("✅ Cᴏɴғɪʀᴍ Oʀᴅᴇʀ", f"confirm_order:{order_id}", "success"),
          ibtn("❌ Cᴀɴᴄᴇʟ Oʀᴅᴇʀ", f"cancel_order:{order_id}", "danger")]]
    )


def deposit_paid_kb(deposit_id):
    return InlineKeyboardMarkup([[ibtn("✅ I Hᴀᴠᴇ Pᴀɪᴅ", f"deposit_paid:{deposit_id}", "success")]])


# ---------- Admin panel — REPLY KEYBOARD (converted from inline) ----------
def admin_main_kb():
    return ReplyKeyboardMarkup(
        [
            [kbtn("➕ Add Admin", style="success"), kbtn("➖ Remove Admin", style="danger")],
            [kbtn("💰 Add Credit", style="success"), kbtn("💸 Remove Credit", style="danger")],
            [kbtn("📢 Broadcast", style="primary"), kbtn("🎟️ Create Promo", style="primary")],
            [kbtn("📊 Status", style="primary"), kbtn("📡 Channel", style="primary")],
            [kbtn("🚫 Ban User", style="danger"), kbtn("✅ Unban User", style="success")],
            [kbtn("🕵️ Track User", style="primary"), kbtn("🔌 Bot ON/OFF", style="danger")],
            [kbtn("🌐 Set Api", style="primary"), kbtn("🔑 Set Api Key", style="primary")],
            [kbtn("💲 Set Price", style="primary"), kbtn("🖼️ Set QR", style="primary")],
            [kbtn("🖼️ Set Welcome Photo", style="primary"), kbtn("🗑️ Remove Welcome Photo", style="danger")],
            [kbtn("🖼️ Set Force Photo", style="primary"), kbtn("🗑️ Remove Force Photo", style="danger")],
            [kbtn("📤 Set Payout Channel", style="primary"), kbtn("🔍 Check Order", style="primary")],
            [kbtn("🏠 Back To User Panel", style="danger")],
        ],
        resize_keyboard=True,
    )


def channel_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [kbtn("➕ Add Channel", style="success"), kbtn("➖ Remove Channel", style="danger")],
            [kbtn("📋 List Channels", style="primary"), kbtn("🗑️ Remove All Channels", style="danger")],
            [kbtn("🔙 Back To Admin Panel", style="primary")],
        ],
        resize_keyboard=True,
    )


def set_price_kb():
    return ReplyKeyboardMarkup(
        [
            [kbtn("𝗜𝗚 ꜰᴏʟʟᴏᴡᴇʀꜱ", style="success"), kbtn("𝗜𝗚 𝐋ɪᴋᴇꜱ", style="success")],
            [kbtn("𝗜𝗚 𝐕ɪᴇᴡꜱ", style="success"), kbtn("𝗧ɢ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ", style="success")],
            [kbtn("𝗧ɢ 𝗩ɪᴇᴡꜱ", style="success"), kbtn("𝗧ɢ 𝗥ᴇᴀᴄᴛɪᴏɴꜱ", style="success")],
            [kbtn("🔙 Back To Admin Panel", style="primary")],
        ],
        resize_keyboard=True,
    )


# text-button -> action-key maps (used to route reply-keyboard taps in admin panel)
ADMIN_MAIN_ACTIONS = {
    "➕ Add Admin": "adm_add_admin",
    "➖ Remove Admin": "adm_remove_admin",
    "💰 Add Credit": "adm_add_credit",
    "💸 Remove Credit": "adm_remove_credit",
    "📢 Broadcast": "adm_broadcast",
    "🎟️ Create Promo": "adm_create_promo",
    "📊 Status": "adm_status",
    "📡 Channel": "adm_channel",
    "🚫 Ban User": "adm_ban",
    "✅ Unban User": "adm_unban",
    "🕵️ Track User": "adm_track",
    "🔌 Bot ON/OFF": "adm_toggle_bot",
    "🌐 Set Api": "adm_set_api",
    "🔑 Set Api Key": "adm_set_apikey",
    "💲 Set Price": "adm_set_price",
    "🖼️ Set QR": "adm_set_qr",
    "🖼️ Set Welcome Photo": "adm_set_welcome_photo",
    "🗑️ Remove Welcome Photo": "adm_remove_welcome_photo",
    "🖼️ Set Force Photo": "adm_set_force_photo",
    "🗑️ Remove Force Photo": "adm_remove_force_photo",
    "📤 Set Payout Channel": "adm_set_payout_channel",
    "🔍 Check Order": "adm_check_order",
    "🏠 Back To User Panel": "adm_exit",
}

CHANNEL_MENU_ACTIONS = {
    "➕ Add Channel": "ch_add",
    "➖ Remove Channel": "ch_remove",
    "📋 List Channels": "ch_list",
    "🗑️ Remove All Channels": "ch_remove_all",
    "🔙 Back To Admin Panel": "adm_back",
}

PRICE_MENU_ACTIONS = {
    "𝗜𝗚 ꜰᴏʟʟᴏᴡᴇʀꜱ": "price_followers",
    "𝗜𝗚 𝐋ɪᴋᴇꜱ": "price_likes",
    "𝗜𝗚 𝐕ɪᴇᴡꜱ": "price_views",
    "𝗧ɢ ꜱᴜʙꜱᴄʀɪʙᴇʀꜱ": "price_tg_subscribers",
    "𝗧ɢ 𝗩ɪᴇᴡꜱ": "price_tg_views",
    "𝗧ɢ 𝗥ᴇᴀᴄᴛɪᴏɴꜱ": "price_tg_reactions",
}

ADMIN_TEXT_ACTIONS = {**ADMIN_MAIN_ACTIONS, **CHANNEL_MENU_ACTIONS, **PRICE_MENU_ACTIONS}
ADMIN_TEXT_PATTERN = "^(" + "|".join(re.escape(k) for k in ADMIN_TEXT_ACTIONS) + ")$"


def force_join_kb(channels):
    rows = []
    for ch in channels:
        link = ch["link"]
        if link:
            rows.append([InlineKeyboardButton(f"📢 {ch['title'] or 'Join Channel'}", url=link, api_kwargs={"style": "primary"})])
    rows.append([ibtn("✅ I'ᴠᴇ Jᴏɪɴᴇᴅ", "check_joined", "success")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# SMM API CALL
# ============================================================
def call_smm_api(action, **params):
    api_url = get_setting("api_url")
    api_key = get_setting("api_key")
    if not api_url or not api_key:
        return {"error": "API not configured"}
    payload = {"key": api_key, "action": action}
    payload.update(params)
    try:
        r = requests.post(api_url, data=payload, timeout=15)
        return r.json()
    except Exception as e:
        logger.error(f"SMM API error: {e}")
        return {"error": str(e)}


def fetch_provider_service(service_id):
    """Calls the provider's 'services' action (standard SMM API v2) and returns the
    matching service dict (with real min/max/rate) so admin doesn't have to guess."""
    result = call_smm_api("services")
    if not isinstance(result, list):
        return None
    for s in result:
        if str(s.get("service", "")) == str(service_id):
            return s
    return None


# ============================================================
# AUTO ORDER-STATUS CHECKER — refunds balance if provider cancels/partials an order
# ============================================================
async def refund_order(context, order, amount, provider_status):
    if amount <= 0:
        return
    update_balance(order["user_id"], amount)
    conn = db()
    conn.execute("UPDATE orders SET refunded=1 WHERE order_id=?", (order["order_id"],))
    conn.commit()
    conn.close()
    try:
        await context.bot.send_message(
            order["user_id"],
            (
                "𓂃𓂃𓂃𓂃𓂃🌿𓂃𓂃𓂃𓂃𓂃\n\n"
                "⚠️ <b>𝗢ʀᴅᴇʀ Sᴛᴀᴛᴜꜱ Uᴘᴅᴀᴛᴇ</b>\n\n"
                f"🆔 <b>Oʀᴅᴇʀ Iᴅ</b> — {order['order_id']}\n"
                f"📌 <b>Pʀᴏᴠɪᴅᴇʀ Sᴛᴀᴛᴜs</b> — {provider_status}\n"
                f"💰 <b>Rᴇғᴜɴᴅᴇᴅ</b> — ₹{amount}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>🌷 Aᴀᴘᴋᴀ ʙᴀʟᴀɴᴄᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄ ʀᴇғᴜɴᴅ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ ʜᴀɪ.</blockquote>\n"
                "𓂃𓂃𓂃𓂃𓂃🌷𓂃𓂃𓂃𓂃𓂃"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


def compute_refund(order, result):
    """Given a provider status API response, decide how much (if anything) to refund.
    Returns (refund_amount, provider_status) or (0, provider_status) if nothing to refund."""
    provider_status = str(result.get("status", "")).strip()
    status_lower = provider_status.lower()

    if status_lower in ("canceled", "cancelled"):
        return order["charge"], provider_status

    if status_lower == "partial":
        try:
            remains = float(result.get("remains", 0) or 0)
        except (TypeError, ValueError):
            remains = 0
        if remains > 0 and order["quantity"]:
            return round((remains / order["quantity"]) * order["charge"], 2), provider_status

    return 0, provider_status


async def check_order_statuses(context: ContextTypes.DEFAULT_TYPE):
    """Runs periodically via JobQueue. Checks provider status for delivered orders and
    auto-refunds balance if the provider cancelled the order or delivered only partially."""
    conn = db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE status='completed' AND refunded=0 "
        "AND api_order_id IS NOT NULL AND api_order_id != ''"
    ).fetchall()
    conn.close()

    logger.info(f"[order-checker] checking {len(orders)} order(s)...")

    for order in orders:
        result = call_smm_api("status", order=order["api_order_id"])
        logger.info(f"[order-checker] {order['order_id']} (api_order_id={order['api_order_id']}) -> {result}")

        if not isinstance(result, dict):
            logger.warning(f"[order-checker] {order['order_id']}: non-dict response, skipping — {result}")
            continue
        if "error" in result:
            logger.warning(f"[order-checker] {order['order_id']}: provider returned error — {result['error']}")
            continue
        if "status" not in result:
            logger.warning(f"[order-checker] {order['order_id']}: no 'status' field in response, skipping — {result}")
            continue

        provider_status = str(result.get("status", "")).strip()
        conn = db()
        conn.execute("UPDATE orders SET provider_status=? WHERE order_id=?", (provider_status, order["order_id"]))
        conn.commit()
        conn.close()

        refund_amount, _ = compute_refund(order, result)
        if refund_amount > 0:
            await refund_order(context, order, refund_amount, provider_status)


# ============================================================
# FORCE-JOIN CHECK
# ============================================================
async def get_channels():
    conn = db()
    rows = conn.execute("SELECT * FROM channels").fetchall()
    conn.close()
    return rows


async def user_joined_all_channels(bot, user_id):
    channels = await get_channels()
    if not channels:
        return True, []
    not_joined = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                not_joined.append(ch)
        except Exception as e:
            logger.warning(f"could not check membership for {ch['chat_id']}: {e}")
            not_joined.append(ch)
    return (len(not_joined) == 0), not_joined


async def send_force_join(update_or_query, context, not_joined):
    force_media_id = get_setting("force_media_id")
    force_media_type = get_setting("force_media_type")
    text = FORCE_JOIN_MSG
    chat_id = update_or_query.effective_chat.id if hasattr(update_or_query, "effective_chat") else update_or_query.message.chat_id
    kb = force_join_kb(not_joined)
    try:
        if force_media_id and force_media_type == "photo":
            await context.bot.send_photo(chat_id, force_media_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif force_media_id and force_media_type == "video":
            await context.bot.send_video(chat_id, force_media_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        logger.error(f"send_force_join error: {e}")


# ============================================================
# BOT ON/OFF GATE
# ============================================================
async def bot_offline_block(update: Update) -> bool:
    if get_setting("bot_status", "on") == "off":
        uid = update.effective_user.id
        if not is_admin(uid):
            if update.message:
                await update.message.reply_text("🔧 Bot ᴀʙʜɪ ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴏғғʟɪɴᴇ ʜᴀɪ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ.")
            return True
    return False


# ============================================================
# /start
# ============================================================
async def notify_admins_new_user(context, user):
    conn = db()
    total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    admin_rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    text = NEW_USER_NOTIFY_MSG.format(
        name=user.first_name or "N/A",
        username=user.username or "N/A",
        chat_id=user.id,
        total_users=total_users,
    )
    for row in admin_rows:
        try:
            await context.bot.send_message(row["user_id"], text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def notify_admins_order_failed(context, order, reason):
    """Alerts all admins the moment an order fails to place with the provider —
    e.g. missing/wrong API URL, API key, or service id — so it can be fixed fast."""
    conn = db()
    admin_rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    text = (
        "⚠️ <b>Oʀᴅᴇʀ Fᴀɪʟᴇᴅ ᴛᴏ Rᴇᴀᴄʜ Pʀᴏᴠɪᴅᴇʀ!</b>\n\n"
        f"🆔 Order: {order['order_id']}\n"
        f"👤 User: {order['user_id']}\n"
        f"🧩 Service: {order['skey']}\n"
        f"📦 Quantity: {order['quantity']}\n"
        f"❌ Reason: {reason}\n\n"
        "💰 User's balance was NOT deducted."
    )
    for row in admin_rows:
        try:
            await context.bot.send_message(row["user_id"], text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await bot_offline_block(update):
        return
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 Yᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.")
        return

    referred_by = None
    if context.args:
        payload = context.args[0]
        if payload.startswith("ref_"):
            try:
                rid = int(payload.replace("ref_", ""))
                if rid != user.id:
                    referred_by = rid
            except ValueError:
                pass

    is_new = ensure_user(user, referred_by=referred_by)
    if is_new and referred_by:
        conn = db()
        conn.execute("UPDATE users SET total_referrals = total_referrals + 1 WHERE user_id=?", (referred_by,))
        conn.commit()
        conn.close()

    if is_new:
        await notify_admins_new_user(context, user)

    joined, not_joined = await user_joined_all_channels(context.bot, user.id)
    if not joined:
        await send_force_join(update, context, not_joined)
        return

    await show_welcome(update.effective_chat.id, context, user)


async def show_welcome(chat_id, context, user):
    bot_username = context.bot.username
    text = WELCOME_MSG.format(first_name=user.first_name or "there", bot_username=f"@{bot_username}" if bot_username else "")
    welcome_media_id = get_setting("welcome_media_id")
    welcome_media_type = get_setting("welcome_media_type")
    try:
        if welcome_media_id and welcome_media_type == "photo":
            await context.bot.send_photo(chat_id, welcome_media_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        elif welcome_media_id and welcome_media_type == "video":
            await context.bot.send_video(chat_id, welcome_media_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        else:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
    except Exception as e:
        logger.error(f"show_welcome error: {e}")


async def check_joined_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    joined, not_joined = await user_joined_all_channels(context.bot, user.id)
    if joined:
        conn = db()
        conn.execute("UPDATE users SET verified=1 WHERE user_id=?", (user.id,))
        conn.commit()
        conn.close()
        try:
            await query.message.delete()
        except Exception:
            pass
        await show_welcome(update.effective_chat.id, context, user)
        await query.answer("✅ Verified!")
    else:
        await query.answer("❌ Aᴀᴘ ᴀʙʜɪ ʙʜɪ sᴀʙ ᴄʜᴀɴɴᴇʟs ᴍᴇ ᴊᴏɪɴ ɴᴀʜɪɴ ʜᴀɪɴ!", show_alert=True)


# ============================================================
# MAIN REPLY-KEYBOARD BUTTON HANDLERS
# ============================================================
async def guard(update: Update) -> bool:
    """returns True if handling should stop"""
    if await bot_offline_block(update):
        return True
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 Yᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.")
        return True
    return False


async def order_panel_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    await update.message.reply_text(ORDER_PANEL_MSG, parse_mode=ParseMode.HTML, reply_markup=order_panel_kb())


async def deposit_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    TEMP[update.effective_user.id] = {"state": ST_DEPOSIT_AMOUNT}
    await update.message.reply_text(DEPOSIT_MSG, parse_mode=ParseMode.HTML)


async def promo_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    TEMP[update.effective_user.id] = {"state": ST_PROMO_INPUT}
    await update.message.reply_text(PROMO_MSG, parse_mode=ParseMode.HTML)


async def refer_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    user = update.effective_user
    u = get_user(user.id)
    bot_username = context.bot.username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    text = REFER_MSG.format(referrals=u["total_referrals"], bonus=REFERRAL_BONUS, referral_link=referral_link)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def balance_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    user = update.effective_user
    u = get_user(user.id)
    text = PROFILE_MSG.format(
        name=user.first_name or "N/A",
        username=user.username or "N/A",
        user_id=user.id,
        balance=round(u["balance"], 2),
        total_orders=u["total_orders"],
        total_referrals=u["total_referrals"],
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def price_list_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    f = get_service("followers")
    l = get_service("likes")
    v = get_service("views")
    ts = get_service("tg_subscribers")
    tv = get_service("tg_views")
    tr = get_service("tg_reactions")
    text = PRICE_LIST_MSG.format(
        followers_price=f["price"], likes_price=l["price"], views_price=v["price"],
        tg_subscribers_price=ts["price"], tg_views_price=tv["price"], tg_reactions_price=tr["price"],
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ============================================================
# CALLBACK QUERY ROUTER
# ============================================================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id

    if data == "check_joined":
        await check_joined_cb(update, context)
        return

    await query.answer()

    if get_setting("bot_status", "on") == "off" and not is_admin(user_id):
        await query.message.reply_text("🔧 Bot ᴀʙʜɪ ᴏғғʟɪɴᴇ ʜᴀɪ.")
        return

    # ---------- USER SIDE ----------
    if data == "platform_instagram":
        await query.message.edit_text(IG_SERVICES_MSG, parse_mode=ParseMode.HTML, reply_markup=ig_services_kb())
        return

    if data == "platform_telegram":
        await query.message.edit_text(TG_SERVICES_MSG, parse_mode=ParseMode.HTML, reply_markup=tg_services_kb())
        return

    if data == "back_home":
        TEMP.pop(user_id, None)
        try:
            await query.message.edit_text("🏠 <b>Rᴇᴛᴜʀɴᴇᴅ ᴛᴏ Mᴀɪɴ Pᴀɴᴇʟ</b>", parse_mode=ParseMode.HTML)
        except BadRequest:
            pass
        return

    if data.startswith("svc_"):
        skey = data.replace("svc_", "")
        svc = get_service(skey)
        if not svc:
            await query.message.reply_text("❌ Sᴇʀᴠɪᴄᴇ ɴᴏᴛ ғᴏᴜɴᴅ.")
            return
        TEMP[user_id] = {"state": ST_ORDER_LINK, "skey": skey}
        detail = SERVICE_DETAIL[skey]["detail"].format(price=svc["price"], min_qty=svc["min_qty"], max_qty=svc["max_qty"])
        await query.message.edit_text(detail, parse_mode=ParseMode.HTML, reply_markup=cancel_service_kb())
        return

    if data == "cancel_service":
        temp = TEMP.pop(user_id, None)
        skey = temp.get("skey") if temp else None
        svc = get_service(skey) if skey else None
        platform = svc["platform"] if svc else "instagram"
        try:
            if platform == "telegram":
                await query.message.edit_text(TG_SERVICES_MSG, parse_mode=ParseMode.HTML, reply_markup=tg_services_kb())
            else:
                await query.message.edit_text(IG_SERVICES_MSG, parse_mode=ParseMode.HTML, reply_markup=ig_services_kb())
        except BadRequest:
            pass
        return

    if data.startswith("confirm_order:"):
        order_id = data.split(":", 1)[1]
        await handle_confirm_order(query, context, order_id)
        return

    if data.startswith("cancel_order:"):
        order_id = data.split(":", 1)[1]
        conn = db()
        conn.execute("DELETE FROM orders WHERE order_id=? AND user_id=?", (order_id, user_id))
        conn.commit()
        conn.close()
        try:
            await query.message.edit_text("❌ <b>Oʀᴅᴇʀ Cᴀɴᴄᴇʟʟᴇᴅ.</b> Rᴇᴛᴜʀɴᴇᴅ ᴛᴏ ᴍᴀɪɴ ᴘᴀɴᴇʟ.", parse_mode=ParseMode.HTML)
        except BadRequest:
            pass
        return

    if data.startswith("deposit_paid:"):
        deposit_id = data.split(":", 1)[1]
        await handle_deposit_paid(query, context, deposit_id)
        return

    if data.startswith("dep_approve:") or data.startswith("dep_reject:"):
        if not is_admin(user_id):
            await query.message.reply_text("🚫 Admin only.")
            return
        action, deposit_id = data.split(":", 1)
        await handle_deposit_decision(query, context, deposit_id, action == "dep_approve")
        return

    # NOTE: Admin panel buttons are now REPLY KEYBOARD based (see admin_button_router)
    # — not inline — so no adm_/ch_/price_ inline branch is needed here anymore.


# ============================================================
# ORDER FLOW — text input handling
# ============================================================
async def handle_confirm_order(query, context, order_id):
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order:
        conn.close()
        await query.message.reply_text("❌ Oʀᴅᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ.")
        return
    user_id = order["user_id"]
    u = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    if u["balance"] < order["charge"]:
        conn.close()
        await query.message.edit_text(
            "❌ <b>Iɴsᴜғғɪᴄɪᴇɴᴛ Bᴀʟᴀɴᴄᴇ!</b>\n\nPʟᴇᴀsᴇ ᴅᴇᴘᴏsɪᴛ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.",
            parse_mode=ParseMode.HTML,
        )
        return

    svc = get_service(order["skey"])

    # ---- validate provider is fully configured BEFORE touching the user's balance ----
    api_url = get_setting("api_url")
    api_key = get_setting("api_key")
    if not api_url or not api_key:
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", ("API not configured", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            "❌ <b>Oʀᴅᴇʀ Fᴀɪʟᴇᴅ!</b>\n\n⚠️ SMM API ᴀʙʜɪ ᴄᴏɴғɪɢᴜʀᴇᴅ ɴᴀʜɪɴ ʜᴀɪ.\n"
            "💰 Aᴀᴘᴋᴀ ʙᴀʟᴀɴᴄᴇ ɴᴀʜɪɴ ᴋᴀᴛᴀ.\n\nAᴅᴍɪɴ ᴋᴏ ᴄᴏɴᴛᴀᴄᴛ ᴋʀᴏ.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admins_order_failed(context, order, "SMM API URL/Key not set (Set Api / Set Api Key).")
        return

    if not svc["service_id"]:
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", ("Service ID not set", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            "❌ <b>Oʀᴅᴇʀ Fᴀɪʟᴇᴅ!</b>\n\n⚠️ Yᴇ sᴇʀᴠɪᴄᴇ ᴀʙʜɪ ᴄᴏɴғɪɢᴜʀᴇᴅ ɴᴀʜɪɴ ʜᴀɪ (API Sᴇʀᴠɪᴄᴇ ID ᴍɪssɪɴɢ).\n"
            "💰 Aᴀᴘᴋᴀ ʙᴀʟᴀɴᴄᴇ ɴᴀʜɪɴ ᴋᴀᴛᴀ.\n\nAᴅᴍɪɴ ᴋᴏ ᴄᴏɴᴛᴀᴄᴛ ᴋʀᴏ.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admins_order_failed(context, order, f"Service '{svc['skey']}' has no API Service ID set (Set Price → enter service id).")
        return

    api_result = call_smm_api(
        "add",
        service=svc["service_id"],
        link=order["link"],
        quantity=order["quantity"],
    )

    api_order_id = None
    error_msg = None
    if isinstance(api_result, dict):
        if api_result.get("order"):
            api_order_id = str(api_result["order"])
        elif api_result.get("error"):
            error_msg = str(api_result["error"])
        else:
            error_msg = f"Unexpected provider response: {api_result}"
    else:
        error_msg = "Invalid/empty response from provider."

    if not api_order_id:
        # provider REJECTED the order — do NOT charge the user
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", (error_msg or "failed", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            f"❌ <b>Oʀᴅᴇʀ Fᴀɪʟᴇᴅ!</b>\n\n⚠️ <b>Rᴇᴀsᴏɴ</b> — {error_msg}\n"
            "💰 Aᴀᴘᴋᴀ ʙᴀʟᴀɴᴄᴇ ɴᴀʜɪɴ ᴋᴀᴛᴀ ʜᴀɪ.\n\nPʟᴇᴀsᴇ ᴅᴏʙᴀʀᴀ ᴛʀʏ ᴋʀᴏ ʏᴀ ᴀᴅᴍɪɴ ᴋᴏ ᴄᴏɴᴛᴀᴄᴛ ᴋʀᴏ.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admins_order_failed(context, order, error_msg or "Unknown error")
        return

    # ---- success: provider accepted the order — now it's safe to charge the user ----
    update_balance(user_id, -order["charge"])
    conn.execute(
        "UPDATE orders SET status='completed', api_order_id=? WHERE order_id=?",
        (api_order_id, order_id),
    )
    conn.execute("UPDATE users SET total_orders = total_orders + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    u = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()

    text = ORDER_CONFIRMED.format(
        user_id=user_id, link=order["link"], quantity=order["quantity"],
        charge=order["charge"], order_id=order_id, total_orders=u["total_orders"],
    )
    await query.message.edit_text(text, parse_mode=ParseMode.HTML)

    # send order notification to payout channel (if set)
    payout_channel_id = get_setting("payout_channel_id")
    if payout_channel_id:
        payout_text = PAYOUT_MSG.format(
            user_id=user_id, service=svc["name"], quantity=order["quantity"],
            charge=order["charge"],
        )
        try:
            await context.bot.send_message(payout_channel_id, payout_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"payout channel notify failed: {e}")


async def process_order_link(update, context, temp):
    link = update.message.text.strip()
    skey = temp["skey"]
    svc = get_service(skey)
    temp["link"] = link
    temp["state"] = ST_ORDER_QTY
    qty_text = SERVICE_DETAIL[skey]["qty_prompt"].format(min_qty=svc["min_qty"], max_qty=svc["max_qty"])
    await update.message.reply_text(qty_text, parse_mode=ParseMode.HTML, reply_markup=cancel_service_kb())


async def process_order_qty(update, context, temp):
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("⚠️ Kᴇᴠᴀʟ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴏ.")
        return
    qty = int(text)
    skey = temp["skey"]
    svc = get_service(skey)
    if qty < svc["min_qty"] or qty > svc["max_qty"]:
        await update.message.reply_text(f"⚠️ Qᴜᴀɴᴛɪᴛʏ {svc['min_qty']} sᴇ {svc['max_qty']} ᴋᴇ ʙᴇᴇᴄʜ ʜᴏɴɪ ᴄʜᴀʜɪʏᴇ.")
        return

    charge = round((qty / svc["unit_base"]) * svc["price"], 2)
    order_id = gen_id("ORD")
    user_id = update.effective_user.id

    conn = db()
    conn.execute(
        "INSERT INTO orders (order_id,user_id,skey,link,quantity,charge,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (order_id, user_id, skey, temp["link"], qty, charge, "pending", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    TEMP.pop(user_id, None)
    text = ORDER_PLACED_PENDING.format(status="Pending", user_id=user_id, link=temp["link"], quantity=qty, charge=charge)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=order_confirm_kb(order_id))


# ============================================================
# DEPOSIT FLOW
# ============================================================
async def process_deposit_amount(update, context, temp):
    text = update.message.text.strip().replace(",", "").replace("₹", "")
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("⚠️ Kᴇᴠᴀʟ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴏ.")
        return
    if amount < 10 or amount > 10000:
        await update.message.reply_text("⚠️ Aᴍᴏᴜɴᴛ ₹10 sᴇ ₹10,000 ᴋᴇ ʙᴇᴇᴄʜ ʜᴏɴᴀ ᴄʜᴀʜɪʏᴇ.")
        return

    deposit_id = gen_id("DEP")
    user_id = update.effective_user.id
    conn = db()
    conn.execute(
        "INSERT INTO deposits (deposit_id,user_id,amount,status,created_at) VALUES (?,?,?,?,?)",
        (deposit_id, user_id, amount, "pending", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    TEMP.pop(user_id, None)

    text = DEPOSIT_PAYMENT_MSG.format(order_id=deposit_id, amount=amount)
    qr_photo_id = get_setting("qr_photo_id")
    if qr_photo_id:
        await update.message.reply_photo(qr_photo_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=deposit_paid_kb(deposit_id))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=deposit_paid_kb(deposit_id))


async def handle_deposit_paid(query, context, deposit_id):
    conn = db()
    dep = conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
    conn.close()
    if not dep:
        await query.message.reply_text("❌ Dᴇᴘᴏsɪᴛ ɴᴏᴛ ғᴏᴜɴᴅ.")
        return
    if dep["status"] != "pending":
        await query.answer("Already processed.", show_alert=True)
        return

    try:
        await query.message.edit_caption(caption="⏳ <b>Wᴀɪᴛɪɴɢ ғᴏʀ ᴀᴅᴍɪɴ ᴀᴘᴘʀᴏᴠᴀʟ...</b>", parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await query.message.edit_text("⏳ <b>Wᴀɪᴛɪɴɢ ғᴏʀ ᴀᴅᴍɪɴ ᴀᴘᴘʀᴏᴠᴀʟ...</b>", parse_mode=ParseMode.HTML)
        except Exception:
            pass

    conn = db()
    admin_rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()

    u = get_user(dep["user_id"])
    admin_text = (
        f"💰 <b>Nᴇᴡ Dᴇᴘᴏsɪᴛ Rᴇqᴜᴇsᴛ</b>\n\n"
        f"🆔 Order: {deposit_id}\n"
        f"👤 User: {dep['user_id']} (@{u['username'] if u else 'N/A'})\n"
        f"💰 Amount: ₹{dep['amount']}"
    )
    kb = InlineKeyboardMarkup(
        [[ibtn("✅ Approve", f"dep_approve:{deposit_id}", "success"), ibtn("❌ Reject", f"dep_reject:{deposit_id}", "danger")]]
    )
    for row in admin_rows:
        try:
            await context.bot.send_message(row["user_id"], admin_text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass


async def handle_deposit_decision(query, context, deposit_id, approved):
    conn = db()
    dep = conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        conn.close()
        await query.answer("Already processed / not found.", show_alert=True)
        return

    new_status = "approved" if approved else "rejected"
    conn.execute("UPDATE deposits SET status=? WHERE deposit_id=?", (new_status, deposit_id))

    if approved:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (dep["amount"], dep["user_id"]))
        # referral bonus on first approved deposit
        u = conn.execute("SELECT * FROM users WHERE user_id=?", (dep["user_id"],)).fetchone()
        if u and u["referred_by"] and not u["referral_credited"]:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (REFERRAL_BONUS, u["referred_by"]))
            conn.execute("UPDATE users SET referral_credited=1 WHERE user_id=?", (dep["user_id"],))
    conn.commit()
    conn.close()

    try:
        await query.edit_message_text(f"{'✅ Approved' if approved else '❌ Rejected'} — {deposit_id}")
    except Exception:
        pass

    try:
        if approved:
            await context.bot.send_message(dep["user_id"], f"✅ <b>Yᴏᴜʀ ᴅᴇᴘᴏsɪᴛ ᴏғ ₹{dep['amount']} ʜᴀs ʙᴇᴇɴ ᴀᴘᴘʀᴏᴠᴇᴅ!</b>", parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(dep["user_id"], f"❌ <b>Yᴏᴜʀ ᴅᴇᴘᴏsɪᴛ ᴏғ ₹{dep['amount']} ᴡᴀs ʀᴇᴊᴇᴄᴛᴇᴅ.</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    if approved:
        payout_channel_id = get_setting("payout_channel_id")
        if payout_channel_id:
            deposit_time = datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
            deposit_payout_text = DEPOSIT_PAYOUT_MSG.format(
                user_id=dep["user_id"], amount=dep["amount"], deposit_time=deposit_time,
            )
            try:
                await context.bot.send_message(payout_channel_id, deposit_payout_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"payout channel deposit notify failed: {e}")


# ============================================================
# PROMO FLOW
# ============================================================
async def process_promo_input(update, context, temp):
    code = update.message.text.strip().upper()
    user_id = update.effective_user.id
    conn = db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    if not promo:
        conn.close()
        await update.message.reply_text("❌ Iɴᴠᴀʟɪᴅ ᴘʀᴏᴍᴏ ᴄᴏᴅᴇ.")
        return
    if promo["used_count"] >= promo["max_uses"]:
        conn.close()
        await update.message.reply_text("❌ Tʜɪs ᴘʀᴏᴍᴏ ᴄᴏᴅᴇ ʜᴀs ʀᴇᴀᴄʜᴇᴅ ɪᴛs ʟɪᴍɪᴛ.")
        return
    already = conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, user_id)).fetchone()
    if already:
        conn.close()
        await update.message.reply_text("❌ Aᴀᴘ ᴘᴀʜʟᴇ ʜɪ ɪss ᴄᴏᴅᴇ ᴋᴏ ʀᴇᴅᴇᴇᴍ ᴋᴀʀ ᴄʜᴜᴋᴇ ʜᴀɪɴ.")
        return

    try:
        conn.execute("INSERT INTO promo_redemptions (code,user_id,redeemed_at) VALUES (?,?,?)", (code, user_id, datetime.utcnow().isoformat()))
        conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (promo["balance"], user_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        await update.message.reply_text("❌ Aᴀᴘ ᴘᴀʜʟᴇ ʜɪ ɪss ᴄᴏᴅᴇ ᴋᴏ ʀᴇᴅᴇᴇᴍ ᴋᴀʀ ᴄʜᴜᴋᴇ ʜᴀɪɴ.")
        return
    conn.close()

    TEMP.pop(user_id, None)
    await update.message.reply_text(
        f"🎉 <b>Pʀᴏᴍᴏ Cᴏᴅᴇ Cʟᴀɪᴍᴇᴅ!</b>\n\n💰 ₹{promo['balance']} ʜᴀs ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ʙᴀʟᴀɴᴄᴇ.",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# TEXT MESSAGE ROUTER (handles all "awaiting input" states)
# ============================================================
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    user_id = update.effective_user.id
    temp = TEMP.get(user_id)
    if not temp:
        return  # not awaiting anything — ignore stray text

    state = temp["state"]

    if state == ST_ORDER_LINK:
        await process_order_link(update, context, temp)
        return
    if state == ST_ORDER_QTY:
        await process_order_qty(update, context, temp)
        return
    if state == ST_DEPOSIT_AMOUNT:
        await process_deposit_amount(update, context, temp)
        return
    if state == ST_PROMO_INPUT:
        await process_promo_input(update, context, temp)
        return

    if user_id in TEMP and not is_admin(user_id):
        return

    await admin_text_router(update, context, temp, state)


# ============================================================
# ADMIN PANEL
# ============================================================
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 Yᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.")
        return
    await update.message.reply_text("🛠️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=admin_main_kb())


async def admin_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes ALL admin-panel reply-keyboard taps (main menu + channel submenu + price submenu)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return  # silently ignore — text just falls through as a normal message for non-admins
    text = update.message.text
    action = ADMIN_TEXT_ACTIONS.get(text)
    if not action:
        return
    await handle_admin_action(update, context, action, user_id)


async def handle_admin_action(update, context, data, user_id):
    conn = db()

    if data == "adm_back":
        TEMP.pop(user_id, None)
        await update.message.reply_text("🛠️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=admin_main_kb())
        conn.close()
        return

    if data == "adm_exit":
        TEMP.pop(user_id, None)
        await update.message.reply_text("🏠 <b>Rᴇᴛᴜʀɴᴇᴅ ᴛᴏ Uꜱᴇʀ Pᴀɴᴇʟ</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        conn.close()
        return

    if data == "adm_add_admin":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_ADMIN}
        await update.message.reply_text("👤 Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴇ ᴀᴅᴍɪɴ ʙᴀɴᴀɴᴀ ʜᴀɪ:")
    elif data == "adm_remove_admin":
        TEMP[user_id] = {"state": ST_ADMIN_REMOVE_ADMIN}
        await update.message.reply_text("👤 Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴇ ᴀᴅᴍɪɴ sᴇ ʜᴀᴛᴀɴᴀ ʜᴀɪ:")
    elif data == "adm_add_credit":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_CREDIT_ID}
        await update.message.reply_text("👤 Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴋᴏ ᴄʀᴇᴅɪᴛ ᴀᴅᴅ ᴋᴀʀɴᴀ ʜᴀɪ:")
    elif data == "adm_remove_credit":
        TEMP[user_id] = {"state": ST_ADMIN_REMOVE_CREDIT_ID}
        await update.message.reply_text("👤 Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴋᴀ ᴄʀᴇᴅɪᴛ ʀᴇᴍᴏᴠᴇ ᴋᴀʀɴᴀ ʜᴀɪ:")
    elif data == "adm_broadcast":
        TEMP[user_id] = {"state": ST_ADMIN_BROADCAST}
        await update.message.reply_text("📢 Bʀᴏᴀᴅᴄᴀsᴛ ᴋᴀ ᴍᴇssᴀɢᴇ ʙʜᴇᴊᴏ:")
    elif data == "adm_create_promo":
        TEMP[user_id] = {"state": ST_ADMIN_PROMO_CODE}
        await update.message.reply_text("🎟️ Nᴀʏᴀ ᴘʀᴏᴍᴏ ᴄᴏᴅᴇ ᴇɴᴛᴇʀ ᴋᴀʀᴏ (text):")
    elif data == "adm_status":
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        banned_users = conn.execute("SELECT COUNT(*) c FROM users WHERE banned=1").fetchone()["c"]
        active_users = total_users - banned_users
        total_admins = conn.execute("SELECT COUNT(*) c FROM admins").fetchone()["c"]
        total_channels = conn.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
        total_promos = conn.execute("SELECT COUNT(*) c FROM promo_codes").fetchone()["c"]
        bot_state = "🟢 Online" if get_setting("bot_status", "on") == "on" else "🔴 Offline"
        text = STATUS_MSG.format(
            bot_state=bot_state, total_users=total_users, active_users=active_users,
            banned_users=banned_users, total_admins=total_admins,
            total_channels=total_channels, total_promos=total_promos,
        )
        await update.message.reply_text(text)
    elif data == "adm_channel":
        await update.message.reply_text("📡 <b>Channel Management</b>", parse_mode=ParseMode.HTML, reply_markup=channel_menu_kb())
    elif data == "adm_ban":
        TEMP[user_id] = {"state": ST_ADMIN_BAN_ID}
        await update.message.reply_text("🚫 Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴇ ʙᴀɴ ᴋᴀʀɴᴀ ʜᴀɪ:")
    elif data == "adm_unban":
        TEMP[user_id] = {"state": ST_ADMIN_UNBAN_ID}
        await update.message.reply_text("✅ Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴇ ᴜɴʙᴀɴ ᴋᴀʀɴᴀ ʜᴀɪ:")
    elif data == "adm_track":
        TEMP[user_id] = {"state": ST_ADMIN_TRACK_ID}
        await update.message.reply_text("🕵️ Uꜱᴇʀ Iᴅ ʙʜᴇᴊᴏ ᴊɪsᴇ ᴛʀᴀᴄᴋ ᴋᴀʀɴᴀ ʜᴀɪ:")
    elif data == "adm_toggle_bot":
        cur = get_setting("bot_status", "on")
        new = "off" if cur == "on" else "on"
        set_setting("bot_status", new)
        await update.message.reply_text(f"🤖 Bot ᴀʙ {'🟢 ON' if new=='on' else '🔴 OFF'} ʜᴀɪ.", reply_markup=admin_main_kb())
    elif data == "adm_set_api":
        TEMP[user_id] = {"state": ST_ADMIN_SET_API}
        await update.message.reply_text("🌐 SMM ᴘʀᴏᴠɪᴅᴇʀ ᴋᴀ API URL ʙʜᴇᴊᴏ:")
    elif data == "adm_set_apikey":
        TEMP[user_id] = {"state": ST_ADMIN_SET_APIKEY}
        await update.message.reply_text("🔑 API Kᴇʏ ʙʜᴇᴊᴏ:")
    elif data == "adm_set_price":
        await update.message.reply_text("💲 <b>Sᴇʟᴇᴄᴛ Sᴇʀᴠɪᴄᴇ Tᴏ Sᴇᴛ Pʀɪᴄᴇ</b>", parse_mode=ParseMode.HTML, reply_markup=set_price_kb())
    elif data == "adm_set_qr":
        TEMP[user_id] = {"state": ST_ADMIN_SET_QR}
        await update.message.reply_text("🖼️ Dᴇᴘᴏsɪᴛ QR ᴋᴀ ᴘʜᴏᴛᴏ ʙʜᴇᴊᴏ:")
    elif data == "adm_set_welcome_photo":
        TEMP[user_id] = {"state": ST_ADMIN_SET_WELCOME_MEDIA}
        await update.message.reply_text("🖼️ Wᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ ʏᴀ ᴠɪᴅᴇᴏ ʙʜᴇᴊᴏ:")
    elif data == "adm_remove_welcome_photo":
        set_setting("welcome_media_id", "")
        set_setting("welcome_media_type", "")
        await update.message.reply_text("🗑️ Wᴇʟᴄᴏᴍᴇ ᴘʜᴏᴛᴏ/ᴠɪᴅᴇᴏ ʀᴇᴍᴏᴠᴇᴅ.")
    elif data == "adm_set_force_photo":
        TEMP[user_id] = {"state": ST_ADMIN_SET_FORCE_MEDIA}
        await update.message.reply_text("🖼️ Fᴏʀᴄᴇ-ᴊᴏɪɴ ᴘʜᴏᴛᴏ ʏᴀ ᴠɪᴅᴇᴏ ʙʜᴇᴊᴏ:")
    elif data == "adm_remove_force_photo":
        set_setting("force_media_id", "")
        set_setting("force_media_type", "")
        await update.message.reply_text("🗑️ Fᴏʀᴄᴇ-ᴊᴏɪɴ ᴘʜᴏᴛᴏ/ᴠɪᴅᴇᴏ ʀᴇᴍᴏᴠᴇᴅ.")
    elif data == "adm_set_payout_channel":
        TEMP[user_id] = {"state": ST_ADMIN_SET_PAYOUT_CHANNEL}
        await update.message.reply_text(
            "📤 Pᴀʏᴏᴜᴛ ᴄʜᴀɴɴᴇʟ ᴋᴀ ʟɪɴᴋ ʙʜᴇᴊᴏ (ᴘᴜʙʟɪᴄ) ʏᴀ ID (ᴘʀɪᴠᴀᴛᴇ):\n\n"
            "Public: https://t.me/yourchannel\nPrivate: -1003657119987\n\n"
            "⚠️ Bot ᴍᴜsᴛ ʙᴇ ᴀɴ Admin ᴏғ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ, ᴠᴀʀɴᴀ ᴏʀᴅᴇʀ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ ɴᴀʜɪɴ ᴊᴀᴇɢᴀ."
        )
    elif data == "adm_check_order":
        TEMP[user_id] = {"state": ST_ADMIN_CHECK_ORDER}
        await update.message.reply_text("🔍 Oʀᴅᴇʀ Iᴅ ʙʜᴇᴊᴏ (ᴡᴏ ID ᴊᴏ ʙᴏᴛ ɴᴇ ᴜsᴇʀ ᴋᴏ ᴅɪᴋʜᴀʏᴀ ᴛʜᴀ, ᴊᴀɪsᴇ ORD1787820162414):")
    elif data.startswith("price_"):
        skey = data.replace("price_", "")
        TEMP[user_id] = {"state": ST_ADMIN_SET_PRICE_VALUE, "skey": skey}
        await update.message.reply_text(f"💲 {skey} ᴋᴇ ʟɪʏᴇ ɴᴀʏᴀ ᴘʀɪᴄᴇ (₹) ʙʜᴇᴊᴏ:")
    elif data == "ch_add":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_CHANNEL}
        await update.message.reply_text(
            "📢 Sᴛᴇᴘ 1: Sᴇɴᴅ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ ᴏʀ ᴘʀɪᴠᴀᴛᴇ ID:\n\n"
            "Public: https://t.me/yourchannel\nPrivate: -1003657119987\n\n"
            "⚠️ Bot ᴍᴜsᴛ ʙᴇ ᴀɴ Admin ᴏғ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ!"
        )
    elif data == "ch_remove":
        channels = conn.execute("SELECT * FROM channels").fetchall()
        if not channels:
            await update.message.reply_text("📭 Kᴏɪ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅ ɴᴀʜɪɴ ʜᴀɪ.")
        else:
            listing = "\n".join([f"• {c['title'] or c['chat_id']} — `{c['chat_id']}`" for c in channels])
            TEMP[user_id] = {"state": ST_ADMIN_REMOVE_CHANNEL}
            await update.message.reply_text(f"📋 Cʜᴀɴɴᴇʟs:\n{listing}\n\nRᴇᴍᴏᴠᴇ ᴋᴀʀɴᴇ ᴋᴇ ʟɪʏᴇ ᴄʜᴀɴɴᴇʟ ID/link ʙʜᴇᴊᴏ:", parse_mode=ParseMode.MARKDOWN)
    elif data == "ch_list":
        channels = conn.execute("SELECT * FROM channels").fetchall()
        if not channels:
            await update.message.reply_text("📭 Kᴏɪ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅ ɴᴀʜɪɴ ʜᴀɪ.")
        else:
            listing = "\n".join([f"• {c['title'] or 'N/A'} | {c['chat_id']} | {c['link'] or 'N/A'}" for c in channels])
            await update.message.reply_text(f"📋 <b>Cʜᴀɴɴᴇʟs</b>\n{listing}", parse_mode=ParseMode.HTML)
    elif data == "ch_remove_all":
        conn.execute("DELETE FROM channels")
        conn.commit()
        await update.message.reply_text("🗑️ Sᴀʙʜɪ ᴄʜᴀɴɴᴇʟs ʀᴇᴍᴏᴠᴇ ᴋᴀʀ ᴅɪʏᴇ ɢᴀʏᴇ.")

    conn.close()


async def admin_text_router(update, context, temp, state):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    if state == ST_ADMIN_ADD_ADMIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        conn = db()
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} ᴀʙ ᴀᴅᴍɪɴ ʜᴀɪ.")

    elif state == ST_ADMIN_REMOVE_ADMIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        conn = db()
        conn.execute("DELETE FROM admins WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} ᴋᴏ ᴀᴅᴍɪɴ sᴇ ʜᴀᴛᴀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_ADD_CREDIT_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        temp["target_id"] = int(text)
        temp["state"] = ST_ADMIN_ADD_CREDIT_AMT
        await update.message.reply_text("💰 Kɪᴛɴᴀ ᴄʀᴇᴅɪᴛ ᴀᴅᴅ ᴋᴀʀɴᴀ ʜᴀɪ (₹):")

    elif state == ST_ADMIN_ADD_CREDIT_AMT:
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ ʙʜᴇᴊᴏ.")
            return
        update_balance(temp["target_id"], amt)
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ ₹{amt} {temp['target_id']} ᴋᴇ ʙᴀʟᴀɴᴄᴇ ᴍᴇ ᴀᴅᴅ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")
        try:
            await context.bot.send_message(temp["target_id"], f"💰 Aᴀᴘᴋᴇ ᴀᴄᴄᴏᴜɴᴛ ᴍᴇ ₹{amt} ᴄʀᴇᴅɪᴛ ᴋɪʏᴀ ɢᴀʏᴀ ʜᴀɪ.")
        except Exception:
            pass

    elif state == ST_ADMIN_REMOVE_CREDIT_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        temp["target_id"] = int(text)
        temp["state"] = ST_ADMIN_REMOVE_CREDIT_AMT
        await update.message.reply_text("💸 Kɪᴛɴᴀ ᴄʀᴇᴅɪᴛ ʀᴇᴍᴏᴠᴇ ᴋᴀʀɴᴀ ʜᴀɪ (₹):")

    elif state == ST_ADMIN_REMOVE_CREDIT_AMT:
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ ʙʜᴇᴊᴏ.")
            return
        update_balance(temp["target_id"], -amt)
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ ₹{amt} {temp['target_id']} ᴋᴇ ʙᴀʟᴀɴᴄᴇ sᴇ ʀᴇᴍᴏᴠᴇ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_BROADCAST:
        TEMP.pop(user_id, None)
        await update.message.reply_text("📢 Bʀᴏᴀᴅᴄᴀsᴛ sᴇɴᴅ ʜᴏ ʀʜᴀ ʜᴀɪ...")
        context.application.create_task(run_broadcast(context, update.message.text))

    elif state == ST_ADMIN_PROMO_CODE:
        temp["code"] = text.upper()
        temp["state"] = ST_ADMIN_PROMO_BAL
        await update.message.reply_text("💰 Iss ᴄᴏᴅᴇ ᴍᴇ ᴋɪᴛɴᴀ ʙᴀʟᴀɴᴄᴇ ʜᴏɢᴀ (₹):")

    elif state == ST_ADMIN_PROMO_BAL:
        try:
            bal = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ᴀᴍᴏᴜɴᴛ ʙʜᴇᴊᴏ.")
            return
        temp["balance"] = bal
        temp["state"] = ST_ADMIN_PROMO_LIMIT
        await update.message.reply_text("👥 Kɪᴛɴᴇ ʟᴏɢ ɪss ᴄᴏᴅᴇ ᴋᴏ ʀᴇᴅᴇᴇᴍ ᴋᴀʀ sᴋᴛᴇ ʜᴀɪɴ (limit):")

    elif state == ST_ADMIN_PROMO_LIMIT:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴏ.")
            return
        limit = int(text)
        conn = db()
        try:
            conn.execute(
                "INSERT INTO promo_codes (code,balance,max_uses,used_count,created_at) VALUES (?,?,?,?,?)",
                (temp["code"], temp["balance"], limit, 0, datetime.utcnow().isoformat()),
            )
            conn.commit()
            await update.message.reply_text(f"✅ Pʀᴏᴍᴏ ᴄᴏᴅᴇ ʙᴀɴᴀ ᴅɪʏᴀ ɢᴀʏᴀ:\n\n🎟️ Code: {temp['code']}\n💰 Balance: ₹{temp['balance']}\n👥 Limit: {limit}")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ Yᴇ ᴄᴏᴅᴇ ᴘᴀʜʟᴇ sᴇ ᴍᴏᴊᴜᴅ ʜᴀɪ.")
        conn.close()
        TEMP.pop(user_id, None)

    elif state == ST_ADMIN_BAN_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        conn = db()
        conn.execute("UPDATE users SET banned=1 WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"🚫 {text} ᴋᴏ ʙᴀɴ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_UNBAN_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        conn = db()
        conn.execute("UPDATE users SET banned=0 WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} ᴋᴏ ᴜɴʙᴀɴ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_TRACK_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ID ʙʜᴇᴊᴏ.")
            return
        target = int(text)
        u = get_user(target)
        if not u:
            await update.message.reply_text("❌ Uꜱᴇʀ ɴᴏᴛ ғᴏᴜɴᴅ.")
        else:
            conn = db()
            orders = conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (target,)).fetchall()
            conn.close()
            history = "\n".join([f"• {o['order_id']} — {o['skey']} x{o['quantity']} — ₹{o['charge']} — {o['status']}" for o in orders]) or "No orders yet."
            await update.message.reply_text(
                f"🕵️ <b>User Track</b>\n\n"
                f"👤 Name: {u['first_name']}\n"
                f"👋 Username: @{u['username'] or 'N/A'}\n"
                f"🆔 ID: {u['user_id']}\n"
                f"💰 Balance: ₹{u['balance']}\n"
                f"📦 Total Orders: {u['total_orders']}\n"
                f"💳 Total Referrals: {u['total_referrals']}\n"
                f"🚫 Banned: {'Yes' if u['banned'] else 'No'}\n\n"
                f"<b>Recent Orders:</b>\n{history}",
                parse_mode=ParseMode.HTML,
            )
        TEMP.pop(user_id, None)

    elif state == ST_ADMIN_ADD_CHANNEL:
        link_or_id = text
        chat_id = link_or_id
        is_private = 0
        link = link_or_id if link_or_id.startswith("http") else None
        if link_or_id.lstrip("-").isdigit():
            chat_id = link_or_id
            is_private = 1
        try:
            chat = await context.bot.get_chat(chat_id)
            title = chat.title or chat.username or str(chat_id)
            if not link and chat.username:
                link = f"https://t.me/{chat.username}"
            conn = db()
            conn.execute(
                "INSERT INTO channels (chat_id,title,link,is_private) VALUES (?,?,?,?)",
                (str(chat.id), title, link, is_private),
            )
            conn.commit()
            conn.close()
            TEMP.pop(user_id, None)
            await update.message.reply_text(f"✅ Cʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ: {title}")
        except Exception as e:
            await update.message.reply_text(f"❌ Cʜᴀɴɴᴇʟ ᴀᴅᴅ ɴᴀʜɪɴ ʜᴏ sᴀᴋᴀ. Cʜᴇᴄᴋ ᴋʀᴏ ʙᴏᴛ ᴀᴅᴍɪɴ ʜᴀɪ ᴏʀ ɴᴀʜɪɴ.\n{e}")

    elif state == ST_ADMIN_REMOVE_CHANNEL:
        conn = db()
        conn.execute("DELETE FROM channels WHERE chat_id=? OR link=?", (text, text))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Cʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ (ᴀɢᴀʀ ᴍᴀᴛᴄʜ ʜᴜᴀ).")

    elif state == ST_ADMIN_SET_API:
        set_setting("api_url", text)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ API URL sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_SET_APIKEY:
        set_setting("api_key", text)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ API Kᴇʏ sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")

    elif state == ST_ADMIN_SET_PRICE_VALUE:
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ᴘʀɪᴄᴇ ʙʜᴇᴊᴏ.")
            return
        temp["price"] = price
        temp["state"] = ST_ADMIN_SET_PRICE_SID
        await update.message.reply_text("🆔 API Sᴇʀᴠɪᴄᴇ ID ʙʜᴇᴊᴏ (ᴀɢᴀʀ ɴᴀʜɪɴ ᴘᴛᴀ ᴛᴏ '0' ʙʜᴇᴊᴏ):")

    elif state == ST_ADMIN_SET_PRICE_SID:
        skey = temp["skey"]
        sid = None if text == "0" else text
        temp["sid"] = sid

        # try auto-syncing real min/max straight from the provider — avoids
        # manual-entry mismatches (this was the root cause of "quantity must be
        # greater than minimum" errors even when a high quantity was entered)
        provider_svc = fetch_provider_service(sid) if sid else None
        if provider_svc:
            try:
                p_min = int(float(provider_svc.get("min", 0)))
                p_max = int(float(provider_svc.get("max", 0)))
            except (TypeError, ValueError):
                p_min, p_max = None, None
            if p_min and p_max:
                conn = db()
                conn.execute(
                    "UPDATE services SET price=?, service_id=?, min_qty=?, max_qty=? WHERE skey=?",
                    (temp["price"], sid, p_min, p_max, skey),
                )
                conn.commit()
                conn.close()
                TEMP.pop(user_id, None)
                await update.message.reply_text(
                    f"✅ {skey} update ho gaya (provider se auto-sync):\n"
                    f"💲 Price: ₹{temp['price']}\n"
                    f"🆔 Service ID: {sid}\n"
                    f"📦 Provider Nᴀᴍᴇ: {provider_svc.get('name', 'N/A')}\n"
                    f"📊 Min: {p_min} | 📈 Max: {p_max}\n"
                    f"💵 Pʀᴏᴠɪᴅᴇʀ Rᴀᴛᴇ (ʀᴇғᴇʀᴇɴᴄᴇ): {provider_svc.get('rate', 'N/A')}"
                )
                return

        # fallback — provider "services" list unavailable/didn't match, ask manually
        temp["state"] = ST_ADMIN_SET_PRICE_MIN
        svc = get_service(skey)
        note = "⚠️ Provider sᴇ auto-sync ɴᴀʜɪɴ ʜᴏ sᴀᴋᴀ (services list ᴍᴇ ID ɴᴀʜɪɴ ᴍɪʟᴀ). " if sid else ""
        await update.message.reply_text(
            f"{note}📊 Mɪɴ Qᴜᴀɴᴛɪᴛʏ ʙʜᴇᴊᴏ (ᴘʀᴏᴠɪᴅᴇʀ ᴋᴇ ᴘᴀɴᴇʟ ᴍᴇ ᴅᴇᴋʜᴏ ᴡᴀʜɪ ᴀssʟᴀɪ Mɪɴ ᴋʏᴀ ʜᴀɪ):\n"
            f"[ᴀʙʜɪ ꜱᴇᴛ ʜᴀɪ: {svc['min_qty']}]"
        )

    elif state == ST_ADMIN_SET_PRICE_MIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴏ.")
            return
        temp["min_qty"] = int(text)
        temp["state"] = ST_ADMIN_SET_PRICE_MAX
        svc = get_service(temp["skey"])
        await update.message.reply_text(
            f"📈 Mᴀx Qᴜᴀɴᴛɪᴛʏ ʙʜᴇᴊᴏ (ᴘʀᴏᴠɪᴅᴇʀ ᴋᴇ ᴘᴀɴᴇʟ ᴍᴇ ᴅᴇᴋʜᴏ ᴡᴀʜɪ ᴀssʟᴀɪ Mᴀx ᴋʏᴀ ʜᴀɪ):\n"
            f"[ᴀʙʜɪ ꜱᴇᴛ ʜᴀɪ: {svc['max_qty']}]"
        )

    elif state == ST_ADMIN_SET_PRICE_MAX:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Vᴀʟɪᴅ ɴᴜᴍʙᴇʀ ʙʜᴇᴊᴏ.")
            return
        max_qty = int(text)
        skey = temp["skey"]
        conn = db()
        conn.execute(
            "UPDATE services SET price=?, service_id=?, min_qty=?, max_qty=? WHERE skey=?",
            (temp["price"], temp["sid"], temp["min_qty"], max_qty, skey),
        )
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(
            f"✅ {skey} update ho gaya:\n"
            f"💲 Price: ₹{temp['price']}\n"
            f"🆔 Service ID: {temp['sid'] or 'not set'}\n"
            f"📊 Min: {temp['min_qty']} | 📈 Max: {max_qty}"
        )

    elif state == ST_ADMIN_SET_PAYOUT_CHANNEL:
        link_or_id = text.strip()
        chat_id = link_or_id
        if link_or_id.lstrip("-").isdigit():
            chat_id = link_or_id
        try:
            chat = await context.bot.get_chat(chat_id)
            member = await context.bot.get_chat_member(chat_id=chat.id, user_id=context.bot.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("⚠️ Bot uss channel ka Admin nahi hai. Pehle bot ko admin banao, phir dobara try karo.")
                return
            title = chat.title or chat.username or str(chat.id)
            set_setting("payout_channel_id", str(chat.id))
            set_setting("payout_channel_title", title)
            TEMP.pop(user_id, None)
            await update.message.reply_text(f"✅ Pᴀʏᴏᴜᴛ ᴄʜᴀɴɴᴇʟ sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ: {title}")
        except Exception as e:
            await update.message.reply_text(f"❌ Cʜᴀɴɴᴇʟ sᴇᴛ ɴᴀʜɪɴ ʜᴏ sᴀᴋᴀ. Cʜᴇᴄᴋ ᴋʀᴏ ʙᴏᴛ ᴀᴅᴍɪɴ ʜᴀɪ ᴏʀ ɴᴀʜɪɴ.\n{e}")

    elif state == ST_ADMIN_CHECK_ORDER:
        order_id = text.strip()
        conn = db()
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        conn.close()
        if not order:
            await update.message.reply_text("❌ Yᴇ Oʀᴅᴇʀ Iᴅ ɴᴀʜɪɴ ᴍɪʟᴀ.")
            return
        if not order["api_order_id"]:
            await update.message.reply_text(f"⚠️ Iss ᴏʀᴅᴇʀ ᴋᴀ ᴋᴏɪ provider ID sᴇᴛ ɴᴀʜɪɴ ʜᴀɪ (status: {order['status']}). Pʀᴏᴠɪᴅᴇʀ ᴘᴇ ᴋʙʜɪ ɢᴀʏᴀ ʜɪ ɴᴀʜɪɴ.")
            return
        if order["refunded"]:
            await update.message.reply_text("ℹ️ Yᴇ ᴏʀᴅᴇʀ ᴘᴀʜʟᴇ sᴇ ʜɪ ʀᴇғᴜɴᴅᴇᴅ ʜᴀɪ.")
            TEMP.pop(user_id, None)
            return

        result = call_smm_api("status", order=order["api_order_id"])
        await update.message.reply_text(f"📡 <b>Provider Raw Response</b>\n<code>{result}</code>", parse_mode=ParseMode.HTML)

        if not isinstance(result, dict) or "status" not in result:
            await update.message.reply_text("⚠️ Provider sᴇ ᴠᴀʟɪᴅ status ɴᴀʜɪɴ ᴍɪʟᴀ. Aᴘɴᴀ API URL/Key & is order ᴋᴀ api_order_id check ᴋʀᴏ.")
            TEMP.pop(user_id, None)
            return

        refund_amount, provider_status = compute_refund(order, result)
        conn = db()
        conn.execute("UPDATE orders SET provider_status=? WHERE order_id=?", (provider_status, order_id))
        conn.commit()
        conn.close()

        if refund_amount > 0:
            await refund_order(context, order, refund_amount, provider_status)
            await update.message.reply_text(f"✅ Rᴇғᴜɴᴅ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ: ₹{refund_amount} (Status: {provider_status})")
        else:
            await update.message.reply_text(f"ℹ️ Provider Status: <b>{provider_status}</b> — ᴋᴏɪ ʀᴇғᴜɴᴅ ᴅᴜᴇ ɴᴀʜɪɴ ʜᴀɪ.", parse_mode=ParseMode.HTML)
        TEMP.pop(user_id, None)


# ---------- photo/video handler for admin media settings ----------
async def photo_video_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    temp = TEMP.get(user_id)
    if not temp or not is_admin(user_id):
        return
    state = temp["state"]

    file_id = None
    media_type = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        media_type = "photo"
    elif update.message.video:
        file_id = update.message.video.file_id
        media_type = "video"
    if not file_id:
        return

    if state == ST_ADMIN_SET_WELCOME_MEDIA:
        set_setting("welcome_media_id", file_id)
        set_setting("welcome_media_type", media_type)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Wᴇʟᴄᴏᴍᴇ ᴍᴇᴅɪᴀ sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")
    elif state == ST_ADMIN_SET_FORCE_MEDIA:
        set_setting("force_media_id", file_id)
        set_setting("force_media_type", media_type)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Fᴏʀᴄᴇ-ᴊᴏɪɴ ᴍᴇᴅɪᴀ sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")
    elif state == ST_ADMIN_SET_QR:
        if media_type != "photo":
            await update.message.reply_text("⚠️ Kᴇᴠᴀʟ ᴘʜᴏᴛᴏ ʙʜᴇᴊᴏ.")
            return
        set_setting("qr_photo_id", file_id)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Dᴇᴘᴏsɪᴛ QR Pʜᴏᴛᴏ sᴇᴛ ᴋᴀʀ ᴅɪʏᴀ ɢᴀʏᴀ.")


# ============================================================
# BROADCAST
# ============================================================
async def run_broadcast(context, text):
    conn = db()
    users = conn.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
    conn.close()
    sent = 0
    failed = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], text)
            sent += 1
        except (Forbidden, BadRequest):
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    logger.info(f"Broadcast done. sent={sent} failed={failed}")


# ============================================================
# ERROR HANDLER
# ============================================================
async def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")


# ============================================================
# MAIN
# ============================================================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))

    # main reply-keyboard buttons
    app.add_handler(MessageHandler(filters.Regex("^𝐎ʀᴅᴇʀ 𝐏ᴀɴᴇʟ$"), order_panel_btn))
    app.add_handler(MessageHandler(filters.Regex("^𝗗ᴇᴩᴏꜱɪᴛ$"), deposit_btn))
    app.add_handler(MessageHandler(filters.Regex("^𝗣ʀᴏᴍᴏ 𝗖ᴏᴅᴇ$"), promo_btn))
    app.add_handler(MessageHandler(filters.Regex("^𝗥ᴇꜰᴇʀ & 𝗘ᴀʀɴ$"), refer_btn))
    app.add_handler(MessageHandler(filters.Regex("^𝗠ʏ 𝗕ᴀʟᴀɴᴄᴇ$"), balance_btn))
    app.add_handler(MessageHandler(filters.Regex("^𝗣ʀɪᴄᴇ 𝗟ɪꜱᴛ$"), price_list_btn))

    # admin panel — reply-keyboard buttons (main menu + channel submenu + price submenu)
    app.add_handler(MessageHandler(filters.Regex(ADMIN_TEXT_PATTERN), admin_button_router))

    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, photo_video_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.add_error_handler(error_handler)

    # auto order-status checker (refunds balance on provider cancel/partial)
    if app.job_queue is not None:
        app.job_queue.run_repeating(check_order_statuses, interval=300, first=30)
    else:
        logger.warning(
            "JobQueue not available — auto refund checker will NOT run. "
            "Install with: pip install \"python-telegram-bot[job-queue]\" --break-system-packages"
        )

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
