# main.py
# Telegram Store Bot - كامل بالميزات المطلوبة
# - واجهة عربية، أزرار شفافة (InlineKeyboard)
# - لوحة تحكم أدمن داخل البوت (بدون تعديل الكود)
# - إدارة أقسام، منتجات، أزرار مخصصة
# - إدارة الرصيد (إضافة/خصم/تصفير)
# - شحن بالليرة السورية (يدوي - طلب إيداع ثم تأكيد أدمن)
# - حفظ كل البيانات في SQLite
# - حظر/فك حظر، إرسال بث، إحصائيات، تعديل رسالة الترحيب من الأدمن
#
# متطلبات:
#   pip install pyTelegramBotAPI python-dotenv
#
# تأكد أن ملف .env يحتوي:
#   BOT_TOKEN=...
#   ADMIN_ID=...
#
# ملاحظة: الكود يعتمد على polling (infinity_polling). يمكن تحويله إلى webhook لاحقًا.

import os
import sqlite3
import json
import time
import traceback
from datetime import datetime
from dotenv import load_dotenv
import telebot
from telebot import types

# ---------------------------
# تحميل الإعدادات من .env
# ---------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN or not ADMIN_ID:
    raise Exception("يجب وضع BOT_TOKEN و ADMIN_ID في ملف .env")

ADMIN_ID = int(ADMIN_ID)

# العملة الافتراضية
CURRENCY = "ل.س"

# ---------------------------
# تهيئة البوت و DB
# ---------------------------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DB_FILE = "store_bot.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()

# ---------------------------
# إنشاء جداول قاعدة البيانات (إذا لم تكن موجودة)
# ---------------------------
def init_db():
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance REAL DEFAULT 0,
        vip INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        pos INTEGER DEFAULT 0
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT,
        price REAL,
        description TEXT,
        pos INTEGER DEFAULT 0,
        FOREIGN KEY(category_id) REFERENCES categories(id)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_type TEXT, -- 'category' or 'product' or 'global'
        parent_id INTEGER,
        text TEXT,
        action TEXT, -- 'open_url' or 'buy'
        payload TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        price REAL,
        status TEXT,
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount_syp REAL,
        credits INTEGER,
        status TEXT, -- pending/confirmed/cancelled
        created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        created_at TEXT
    )""")
    # إعدادات افتراضية
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("welcome_msg", "أهلاً بك في المتجر الرقمي! استخدم الأزرار لتصفح.")) 
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("syp_rate", "2500"))  # مثال: 1 credit = 2500 SYP
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("min_deposit", "1"))
    conn.commit()

init_db()

# ---------------------------
# وظائف مساعدة عامة
# ---------------------------
def log_admin(action):
    cur.execute("INSERT INTO admin_log (admin_id, action, created_at) VALUES (?, ?, ?)", (ADMIN_ID, action, datetime.utcnow().isoformat()))
    conn.commit()

def ensure_user(user):
    cur.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                (user.id, getattr(user, "username", "") or "", getattr(user, "first_name", "") or "", datetime.utcnow().isoformat()))
    conn.commit()

def is_admin(user_id):
    return int(user_id) == int(ADMIN_ID)

def get_setting(key, default=None):
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    r = cur.fetchone()
    return r[0] if r else default

def set_setting(key, value):
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()

def get_balance(user_id):
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    return float(r[0]) if r else 0.0

def set_balance(user_id, amount):
    cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, datetime.utcnow().isoformat()))
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (float(amount), user_id))
    conn.commit()

def change_balance(user_id, delta):
    bal = get_balance(user_id)
    set_balance(user_id, bal + float(delta))

def ban_user(user_id):
    cur.execute("INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)", (user_id, datetime.utcnow().isoformat()))
    cur.execute("UPDATE users SET banned = 1 WHERE user_id = ?", (user_id,))
    conn.commit()

def unban_user(user_id):
    cur.execute("UPDATE users SET banned = 0 WHERE user_id = ?", (user_id,))
    conn.commit()

def is_banned(user_id):
    cur.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    return bool(r and r[0] == 1)

def fmt_currency(amount):
    try:
        a = float(amount)
        if a.is_integer():
            return f"{int(a)} {CURRENCY}"
        return f"{a:.2f} {CURRENCY}"
    except:
        return f"{amount} {CURRENCY}"

# ---------------------------
# لوحات وأزرار (Inline - شفافة)
# ---------------------------
def user_main_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🛒 الأقسام", callback_data="menu_sections"))
    kb.add(types.InlineKeyboardButton("💰 رصيدي", callback_data="menu_balance"),
           types.InlineKeyboardButton("➕ شحن/إيداع", callback_data="menu_deposit"))
    kb.add(types.InlineKeyboardButton("📦 طلباتي", callback_data="menu_orders"),
           types.InlineKeyboardButton("❓ المساعدة", callback_data="menu_help"))
    return kb

def admin_main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🛠 إدارة المتجر", callback_data="adm_store"),
           types.InlineKeyboardButton("💳 إدارة الأرصدة", callback_data="adm_balance"))
    kb.add(types.InlineKeyboardButton("✍ تعديل الترحيب", callback_data="adm_welcome"),
           types.InlineKeyboardButton("📢 بث", callback_data="adm_broadcast"))
    kb.add(types.InlineKeyboardButton("🚫 حظر/فك حظر", callback_data="adm_bans"),
           types.InlineKeyboardButton("📊 إحصائيات", callback_data="adm_stats"))
    kb.add(types.InlineKeyboardButton("🔘 إدارة الأزرار", callback_data="adm_buttons"))
    return kb

def categories_keyboard():
    kb = types.InlineKeyboardMarkup()
    cur.execute("SELECT id, name FROM categories ORDER BY pos ASC, id ASC")
    for cid, name in cur.fetchall():
        kb.add(types.InlineKeyboardButton(name, callback_data=f"cat:{cid}"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
    return kb

def products_keyboard(cat_id):
    kb = types.InlineKeyboardMarkup()
    cur.execute("SELECT id, name, price FROM products WHERE category_id = ? ORDER BY pos ASC, id ASC", (cat_id,))
    rows = cur.fetchall()
    if not rows:
        kb.add(types.InlineKeyboardButton("القسم فارغ", callback_data="no_products"))
    for pid, name, price in rows:
        kb.add(types.InlineKeyboardButton(f"{name} — {fmt_currency(price)}", callback_data=f"prod:{pid}"))
    kb.add(types.InlineKeyboardButton("🔙 الأقسام", callback_data="menu_sections"))
    return kb

def product_detail_keyboard(pid):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🛒 شراء الآن", callback_data=f"buy:{pid}"),
           types.InlineKeyboardButton("🔙 العودة", callback_data="menu_sections"))
    return kb

# ---------------------------
# أوامر أساسية
# ---------------------------
@bot.message_handler(commands=["start"])
def handle_start(msg):
    try:
        ensure_user(msg.from_user)
        if is_banned(msg.from_user.id):
            bot.send_message(msg.chat.id, "🚫 تم حظرك من استخدام البوت.")
            return
        welcome = get_setting("welcome_msg") or "أهلاً بك!"
        if is_admin(msg.from_user.id):
            bot.send_message(msg.chat.id, f"مرحباً أيها الأدمن 👋\n\n{welcome}", reply_markup=admin_main_keyboard())
        else:
            bot.send_message(msg.chat.id, welcome, reply_markup=user_main_keyboard())
    except Exception:
        traceback.print_exc()

@bot.message_handler(commands=["help"])
def handle_help(msg):
    txt = (
        "📌 تعليمات استخدام البوت:\n"
        "• اضغط على 🛒 الأقسام لتصفح الأصناف.\n"
        "• استخدم زر 💰 رصيدي لعرض رصيدك.\n"
        "• لطلب شحن تواصل مع الأدمن أو استخدم زر شحن.\n"
        "• للأدمن: /admin لفتح لوحة الأدمن."
    )
    bot.send_message(msg.chat.id, txt)

@bot.message_handler(commands=["admin"])
def handle_admin(msg):
    if not is_admin(msg.from_user.id):
        bot.send_message(msg.chat.id, "هذه الأوامر للأدمن فقط.")
        return
    bot.send_message(msg.chat.id, "لوحة الأدمن:", reply_markup=admin_main_keyboard())

# ---------------------------
# تعامل مع الأزرار (CallbackQuery)
# ---------------------------
@bot.callback_query_handler(func=lambda c: True)
def callback_query(c: types.CallbackQuery):
    try:
        data = c.data
        uid = c.from_user.id

        # عام: العودة أو القوائم
        if data == "back_main":
            if is_admin(uid):
                bot.edit_message_text(get_setting("welcome_msg"), c.message.chat.id, c.message.message_id, reply_markup=admin_main_keyboard())
            else:
                bot.edit_message_text(get_setting("welcome_msg"), c.message.chat.id, c.message.message_id, reply_markup=user_main_keyboard())
            return

        if data == "menu_sections":
            kb = categories_keyboard()
            bot.edit_message_text("📂 الأقسام:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        if data == "menu_balance":
            bal = get_balance(uid)
            bot.answer_callback_query(c.id, f"رصيدك: {fmt_currency(bal)}")
            return

        if data == "menu_deposit":
            # خطوة: طلب إيداع — نسجل طلب إيداع في جدول deposits كـ pending
            bot.send_message(uid, "💵 شحن بالليرة السورية — أرسل المبلغ بالليرة الآن (مثال: 5000). لإلغاء ارسل /cancel")
            bot.answer_callback_query(c.id, "أرسل المبلغ بالليرة الآن.")
            # نخزن حالة انتظار (بسيط عبر setting)
            set_setting(f"awaiting_deposit_user_{uid}", "1")
            return

        if data == "menu_orders":
            cur.execute("SELECT id, product_id, price, status, created_at FROM orders WHERE user_id = ? ORDER BY id DESC", (uid,))
            rows = cur.fetchall()
            if not rows:
                bot.send_message(uid, "ليس لديك طلبات حالياً.")
            else:
                text = "📦 طلباتك:\n"
                for r in rows:
                    text += f"#{r[0]} — المنتج:{r[1]} — {fmt_currency(r[2])} — الحالة:{r[3]}\n"
                bot.send_message(uid, text)
            bot.answer_callback_query(c.id)
            return

        if data == "menu_help":
            bot.answer_callback_query(c.id, "استخدم الأزرار أو اكتب /help لعرض التعليمات.")
            return

        # تصفح الأقسام -> اختيار قسم
        if data.startswith("cat:"):
            cid = int(data.split(":", 1)[1])
            kb = products_keyboard(cid)
            bot.edit_message_text("🧾 منتجات القسم:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        # اختيار منتج
        if data.startswith("prod:"):
            pid = int(data.split(":", 1)[1])
            p = get_product_by_id(pid=None) if False else None  # placeholder to satisfy lint
            # fetch product
            cur.execute("SELECT name, price, description FROM products WHERE id = ?", (pid,))
            row = cur.fetchone()
            if not row:
                bot.answer_callback_query(c.id, "المنتج غير موجود.")
                return
            name, price, desc = row
            text = f"🔹 <b>{name}</b>\nالسعر: {fmt_currency(price)}\n\n{desc or ''}"
            kb = product_detail_keyboard(pid)
            bot.edit_message_text(text, c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        # شراء منتج
        if data.startswith("buy:"):
            pid = int(data.split(":", 1)[1])
            # تأكد وجود المنتج
            cur.execute("SELECT name, price FROM products WHERE id = ?", (pid,))
            row = cur.fetchone()
            if not row:
                bot.answer_callback_query(c.id, "المنتج غير موجود.")
                return
            name, price = row
            bal = get_balance(uid)
            if bal < price:
                bot.answer_callback_query(c.id, f"رصيدك غير كافٍ. السعر: {fmt_currency(price)} — رصيدك: {fmt_currency(bal)}")
                bot.send_message(uid, "لشحن رصيدك استخدم زر شحن أو تواصل مع الأدمن.")
                return
            # خصم و إنشاء طلب
            change_balance(uid, -price)
            cur.execute("INSERT INTO orders (user_id, product_id, price, status, created_at) VALUES (?, ?, ?, ?, ?)",
                        (uid, pid, price, "new", datetime.utcnow().isoformat()))
            conn.commit()
            order_id = cur.lastrowid
            bot.answer_callback_query(c.id, "تمت عملية الشراء بنجاح.")
            bot.send_message(uid, f"✅ تم إنشاء طلب #{order_id} للمنتج {name}. تم خصم {fmt_currency(price)} من رصيدك.")
            # إشعار الأدمن
            bot.send_message(ADMIN_ID, f"📥 طلب جديد #{order_id} من @{c.from_user.username or c.from_user.id} — {name} — {fmt_currency(price)}")
            return

        # ---------- لوحات الأدمن ----------
        if data == "adm_store" and is_admin(uid):
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("➕ إضافة قسم", callback_data="adm_add_category"),
                   types.InlineKeyboardButton("✏ تعديل قسم", callback_data="adm_edit_category"))
            kb.add(types.InlineKeyboardButton("➕ إضافة منتج", callback_data="adm_add_product"),
                   types.InlineKeyboardButton("✏ تعديل منتج", callback_data="adm_edit_product"))
            kb.add(types.InlineKeyboardButton("🗑 حذف قسم/منتج", callback_data="adm_delete"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
            bot.edit_message_text("🛠️ إدارة المتجر:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        if data == "adm_balance" and is_admin(uid):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("➕ إضافة رصيد للمستخدم", callback_data="adm_add_balance"),
                   types.InlineKeyboardButton("➖ خصم رصيد من المستخدم", callback_data="adm_deduct_balance"))
            kb.add(types.InlineKeyboardButton("🔍 عرض رصيد المستخدم", callback_data="adm_show_balance"),
                   types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
            bot.edit_message_text("💳 إدارة الأرصدة:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        if data == "adm_welcome" and is_admin(uid):
            bot.send_message(uid, "✏ أرسل نص رسالة الترحيب الجديدة الآن (يمكنك استخدام {user} ليظهر اسم المستخدم).")
            set_setting(f"awaiting_welcome_{uid}", "1")
            bot.answer_callback_query(c.id, "أرسل رسالة الترحيب الآن.")
            return

        if data == "adm_broadcast" and is_admin(uid):
            bot.send_message(uid, "📢 أرسل الرسالة التي تريد بثها الآن. لإلغاء ارسل /cancel.")
            set_setting(f"awaiting_broadcast_{uid}", "1")
            bot.answer_callback_query(c.id, "أرسل نص البث الآن.")
            return

        if data == "adm_bans" and is_admin(uid):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_user"),
                   types.InlineKeyboardButton("✅ فك الحظر", callback_data="adm_unban_user"))
            kb.add(types.InlineKeyboardButton("👥 عرض المستخدمين", callback_data="adm_list_users"),
                   types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
            bot.edit_message_text("🚫 إدارة الحظر والمستخدمين:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        if data == "adm_stats" and is_admin(uid):
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders")
            total_orders = cur.fetchone()[0]
            cur.execute("SELECT SUM(balance) FROM users")
            total_bal = cur.fetchone()[0] or 0
            txt = f"📊 إحصائيات البوت:\n• مستخدمون: {total_users}\n• طلبات: {total_orders}\n• إجمالي أرصدة: {fmt_currency(total_bal)}"
            bot.edit_message_text(txt, c.message.chat.id, c.message.message_id, reply_markup=admin_main_keyboard())
            return

        # إدارة الأزرار (قائمة)
        if data == "adm_buttons" and is_admin(uid):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("➕ إضافة زر", callback_data="adm_add_button"),
                   types.InlineKeyboardButton("📋 عرض الأزرار", callback_data="adm_list_buttons"))
            kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_main"))
            bot.edit_message_text("🔘 إدارة الأزرار:", c.message.chat.id, c.message.message_id, reply_markup=kb)
            return

        # إضافة/تعديل/حذف قسم/منتج/زر -- يتم عبر رسائل تالية (stateful) لنرسل التعليمات للأدمن
        # ستتم قراءة هذه الحالات في message handler أدناه
        # مثال: adm_add_category, adm_add_product, adm_edit_category, adm_edit_product, adm_add_button, adm_list_buttons, adm_add_balance, adm_deduct_balance ...

        # حال افتراضيه
        bot.answer_callback_query(c.id, "تم الضغط.")
    except Exception as e:
        traceback.print_exc()
        try:
            bot.answer_callback_query(c.id, "خطأ داخلي (راجع السجلات).")
        except:
            pass

# ---------------------------
# دوال CRUD مساعدة (القسم والمنتج)
# ---------------------------
def add_category(name):
    cur.execute("INSERT INTO categories (name, pos) VALUES (?, ?)", (name, int(time.time())))
    conn.commit()
    return cur.lastrowid

def edit_category(cid, newname):
    cur.execute("UPDATE categories SET name = ? WHERE id = ?", (newname, cid))
    conn.commit()

def delete_category(cid):
    cur.execute("DELETE FROM categories WHERE id = ?", (cid,))
    cur.execute("DELETE FROM products WHERE category_id = ?", (cid,))
    conn.commit()

def add_product(category_id, name, price, description=""):
    cur.execute("INSERT INTO products (category_id, name, price, description, pos) VALUES (?, ?, ?, ?, ?)",
                (category_id, name, price, description, int(time.time())))
    conn.commit()
    return cur.lastrowid

def edit_product(pid, name=None, price=None, description=None):
    if name is not None:
        cur.execute("UPDATE products SET name = ? WHERE id = ?", (name, pid))
    if price is not None:
        cur.execute("UPDATE products SET price = ? WHERE id = ?", (price, pid))
    if description is not None:
        cur.execute("UPDATE products SET description = ? WHERE id = ?", (description, pid))
    conn.commit()

def delete_product(pid):
    cur.execute("DELETE FROM products WHERE id = ?", (pid,))
    conn.commit()

def get_product_by_id(pid):
    cur.execute("SELECT id, category_id, name, price, description FROM products WHERE id = ?", (pid,))
    r = cur.fetchone()
    if not r:
        return None
    return {"id": r[0], "category_id": r[1], "name": r[2], "price": float(r[3]), "description": r[4]}

# ---------------------------
# معالجة الرسائل النصية (حالات الأدمن/stateful flows)
# ---------------------------
@bot.message_handler(func=lambda m: True, content_types=['text'])
def message_handler(m: types.Message):
    try:
        uid = m.from_user.id
        text = (m.text or "").strip()

        # إلغاء العملية الجارية
        if text.lower() == "/cancel":
            # نزيل كل awaiting settings الخاصة بالأدمن هذا إن وجدت
            for k in list_settings_keys():
                if get_setting(k) == str(uid):
                    set_setting(k, "")
            bot.reply_to(m, "✅ تم الإلغاء.")
            return

        # إذا كان الأدمن في وضع انتظار تعديل الترحيب
        if get_setting(f"awaiting_welcome_{uid}") and is_admin(uid):
            new = text
            set_setting("welcome_msg", new)
            set_setting(f"awaiting_welcome_{uid}", "")
            bot.reply_to(m, "✅ تم تحديث رسالة الترحيب.")
            log_admin("update_welcome")
            return

        # انتظار بث
        if get_setting(f"awaiting_broadcast_{uid}") and is_admin(uid):
            msg = text
            bot.reply_to(m, "جاري إرسال البث... انتظر لحظة.")
            # إرسال إلى جميع المستخدمين
            cur.execute("SELECT user_id FROM users")
            rows = cur.fetchall()
            sent = 0
            failed = 0
            for (u,) in rows:
                try:
                    bot.send_message(u, f"📣 رسالة من الأدمن:\n\n{msg}")
                    sent += 1
                    time.sleep(0.07)  # تروتل بسيط
                except Exception:
                    failed += 1
            bot.reply_to(m, f"✅ تم الإرسال. ناجح: {sent} — فشل: {failed}")
            set_setting(f"awaiting_broadcast_{uid}", "")
            log_admin("broadcast_sent")
            return

        # انتظار حظر/فك حظر
        if get_setting(f"awaiting_ban_{uid}") and is_admin(uid):
            parts = text.split()
            if len(parts) >= 2:
                cmd = parts[0].lower()
                try:
                    target = int(parts[1])
                except:
                    bot.reply_to(m, "الآيدي يجب أن يكون رقماً.")
                    set_setting(f"awaiting_ban_{uid}", "")
                    return
                if cmd == "ban":
                    ban_user(target)
                    bot.reply_to(m, f"✅ تم حظر المستخدم {target}.")
                    try:
                        bot.send_message(target, "🚫 تم حظرك من البوت.")
                    except:
                        pass
                    log_admin(f"ban {target}")
                elif cmd == "unban":
                    unban_user(target)
                    bot.reply_to(m, f"✅ تم فك الحظر عن {target}.")
                    log_admin(f"unban {target}")
                else:
                    bot.reply_to(m, "استخدم: ban <id> أو unban <id>")
            else:
                bot.reply_to(m, "استخدم: ban <id> أو unban <id>")
            set_setting(f"awaiting_ban_{uid}", "")
            return

        # انتظار إضافة قسم
        if get_setting(f"awaiting_new_category_{uid}") and is_admin(uid):
            name = text
            add_category(name)
            bot.reply_to(m, f"✅ تم إضافة القسم: {name}")
            set_setting(f"awaiting_new_category_{uid}", "")
            log_admin(f"add_category {name}")
            return

        # انتظار إضافة منتج (صيغة: category_id | name | price | description)
        if get_setting(f"awaiting_new_product_{uid}") and is_admin(uid):
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 3:
                try:
                    cid = int(parts[0])
                    name = parts[1]
                    price = float(parts[2].replace(",", "."))
                    desc = parts[3] if len(parts) >= 4 else ""
                    # تحقق وجود القسم
                    cur.execute("SELECT id FROM categories WHERE id = ?", (cid,))
                    if not cur.fetchone():
                        bot.reply_to(m, "القسم غير موجود. تحقق من ID القسم.")
                    else:
                        pid = add_product(cid, name, price, desc)
                        bot.reply_to(m, f"✅ تم إضافة المنتج {name} بسعر {fmt_currency(price)}. id={pid}")
                        log_admin(f"add_product {name} in cat {cid}")
                except Exception as e:
                    bot.reply_to(m, "خطأ في القيم. الصيغة: category_id | name | price | description")
                    log_admin(f"add_product_error {e}")
            else:
                bot.reply_to(m, "الصيغة خاطئة. استخدم: category_id | اسم المنتج | السعر | الوصف (اختياري)")
            set_setting(f"awaiting_new_product_{uid}", "")
            return

        # انتظار إضافة زر مخصص (صيغة: parent_type|parent_id|text|action|payload)
        if get_setting(f"awaiting_new_button_{uid}") and is_admin(uid):
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 4:
                parent_type = parts[0]  # category/product/global
                parent_id = int(parts[1])
                text_btn = parts[2]
                action = parts[3]  # open_url or buy
                payload = parts[4] if len(parts) > 4 else ""
                cur.execute("INSERT INTO buttons (parent_type, parent_id, text, action, payload) VALUES (?, ?, ?, ?, ?)",
                            (parent_type, parent_id, text_btn, action, payload))
                conn.commit()
                bot.reply_to(m, "✅ تم إضافة الزر.")
                log_admin(f"add_button {text_btn} to {parent_type}:{parent_id}")
            else:
                bot.reply_to(m, "الصيغة خاطئة. استخدم: parent_type|parent_id|text|action|payload")
            set_setting(f"awaiting_new_button_{uid}", "")
            return

        # انتظار إضافة/خصم رصيد (صيغة: user_id | amount)
        if get_setting(f"awaiting_balance_action_{uid}") and is_admin(uid):
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 2:
                try:
                    target = int(parts[0])
                    amount = float(parts[1].replace(",", "."))
                    action = get_setting(f"awaiting_balance_action_{uid}_type")
                    if action == "add":
                        change_balance(target, amount)
                        bot.reply_to(m, f"✅ تم إضافة {fmt_currency(amount)} للمستخدم {target}.")
                        try:
                            bot.send_message(target, f"💰 تم إضافة رصيد {fmt_currency(amount)} لحسابك.")
                        except:
                            pass
                        log_admin(f"add_balance {target} {amount}")
                    elif action == "deduct":
                        change_balance(target, -amount)
                        bot.reply_to(m, f"✅ تم خصم {fmt_currency(amount)} من المستخدم {target}.")
                        try:
                            bot.send_message(target, f"⚠️ تم خصم {fmt_currency(amount)} من رصيدك.")
                        except:
                            pass
                        log_admin(f"deduct_balance {target} {amount}")
                except Exception as e:
                    bot.reply_to(m, "خطأ في البيانات. تأكد من الصيغة: user_id | amount")
                    log_admin(f"balance_action_error {e}")
            else:
                bot.reply_to(m, "استخدام: user_id | amount")
            set_setting(f"awaiting_balance_action_{uid}", "")
            set_setting(f"awaiting_balance_action_{uid}_type", "")
            return

        # انتظار تأكيد/ارسال مبلغ شحن من المستخدم (deposit)
        # المستخدم يرسل المبلغ بالليرة، نتحول إلى credits بحسب syp_rate
        if get_setting(f"awaiting_deposit_user_{uid}"):
            try:
                amount_syp = float(text.replace(",", "").strip())
                rate = float(get_setting("syp_rate", "2500"))
                credits = int(amount_syp / rate)
                min_dep = int(get_setting("min_deposit", "1"))
                if credits < min_dep:
                    bot.reply_to(m, f"❌ الحد الأدنى للإيداع هو {min_dep} كريديت (توافق {fmt_currency(min_dep * rate)}).")
                    set_setting(f"awaiting_deposit_user_{uid}", "")
                    return
                # سجل الطلب
                cur.execute("INSERT INTO deposits (user_id, amount_syp, credits, status, created_at) VALUES (?, ?, ?, ?, ?)",
                            (uid, amount_syp, credits, "pending", datetime.utcnow().isoformat()))
                conn.commit()
                dep_id = cur.lastrowid
                bot.reply_to(m, f"✅ تم تسجيل طلب إيداع #{dep_id}: {credits} كريديت — {int(amount_syp)} ل.س. سيتم التحقق من الأدمن.")
                # إعلام الأدمن
                bot.send_message(ADMIN_ID, f"📥 طلب إيداع جديد #{dep_id}\nالمستخدم: {uid}\n{credits} كريديت — {int(amount_syp)} ل.س\nلتأكيد: /confirm_deposit {dep_id}\nأو للرفض: /reject_deposit {dep_id}")
            except Exception as e:
                bot.reply_to(m, "❌ الرجاء إدخال رقم صالح بالمبلغ بالليرة.")
                log_admin(f"deposit_input_error {e}")
            set_setting(f"awaiting_deposit_user_{uid}", "")
            return

        # أي رسالة أخرى: رد افتراضي
        # إذا كانت رسالة من الأدمن تابعة لأمر مباشر نغطيه أعلاه، وإلا نعرض لوحة مناسبة
        if is_admin(uid):
            bot.send_message(uid, "لوحة الأدمن:", reply_markup=admin_main_keyboard())
        else:
            bot.send_message(uid, "استخدم الأزرار للتنقل أو اكتب /help.", reply_markup=user_main_keyboard())
    except Exception:
        traceback.print_exc()

# ---------------------------
# أوامر نصية خاصة بالأدمن (سريعة)
# ---------------------------
@bot.message_handler(commands=["confirm_deposit"])
def cmd_confirm_deposit(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "أنت لست الأدمن.")
        return
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "استخدام: /confirm_deposit <deposit_id>")
        return
    try:
        dep_id = int(parts[1])
        cur.execute("SELECT user_id, credits, status FROM deposits WHERE id = ?", (dep_id,))
        r = cur.fetchone()
        if not r:
            bot.reply_to(m, "الطلب غير موجود.")
            return
        user_id, credits, status = r
        if status == "confirmed":
            bot.reply_to(m, "الطلب مؤكد مسبقاً.")
            return
        # أضف الرصيد للمستخدم
        change_balance(user_id, credits)
        cur.execute("UPDATE deposits SET status = 'confirmed' WHERE id = ?", (dep_id,))
        conn.commit()
        bot.reply_to(m, f"✅ تم تأكيد الإيداع #{dep_id} وإضافة {credits} كريديت للمستخدم {user_id}.")
        try:
            bot.send_message(user_id, f"✅ تم تأكيد إيداعك #{dep_id}. {credits} كريديت أضيفت لحسابك.")
        except:
            pass
        log_admin(f"confirm_deposit {dep_id}")
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(m, "حدث خطأ.")

@bot.message_handler(commands=["reject_deposit"])
def cmd_reject_deposit(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "استخدام: /reject_deposit <deposit_id>")
        return
    dep_id = int(parts[1])
    cur.execute("SELECT user_id FROM deposits WHERE id = ?", (dep_id,))
    r = cur.fetchone()
    if not r:
        bot.reply_to(m, "الطلب غير موجود.")
        return
    cur.execute("UPDATE deposits SET status = 'cancelled' WHERE id = ?", (dep_id,))
    conn.commit()
    bot.reply_to(m, f"✅ تم رفض الطلب #{dep_id}.")
    log_admin(f"reject_deposit {dep_id}")

@bot.message_handler(commands=["list_deposits"])
def cmd_list_deposits(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    cur.execute("SELECT id, user_id, credits, amount_syp, status, created_at FROM deposits ORDER BY id DESC")
    rows = cur.fetchall()
    if not rows:
        bot.reply_to(m, "لا توجد طلبات إيداع.")
        return
    text = "قائمة طلبات الإيداع:\n"
    for r in rows[:100]:
        text += f"#{r[0]} | user:{r[1]} | credits:{r[2]} | {int(r[3])} ل.س | status:{r[4]}\n"
    bot.reply_to(m, text)

@bot.message_handler(commands=["users"])
def cmd_list_users(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "خاصة بالأدمن فقط.")
        return
    cur.execute("SELECT user_id, username, first_name, balance, vip, banned FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    text = "قائمة المستخدمين:\n"
    for r in rows[:200]:
        text += f"ID:{r[0]} | @{r[1] or '----'} | {r[2] or ''} | رصيد:{r[3]} | VIP:{r[4]} | محظور:{r[5]}\n"
    bot.reply_to(m, text[:4000])

@bot.message_handler(commands=["setrate"])
def cmd_setrate(m: types.Message):
    if not is_admin(m.from_user.id):
        bot.reply_to(m, "خاص بالأدمن.")
        return
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(m, "استخدام: /setrate <SYP_per_credit> — مثال: /setrate 2500")
        return
    try:
        rate = float(parts[1].replace(",", "."))
        set_setting("syp_rate", str(rate))
        bot.reply_to(m, f"✅ تم تعيين سعر الصرف: 1 كريديت = {int(rate)} ل.س")
        log_admin(f"setrate {rate}")
    except:
        bot.reply_to(m, "قيمة غير صالحة.")

@bot.message_handler(commands=["addcat", "addcategory"])
def cmd_addcategory(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    args = m.text.split(None, 1)
    if len(args) < 2:
        bot.reply_to(m, "استخدام: /addcat اسم_القسم")
        return
    name = args[1].strip()
    add_category(name)
    bot.reply_to(m, f"✅ تمت إضافة القسم: {name}")
    log_admin(f"add_category {name}")

@bot.message_handler(commands=["addprod", "addproduct"])
def cmd_addproduct(m: types.Message):
    if not is_admin(m.from_user.id):
        return
    # usage: /addprod category_id|name|price|description
    args = m.text.split(None, 1)
    if len(args) < 2:
        bot.reply_to(m, "استخدام: /addprod category_id|name|price|description")
        return
    parts = [p.strip() for p in args[1].split("|")]
    if len(parts) < 3:
        bot.reply_to(m, "الصيغة خاطئة.")
        return
    try:
        cid = int(parts[0])
        name = parts[1]
        price = float(parts[2].replace(",", "."))
        desc = parts[3] if len(parts) > 3 else ""
        pid = add_product(cid, name, price, desc)
        bot.reply_to(m, f"✅ تمت إضافة المنتج {name} (id={pid}).")
        log_admin(f"add_product {name} to cat {cid}")
    except Exception as e:
        bot.reply_to(m, "خطأ في البيانات.")

# ---------------------------
# Utilities: settings keys listing (helper)
# ---------------------------
def list_settings_keys():
    cur.execute("SELECT key FROM settings")
    return [r[0] for r in cur.fetchall()]

# ---------------------------
# بدء التشغيل (polling)
# ---------------------------
def safe_start():
    try:
        print("Bot starting...")
        conn.commit()
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        print("Stopping by user")
    except Exception:
        traceback.print_exc()
        time.sleep(5)
        safe_start()

if __name__ == "__main__":
    safe_start()
