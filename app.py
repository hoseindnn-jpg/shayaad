# ==================== IMPORTS ====================
from flask import Flask, request, jsonify
import sqlite3
import random
import string
import os
import requests
import json
from datetime import datetime

# ==================== CONFIG ====================
app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME")
DB_PATH = os.environ.get("DB_PATH", "bot.db")
QUESTIONS_FILE = os.environ.get("QUESTIONS_FILE", "questions.json")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# ==================== HELPERS ====================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def normalize_text(text):
    """نرمال‌سازی متن فارسی: حذف فاصله‌های اضافی، یکسان‌سازی نیم‌فاصله و..."""
    if not text:
        return ""
    text = text.strip()
    text = text.replace("\u200c", " ")  # نیم‌فاصله → فاصله
    text = " ".join(text.split())       # حذف فاصله‌های اضافی
    return text

def generate_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def load_questions(category=None):
    """بارگذاری سوالات از فایل JSON با قابلیت فیلتر بر اساس دسته‌بندی"""
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    valid = [item for item in data if item.get("question") and item.get("answer")]

    if category:
        valid = [item for item in valid if item.get("category") == category]

    return valid

def db():
    """مدیریت اتصال به دیتابیس با context manager"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== TELEGRAM API ====================
def tg_request(method, data=None):
    url = f"{TELEGRAM_API}/{method}"
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Telegram API error: {e}")
        return {"ok": False, "description": str(e)}  

def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    
    result = tg_request("sendMessage", params)
    if not result.get("ok"):
        print(f"❌ ارسال پیام به {chat_id} ناموفق: {result.get('description', 'خطای ناشناخته')}")
    return result

def answer_callback(callback_id, text=None):
    return tg_request("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })

def inline_keyboard(rows):
    return {"inline_keyboard": rows}

def button(text, callback_data):
    return {"text": text, "callback_data": callback_data}

# ==================== DATABASE INIT ====================
def init_db():
    conn = db()
    
    # جدول games
    conn.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL,
            owner_chat_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            category TEXT DEFAULT NULL
        )
    """)
    
    # جدول players
    conn.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT,
            game_id INTEGER NOT NULL,
            score INTEGER DEFAULT 0,
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)
    
    # جدول user_states
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            data TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    
    # جدول rounds
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            status TEXT DEFAULT 'answering',
            created_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)
    
    # جدول round_players
    conn.execute("""
        CREATE TABLE IF NOT EXISTS round_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            has_answered INTEGER DEFAULT 0,
            can_vote INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            penalty INTEGER DEFAULT 0,
            FOREIGN KEY (round_id) REFERENCES rounds(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
    """)
    
    # جدول answers
    conn.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            answer_text TEXT NOT NULL,
            FOREIGN KEY (round_id) REFERENCES rounds(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
    """)
    
    # جدول options
    conn.execute("""
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            option_number INTEGER NOT NULL,
            option_text TEXT NOT NULL,
            is_correct INTEGER DEFAULT 0,
            player_id INTEGER,
            FOREIGN KEY (round_id) REFERENCES rounds(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
    """)
    
    # جدول votes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            voter_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            FOREIGN KEY (round_id) REFERENCES rounds(id),
            FOREIGN KEY (voter_id) REFERENCES players(id),
            FOREIGN KEY (option_id) REFERENCES options(id)
        )
    """)
    
    # ===== MIGRATIONS =====
    migrations = [
        "ALTER TABLE players ADD COLUMN normalized_name TEXT",
        "ALTER TABLE round_players ADD COLUMN score INTEGER DEFAULT 0",
        "ALTER TABLE round_players ADD COLUMN penalty INTEGER DEFAULT 0",
        "ALTER TABLE games ADD COLUMN category TEXT DEFAULT NULL"
    ]
    
    for migration in migrations:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # ستون از قبل وجود دارد
    
    conn.commit()
    conn.close()
    print("✅ Database initialized successfully")

# ==================== PENALTY MAPPINGS ====================
_penalty_mappings = {}

# ==================== STATE MANAGEMENT ====================
def set_user_state(user_id, state, data=None):
    conn = db()
    conn.execute("""
        INSERT OR REPLACE INTO user_states (user_id, state, data, updated_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, state, data, now()))
    conn.commit()
    conn.close()

def get_user_state(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row

def clear_user_state(user_id):
    conn = db()
    conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ==================== GAME QUERIES ====================
def get_game_by_code(code):
    conn = db()
    row = conn.execute("SELECT * FROM games WHERE code = ?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_game_by_id(game_id):
    conn = db()
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_player(user_id, game_id):
    conn = db()
    row = conn.execute("SELECT * FROM players WHERE user_id = ? AND game_id = ?", (user_id, game_id)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_player_by_id(player_id):
    conn = db()
    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_players(game_id):
    conn = db()
    rows = conn.execute("SELECT * FROM players WHERE game_id = ? ORDER BY score DESC", (game_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_active_round(game_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM rounds WHERE game_id = ? AND status != 'finished' ORDER BY id DESC LIMIT 1",
        (game_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_round(round_id):
    conn = db()
    row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def is_owner(game, user_id):
    return game and game["owner_id"] == user_id

def join_link(game_code):
    return f"https://t.me/{BOT_USERNAME}?start={game_code}"

# ==================== MENU FUNCTIONS ====================
def send_main_menu(chat_id, user_id=None):
    """ارسال منوی اصلی با دکمه‌ها"""
    keyboard = inline_keyboard([
        [button("🎮 ساخت بازی جدید", "new_game")],
        [button("📖 راهنما", "show_help")]
    ])
    
    send_message(
        chat_id,
        "🎮 **به بازی گروهی شیاد خوش اومدی!**\n\n"
        "برای شروع یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=keyboard
    )

def send_back_to_menu_button(chat_id, text):
    """ارسال پیام با دکمه بازگشت به منو"""
    send_message(
        chat_id,
        text,
        reply_markup=inline_keyboard([
            [button("🏠 بازگشت به منو", "back_to_menu")]
        ])
    )

# ==================== GAME CREATION ====================
def create_game(chat_id, user_id):
    code = generate_code()
    conn = db()
    conn.execute(
        "INSERT INTO games (code, owner_id, owner_chat_id, created_at) VALUES (?, ?, ?, ?)",
        (code, user_id, chat_id, now())
    )
    conn.commit()
    conn.close()

    link = join_link(code)
    send_message(
        chat_id,
        f"🎮 **بازی جدید ساخته شد!**\n\n"
        f"🔗 لینک دعوت:\n`{link}`\n\n"
        f"کد بازی: `{code}`\n\n"
        "این لینک رو برای دوستات بفرست تا عضو بشن.\n"
        "حداقل ۳ نفر لازمه تا بشه بازی رو شروع کرد.",
        reply_markup=inline_keyboard([
            [button("🚀 شروع دور جدید", f"start_round:{code}")]
        ])
    )

    # درخواست انتخاب دسته‌بندی
    send_message(
        chat_id,
        "🎯 **دسته‌بندی سوالات رو انتخاب کن:**\n\n"
        "بعداً هم می‌تونی از طریق دکمه «تغییر دسته‌بندی» عوضش کنی.",
        reply_markup=inline_keyboard([
            [button("📚 اطلاعات عمومی", f"set_category:{code}:سخت")],
            [button("🤪 سوالات عجیب و خنده‌دار", f"set_category:{code}:عجیب")]
        ])
    )

# ==================== JOIN GAME ====================
def join_game_start(chat_id, user_id, game_code):
    game = get_game_by_code(game_code)
    if not game:
        send_back_to_menu_button(chat_id, "❌ بازی پیدا نشد. کد رو دوباره چک کن.")
        return

    game_id = game["id"]
    existing = get_player(user_id, game_id)
    if existing:
        send_message(chat_id, f"✅ تو قبلاً با نام **{existing['name']}** عضو شدی.")
        return

    set_user_state(user_id, f"awaiting_name:{game_code}")
    send_message(chat_id, "👋 سلام! لطفاً **اسم خودت** رو برای بازی بفرست:")

def save_player_name(chat_id, user_id, name, game_code):
    game = get_game_by_code(game_code)
    if not game:
        send_back_to_menu_button(chat_id, "❌ بازی پیدا نشد.")
        clear_user_state(user_id)
        return

    game_id = game["id"]
    existing = get_player(user_id, game_id)
    if existing:
        send_message(chat_id, f"✅ تو قبلاً با نام **{existing['name']}** عضو شدی.")
        clear_user_state(user_id)
        return

    name = name.strip()
    if not name or len(name) > 50:
        send_message(chat_id, "⚠️ اسم باید بین ۱ تا ۵۰ کاراکتر باشه. دوباره بفرست:")
        return

    normalized = normalize_text(name)

    conn = db()
    conn.execute(
        "INSERT INTO players (user_id, name, normalized_name, game_id) VALUES (?, ?, ?, ?)",
        (user_id, name, normalized, game_id)
    )
    conn.commit()
    conn.close()

    clear_user_state(user_id)
    send_back_to_menu_button(chat_id, f"✅ با نام **{name}** عضو بازی شدی! 🎉\nمنتظر شروع بازی باش.")

    # اطلاع به مدیر
    owner_msg = f"👤 **{name}** به بازی اضافه شد."
    send_message(game["owner_chat_id"], owner_msg)

# ==================== START NEW ROUND ====================
def start_new_round(chat_id, user_id, game_code):
    game = get_game_by_code(game_code)
    if not game:
        send_back_to_menu_button(chat_id, "❌ بازی پیدا نشد.")
        return

    if not is_owner(game, user_id):
        send_message(chat_id, "❌ فقط مدیر بازی می‌تونه دور جدید رو شروع کنه.")
        return

    # بررسی دور فعال
    active = get_active_round(game["id"])
    if active:
        send_message(chat_id, "⚠️ یه دور هنوز در جریانه. اول اون رو تموم کن.")
        return

    players = get_players(game["id"])
    if len(players) < 3:
        send_message(chat_id, "⚠️ حداقل ۳ بازیکن لازمه. هنوز به اندازه کافی عضو نشدن.")
        return

    # بررسی دسته‌بندی
    category = game.get("category")
    if not category:
        send_message(
            chat_id,
            "⚠️ اول باید دسته‌بندی سوالات رو مشخص کنی.",
            reply_markup=inline_keyboard([
                [button("📚 اطلاعات عمومی", f"set_category:{game_code}:سخت")],
                [button("🤪 سوالات عجیب و خنده‌دار", f"set_category:{game_code}:عجیب")]
            ])
        )
        return

    questions = load_questions(category=category)
    if not questions:
        send_message(
            chat_id,
            f"❌ هیچ سوالی برای دسته «{category}» پیدا نشد. لطفاً دسته‌بندی رو عوض کن.",
            reply_markup=inline_keyboard([
                [button("📚 اطلاعات عمومی", f"set_category:{game_code}:سخت")],
                [button("🤪 سوالات عجیب و خنده‌دار", f"set_category:{game_code}:عجیب")]
            ])
        )
        return

    # انتخاب سوال تصادفی
    q = random.choice(questions)
    question_text = q["question"]
    correct_answer = q["answer"]

    # درج دور جدید
    conn = db()
    cur = conn.execute(
        "INSERT INTO rounds (game_id, question, correct_answer, status, created_at) VALUES (?, ?, ?, 'answering', ?)",
        (game["id"], question_text, correct_answer, now())
    )
    round_id = cur.lastrowid

    # درج round_players برای همه بازیکنان
    for p in players:
        conn.execute(
            "INSERT INTO round_players (round_id, player_id, has_answered, can_vote, score, penalty) VALUES (?, ?, 0, 0, 0, 0)",
            (round_id, p["id"])
        )
    conn.commit()
    conn.close()

    # ارسال سوال به بازیکنان
    for p in players:
        try:
            send_message(
                p["user_id"],
                f"📝 **دور جدید شروع شد!**\n\n"
                f"❓ **سوال:**\n{question_text}\n\n"
                "✏️ جوابت رو به صورت **خصوصی** برای من بفرست.\n"
                "⚠️ اگه جواب درست رو بفرستی، رد می‌شه و باید یه جواب خلاقانه بدی!"
            )
        except:
            pass

    # ارسال پیام به مدیر
    send_message(
        chat_id,
        f"🚀 **دور جدید شروع شد!**\n\n"
        f"📝 سوال:\n{question_text}\n\n"
        f"👥 بازیکنان: {len(players)} نفر\n"
        f"📂 دسته: {category}\n\n"
        "منتظر جواب بازیکنان باش...",
        reply_markup=inline_keyboard([
            [button("⏹ پایان ارسال جواب‌ها", f"end_answers:{round_id}")]
        ])
    )

# ==================== HANDLE ANSWERS ====================
def handle_answer_message(chat_id, user_id, message_text):
    """پردازش پاسخ بازیکن به سوال"""
    conn = db()
    # پیدا کردن بازی و دور فعال بازیکن
    row = conn.execute("""
        SELECT r.id as round_id, r.game_id, r.correct_answer, r.status, rp.player_id, rp.has_answered
        FROM rounds r
        JOIN round_players rp ON rp.round_id = r.id
        JOIN players p ON p.id = rp.player_id
        WHERE p.user_id = ? AND r.status = 'answering'
        ORDER BY r.id DESC LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()

    if not row:
        return False  # بازیکن در دور فعالی نیست

    round_id = row["round_id"]
    correct_answer = normalize_text(row["correct_answer"])
    status = row["status"]
    player_id = row["player_id"]
    has_answered = row["has_answered"]

    if status != "answering":
        return False

    answer_text = message_text.strip()
    if not answer_text or len(answer_text) > 200:
        send_message(chat_id, "⚠️ جوابت باید بین ۱ تا ۲۰۰ کاراکتر باشه.")
        return True

    answer_normalized = normalize_text(answer_text)

    # رد جواب درست
    if answer_normalized == correct_answer:
        send_message(chat_id, "🚫 این جواب درسته! باید یه جواب خلاقانه و اشتباه بدی. دوباره تلاش کن.")
        return True

    # بررسی تکراری نبودن با جواب‌های دیگران
    conn2 = db()
    existing_answers = conn2.execute("""
        SELECT a.answer_text FROM answers a
        JOIN round_players rp ON rp.player_id = a.player_id
        WHERE a.round_id = ? AND a.player_id != ?
    """, (round_id, player_id)).fetchall()
    conn2.close()

    for ans in existing_answers:
        if abs(len(answer_normalized) - len(normalize_text(ans["answer_text"]))) <= 2:
            if answer_normalized == normalize_text(ans["answer_text"]):
                send_message(chat_id, "⚠️ این جواب خیلی شبیه جواب یه بازیکن دیگه‌ست. یه چیز متفاوت بنویس.")
                return True

    # ثبت یا آپدیت پاسخ
    conn3 = db()
    if has_answered:
        conn3.execute(
            "UPDATE answers SET answer_text = ? WHERE round_id = ? AND player_id = ?",
            (answer_text, round_id, player_id)
        )
        send_message(chat_id, f"✅ جوابت آپدیت شد: **{answer_text}**")
    else:
        conn3.execute(
            "INSERT INTO answers (round_id, player_id, answer_text) VALUES (?, ?, ?)",
            (round_id, player_id, answer_text)
        )
        conn3.execute(
            "UPDATE round_players SET has_answered = 1 WHERE round_id = ? AND player_id = ?",
            (round_id, player_id)
        )
        send_message(chat_id, f"✅ جوابت ثبت شد: **{answer_text}**\nمنتظر رأی‌گیری باش...")
    conn3.commit()
    conn3.close()

    return True

# ==================== CLOSE ANSWERS & PREPARE OPTIONS ====================
def close_answers_and_prepare_options(round_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    conn = db()
    # فعال کردن can_vote برای پاسخ‌دهندگان
    conn.execute("""
        UPDATE round_players SET can_vote = 1
        WHERE round_id = ? AND has_answered = 1
    """, (round_id,))

    # گرفتن پاسخ‌ها
    answers = conn.execute("""
        SELECT a.id, a.answer_text, a.player_id, p.name
        FROM answers a
        JOIN players p ON p.id = a.player_id
        WHERE a.round_id = ?
    """, (round_id,)).fetchall()

    # پیدا کردن جواب درست
    correct_answer = round_data["correct_answer"]

    # ساختن گزینه‌ها: جواب‌ها + جواب درست
    option_number = 1
    correct_option_id = None
    answer_items = [dict(a) for a in answers]

    for ans in answer_items:
        conn.execute(
            "INSERT INTO options (round_id, option_number, option_text, is_correct, player_id) VALUES (?, ?, ?, 0, ?)",
            (round_id, option_number, ans["answer_text"], ans["player_id"])
        )
        option_number += 1

    # اضافه کردن جواب درست
    conn.execute(
        "INSERT INTO options (round_id, option_number, option_text, is_correct, player_id) VALUES (?, ?, ?, 1, NULL)",
        (round_id, option_number, correct_answer)
    )
    correct_option_id = option_number

    # تغییر وضعیت دور
    conn.execute("UPDATE rounds SET status = 'reviewing' WHERE id = ?", (round_id,))
    conn.commit()
    conn.close()

    # ارسال لیست جواب‌ها به مدیر
    game = get_game_by_id(round_data["game_id"])
    answer_list = "\n".join([f"{i+1}. {ans['answer_text']} — {ans['name']}" for i, ans in enumerate(answer_items)])
    answer_list += f"\n\n✅ {correct_option_id}. {correct_answer} (جواب درست)"

    send_message(
        game["owner_chat_id"],
        f"📋 **جواب‌های ثبت شده:**\n\n{answer_list}\n\n"
        "حالا می‌تونی رأی‌گیری رو شروع کنی.",
        reply_markup=inline_keyboard([
            [button("🗳 شروع رأی‌گیری", f"start_voting:{round_id}")]
        ])
    )

# ==================== OPTIONS HELPERS ====================
def get_options(round_id):
    conn = db()
    rows = conn.execute("SELECT * FROM options WHERE round_id = ? ORDER BY option_number", (round_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def format_options(options):
    text = ""
    for opt in options:
        text += f"{opt['option_number']}. {opt['option_text']}\n"
    return text

# ==================== REQUEST END ANSWERS ====================
def request_end_answers(round_id, chat_id, user_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    if not is_owner(game, user_id):
        return

    conn = db()
    missing = conn.execute("""
        SELECT p.name FROM players p
        JOIN round_players rp ON rp.player_id = p.id
        WHERE rp.round_id = ? AND rp.has_answered = 0
    """, (round_id,)).fetchall()
    conn.close()

    if missing:
        missing_names = "\n".join([f"• {m['name']}" for m in missing])
        send_message(
            chat_id,
            f"⚠️ این افراد هنوز جواب ندادن:\n{missing_names}\n\n"
            "می‌خوای صبر کنی یا به‌زور تمومش کنی؟",
            reply_markup=inline_keyboard([
                [button("⏹ پایان اجباری", f"force_end_answers:{round_id}")],
                [button("🔙 صبر می‌کنم", f"cancel_action:{round_id}")]
            ])
        )
    else:
        close_answers_and_prepare_options(round_id)

# ==================== START VOTING ====================
def start_voting(round_id, chat_id, user_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    if not is_owner(game, user_id):
        return

    if round_data["status"] != "reviewing":
        send_message(chat_id, "⚠️ الان وضعیت رأی‌گیری نیست.")
        return

    options = get_options(round_id)
    options_text = format_options(options)

    # ارسال گزینه‌ها به رأی‌دهندگان
    conn = db()
    voters = conn.execute("""
        SELECT p.user_id, p.name FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.can_vote = 1
    """, (round_id,)).fetchall()
    conn.close()

    for v in voters:
        try:
            send_message(
                v["user_id"],
                f"🗳 **وقت رأی‌گیریه!**\n\n"
                f"کدوم جواب به نظرت درسته؟\n\n"
                f"{options_text}\n\n"
                "⚠️ فقط **عدد گزینه** رو بفرست.\n"
                "⚠️ نمی‌تونی به جواب خودت رأی بدی!"
            )
        except:
            pass

    # تغییر وضعیت
    conn2 = db()
    conn2.execute("UPDATE rounds SET status = 'voting' WHERE id = ?", (round_id,))
    conn2.commit()
    conn2.close()

    send_message(
        chat_id,
        "🗳 رأی‌گیری شروع شد! منتظر رأی بازیکنان باش...",
        reply_markup=inline_keyboard([
            [button("⏹ پایان رأی‌گیری", f"end_voting:{round_id}")]
        ])
    )

# ==================== HANDLE VOTE ====================
def handle_vote_message(chat_id, user_id, message_text):
    """پردازش رأی بازیکن"""
    message_text = message_text.strip()
    if not message_text.isdigit():
        return False

    option_number = int(message_text)

    conn = db()
    row = conn.execute("""
        SELECT r.id as round_id, r.status, rp.player_id, rp.can_vote
        FROM rounds r
        JOIN round_players rp ON rp.round_id = r.id
        JOIN players p ON p.id = rp.player_id
        WHERE p.user_id = ? AND r.status = 'voting'
        ORDER BY r.id DESC LIMIT 1
    """, (user_id,)).fetchone()
    conn.close()

    if not row:
        return False

    round_id = row["round_id"]
    player_id = row["player_id"]
    can_vote = row["can_vote"]

    if not can_vote:
        send_message(chat_id, "⚠️ تو نمی‌تونی رأی بدی (چون جواب ندادی).")
        return True

    # پیدا کردن گزینه
    conn2 = db()
    option = conn2.execute(
        "SELECT * FROM options WHERE round_id = ? AND option_number = ?",
        (round_id, option_number)
    ).fetchone()
    conn2.close()

    if not option:
        send_message(chat_id, "⚠️ این شماره گزینه وجود نداره. دوباره بفرست.")
        return True

    # بررسی اینکه به جواب خودش رأی نده
    if option["player_id"] == player_id:
        send_message(chat_id, "🚫 نمی‌تونی به جواب خودت رأی بدی! یه گزینه دیگه انتخاب کن.")
        return True

    # ثبت رأی
    conn3 = db()
    existing_vote = conn3.execute(
        "SELECT * FROM votes WHERE round_id = ? AND voter_id = ?",
        (round_id, player_id)
    ).fetchone()

    if existing_vote:
        conn3.execute(
            "UPDATE votes SET option_id = ? WHERE round_id = ? AND voter_id = ?",
            (option["id"], round_id, player_id)
        )
        send_message(chat_id, f"✅ رأی‌ت آپدیت شد: گزینه {option_number}")
    else:
        conn3.execute(
            "INSERT INTO votes (round_id, voter_id, option_id) VALUES (?, ?, ?)",
            (round_id, player_id, option["id"])
        )
        send_message(chat_id, f"✅ رأی‌ت ثبت شد: گزینه {option_number}")
    conn3.commit()
    conn3.close()

    return True

# ==================== MISSING VOTES ====================
def get_missing_vote_players(round_id):
    conn = db()
    rows = conn.execute("""
        SELECT p.name FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.can_vote = 1
        AND rp.player_id NOT IN (SELECT voter_id FROM votes WHERE round_id = ?)
    """, (round_id, round_id)).fetchall()
    conn.close()
    return [r["name"] for r in rows]

def request_end_voting(round_id, chat_id, user_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    if not is_owner(game, user_id):
        return

    missing = get_missing_vote_players(round_id)
    if missing:
        missing_names = "\n".join([f"• {m}" for m in missing])
        send_message(
            chat_id,
            f"⚠️ این افراد هنوز رأی ندادن:\n{missing_names}\n\n"
            "می‌خوای صبر کنی یا به‌زور تمومش کنی؟",
            reply_markup=inline_keyboard([
                [button("⏹ پایان اجباری", f"force_end_voting:{round_id}")],
                [button("🔙 صبر می‌کنم", f"cancel_action:{round_id}")]
            ])
        )
    else:
        finalize_round(round_id)

# ==================== FINALIZE ROUND ====================
def finalize_round(round_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    game_code = game["code"]

    conn = db()
    
    # پیدا کردن گزینه درست
    correct_option = conn.execute(
        "SELECT * FROM options WHERE round_id = ? AND is_correct = 1",
        (round_id,)
    ).fetchone()

    # محاسبه امتیازهای این دور
    round_scores = {}
    
    # ۱. رأی‌دهندگان درست +۱ امتیاز
    correct_voters = conn.execute("""
        SELECT v.voter_id FROM votes v
        WHERE v.round_id = ? AND v.option_id = ?
    """, (round_id, correct_option["id"])).fetchall()
    
    for cv in correct_voters:
        player_id = cv["voter_id"]
        round_scores[player_id] = round_scores.get(player_id, 0) + 1
        # به‌روزرسانی امتیاز کل
        conn.execute("UPDATE players SET score = score + 1 WHERE id = ?", (player_id,))

    # ۲. برای هر جواب اشتباه، به نویسنده‌اش تعداد رأی‌ها امتیاز
    wrong_options = conn.execute("""
        SELECT * FROM options WHERE round_id = ? AND is_correct = 0
    """, (round_id,)).fetchall()

    for opt in wrong_options:
        if opt["player_id"]:
            vote_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM votes WHERE round_id = ? AND option_id = ?",
                (round_id, opt["id"])
            ).fetchone()["cnt"]
            if vote_count > 0:
                player_id = opt["player_id"]
                round_scores[player_id] = round_scores.get(player_id, 0) + vote_count
                # به‌روزرسانی امتیاز کل
                conn.execute(
                    "UPDATE players SET score = score + ? WHERE id = ?",
                    (vote_count, player_id)
                )

    # ۳. ذخیره امتیاز این دور در round_players
    for player_id, score in round_scores.items():
        conn.execute(
            "UPDATE round_players SET score = ? WHERE round_id = ? AND player_id = ?",
            (score, round_id, player_id)
        )

    # ۴. برای بازیکنانی که امتیاز نگرفتن، صفر ثبت کن
    conn.execute("""
        UPDATE round_players SET score = 0 
        WHERE round_id = ? AND score IS NULL
    """, (round_id,))

    # پایان دور
    conn.execute("UPDATE rounds SET status = 'finished', finished_at = ? WHERE id = ?", (now(), round_id))
    conn.commit()
    # گرفتن جدول امتیازات
    players = get_players(game["id"])
    conn.close()

    # ساخت جدول امتیازات
    scoreboard = "📊 **جدول امتیازات:**\n\n"
    for i, p in enumerate(players, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        scoreboard += f"{medal} {p['name']}: {p['score']} امتیاز\n"

    # ارسال نتایج به بازیکنان
    for p in players:
        try:
            if p["user_id"] == game["owner_id"]:
                # بازیکن-مدیر: دکمه‌های مدیریت
                send_message(
                    p["user_id"],
                    f"🏁 **دور تموم شد!**\n\n"
                    f"❓ سوال: {round_data['question']}\n"
                    f"✅ جواب درست: {round_data['correct_answer']}\n\n"
                    f"{scoreboard}",
                    reply_markup=inline_keyboard([
                        [button("🚀 شروع دور جدید", f"start_round:{game_code}")],
                        [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
                        [button("🔄 تغییر دسته‌بندی", f"change_category:{game_code}")]
                    ])
                )
            else:
                send_message(
                    p["user_id"],
                    f"🏁 **دور تموم شد!**\n\n"
                    f"❓ سوال: {round_data['question']}\n"
                    f"✅ جواب درست: {round_data['correct_answer']}\n\n"
                    f"{scoreboard}"
                )
        except:
            pass

    # ارسال پنل مدیریت به مدیر (اگر خودش بازیکن نباشد)
    if not any(p["user_id"] == game["owner_id"] for p in players):
        send_message(
            game["owner_chat_id"],
            f"🏁 **دور تموم شد!**\n\n"
            f"❓ سوال: {round_data['question']}\n"
            f"✅ جواب درست: {round_data['correct_answer']}\n\n"
            f"{scoreboard}",
            reply_markup=inline_keyboard([
                [button("🚀 شروع دور جدید", f"start_round:{game_code}")],
                [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
                [button("🔄 تغییر دسته‌بندی", f"change_category:{game_code}")]
            ])
        )

# ==================== PENALTY SYSTEM ====================
def show_penalty_player_list(round_id, admin_id, chat_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    if not is_owner(game, admin_id):
        return

    conn = db()
    players = conn.execute("""
        SELECT p.id, p.name, rp.score FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.penalty = 0 AND rp.score > 0
        ORDER BY rp.score DESC
    """, (round_id,)).fetchall()
    conn.close()

    player_list = [dict(p) for p in players]

    if not player_list:
        send_message(chat_id, "✅ همه بازیکنان یا جریمه شدن یا امتیازی ندارن.")
        return

    # ساخت نگاشت عدد → player_id
    mapping = {}
    text = "🚫 **انتخاب بازیکن برای جریمه:**\n\n"
    for i, p in enumerate(player_list, 1):
        mapping[i] = p["id"]
        text += f"{i}. {p['name']} — {p['score']} امتیاز\n"

    text += "\n❌ برای لغو، «انصراف» یا /cancel رو بفرست."

    _penalty_mappings[round_id] = mapping
    set_user_state(admin_id, f"penalty_waiting:{round_id}")

    send_message(chat_id, text)

def apply_penalty(round_id, penalized_player_id):
    conn = db()
    rp = conn.execute(
        "SELECT * FROM round_players WHERE round_id = ? AND player_id = ?",
        (round_id, penalized_player_id)
    ).fetchone()

    if not rp or rp["penalty"] == 1:
        conn.close()
        return None

    score_to_deduct = rp["score"]  # امتیاز این دور

    # صفر کردن امتیاز این دور و علامت جریمه
    conn.execute(
        "UPDATE round_players SET score = 0, penalty = 1 WHERE round_id = ? AND player_id = ?",
        (round_id, penalized_player_id)
    )
    
    # کم کردن از امتیاز کل بازیکن
    conn.execute(
        "UPDATE players SET score = score - ? WHERE id = ?",
        (score_to_deduct, penalized_player_id)
    )
    conn.commit()
    conn.close()
    return score_to_deduct

def recalculate_and_broadcast(round_id):
    round_data = get_round(round_id)
    if not round_data:
        return

    game = get_game_by_id(round_data["game_id"])
    game_code = game["code"]
    players = get_players(game["id"])

    # پیدا کردن جریمه‌شده‌ها
    conn = db()
    penalized = conn.execute("""
        SELECT p.name FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.penalty = 1
    """, (round_id,)).fetchall()
    conn.close()

    penalized_names = [p["name"] for p in penalized]

    # ساخت جدول امتیازات
    scoreboard = "📊 **جدول امتیازات (به‌روز شده):**\n\n"
    for i, p in enumerate(players, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        penalty_tag = " 🚫" if p["name"] in penalized_names else ""
        scoreboard += f"{medal} {p['name']}: {p['score']} امتیاز{penalty_tag}\n"

    if penalized_names:
        scoreboard += f"\n🚫 جریمه‌شده: {', '.join(penalized_names)}"

    # ارسال به همه بازیکنان
    for p in players:
        try:
            if p["user_id"] == game["owner_id"]:
                send_message(
                    p["user_id"],
                    f"{scoreboard}",
                    reply_markup=inline_keyboard([
                        [button("🚀 شروع دور جدید", f"start_round:{game_code}")],
                        [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
                        [button("🔄 تغییر دسته‌بندی", f"change_category:{game_code}")]
                    ])
                )
            else:
                send_message(p["user_id"], scoreboard)
        except:
            pass

    # اگر مدیر بازیکن نیست
        # اگر مدیر بازیکن نیست
    if not any(p["user_id"] == game["owner_id"] for p in players):
        send_message(
            game["owner_chat_id"],
            f"{scoreboard}",
            reply_markup=inline_keyboard([
                [button("🚀 شروع دور جدید", f"start_round:{game_code}")],
                [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
                [button("🔄 تغییر دسته‌بندی", f"change_category:{game_code}")]
            ])
        )

# ==================== HANDLE CALLBACKS ====================
def handle_callback(callback_data, chat_id, user_id, callback_id=None):
    """مدیریت کلیک روی دکمه‌های اینلاین"""
    print(f"Callback: {callback_data}")
    
    parts = callback_data.split(":")
    action = parts[0]
    
    if action == "new_game":
        create_game(chat_id, user_id)
        if callback_id:
            answer_callback(callback_id, "🎮 بازی جدید ساخته شد!")
        
    elif action == "show_help":
        send_message(
            chat_id,
            "📖 **راهنمای بازی شیاد:**\n\n"
            "🎮 **نحوه بازی:**\n"
            "1️⃣ مدیر بازی رو می‌سازه و لینک دعوت رو می‌فرسته\n"
            "2️⃣ بازیکنان با کلیک روی لینک و وارد کردن اسم عضو میشن\n"
            "3️⃣ مدیر دور جدید رو شروع می‌کنه و سوال نمایش داده میشه\n"
            "4️⃣ بازیکنان باید جواب **اشتباه** اما باورپذیر بدن\n"
            "5️⃣ مدیر جواب‌ها رو می‌بینه و رأی‌گیری رو شروع می‌کنه\n"
            "6️⃣ بازیکنان به جوابی که فکر می‌کنن درسته رأی میدن\n"
            "7️⃣ امتیازات محاسبه و نتایج نمایش داده میشه\n\n"
            "📊 **امتیازدهی:**\n"
            "✅ هر رأی درست: +1 امتیاز\n"
            "🎭 هر رأی به جواب اشتباه: +1 امتیاز برای نویسنده\n"
            "🚫 مدیر می‌تونه به بازیکن پرامتیاز جریمه بده\n\n"
            "برای شروع روی «ساخت بازی جدید» کلیک کن!",
            reply_markup=inline_keyboard([
                [button("🎮 ساخت بازی جدید", "new_game")],
                [button("🏠 بازگشت به منو", "back_to_menu")]
            ])
        )
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "back_to_menu":
        clear_user_state(user_id)
        send_main_menu(chat_id, user_id)
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "start_round":
        if len(parts) >= 2:
            game_code = parts[1]
            start_new_round(chat_id, user_id, game_code)
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "set_category":
        if len(parts) >= 3:
            game_code = parts[1]
            category = parts[2]
            conn = db()
            conn.execute("UPDATE games SET category = ? WHERE code = ?", (category, game_code))
            conn.commit()
            conn.close()
            send_message(
                chat_id,
                f"✅ دسته‌بندی به «{category}» تغییر کرد.",
                reply_markup=inline_keyboard([
                    [button("🚀 شروع دور جدید", f"start_round:{game_code}")]
                ])
            )
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "change_category":
        if len(parts) >= 2:
            game_code = parts[1]
            send_message(
                chat_id,
                "🎯 **دسته‌بندی جدید رو انتخاب کن:**",
                reply_markup=inline_keyboard([
                    [button("📚 اطلاعات عمومی", f"set_category:{game_code}:سخت")],
                    [button("🤪 سوالات عجیب و خنده‌دار", f"set_category:{game_code}:عجیب")]
                ])
            )
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "end_answers":
        if len(parts) >= 2:
            round_id = int(parts[1])
            request_end_answers(round_id, chat_id, user_id)
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "force_end_answers":
        if len(parts) >= 2:
            round_id = int(parts[1])
            close_answers_and_prepare_options(round_id)
        if callback_id:
            answer_callback(callback_id, "✅ ارسال جواب‌ها بسته شد.")
        
    elif action == "start_voting":
        if len(parts) >= 2:
            round_id = int(parts[1])
            start_voting(round_id, chat_id, user_id)
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "end_voting":
        if len(parts) >= 2:
            round_id = int(parts[1])
            request_end_voting(round_id, chat_id, user_id)
        if callback_id:
            answer_callback(callback_id)
        
    elif action == "force_end_voting":
        if len(parts) >= 2:
            round_id = int(parts[1])
            finalize_round(round_id)
        if callback_id:
            answer_callback(callback_id, "✅ رأی‌گیری بسته و نتایج محاسبه شد.")
        
    elif action == "cancel_action":
        if callback_id:
            answer_callback(callback_id, "❌ عملیات لغو شد.")
        
    elif action == "penalty_start":
        if len(parts) >= 2:
            round_id = int(parts[1])
            show_penalty_player_list(round_id, user_id, chat_id)
        if callback_id:
            answer_callback(callback_id)
        
    else:
        if callback_id:
            answer_callback(callback_id, "⚠️ دستور نامعتبر")

# ==================== HANDLE MESSAGES ====================
def handle_message(chat_id, user_id, text, username=None, first_name=None):
    """مدیریت پیام‌های متنی"""
    text = text.strip()
    
    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1:
            game_code = parts[1]
            join_game_start(chat_id, user_id, game_code)
        else:
            send_main_menu(chat_id, user_id)
        return
    
    elif text in ["/cancel", "انصراف", "لغو"]:
        clear_user_state(user_id)
        send_back_to_menu_button(chat_id, "❌ عملیات کنونی لغو شد.")
        return
    
    elif text in ["/help", "راهنما"]:
        handle_callback("show_help", chat_id, user_id)
        return
    
    elif text == "/menu":
        send_main_menu(chat_id, user_id)
        return
    
    # بررسی وضعیت کاربر
    state_row = get_user_state(user_id)
    if state_row:
        state = state_row["state"]
        
        if state.startswith("awaiting_name:"):
            game_code = state.split(":")[1]
            save_player_name(chat_id, user_id, text, game_code)
            return
        
        elif state.startswith("penalty_waiting:"):
            round_id = int(state.split(":")[1])
            if text.isdigit():
                number = int(text)
                mapping = _penalty_mappings.get(round_id, {})
                if number in mapping:
                    player_id = mapping[number]
                    deducted = apply_penalty(round_id, player_id)
                    if deducted is not None:
                        player = get_player_by_id(player_id)
                        send_message(
                            chat_id,
                            f"🚫 **جریمه اعمال شد!**\n\n"
                            f"👤 بازیکن: {player['name']}\n"
                            f"➖ امتیاز کسر شده: {deducted}\n\n"
                            "🔄 جدول امتیازات در حال به‌روزرسانی..."
                        )
                        recalculate_and_broadcast(round_id)
                    else:
                        send_message(chat_id, "⚠️ این بازیکن قبلاً جریمه شده!")
                    _penalty_mappings.pop(round_id, None)
                    clear_user_state(user_id)
                else:
                    send_message(chat_id, "⚠️ شماره نامعتبر. دوباره انتخاب کن:")
            else:
                send_message(chat_id, "⚠️ لطفاً فقط عدد وارد کن:")
            return
    
    # بررسی اگر کاربر در حال پاسخ دادن به سوال است
    if handle_answer_message(chat_id, user_id, text):
        return
    
    # بررسی اگر کاربر در حال رأی دادن است
    if handle_vote_message(chat_id, user_id, text):
        return
    
    # اگر هیچ‌کدام نبود، منوی اصلی
    send_main_menu(chat_id, user_id)

# ==================== WEBHOOK HANDLERS ====================
@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    print(f"📥 Received update: {json.dumps(data, indent=2, ensure_ascii=False)}")
    
    try:
        if "message" in data:
            msg = data["message"]
            chat_id = msg["chat"]["id"]
            user_id = msg["from"]["id"]
            text = msg.get("text", "").strip()
            username = msg["from"].get("username")
            first_name = msg["from"].get("first_name", "")
            
            if text:
                handle_message(chat_id, user_id, text, username, first_name)
        
        elif "callback_query" in data:
            cb = data["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            user_id = cb["from"]["id"]
            callback_data = cb["data"]
            callback_id = cb["id"]
            
            handle_callback(callback_data, chat_id, user_id, callback_id)
        
        else:
            print(f"⚠️ Unhandled update type: {data.keys()}")
    
    except Exception as e:
        print(f"❌ Error processing update: {e}")
        import traceback
        traceback.print_exc()
    
    return jsonify({"ok": True})

@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        return "❌ WEBHOOK_URL not set", 400
    
    result = tg_request("setWebhook", {"url": webhook_url})
    return jsonify(result)

@app.route("/delete-webhook", methods=["GET"])
def delete_webhook():
    result = tg_request("deleteWebhook")
    return jsonify(result)

@app.route("/init-db", methods=["GET"])
def init_db_route():
    init_db()
    return "✅ Database initialized", 200

@app.route("/", methods=["GET"])
def index():
    return "🤖 Bot is running!", 200

# ==================== MAIN ====================
if __name__ == "__main__":
    # Initialize database
    init_db()
    
    # Set webhook on startup
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        print(f"🔗 Setting webhook to: {webhook_url}")
        result = tg_request("setWebhook", {"url": webhook_url})
        print(f"📡 Webhook result: {json.dumps(result, indent=2, ensure_ascii=False)}")
    else:
        print("⚠️ WEBHOOK_URL not set — webhook won't be configured automatically")
    
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port)

