# smm_panel_bot.py - SMM Panel Bot with Auto-Payment Verification

import logging
import sqlite3
import time
import random
import string
import re
import asyncio
import aiohttp
import json
from datetime import datetime
from io import BytesIO

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
# CONFIG
# ============================================================
BOT_TOKEN = "8666643142:AAGeTnxcyPIbCUiCZJofy119X3obi6m6Fpc"
OWNER_ID = 8408439521
DB_FILE = "smm_panel.db"

# Auto-Payment API Configuration
PAYMENT_API_KEY = "fb559a6b9c1048f19e85"  # REPLACE WITH YOUR ACTUAL KEY
PAYMENT_API_URL = "https://auto-payment.ximanta.xyz/api/verify"

# QR Generation API
QR_API_URL = "https://qr-api-vercel.vercel.app/qr"

# UPI Configuration
UPI_ID = "7368014753@fam"
BOT_NAME = "SMM Panel"

# Auto-Confirmation Settings
AUTO_CONFIRM_ENABLED = True
AUTO_CONFIRM_MAX_RETRIES = 3
AUTO_CONFIRM_TIMEOUT = 30  # seconds per check

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("smm_panel_bot")

# ============================================================
# BUTTON HELPERS
# ============================================================
def ibtn(text, callback_data, style=None):
    kwargs = {}
    if style:
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
            created_at TEXT,
            remark TEXT,
            paid_amount REAL DEFAULT 0
        )
    """)

    dep_cols = [r["name"] for r in cur.execute("PRAGMA table_info(deposits)").fetchall()]
    if "remark" not in dep_cols:
        cur.execute("ALTER TABLE deposits ADD COLUMN remark TEXT")
    if "paid_amount" not in dep_cols:
        cur.execute("ALTER TABLE deposits ADD COLUMN paid_amount REAL DEFAULT 0")

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

    # Seed default services
    defaults = [
        ("followers", "Instagram Followers", 6.59, 100, None, 100, 10000, "instagram"),
        ("likes", "Instagram Likes", 4.5, 100, None, 10, 100000, "instagram"),
        ("views", "Instagram Views", 7.0, 10000, None, 100, 100000, "instagram"),
        ("tg_subscribers", "Telegram Subscribers", 6.0, 100, None, 10, 10000, "telegram"),
        ("tg_views", "Telegram Views", 3.5, 1000, None, 10, 1000000, "telegram"),
        ("tg_reactions", "Telegram Reactions", 1.5, 100, None, 10, 100000, "telegram"),
    ]
    for skey, name, price, unit_base, sid, mn, mx, platform in defaults:
        cur.execute("SELECT 1 FROM services WHERE skey=?", (skey,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO services (skey,name,price,unit_base,service_id,min_qty,max_qty,platform) VALUES (?,?,?,?,?,?,?,?)",
                (skey, name, price, unit_base, sid, mn, mx, platform),
            )

    # Seed default settings
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
        return False
    conn.execute(
        "INSERT INTO users (user_id,username,first_name,balance,referred_by,joined_at) VALUES (?,?,?,?,?,?)",
        (tg_user.id, tg_user.username or "", tg_user.first_name or "", 0, referred_by, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return True


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


def generate_remark(length=10):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))


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
# STATE FLAGS
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

TEMP = {}

# ============================================================
# TEXT TEMPLATES (CLEAN, READABLE)
# ============================================================
WELCOME_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

👋 <b>Hello, {first_name}!</b>
🌐 <b>Welcome to</b> • {bot_username}
─────────────────────
✨ <b>SMM Panel</b>
         Make Social Media Great Again.
┌─────────────────────┐
│  🔥 Followers • ❤️ Likes
│  📈 Reach • 👁️ Views  
└─────────────────────┘
─────────────────────
<blockquote>⚡ Instant Delivery
📞 24/7 Support — @S7U1R</blockquote>
✨🌟 <b>Choose An Option Below</b> 🌟✨"""

ORDER_PANEL_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

🌟 <b>Welcome to</b> <b>Your Order Panel</b>
─────────────────────
🚀 <b>Choose Your Platform</b>
<b>To Continue Your Order</b>
┌─────────────────────┐
│✨ <b>Choose Your Platform</b>
└─────────────────────┘
─────────────────────
🔥 <b>Fast</b> • <b>Secure</b> • <b>Reliable</b>
✨✨✨✨✨🌟✨✨✨✨✨"""

IG_SERVICES_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

┌─────────────────────┐
 📸 <b>Instagram Services</b>
 👇 <i>Select Your Service</i>
└─────────────────────┘"""

TG_SERVICES_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

┌─────────────────────┐
 📸 <b>Telegram Services</b>
 👇 <i>Select Your Service</i>
└─────────────────────┘"""

SERVICE_DETAIL = {
    "followers": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

🍄 <b>Instagram Followers</b>

✨ High Quality • Real Accounts

<blockquote expandable>❤️ <b>Quality</b> — High
👁️ <b>Type</b> — Real Accounts
⚡ <b>Speed</b> — Instant
🩸 <b>Drop</b> — Low Drop
🩸 <b>Refund</b> — No Refund</blockquote>
─────────────────────
💰 <b>₹{price} / 100 Followers</b>

📠 Min — {min_qty}
📈 Max — {max_qty}

<b>📺 Send Your Post
Provider Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Followers</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "Post / Profile Link",
    },
    "likes": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

❤️ <b>Instagram Likes Service</b>

✨ High Speed • Premium Quality

<blockquote expandable>🚀 <b>Speed</b> — High
🔥 <b>Quality</b> — Premium
🎯 <b>Target</b> — HQ Accounts
⚡ <b>Drop</b> — Non Drop
⚠️ <b>Refund</b> — No Refund</blockquote>
─────────────────────
💰 <b>₹{price} / 100 Likes</b>

📠 Min — {min_qty}
📈 Max — {max_qty}

<b>📺 Send Your Reel / Post Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Likes</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "Reel / Post Link",
    },
    "views": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

❤️ <b>Instagram Views Service</b>

✨ High Speed • Premium Quality

<blockquote expandable>🚀 <b>Speed</b> — High
🔥 <b>Quality</b> — Premium
🎯 <b>Target</b> — HQ Accounts
⚡ <b>Drop</b> — Non Drop
⚠️ <b>Refund</b> — No Refund</blockquote>
─────────────────────
💰 <b>₹{price} / 10,000 Views</b>

📠 Min — {min_qty}
📈 Max — {max_qty}

<b>📺 Send Your Reel Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Views</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "Reel Link",
    },
    "tg_subscribers": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

📦 <b>Telegram ~ Members</b>

✨ High Quality • Fast Delivery
<blockquote expandable>💰 <b>Price</b> — ₹{price} = 100 Subs
🔰 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
⚡ <b>Start</b> — Instant
💧 <b>Drop</b> — Very Low • Non Drop</blockquote>
─────────────────────
<b>🔗 Send Your Telegram 
Channel & Group Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Subscribers</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "TG Channel / Group Link",
    },
    "tg_views": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

📦 <b>Telegram ~ Post Views 🫧</b>

✨ High Quality • Fast Delivery
<blockquote expandable>💰 <b>Price</b> — ₹{price} = 1000 Views
🔰 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
⚡ <b>Start</b> — Instant
🥷 <b>Drop</b> — 100% Non Drop</blockquote>
─────────────────────
<b>🔗 Send Your
Telegram Channel Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Views</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "TG Channel Link",
    },
    "tg_reactions": {
        "detail": """✨✨✨✨✨🌟✨✨✨✨✨

📦 <b>Telegram ~ Post Reactions</b>

✨ Real • Mixed Quality • Fast
<blockquote expandable>💰 <b>Price</b> — ₹{price} = 100 Reactions
🔰 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
⚡ <b>Start</b> — Fast
💎 <b>Quality</b> — Real Mixed</blockquote>
─────────────────────
<b>🔗 Send Your
Post Link To Continue</b>""",
        "qty_prompt": """✨✨✨✨✨🌟✨✨✨✨✨

🎯 <b>Select Reactions</b>

⚡ <i>Enter Quantity</i>
─────────────────────
📠 <b>Min</b> — {min_qty}
📈 <b>Max</b> — {max_qty}
─────────────────────
👇 <b>Enter Quantity</b>""",
        "link_label": "Post Link",
    },
}

ORDER_PLACED_PENDING = """✨✨✨✨✨🌟✨✨✨✨✨

✅ <b>Your Order Placed</b>

<blockquote>⏳ <b>Status :</b> {status}</blockquote>
─────────────────────
👤 <b>User ID</b> — {user_id}
🔗 <b>Link</b> — {link}
📦 <b>Quantity</b> — {quantity}
💰 <b>Charge</b> — ₹{charge}
✨✨✨✨✨🌟✨✨✨✨✨"""

ORDER_CONFIRMED = """✨✨✨✨✨🌟✨✨✨✨✨

✅ <b>Your Order Placed</b> — <i>Successfully!</i>
─────────────────────
👤 <b>User ID</b> — {user_id}
🔗 <b>Link</b> — {link}
📦 <b>Quantity</b> — {quantity}
💰 <b>Charge</b> — ₹{charge}

🔄 <b>Order ID</b> — {order_id}
📊 <b>Your Total Orders</b> — {total_orders}
─────────────────────
<blockquote>🌟 Your order has been processed successfully.</blockquote>
✨✨✨✨✨🌟✨✨✨✨✨"""

PAYOUT_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

✅ <b>Your Order Placed</b> — <i>Successfully!</i>

<blockquote>👤 <b>User ID</b> — <a href="tg://openmessage?user_id={user_id}">{user_id}</a>
📦 <b>Service</b> — {service}
📊 <b>Quantity</b> — {quantity}
💰 <b>Charge</b> — ₹{charge}</blockquote>
─────────────────────
✨✨✨✨✨🌟✨✨✨✨✨"""

NEW_USER_NOTIFY_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

🆕 <b>New User Joined!</b>

<blockquote expandable>👤 <b>Name</b> — {name}
🔖 <b>Username</b> — @{username}
🆔 <b>Chat ID</b> — {chat_id}</blockquote>
─────────────────────
🔥 <b>Total Users</b> — {total_users}
✨✨✨✨✨🌟✨✨✨✨✨"""

DEPOSIT_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

💳 <b>Deposit</b>

✨ Fast • Easy • Secure
💰 <b>Amount</b> — Enter Your Amount
<blockquote expandable>📌 <b>Min</b> — ₹10
📌 <b>Max</b> — ₹10,000
✏️ <b>Examples</b> — 100, 250, 500</blockquote>
─────────────────────
💰 <b>Deposit Amount</b>
📠 Min — ₹10
📈 Max — ₹10,000

<b>👇 Send Your Amount To 
     Continue</b>"""

DEPOSIT_PAYMENT_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

💳 <b>Deposit Payment</b>

✨ Fast • Simple • Secure

🔄 <b>Order ID</b> — {order_id}
💰 <b>Amount</b> — ₹{amount}
📊 <b>Range</b> — ₹10 - ₹10,000
─────────────────────
<blockquote expandable>📋 <b>Payment Steps :- </b>

1️⃣ Scan QR via Any UPI App
2️⃣ Complete the Payment
3️⃣ Click <b>I Have Paid</b></blockquote>
<b>👇 Complete Your Payment To Continue</b>"""

DEPOSIT_PAYOUT_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

✅ <b>Deposit Received</b> — <i>Successfully!</i>

<blockquote>👤 <b>User ID</b> — <a href="tg://openmessage?user_id={user_id}">{user_id}</a>
💰 <b>Amount</b> — ₹{amount}
🕐 <b>Deposited At</b> — {deposit_time}</blockquote>
─────────────────────
✨✨✨✨✨🌟✨✨✨✨✨"""

FORCE_JOIN_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

🔒 <b>Force Join Required</b>

✨ Join • Verify • Continue
<blockquote expandable> • 📢 Please Join All Channels Below
To Use This Bot.
• ✅ After Joining All Channels,
Tap <b>I've Joined</b> To Continue.</blockquote>
─────────────────────
🌟 <b>Join All Channels & Verify 
    Your Join</b>
✨✨✨✨✨🌟✨✨✨✨✨"""

PROMO_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

🎟️ <b>Promo Code</b>

<blockquote expandable>🎫 <b>Promo Code</b> — Enter Your Code</blockquote>
─────────────────────
<b>📺 Send Your Promo Code To 
   Continue</b>"""

REFER_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

🎁 <b>Refer & Earn</b>

✨ Share • Refer • Earn

🔥 <b>Total Referrals</b> — {referrals}
💰 <b>Bonus</b> — ₹{bonus} / Referral
🔗 <b>Your Link</b> —
{referral_link}
─────────────────────
<b>📣 Share Your Link And 
     Start Earning 💰</b>"""

PROFILE_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

👤 <b>User Profile</b>

✨ Account Status • Active
<blockquote expandable>👤 <b>User</b> — {name}
👋 <b>Username</b> — @{username}
🆔 <b>User ID</b> — {user_id}</blockquote>
─────────────────────
<blockquote expandable>💰 <b>Balance</b> — ₹{balance}
📦 <b>Total Orders</b> — {total_orders}
💳 <b>Total Referrals</b> — {total_referrals}</blockquote>"""

PRICE_LIST_MSG = """✨✨✨✨✨🌟✨✨✨✨✨

📋 <b>Service Price List</b>

✨ Instagram Services
<blockquote expandable>📸 <b>Followers</b> — ₹{followers_price} / 100
❤️ <b>Likes</b> — ₹{likes_price} / 100
👀 <b>Views</b> — ₹{views_price} / 10,000</blockquote>

✨ Telegram Services
<blockquote expandable>📦 <b>Subscribers</b> — ₹{tg_subscribers_price} / 100
👁️ <b>Views</b> — ₹{tg_views_price} / 1,000
💎 <b>Reactions</b> — ₹{tg_reactions_price} / 100</blockquote>
─────────────────────"""

STATUS_MSG = """┌─────────────────────✨ Smm Panel ✨─────────────────────┐
│ 📊 Bot Status
├─────────────────────────────────────────────────────
│ 🤖 Bot: {bot_state}
│ 🔥 Total Users: {total_users}
│ ✅ Active Users: {active_users}
│ 🚫 Banned Users: {banned_users}
│ 🛡️ Admins: {total_admins}
│ 📢 Channels: {total_channels}
│ 🎟️ Promo Codes: {total_promos}
├─────────────────────────────────────────────────────
│ Full bot statistics
└─────────────────────────────────────────────────────┘"""

REFERRAL_BONUS = 1

# ============================================================
# KEYBOARDS
# ============================================================
def main_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [kbtn("🛒 Order Panel", style="primary"), kbtn("💳 Deposit", style="primary")],
            [kbtn("🎟️ Promo Code", style="success"), kbtn("🎁 Refer & Earn", style="success")],
            [kbtn("💰 My Balance", style="success"), kbtn("📋 Price List", style="success")],
        ],
        resize_keyboard=True,
    )


def order_panel_kb():
    return InlineKeyboardMarkup(
        [[ibtn("📸 Instagram", "platform_instagram", "success"), ibtn("📦 Telegram", "platform_telegram", "success")]]
    )


def ig_services_kb():
    return InlineKeyboardMarkup(
        [
            [ibtn("👥 Followers", "svc_followers", "success"), ibtn("❤️ Likes", "svc_likes", "success")],
            [ibtn("👀 Views", "svc_views", "success")],
            [ibtn("🏠 Back To Home", "back_home", "primary")],
        ]
    )


def tg_services_kb():
    return InlineKeyboardMarkup(
        [
            [ibtn("👥 Subscribers", "svc_tg_subscribers", "success"), ibtn("👀 Views", "svc_tg_views", "success")],
            [ibtn("💎 Reactions", "svc_tg_reactions", "success")],
            [ibtn("🏠 Back To Home", "back_home", "primary")],
        ]
    )


def cancel_service_kb():
    return InlineKeyboardMarkup([[ibtn("❌ Cancel Service", "cancel_service", "danger")]])


def order_confirm_kb(order_id):
    return InlineKeyboardMarkup(
        [[ibtn("✅ Confirm Order", f"confirm_order:{order_id}", "success"),
          ibtn("❌ Cancel Order", f"cancel_order:{order_id}", "danger")]]
    )


def deposit_paid_kb(deposit_id):
    return InlineKeyboardMarkup([[ibtn("✅ I Have Paid", f"deposit_paid:{deposit_id}", "success")]])


# ---------- Admin panel ----------
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
            [kbtn("💰 Set Price", style="primary"), kbtn("🖼️ Set QR", style="primary")],
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
            [kbtn("👥 Followers", style="success"), kbtn("❤️ Likes", style="success")],
            [kbtn("👀 Views", style="success"), kbtn("👥 TG Subscribers", style="success")],
            [kbtn("👀 TG Views", style="success"), kbtn("💎 TG Reactions", style="success")],
            [kbtn("🔙 Back To Admin Panel", style="primary")],
        ],
        resize_keyboard=True,
    )


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
    "💰 Set Price": "adm_set_price",
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
    "👥 Followers": "price_followers",
    "❤️ Likes": "price_likes",
    "👀 Views": "price_views",
    "👥 TG Subscribers": "price_tg_subscribers",
    "👀 TG Views": "price_tg_views",
    "💎 TG Reactions": "price_tg_reactions",
}

ADMIN_TEXT_ACTIONS = {**ADMIN_MAIN_ACTIONS, **CHANNEL_MENU_ACTIONS, **PRICE_MENU_ACTIONS}
ADMIN_TEXT_PATTERN = "^(" + "|".join(re.escape(k) for k in ADMIN_TEXT_ACTIONS) + ")$"


def force_join_kb(channels):
    rows = []
    for ch in channels:
        link = ch["link"]
        if link:
            rows.append([InlineKeyboardButton(f"📢 {ch['title'] or 'Join Channel'}", url=link, api_kwargs={"style": "primary"})])
    rows.append([ibtn("✅ I've Joined", "check_joined", "success")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# AUTO-PAYMENT VERIFICATION (using external API)
# ============================================================
async def verify_payment(remark, expected_amount):
    """
    Verify payment using the auto-payment API.
    Returns: (verified, received_amount, status)
    status: 'exact', 'partial', 'overpaid', 'not_found', 'error'
    """
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "key": PAYMENT_API_KEY,
                "remark": remark,
                "amount": expected_amount,
            }
            async with session.get(PAYMENT_API_URL, params=params, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("verified"):
                        return True, data.get("received", 0), data.get("status", "exact")
                    else:
                        return False, 0, data.get("status", "not_found")
                else:
                    logger.error(f"Payment API error: {resp.status} - {await resp.text()}")
                    return False, 0, "error"
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        return False, 0, "error"


# ============================================================
# QR GENERATION (using external API)
# ============================================================
async def generate_qr(upi, amount, remark):
    """Generate QR code using the QR API."""
    try:
        qr_url = f"{QR_API_URL}?upi={upi}&amount={amount}&bot_name={BOT_NAME}&remark={remark}"
        async with aiohttp.ClientSession() as session:
            async with session.get(qr_url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.read()  # returns image bytes
                else:
                    logger.error(f"QR API error: {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        return None


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
    result = call_smm_api("services")
    if not isinstance(result, list):
        return None
    for s in result:
        if str(s.get("service", "")) == str(service_id):
            return s
    return None


# ============================================================
# AUTO ORDER-STATUS CHECKER
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
                "✨✨✨✨✨🌟✨✨✨✨✨\n\n"
                "⚠️ <b>Order Status Update</b>\n\n"
                f"🔄 <b>Order ID</b> — {order['order_id']}\n"
                f"📌 <b>Provider Status</b> — {provider_status}\n"
                f"💰 <b>Refunded</b> — ₹{amount}\n"
                "─────────────────────\n"
                "<blockquote>🌟 Your balance has been automatically refunded due to provider issue.</blockquote>\n"
                "✨✨✨✨✨🌟✨✨✨✨✨"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


def compute_refund(order, result):
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
    conn = db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE status='completed' AND refunded=0 "
        "AND api_order_id IS NOT NULL AND api_order_id != ''"
    ).fetchall()
    conn.close()
    logger.info(f"[order-checker] checking {len(orders)} order(s)...")
    for order in orders:
        result = call_smm_api("status", order=order["api_order_id"])
        if not isinstance(result, dict) or "status" not in result:
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
                await update.message.reply_text("🔧 Bot is currently offline for maintenance. Please try again later.")
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
    conn = db()
    admin_rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    text = (
        "⚠️ <b>Order Failed to Reach Provider!</b>\n\n"
        f"🔄 Order: {order['order_id']}\n"
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
        await update.message.reply_text("🚫 You are banned from using this bot.")
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
        await query.answer("❌ You still haven't joined all channels. Please join and try again.", show_alert=True)


# ============================================================
# MAIN REPLY-KEYBOARD BUTTON HANDLERS
# ============================================================
async def guard(update: Update) -> bool:
    if await bot_offline_block(update):
        return True
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
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
        await query.message.reply_text("🔧 Bot is offline for maintenance.")
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
            await query.message.edit_text("🏠 <b>Returned to Main Panel</b>", parse_mode=ParseMode.HTML)
        except BadRequest:
            pass
        return

    if data.startswith("svc_"):
        skey = data.replace("svc_", "")
        svc = get_service(skey)
        if not svc:
            await query.message.reply_text("❌ Service not found.")
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
            await query.message.edit_text("❌ <b>Order Cancelled.</b> Returned to main panel.", parse_mode=ParseMode.HTML)
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


# ============================================================
# ORDER FLOW
# ============================================================
async def handle_confirm_order(query, context, order_id):
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order:
        conn.close()
        await query.message.reply_text("❌ Order not found.")
        return
    user_id = order["user_id"]
    u = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    if u["balance"] < order["charge"]:
        conn.close()
        await query.message.edit_text(
            "❌ <b>Insufficient Balance!</b>\n\nPlease deposit and try again.",
            parse_mode=ParseMode.HTML,
        )
        return

    svc = get_service(order["skey"])

    api_url = get_setting("api_url")
    api_key = get_setting("api_key")
    if not api_url or not api_key:
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", ("API not configured", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            "❌ <b>Order Failed!</b>\n\n⚠️ SMM API is not configured yet.\n"
            "💰 Your balance was not deducted.\n\nAdmin will be notified.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admins_order_failed(context, order, "SMM API URL/Key not set (Set Api / Set Api Key).")
        return

    if not svc["service_id"]:
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", ("Service ID not set", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            "❌ <b>Order Failed!</b>\n\n⚠️ Your service is not configured yet (API Service ID missing).\n"
            "💰 Your balance was not deducted.\n\nAdmin will be notified.",
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
        conn.execute("UPDATE orders SET status='failed', provider_status=? WHERE order_id=?", (error_msg or "failed", order_id))
        conn.commit()
        conn.close()
        await query.message.edit_text(
            f"❌ <b>Order Failed!</b>\n\n⚠️ <b>Reason</b> — {error_msg}\n"
            "💰 Your balance was not deducted.\n\nPlease try again or contact admin.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admins_order_failed(context, order, error_msg or "Unknown error")
        return

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
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return
    qty = int(text)
    skey = temp["skey"]
    svc = get_service(skey)
    if qty < svc["min_qty"] or qty > svc["max_qty"]:
        await update.message.reply_text(f"⚠️ Quantity must be between {svc['min_qty']} and {svc['max_qty']}.")
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
# DEPOSIT FLOW (with auto-payment verification)
# ============================================================
async def process_deposit_amount(update, context, temp):
    text = update.message.text.strip().replace(",", "").replace("₹", "")
    try:
        amount = float(text)
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return
    if amount < 10 or amount > 10000:
        await update.message.reply_text("⚠️ Amount must be between ₹10 and ₹10,000.")
        return

    deposit_id = gen_id("DEP")
    remark = generate_remark(10)
    user_id = update.effective_user.id

    conn = db()
    conn.execute(
        "INSERT INTO deposits (deposit_id,user_id,amount,status,created_at,remark) VALUES (?,?,?,?,?,?)",
        (deposit_id, user_id, amount, "pending", datetime.utcnow().isoformat(), remark),
    )
    conn.commit()
    conn.close()

    TEMP.pop(user_id, None)

    # Generate QR
    qr_data = await generate_qr(UPI_ID, int(amount), remark)
    if qr_data is None:
        await update.message.reply_text("❌ Failed to generate QR. Please try again later.")
        return

    payment_text = DEPOSIT_PAYMENT_MSG.format(order_id=deposit_id, amount=amount)

    await update.message.reply_photo(
        photo=BytesIO(qr_data),
        caption=payment_text,
        parse_mode=ParseMode.HTML,
        reply_markup=deposit_paid_kb(deposit_id),
    )


async def handle_deposit_paid(query, context, deposit_id):
    conn = db()
    dep = conn.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,)).fetchone()
    conn.close()
    if not dep:
        await query.message.reply_text("❌ Deposit not found.")
        return
    if dep["status"] != "pending":
        await query.answer("Already processed.", show_alert=True)
        return

    # Update status to "submitted"
    conn = db()
    conn.execute("UPDATE deposits SET status='submitted' WHERE deposit_id=?", (deposit_id,))
    conn.commit()
    conn.close()

    # Notify user that verification is in progress
    status_msg = await query.message.reply_text(
        f"🔍 <b>Checking your payment...</b>\n"
        f"⏳ Looking for payment of ₹{dep['amount']}\n"
        f"📝 Remark: <code>{dep['remark']}</code>\n\n"
        f"<i>Please wait, this may take up to 30 seconds...</i>",
        parse_mode=ParseMode.HTML,
    )

    # Auto-verify
    verified = False
    received_amount = 0
    status = "not_found"
    
    if AUTO_CONFIRM_ENABLED:
        for attempt in range(1, AUTO_CONFIRM_MAX_RETRIES + 1):
            await status_msg.edit_text(
                f"🔍 <b>Checking your payment...</b>\n"
                f"⏳ Attempt {attempt}/{AUTO_CONFIRM_MAX_RETRIES}\n"
                f"📝 Remark: <code>{dep['remark']}</code>\n\n"
                f"<i>Searching for payment confirmation...</i>",
                parse_mode=ParseMode.HTML,
            )
            
            verified, received_amount, status = await verify_payment(dep['remark'], dep['amount'])
            
            if verified:
                break
            
            if attempt < AUTO_CONFIRM_MAX_RETRIES:
                await asyncio.sleep(5)

    if verified:
        # Update deposit
        conn = db()
        conn.execute("UPDATE deposits SET status='approved', paid_amount=? WHERE deposit_id=?", (received_amount, deposit_id))
        conn.commit()
        conn.close()

        # Update user balance
        update_balance(dep["user_id"], received_amount)

        # Notify user
        if status == "exact":
            await status_msg.edit_text(
                f"✅ <b>Payment Verified!</b>\n\n"
                f"<blockquote>💵 Amount: ₹{dep['amount']} (exact match)\n"
                f"💰 New Balance: ₹{get_user(dep['user_id'])['balance']}</blockquote>\n\n"
                f"🎉 Recharged successfully!",
                parse_mode=ParseMode.HTML,
            )
        elif status == "partial":
            await status_msg.edit_text(
                f"⚠️ <b>Partial Payment Detected</b>\n\n"
                f"<blockquote>💰 Expected: ₹{dep['amount']}\n"
                f"💵 Received: ₹{received_amount}\n"
                f"✅ Added: ₹{received_amount} to balance</blockquote>\n\n"
                f"<i>You paid less than the requested amount.\n"
                f"Your balance has been updated with the amount you paid.</i>",
                parse_mode=ParseMode.HTML,
            )
        elif status == "overpaid":
            await status_msg.edit_text(
                f"🎉 <b>Overpayment Detected!</b>\n\n"
                f"<blockquote>💰 Expected: ₹{dep['amount']}\n"
                f"💵 Received: ₹{received_amount}\n"
                f"✅ Added: ₹{received_amount} to balance</blockquote>\n\n"
                f"<i>You paid more than the requested amount!\n"
                f"Your balance has been updated with the full amount you paid.</i>",
                parse_mode=ParseMode.HTML,
            )

        # Send to payout channel
        payout_channel_id = get_setting("payout_channel_id")
        if payout_channel_id:
            deposit_time = datetime.utcnow().strftime("%d %b %Y, %I:%M %p UTC")
            deposit_payout_text = DEPOSIT_PAYOUT_MSG.format(
                user_id=dep["user_id"], amount=received_amount, deposit_time=deposit_time,
            )
            try:
                await context.bot.send_message(payout_channel_id, deposit_payout_text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"payout channel deposit notify failed: {e}")

    else:
        # Auto-verification failed -> send to admin
        conn = db()
        conn.execute("UPDATE deposits SET status='pending' WHERE deposit_id=?", (deposit_id,))
        conn.commit()
        conn.close()

        await status_msg.edit_text(
            f"⏳ <b>Payment Not Found Automatically</b>\n\n"
            f"<i>We couldn't automatically verify your payment.\n"
            f"Your request has been sent to admin for manual verification.\n"
            f"You'll be notified once approved.</i>",
            parse_mode=ParseMode.HTML,
        )

        user = query.from_user
        admin_message = (
            f"💳 <b>Payment Received (Manual Verification)</b>\n\n"
            f"<blockquote>👤 User: {user.first_name}\n"
            f"🆔 ID: <code>{dep['user_id']}</code>\n"
            f"💰 Amount: ₹{dep['amount']}\n"
            f"📝 Remark: <code>{dep['remark']}</code>\n"
            f"⏰ Time: {query.message.date.strftime('%Y-%m-%d %H:%M:%S')}</blockquote>\n\n"
            f"⚠️ Auto-verification failed. Please verify manually."
        )

        keyboard = [
            [
                ibtn("✅ Confirm", f"dep_approve:{deposit_id}", "success"),
                ibtn("❌ Decline", f"dep_reject:{deposit_id}", "danger"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,  # or use admin chat list
                text=admin_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Error sending to admin: {e}")
            await status_msg.edit_text(
                "❌ Failed to send payment confirmation to admin. Please try again.",
                parse_mode=ParseMode.HTML,
            )


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
        update_balance(dep["user_id"], dep["amount"])

        # Referral bonus
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
            await context.bot.send_message(dep["user_id"], f"✅ <b>Your deposit of ₹{dep['amount']} has been approved!</b>", parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(dep["user_id"], f"❌ <b>Your deposit of ₹{dep['amount']} was rejected.</b>", parse_mode=ParseMode.HTML)
    except Exception:
        pass


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
        await update.message.reply_text("❌ Invalid promo code.")
        return
    if promo["used_count"] >= promo["max_uses"]:
        conn.close()
        await update.message.reply_text("❌ This promo code has reached its usage limit.")
        return
    already = conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, user_id)).fetchone()
    if already:
        conn.close()
        await update.message.reply_text("❌ You have already redeemed this code.")
        return

    try:
        conn.execute("INSERT INTO promo_redemptions (code,user_id,redeemed_at) VALUES (?,?,?)", (code, user_id, datetime.utcnow().isoformat()))
        conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (promo["balance"], user_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        await update.message.reply_text("❌ You have already redeemed this code.")
        return
    conn.close()

    TEMP.pop(user_id, None)
    await update.message.reply_text(
        f"🎉 <b>Promo Code Claimed!</b>\n\n💰 ₹{promo['balance']} has been added to your balance.",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# TEXT MESSAGE ROUTER
# ============================================================
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await guard(update):
        return
    user_id = update.effective_user.id
    temp = TEMP.get(user_id)
    if not temp:
        return

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
        await update.message.reply_text("🚫 You are not authorized.")
        return
    await update.message.reply_text("🛠️ <b>Admin Panel</b>", parse_mode=ParseMode.HTML, reply_markup=admin_main_kb())


async def admin_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
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
        await update.message.reply_text("🏠 <b>Returned to User Panel</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        conn.close()
        return

    if data == "adm_add_admin":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_ADMIN}
        await update.message.reply_text("👤 Enter user ID to add as admin:")
    elif data == "adm_remove_admin":
        TEMP[user_id] = {"state": ST_ADMIN_REMOVE_ADMIN}
        await update.message.reply_text("👤 Enter user ID to remove from admin:")
    elif data == "adm_add_credit":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_CREDIT_ID}
        await update.message.reply_text("👤 Enter user ID to add credit:")
    elif data == "adm_remove_credit":
        TEMP[user_id] = {"state": ST_ADMIN_REMOVE_CREDIT_ID}
        await update.message.reply_text("👤 Enter user ID to remove credit:")
    elif data == "adm_broadcast":
        TEMP[user_id] = {"state": ST_ADMIN_BROADCAST}
        await update.message.reply_text("📢 Enter broadcast message:")
    elif data == "adm_create_promo":
        TEMP[user_id] = {"state": ST_ADMIN_PROMO_CODE}
        await update.message.reply_text("🎟️ Enter promo code (text):")
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
        await update.message.reply_text("🚫 Enter user ID to ban:")
    elif data == "adm_unban":
        TEMP[user_id] = {"state": ST_ADMIN_UNBAN_ID}
        await update.message.reply_text("✅ Enter user ID to unban:")
    elif data == "adm_track":
        TEMP[user_id] = {"state": ST_ADMIN_TRACK_ID}
        await update.message.reply_text("🕵️ Enter user ID to track:")
    elif data == "adm_toggle_bot":
        cur = get_setting("bot_status", "on")
        new = "off" if cur == "on" else "on"
        set_setting("bot_status", new)
        await update.message.reply_text(f"🤖 Bot is now {'🟢 ON' if new=='on' else '🔴 OFF'}", reply_markup=admin_main_kb())
    elif data == "adm_set_api":
        TEMP[user_id] = {"state": ST_ADMIN_SET_API}
        await update.message.reply_text("🌐 Enter SMM provider API URL:")
    elif data == "adm_set_apikey":
        TEMP[user_id] = {"state": ST_ADMIN_SET_APIKEY}
        await update.message.reply_text("🔑 Enter API Key:")
    elif data == "adm_set_price":
        await update.message.reply_text("💰 <b>Select Service To Set Price</b>", parse_mode=ParseMode.HTML, reply_markup=set_price_kb())
    elif data == "adm_set_qr":
        TEMP[user_id] = {"state": ST_ADMIN_SET_QR}
        await update.message.reply_text("🖼️ Send deposit QR photo:")
    elif data == "adm_set_welcome_photo":
        TEMP[user_id] = {"state": ST_ADMIN_SET_WELCOME_MEDIA}
        await update.message.reply_text("🖼️ Send welcome photo or video:")
    elif data == "adm_remove_welcome_photo":
        set_setting("welcome_media_id", "")
        set_setting("welcome_media_type", "")
        await update.message.reply_text("🗑️ Welcome media removed.")
    elif data == "adm_set_force_photo":
        TEMP[user_id] = {"state": ST_ADMIN_SET_FORCE_MEDIA}
        await update.message.reply_text("🖼️ Send force-join photo or video:")
    elif data == "adm_remove_force_photo":
        set_setting("force_media_id", "")
        set_setting("force_media_type", "")
        await update.message.reply_text("🗑️ Force-join media removed.")
    elif data == "adm_set_payout_channel":
        TEMP[user_id] = {"state": ST_ADMIN_SET_PAYOUT_CHANNEL}
        await update.message.reply_text(
            "📤 Enter payout channel link or ID (public or private):\n\n"
            "Public: https://t.me/yourchannel\nPrivate: -1003657119987\n\n"
            "⚠️ Bot must be admin of that channel."
        )
    elif data == "adm_check_order":
        TEMP[user_id] = {"state": ST_ADMIN_CHECK_ORDER}
        await update.message.reply_text("🔍 Enter Order ID (e.g., ORD1787820162414):")
    elif data.startswith("price_"):
        skey = data.replace("price_", "")
        TEMP[user_id] = {"state": ST_ADMIN_SET_PRICE_VALUE, "skey": skey}
        await update.message.reply_text(f"💰 Enter new price for {skey} (₹):")
    elif data == "ch_add":
        TEMP[user_id] = {"state": ST_ADMIN_ADD_CHANNEL}
        await update.message.reply_text(
            "📢 Step 1: Send the channel link or private ID:\n\n"
            "Public: https://t.me/yourchannel\nPrivate: -1003657119987\n\n"
            "⚠️ Bot must be admin of the channel!"
        )
    elif data == "ch_remove":
        channels = conn.execute("SELECT * FROM channels").fetchall()
        if not channels:
            await update.message.reply_text("📭 No channels added yet.")
        else:
            listing = "\n".join([f"• {c['title'] or c['chat_id']} — `{c['chat_id']}`" for c in channels])
            TEMP[user_id] = {"state": ST_ADMIN_REMOVE_CHANNEL}
            await update.message.reply_text(f"📋 Channels:\n{listing}\n\nEnter channel ID/link to remove:", parse_mode=ParseMode.MARKDOWN)
    elif data == "ch_list":
        channels = conn.execute("SELECT * FROM channels").fetchall()
        if not channels:
            await update.message.reply_text("📭 No channels added yet.")
        else:
            listing = "\n".join([f"• {c['title'] or 'N/A'} | {c['chat_id']} | {c['link'] or 'N/A'}" for c in channels])
            await update.message.reply_text(f"📋 <b>Channels</b>\n{listing}", parse_mode=ParseMode.HTML)
    elif data == "ch_remove_all":
        conn.execute("DELETE FROM channels")
        conn.commit()
        await update.message.reply_text("🗑️ All channels removed.")

    conn.close()


async def admin_text_router(update, context, temp, state):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    if state == ST_ADMIN_ADD_ADMIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        conn = db()
        conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} added as admin.")

    elif state == ST_ADMIN_REMOVE_ADMIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        conn = db()
        conn.execute("DELETE FROM admins WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} removed from admin.")

    elif state == ST_ADMIN_ADD_CREDIT_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        temp["target_id"] = int(text)
        temp["state"] = ST_ADMIN_ADD_CREDIT_AMT
        await update.message.reply_text("💰 Enter amount to add (₹):")

    elif state == ST_ADMIN_ADD_CREDIT_AMT:
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Enter a valid amount.")
            return
        update_balance(temp["target_id"], amt)
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ ₹{amt} added to {temp['target_id']}'s balance.")

    elif state == ST_ADMIN_REMOVE_CREDIT_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        temp["target_id"] = int(text)
        temp["state"] = ST_ADMIN_REMOVE_CREDIT_AMT
        await update.message.reply_text("💰 Enter amount to remove (₹):")

    elif state == ST_ADMIN_REMOVE_CREDIT_AMT:
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Enter a valid amount.")
            return
        update_balance(temp["target_id"], -amt)
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ ₹{amt} removed from {temp['target_id']}'s balance.")

    elif state == ST_ADMIN_BROADCAST:
        TEMP.pop(user_id, None)
        await update.message.reply_text("📢 Broadcasting started...")
        context.application.create_task(run_broadcast(context, update.message.text))

    elif state == ST_ADMIN_PROMO_CODE:
        temp["code"] = text.upper()
        temp["state"] = ST_ADMIN_PROMO_BAL
        await update.message.reply_text("💰 Enter balance for this code (₹):")

    elif state == ST_ADMIN_PROMO_BAL:
        try:
            bal = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Enter a valid amount.")
            return
        temp["balance"] = bal
        temp["state"] = ST_ADMIN_PROMO_LIMIT
        await update.message.reply_text("👥 Enter max uses (limit):")

    elif state == ST_ADMIN_PROMO_LIMIT:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid number.")
            return
        limit = int(text)
        conn = db()
        try:
            conn.execute(
                "INSERT INTO promo_codes (code,balance,max_uses,used_count,created_at) VALUES (?,?,?,?,?)",
                (temp["code"], temp["balance"], limit, 0, datetime.utcnow().isoformat()),
            )
            conn.commit()
            await update.message.reply_text(f"✅ Promo code created:\n\n🎟️ Code: {temp['code']}\n💰 Balance: ₹{temp['balance']}\n👥 Limit: {limit}")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ That code already exists.")
        conn.close()
        TEMP.pop(user_id, None)

    elif state == ST_ADMIN_BAN_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        conn = db()
        conn.execute("UPDATE users SET banned=1 WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"🚫 {text} banned.")

    elif state == ST_ADMIN_UNBAN_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        conn = db()
        conn.execute("UPDATE users SET banned=0 WHERE user_id=?", (int(text),))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text(f"✅ {text} unbanned.")

    elif state == ST_ADMIN_TRACK_ID:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid numeric ID.")
            return
        target = int(text)
        u = get_user(target)
        if not u:
            await update.message.reply_text("❌ User not found.")
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
            await update.message.reply_text(f"✅ Channel added: {title}")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to add channel. Check bot is admin.\n{e}")

    elif state == ST_ADMIN_REMOVE_CHANNEL:
        conn = db()
        conn.execute("DELETE FROM channels WHERE chat_id=? OR link=?", (text, text))
        conn.commit()
        conn.close()
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Channel removed.")

    elif state == ST_ADMIN_SET_API:
        set_setting("api_url", text)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ API URL set.")

    elif state == ST_ADMIN_SET_APIKEY:
        set_setting("api_key", text)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ API Key set.")

    elif state == ST_ADMIN_SET_PRICE_VALUE:
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("⚠️ Enter a valid price.")
            return
        temp["price"] = price
        temp["state"] = ST_ADMIN_SET_PRICE_SID
        await update.message.reply_text("🔄 Enter API Service ID (or '0' to skip):")

    elif state == ST_ADMIN_SET_PRICE_SID:
        skey = temp["skey"]
        sid = None if text == "0" else text
        temp["sid"] = sid

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
                    f"✅ {skey} updated (auto-sync from provider):\n"
                    f"💰 Price: ₹{temp['price']}\n"
                    f"🔄 Service ID: {sid}\n"
                    f"📦 Provider Name: {provider_svc.get('name', 'N/A')}\n"
                    f"📊 Min: {p_min} | Max: {p_max}\n"
                    f"💵 Provider Rate: {provider_svc.get('rate', 'N/A')}"
                )
                return

        temp["state"] = ST_ADMIN_SET_PRICE_MIN
        svc = get_service(skey)
        await update.message.reply_text(
            f"⚠️ Provider auto-sync not available. Enter Min Quantity:\n"
            f"[Current: {svc['min_qty']}]"
        )

    elif state == ST_ADMIN_SET_PRICE_MIN:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid number.")
            return
        temp["min_qty"] = int(text)
        temp["state"] = ST_ADMIN_SET_PRICE_MAX
        svc = get_service(temp["skey"])
        await update.message.reply_text(
            f"📈 Enter Max Quantity:\n[Current: {svc['max_qty']}]"
        )

    elif state == ST_ADMIN_SET_PRICE_MAX:
        if not text.isdigit():
            await update.message.reply_text("⚠️ Enter a valid number.")
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
            f"✅ {skey} updated:\n"
            f"💰 Price: ₹{temp['price']}\n"
            f"🔄 Service ID: {temp['sid'] or 'not set'}\n"
            f"📊 Min: {temp['min_qty']} | Max: {max_qty}"
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
                await update.message.reply_text("⚠️ Bot is not admin of that channel. Please add bot as admin first.")
                return
            title = chat.title or chat.username or str(chat.id)
            set_setting("payout_channel_id", str(chat.id))
            set_setting("payout_channel_title", title)
            TEMP.pop(user_id, None)
            await update.message.reply_text(f"✅ Payout channel set: {title}")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed. Check bot is admin.\n{e}")

    elif state == ST_ADMIN_CHECK_ORDER:
        order_id = text.strip()
        conn = db()
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        conn.close()
        if not order:
            await update.message.reply_text("❌ Order not found.")
            return
        if not order["api_order_id"]:
            await update.message.reply_text(f"⚠️ This order has no provider ID (status: {order['status']}). Provider may not have processed it.")
            return
        if order["refunded"]:
            await update.message.reply_text("ℹ️ This order has already been refunded.")
            TEMP.pop(user_id, None)
            return

        result = call_smm_api("status", order=order["api_order_id"])
        await update.message.reply_text(f"📡 <b>Provider Raw Response</b>\n<code>{result}</code>", parse_mode=ParseMode.HTML)

        if not isinstance(result, dict) or "status" not in result:
            await update.message.reply_text("⚠️ Provider did not return valid status.")
            TEMP.pop(user_id, None)
            return

        refund_amount, provider_status = compute_refund(order, result)
        conn = db()
        conn.execute("UPDATE orders SET provider_status=? WHERE order_id=?", (provider_status, order_id))
        conn.commit()
        conn.close()

        if refund_amount > 0:
            await refund_order(context, order, refund_amount, provider_status)
            await update.message.reply_text(f"✅ Refund processed: ₹{refund_amount} (Status: {provider_status})")
        else:
            await update.message.reply_text(f"ℹ️ Provider Status: <b>{provider_status}</b> — No refund needed.", parse_mode=ParseMode.HTML)
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
        await update.message.reply_text("✅ Welcome media set.")
    elif state == ST_ADMIN_SET_FORCE_MEDIA:
        set_setting("force_media_id", file_id)
        set_setting("force_media_type", media_type)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Force-join media set.")
    elif state == ST_ADMIN_SET_QR:
        if media_type != "photo":
            await update.message.reply_text("⚠️ Please send a photo for QR.")
            return
        set_setting("qr_photo_id", file_id)
        TEMP.pop(user_id, None)
        await update.message.reply_text("✅ Deposit QR photo set.")


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
    app.add_handler(MessageHandler(filters.Regex("^🛒 Order Panel$"), order_panel_btn))
    app.add_handler(MessageHandler(filters.Regex("^💳 Deposit$"), deposit_btn))
    app.add_handler(MessageHandler(filters.Regex("^🎟️ Promo Code$"), promo_btn))
    app.add_handler(MessageHandler(filters.Regex("^🎁 Refer & Earn$"), refer_btn))
    app.add_handler(MessageHandler(filters.Regex("^💰 My Balance$"), balance_btn))
    app.add_handler(MessageHandler(filters.Regex("^📋 Price List$"), price_list_btn))

    # admin panel
    app.add_handler(MessageHandler(filters.Regex(ADMIN_TEXT_PATTERN), admin_button_router))

    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, photo_video_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.add_error_handler(error_handler)

    # auto order-status checker
    if app.job_queue is not None:
        app.job_queue.run_repeating(check_order_statuses, interval=300, first=30)
    else:
        logger.warning("JobQueue not available - auto refund checker will NOT run.")

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()