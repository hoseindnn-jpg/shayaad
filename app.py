# ==================== IMPORTS & CONFIG ====================
import os
import sqlite3
import json
import time
import random
import string
import datetime
from flask import Flask, request, jsonify
import requests

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "YOUR_BOT_USERNAME")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-app.onrender.com/telegram/webhook")
DB_PATH = os.environ.get("DB_PATH", "game.db")
QUESTIONS_FILE = os.environ.get("QUESTIONS_FILE", "questions.json")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Debug info
print(f"📁 Database path: {os.path.abspath(DB_PATH)}")
print(f"📁 Working dir: {os.getcwd()}")

_db_initialized = False

# ==================== DATABASE FUNCTIONS ====================
def db():
    """باز کردن اتصال دیتابیس"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """ساخت جداول در صورت نبودن"""
    conn = db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                owner_id INTEGER NOT NULL,
                owner_chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                category TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT,
                game_id INTEGER NOT NULL,
                score INTEGER DEFAULT 0,
                FOREIGN KEY (game_id) REFERENCES games(id)
            );

            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                data TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                status TEXT DEFAULT 'answering',
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (game_id) REFERENCES games(id)
            );

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
            );

            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id),
                FOREIGN KEY (player_id) REFERENCES players(id)
            );

            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                option_number INTEGER NOT NULL,
                option_text TEXT NOT NULL,
                is_correct INTEGER DEFAULT 0,
                player_id INTEGER,
                FOREIGN KEY (round_id) REFERENCES rounds(id),
                FOREIGN KEY (player_id) REFERENCES players(id)
            );

            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                voter_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                FOREIGN KEY (round_id) REFERENCES rounds(id),
                FOREIGN KEY (voter_id) REFERENCES players(id),
                FOREIGN KEY (option_id) REFERENCES options(id)
            );
        """)

        # migrations
        migrations = [
            "ALTER TABLE players ADD COLUMN normalized_name TEXT",
            "ALTER TABLE round_players ADD COLUMN score INTEGER DEFAULT 0",
            "ALTER TABLE round_players ADD COLUMN penalty INTEGER DEFAULT 0",
            "ALTER TABLE games ADD COLUMN category TEXT DEFAULT NULL"
        ]
        for m in migrations:
            try:
                conn.execute(m)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB init error: {e}")
        raise
    finally:
        conn.close()

def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

# ==================== FLASK APP & BEFORE REQUEST ====================
app = Flask(__name__)

@app.before_request
def before_request():
    ensure_db()

# ==================== TELEGRAM API HELPERS ====================
def tg_request(method, params):
    url = f"{API_URL}/{method}"
    try:
        r = requests.post(url, json=params, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Telegram API error ({method}): {e}")
        return {"ok": False}

def send_message(chat_id, text, **kwargs):
    return tg_request("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", **kwargs})

def answer_callback(callback_id, text=None, show_alert=False):
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
        params["show_alert"] = show_alert
    return tg_request("answerCallbackQuery", params)

def button(text, data):
    return {"text": text, "callback_data": data}

def inline_keyboard(buttons):
    return {"inline_keyboard": buttons}

def send_main_menu(chat_id, user_id):
    send_message(
        chat_id,
        "🎮 **به بازی شیاد خوش اومدی!**\n\nیک بازی جدید بساز یا راهنما رو ببین:",
        reply_markup=inline_keyboard([
            [button("🎮 ساخت بازی جدید", "new_game")],
            [button("📖 راهنما", "show_help")]
        ])
    )

def send_back_to_menu_button(chat_id, text):
    send_message(
        chat_id,
        text,
        reply_markup=inline_keyboard([[button("🏠 بازگشت به منو", "back_to_menu")]])
    )

# ==================== UTILITIES ====================
def normalize(text):
    """نرمال‌سازی متن برای مقایسه"""
    return ''.join(c for c in text if c.isalnum()).lower()

def get_user_state(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row

def set_user_state(user_id, state, data=None, chat_id=None):
    conn = db()
    now = datetime.datetime.now().isoformat()
    conn.execute("INSERT OR REPLACE INTO user_states (user_id, state, data, updated_at) VALUES (?, ?, ?, ?)",
                 (user_id, state, data, now))
    conn.commit()
    conn.close()

def clear_user_state(user_id):
    conn = db()
    conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_player_by_id(player_id):
    conn = db()
    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    conn.close()
    return row

# ==================== GAME CREATION ====================
def create_game(chat_id, user_id):
    game_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    now = datetime.datetime.now().isoformat()
    conn = db()
    try:
        conn.execute("INSERT INTO games (code, owner_id, owner_chat_id, created_at) VALUES (?, ?, ?, ?)",
                     (game_code, user_id, chat_id, now))
        conn.commit()
        game_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        conn.close()
        send_message(chat_id, "❌ خطا در ساخت بازی. لطفاً دوباره تلاش کنید.")
        return
    conn.close()

    invite_link = f"https://t.me/{BOT_USERNAME}?start={game_code}"
    send_message(
        chat_id,
        f"🎮 **بازی جدید ساخته شد!**\n\n🔑 کد بازی: `{game_code}`\n\n"
        f"لینک دعوت:\n{invite_link}\n\n"
        "برای شروع دور جدید، دکمه زیر را بزنید:",
        reply_markup=inline_keyboard([
            [button("🚀 شروع دور جدید", f"start_round:{game_code}")]
        ])
    )

# ==================== JOIN GAME ====================
def join_game_start(chat_id, user_id, game_code):
    conn = db()
    game = conn.execute("SELECT * FROM games WHERE code = ?", (game_code,)).fetchone()
    if not game:
        conn.close()
        send_message(chat_id, "❌ کد بازی نامعتبر است.")
        return
    # چک تکراری
    existing = conn.execute("SELECT * FROM players WHERE user_id = ? AND game_id = ?",
                            (user_id, game["id"])).fetchone()
    if existing:
        conn.close()
        send_message(chat_id, "⚠️ شما قبلاً در این بازی عضو شده‌اید!")
        return
    conn.close()

    set_user_state(user_id, f"awaiting_name:{game_code}")
    send_message(chat_id, "📝 لطفاً اسم خودت رو وارد کن:")

def save_player_name(chat_id, user_id, name, game_code):
    name = name.strip()
    if len(name) < 2 or len(name) > 20:
        send_message(chat_id, "⚠️ اسم باید بین ۲ تا ۲۰ حرف باشه. دوباره بفرست:")
        return

    conn = db()
    game = conn.execute("SELECT * FROM games WHERE code = ?", (game_code,)).fetchone()
    if not game:
        conn.close()
        send_message(chat_id, "❌ بازی یافت نشد.")
        clear_user_state(user_id)
        return

    norm = normalize(name)
    # چک اسم تکراری در بازی
    dup = conn.execute("SELECT * FROM players WHERE game_id = ? AND normalized_name = ?",
                       (game["id"], norm)).fetchone()
    if dup:
        conn.close()
        send_message(chat_id, "⚠️ این اسم قبلاً توی بازی استفاده شده. یه اسم دیگه بزن:")
        return

    conn.execute("INSERT INTO players (user_id, name, normalized_name, game_id) VALUES (?, ?, ?, ?)",
                 (user_id, name, norm, game["id"]))
    conn.commit()
    conn.close()
    clear_user_state(user_id)
    send_message(chat_id, f"✅ با اسم **{name}** وارد بازی شدی! منتظر شروع دور باش.")

    # اطلاع‌رسانی به مدیر
    send_message(game["owner_chat_id"], f"👤 {name} به بازی پیوست.")

# ==================== START ROUND ====================
def load_questions(category=None):
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            all_qs = json.load(f)
    except Exception as e:
        print(f"Load questions error: {e}")
        return []
    if category:
        all_qs = [q for q in all_qs if q.get("category", "سخت") == category]
    if not all_qs:
        return []
    question = random.choice(all_qs)
    return question

def start_new_round(chat_id, user_id, game_code):
    conn = db()
    game = conn.execute("SELECT * FROM games WHERE code = ?", (game_code,)).fetchone()
    if not game or game["owner_id"] != user_id:
        conn.close()
        send_message(chat_id, "❌ فقط مدیر می‌تواند دور جدید شروع کند.")
        return

    # دریافت بازیکنان
    players = conn.execute("SELECT * FROM players WHERE game_id = ?", (game["id"],)).fetchall()
    if len(players) < 2:
        conn.close()
        send_message(chat_id, "⚠️ حداقل ۲ بازیکن لازم است.")
        return

    category = game["category"]
    if not category:
        conn.close()
        send_message(
            chat_id,
            "🎯 **قبل از شروع اولین دور، دسته‌بندی سوالات رو انتخاب کن:**",
            reply_markup=inline_keyboard([
                [button("📚 اطلاعات عمومی", f"set_category_first:{game_code}:سخت")],
                [button("🤪 سوالات عجیب و خنده‌دار", f"set_category_first:{game_code}:عجیب")]
            ])
        )
        return
    q = load_questions(category)
    if not q:
        conn.close()
        send_message(chat_id, "❌ سوالی برای این دسته‌بندی یافت نشد.")
        return

    now = datetime.datetime.now().isoformat()
    conn.execute("INSERT INTO rounds (game_id, question, correct_answer, status, created_at) VALUES (?, ?, ?, 'answering', ?)",
                 (game["id"], q["question"], q["answer"], now))
    round_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ثبت round_players
    for p in players:
        conn.execute("INSERT INTO round_players (round_id, player_id) VALUES (?, ?)", (round_id, p["id"]))
    conn.commit()
    conn.close()
    # 🔄 تغییر دوم: اول سوال رو فقط به مدیر نشون بده
    send_message(
        chat_id,
        f"📝 **پیش‌نمایش سوال:**\n\n"
        f"🎲 {q['question']}\n\n"
        f"👥 این سوال برای {len(players)} بازیکن ارسال خواهد شد.",
        reply_markup=inline_keyboard([
            [button("🔄 تغییر سوال", f"change_question:{round_id}")],
            [button("📤 ارسال به بازیکنان", f"send_question:{round_id}")]
        ])
    )


# ==================== HANDLE ANSWERS ====================
_used_answers = set()  # فقط برای جلوگیری از تکرار در یک دور (reset شود؟ بهتر در دیتابیس)

def handle_answer_message(chat_id, user_id, text):
    """بررسی و ثبت پاسخ بازیکن"""
    conn = db()
    # پیدا کردن آخرین دور فعال که کاربر در آن عضو است
    row = conn.execute("""
        SELECT rp.id, rp.round_id, rp.player_id, rp.has_answered, r.question, r.correct_answer, r.status
        FROM round_players rp
        JOIN rounds r ON r.id = rp.round_id
        JOIN players p ON p.id = rp.player_id
        WHERE p.user_id = ? AND r.status = 'answering' AND rp.has_answered = 0
        ORDER BY r.created_at DESC LIMIT 1
    """, (user_id,)).fetchone()
    if not row:
        conn.close()
        return False  # کاربر در حال جواب‌دهی نیست

    if row["has_answered"]:
        conn.close()
        return False

    text = text.strip()
    # چک اینکه جواب درست نباشه
    if normalize(text) == normalize(row["correct_answer"]):
        conn.close()
        send_message(chat_id, "⚠️ نمیتونی جواب درست رو بفرستی! یه جواب اشتباه بده.")
        return True

    # ثبت جواب
    try:
        conn.execute("INSERT INTO answers (round_id, player_id, answer_text) VALUES (?, ?, ?)",
                     (row["round_id"], row["player_id"], text))
        conn.execute("UPDATE round_players SET has_answered = 1 WHERE id = ?", (row["id"],))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        send_message(chat_id, "⚠️ قبلاً جواب دادی دیگه!")
        return True

    # به مدیر اطلاع بده
    game_code_row = conn.execute("""
        SELECT g.code FROM games g JOIN rounds r ON r.game_id = g.id WHERE r.id = ?
    """, (row["round_id"],)).fetchone()
    if game_code_row:
        owner_chat = conn.execute("SELECT owner_chat_id FROM games WHERE code = ?",
                                  (game_code_row["code"],)).fetchone()
        if owner_chat:
            send_message(owner_chat["owner_chat_id"],
                         f"📩 بازیکن {get_player_by_id(row['player_id'])['name']} پاسخ داد.")
    conn.close()
    return True

def request_end_answers(round_id, chat_id, user_id):
    """مدیر می‌خواهد زمان جواب‌دهی تمام شود"""
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not round_row or round_row["status"] != "answering":
        conn.close()
        return
    # تأیید مدیر
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    if game["owner_id"] != user_id:
        conn.close()
        return

    # نمایش دکمه اتمام
    not_answered = conn.execute("""
        SELECT p.name FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.has_answered = 0
    """, (round_id,)).fetchall()
    if not_answered:
        names = ", ".join(p["name"] for p in not_answered)
        send_message(chat_id,
                     f"هنوز {names} جواب ندادن. مطمئنی تموم کنم؟",
                     reply_markup=inline_keyboard([
                         [button("✅ بله تمومش کن", f"force_end_answers:{round_id}")],
                         [button("❌ صبر کن", f"cancel_action")]
                     ]))
    else:
        close_answers_and_prepare_options(round_id)
    conn.close()

def close_answers_and_prepare_options(round_id):
    """بستن جواب‌ها و ساخت گزینه‌ها برای رأی‌گیری"""
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ? AND status = 'answering'", (round_id,)).fetchone()
    if not round_row:
        conn.close()
        return

    answers_list = conn.execute("""
        SELECT a.id, a.answer_text, a.player_id, p.name
        FROM answers a
        JOIN players p ON p.id = a.player_id
        WHERE a.round_id = ?
    """, (round_id,)).fetchall()
    if not answers_list:
        conn.close()
        return

    # اضافه کردن جواب درست به صورت رندوم بین گزینه‌ها
    all_options = []
    for i, ans in enumerate(answers_list, 1):
        all_options.append((ans["answer_text"], ans["player_id"]))
    correct_pos = random.choice(range(len(all_options) + 1))
    all_options.insert(correct_pos, (round_row["correct_answer"], None))

    # ذخیره گزینه‌ها در جدول options
    for idx, (opt_text, player_id) in enumerate(all_options, 1):
        is_correct = 1 if player_id is None else 0
        conn.execute("INSERT INTO options (round_id, option_number, option_text, is_correct, player_id) VALUES (?, ?, ?, ?, ?)",
                     (round_id, idx, opt_text, is_correct, player_id))

    conn.execute("UPDATE rounds SET status = 'voting' WHERE id = ?", (round_id,))
    conn.execute("UPDATE round_players SET can_vote = 1 WHERE round_id = ?", (round_id,))
    conn.commit()

    # ارسال گزینه‌ها به همه بازیکنان
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    owner_chat = game["owner_chat_id"]
    players = conn.execute("""
        SELECT p.user_id, p.id FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ?
    """, (round_id,)).fetchall()

    option_text = "\n".join([f"{opt['option_number']}) {opt['option_text']}" for opt in
                              conn.execute("SELECT * FROM options WHERE round_id = ? ORDER BY option_number",
                                           (round_id,)).fetchall()])
    for p in players:
        send_message(p["user_id"],
                     f"🗳 **رأی‌گیری شروع شد!**\n\nسوال: {round_row['question']}\n\nگزینه‌ها:\n{option_text}\n\n"
                     "لطفاً **فقط شماره گزینه** درست رو بفرست.")

    # دکمه‌های مدیر
    send_message(owner_chat,
                 f"گزینه‌ها آماده شدن. بازیکنان رأی می‌دن.",
                 reply_markup=inline_keyboard([
                     [button("🛑 پایان رأی‌گیری", f"end_voting:{round_id}")]
                 ]))
    conn.close()

# ==================== HANDLE VOTES ====================
def handle_vote_message(chat_id, user_id, text):
    """دریافت رأی بازیکن"""
    conn = db()
    # یافتن دور رأی‌گیری فعال
    row = conn.execute("""
        SELECT rp.round_id, rp.player_id, rp.can_vote
        FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE p.user_id = ? AND rp.can_vote = 1
        AND rp.round_id IN (SELECT id FROM rounds WHERE status = 'voting')
        ORDER BY rp.round_id DESC LIMIT 1
    """, (user_id,)).fetchone()
    if not row:
        conn.close()
        return False

    if not row["can_vote"]:
        conn.close()
        return False

    if not text.isdigit():
        send_message(chat_id, "⚠️ فقط شماره گزینه رو بفرست (مثلاً 2)")
        return True
    opt_num = int(text)

    # اعتبارسنجی گزینه
    option = conn.execute("SELECT id FROM options WHERE round_id = ? AND option_number = ?",
                          (row["round_id"], opt_num)).fetchone()
    if not option:
        send_message(chat_id, "❌ گزینه نامعتبر. دوباره بفرست.")
        return True

    # ثبت رأی
    try:
        conn.execute("INSERT INTO votes (round_id, voter_id, option_id) VALUES (?, ?, ?)",
                     (row["round_id"], row["player_id"], option["id"]))
        conn.execute("UPDATE round_players SET can_vote = 0 WHERE round_id = ? AND player_id = ?",
                     (row["round_id"], row["player_id"]))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        send_message(chat_id, "⚠️ قبلاً رأی دادی!")
        return True

    send_message(chat_id, "✅ رأی شما ثبت شد.")
    conn.close()
    return True

def request_end_voting(round_id, chat_id, user_id):
    """مدیر می‌خواهد رأی‌گیری تمام شود"""
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ? AND status = 'voting'", (round_id,)).fetchone()
    if not round_row:
        conn.close()
        return
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    if game["owner_id"] != user_id:
        conn.close()
        return

    not_voted = conn.execute("""
        SELECT p.name FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.can_vote = 1
    """, (round_id,)).fetchall()
    if not_voted:
        names = ", ".join(p["name"] for p in not_voted)
        send_message(chat_id,
                     f"هنوز {names} رأی ندادن. مطمئنی تموم کنم؟",
                     reply_markup=inline_keyboard([
                         [button("✅ بله تمومش کن", f"force_end_voting:{round_id}")],
                         [button("❌ صبر کن", f"cancel_action")]
                     ]))
    else:
        finalize_round(round_id)
    conn.close()

# ==================== FINALIZE ROUND & SCORING ====================
def finalize_round(round_id):
    """محاسبه امتیازات و نمایش نتایج"""
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not round_row or round_row["status"] != "voting":
        conn.close()
        return

    # آپدیت وضعیت
    conn.execute("UPDATE rounds SET status = 'finished', finished_at = ? WHERE id = ?",
                 (datetime.datetime.now().isoformat(), round_id))

    # یافتن گزینه درست
    correct_option = conn.execute("SELECT id, option_number FROM options WHERE round_id = ? AND is_correct = 1",
                                  (round_id,)).fetchone()

    # محاسبه امتیاز هر رأی دهنده
    votes_list = conn.execute("""
        SELECT v.voter_id, v.option_id, o.is_correct
        FROM votes v
        JOIN options o ON o.id = v.option_id
        WHERE v.round_id = ?
    """, (round_id,)).fetchall()

    # دیکشنری امتیاز برای بازیکن
    player_scores = {}
    # ابتدا صفر
    players_in_round = conn.execute("SELECT player_id FROM round_players WHERE round_id = ?", (round_id,)).fetchall()
    for p in players_in_round:
        player_scores[p["player_id"]] = 0

    # امتیاز رأی‌دهندگان درست
    for v in votes_list:
        if v["is_correct"]:
            player_scores[v["voter_id"]] = player_scores.get(v["voter_id"], 0) + 1
        else:
            # رأی به گزینه اشتباه -> نویسنده گزینه اشتباه امتیاز می‌گیرد
            author = conn.execute("SELECT player_id FROM options WHERE id = ?", (v["option_id"],)).fetchone()
            if author and author["player_id"]:
                player_scores[author["player_id"]] = player_scores.get(author["player_id"], 0) + 1

    # ذخیره امتیازات در round_players.score
    for pid, score in player_scores.items():
        conn.execute("UPDATE round_players SET score = ? WHERE round_id = ? AND player_id = ?",
                     (score, round_id, pid))
        # به امتیاز کل هم اضافه می‌شود
        conn.execute("UPDATE players SET score = score + ? WHERE id = ?", (score, pid))

    conn.commit()

    # --- ساخت جدول نتایج ---
    gm = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    players = conn.execute("""
        SELECT p.id, p.name, p.score, rp.score as round_score
        FROM players p
        JOIN round_players rp ON rp.player_id = p.id
        WHERE rp.round_id = ?
        ORDER BY p.score DESC
    """, (round_id,)).fetchall()

    leaderboard = "🏆 **نتایج این دور:**\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, pl in enumerate(players):
        medal = medals[i] if i < 3 else f"{i+1}."
        leaderboard += f"{medal} {pl['name']}: {pl['round_score']} امتیاز (کل: {pl['score']})\n"

    leaderboard += f"\n✅ جواب درست: **{round_row['correct_answer']}**"

    # ارسال به همه
    all_players = conn.execute("SELECT user_id FROM players WHERE game_id = ?", (gm["id"],)).fetchall()
    for p in all_players:
        send_message(p["user_id"], leaderboard)

    # دکمه‌های مدیر
    send_message(
        gm["owner_chat_id"],
        "دور تموم شد.",
        reply_markup=inline_keyboard([
            [button("🚀 شروع دور جدید", f"start_round:{gm['code']}")],
            [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
            [button("🔄 تغییر دسته‌بندی", f"change_category:{gm['code']}")]
        ])
    )
    conn.close()
# ==================== PENALTY SYSTEM ====================
_penalty_mappings = {}  # {round_id: {number: player_id}}

def show_penalty_player_list(round_id, user_id, chat_id):
    """نمایش لیست بازیکنان برای جریمه"""
    conn = db()
    # بررسی اینکه کاربر مدیر است
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not round_row:
        conn.close()
        send_message(chat_id, "❌ دور یافت نشد.")
        return
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    if game["owner_id"] != user_id:
        conn.close()
        send_message(chat_id, "❌ فقط مدیر می‌تواند جریمه ثبت کند.")
        return

    # دریافت بازیکنان با امتیاز بالا در این دور
    players = conn.execute("""
        SELECT p.id, p.name, rp.score, p.score as total_score
        FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ? AND rp.penalty = 0
        ORDER BY rp.score DESC
        LIMIT 5
    """, (round_id,)).fetchall()
    conn.close()

    if not players:
        send_message(chat_id, "⚠️ بازیکنی برای جریمه وجود ندارد.")
        return

    # ساخت لیست شماره‌گذاری شده
    mapping = {}
    text = "🚫 **انتخاب بازیکن برای جریمه:**\n\n"
    for idx, p in enumerate(players, 1):
        mapping[idx] = p["id"]
        text += f"{idx}. {p['name']} - امتیاز این دور: {p['score']} (کل: {p['total_score']})\n"

    _penalty_mappings[round_id] = mapping
    set_user_state(user_id, f"penalty_waiting:{round_id}", chat_id=chat_id)

    send_message(
        chat_id,
        text + "\nشماره بازیکن مورد نظر را بفرستید:",
        reply_markup=inline_keyboard([[button("❌ لغو", "cancel_action")]])
    )

def apply_penalty(round_id, player_id):
    """اعمال جریمه به بازیکن"""
    conn = db()
    # چک اینکه قبلاً جریمه نشده باشد
    existing = conn.execute("SELECT penalty FROM round_players WHERE round_id = ? AND player_id = ?",
                           (round_id, player_id)).fetchone()
    if existing and existing["penalty"]:
        conn.close()
        return None

    # محاسبه امتیاز کسر شده (مثلاً ۳ امتیاز)
    deduction = 3
    conn.execute("UPDATE round_players SET penalty = ? WHERE round_id = ? AND player_id = ?",
                 (deduction, round_id, player_id))
    conn.execute("UPDATE players SET score = score - ? WHERE id = ?", (deduction, player_id))
    conn.commit()
    conn.close()
    return deduction

def recalculate_and_broadcast(round_id):
    """محاسبه مجدد امتیازات و ارسال به همه"""
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not round_row:
        conn.close()
        return
    
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    players = conn.execute("""
        SELECT p.id, p.name, p.score, rp.score as round_score, rp.penalty
        FROM players p
        JOIN round_players rp ON rp.player_id = p.id
        WHERE rp.round_id = ?
        ORDER BY p.score DESC
    """, (round_id,)).fetchall()
    
    leaderboard = "📊 **جدول امتیازات (پس از جریمه):**\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(players):
        medal = medals[i] if i < 3 else f"{i+1}."
        penalty_text = f" (-{p['penalty']})" if p["penalty"] else ""
        leaderboard += f"{medal} {p['name']}: {p['round_score']}{penalty_text} امتیاز (کل: {p['score']})\n"
    
    # ارسال به همه بازیکنان
    all_players = conn.execute("SELECT user_id FROM players WHERE game_id = ?", (game["id"],)).fetchall()
    for pl in all_players:
        send_message(pl["user_id"], leaderboard)
    
    # اگر مدیر بازیکن نیست
    if not any(p["id"] == game["owner_id"] for p in players):
        send_message(
            game["owner_chat_id"],
            leaderboard,
            reply_markup=inline_keyboard([
                [button("🚀 شروع دور جدید", f"start_round:{game['code']}")],
                [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")],
                [button("🔄 تغییر دسته‌بندی", f"change_category:{game['code']}")]
            ])
        )
    conn.close()

# ==================== HANDLE CALLBACKS ====================
def handle_callback(chat_id, user_id, callback_data, callback_id=None):
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
                f"✅ دسته‌بندی تغییر کرد.",
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
        clear_user_state(user_id)
        if callback_id:
            answer_callback(callback_id, "❌ عملیات لغو شد.")
        
    elif action == "penalty_start":
        if len(parts) >= 2:
            round_id = int(parts[1])
            show_penalty_player_list(round_id, user_id, chat_id)
        if callback_id:
            answer_callback(callback_id)
    elif action == "set_category_first":
        # مشابه set_category ولی بعدش مستقیم start_new_round رو صدا می‌زنه
        if len(parts) >= 3:
            game_code = parts[1]
            category = parts[2]
            conn = db()
            conn.execute("UPDATE games SET category = ? WHERE code = ?", (category, game_code))
            conn.commit()
            conn.close()
            send_message(chat_id, f"✅ دسته‌بندی تغییر کرد.")
            # حالا start_new_round رو دوباره صدا کن
            start_new_round(chat_id, user_id, game_code)
        if callback_id:
            answer_callback(callback_id)
    elif action == "change_question":
        if len(parts) >= 2:
            round_id = int(parts[1])
            # گرفتن اطلاعات دور و بازی
            conn = db()
            round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
            if round_row:
                game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
                category = game["category"] or "سخت"
                # سوال جدید با همون دسته‌بندی
                new_q = load_questions(category)
                if new_q:
                    # آپدیت سوال در دیتابیس
                    conn.execute(
                        "UPDATE rounds SET question = ?, correct_answer = ? WHERE id = ?",
                        (new_q["question"], new_q["answer"], round_id)
                    )
                    conn.commit()
                    conn.close()
                    # نمایش سوال جدید به مدیر
                    send_message(
                        chat_id,
                        f"🔄 **سوال جدید:**\n\n"
                        f"🎲 {new_q['question']}\n\n"
                        reply_markup=inline_keyboard([
                            [button("🔄 تغییر سوال", f"change_question:{round_id}")],
                            [button("📤 ارسال به بازیکنان", f"send_question:{round_id}")]
                        ])
                    )
                else:
                    conn.close()
                    send_message(chat_id, "❌ سوال دیگه‌ای برای این دسته‌بندی پیدا نشد.")
        if callback_id:
            answer_callback(callback_id)
    elif action == "send_question":
        if len(parts) >= 2:
            round_id = int(parts[1])
            conn = db()
            round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
            if round_row:
                game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
                # ارسال سوال به همه بازیکنان
                players = conn.execute("""
                    SELECT p.user_id, p.name FROM round_players rp
                    JOIN players p ON p.id = rp.player_id
                    WHERE rp.round_id = ?
                """, (round_id,)).fetchall()
                
                for p in players:
                    send_message(
                        p["user_id"],
                        f"🎲 **دور جدید - سوال:**\n\n"
                        f"📝 {round_row['question']}\n\n"
                        f"⏳ لطفاً جواب خودتون رو به صورت متن بفرستید."
                    )
                
                # ارسال کنترل به مدیر
                send_message(
                    chat_id,
                    f"✅ **سوال به {len(players)} بازیکن ارسال شد!**\n\n"
                    f"📝 سوال: {round_row['question']}\n"
                    f"⏳ منتظر جواب بازیکنان...",
                    reply_markup=inline_keyboard([
                        [button("🛑 پایان زمان جواب‌دهی", f"end_answers:{round_id}")]
                    ])
                )
            conn.close()
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
        handle_callback(chat_id, user_id, "show_help")
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
    print(f"📥 Received update")
    
    try:
        # اطمینان از وجود دیتابیس
        ensure_db()
        
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
            
            handle_callback(chat_id, user_id, callback_data, callback_id)
        
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

# ==================== STARTUP ====================
def start_voting(round_id, chat_id, user_id):
    """شروع رأی‌گیری (برای callback)"""
    # این تابع قبلاً در بخش اول بود، اما اگر نیاز است اینجا هم باشد
    conn = db()
    round_row = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not round_row:
        conn.close()
        return
    game = conn.execute("SELECT * FROM games WHERE id = ?", (round_row["game_id"],)).fetchone()
    if game["owner_id"] != user_id:
        conn.close()
        return
    conn.execute("UPDATE rounds SET status = 'voting' WHERE id = ?", (round_id,))
    conn.execute("UPDATE round_players SET can_vote = 1 WHERE round_id = ?", (round_id,))
    conn.commit()
    
    # ارسال گزینه‌ها به همه
    options = conn.execute("SELECT * FROM options WHERE round_id = ? ORDER BY option_number", (round_id,)).fetchall()
    option_text = "\n".join([f"{opt['option_number']}) {opt['option_text']}" for opt in options])
    
    players = conn.execute("""
        SELECT p.chat_id FROM round_players rp
        JOIN players p ON p.id = rp.player_id
        WHERE rp.round_id = ?
    """, (round_id,)).fetchall()
    
    for p in players:
        send_message(p["chat_id"], f"🗳 **رأی‌گیری شروع شد!**\n\nگزینه‌ها:\n{option_text}\n\nفقط شماره گزینه رو بفرستید.")
    
    send_message(chat_id, "✅ رأی‌گیری شروع شد.")
    conn.close()

# ==================== MAIN ====================
if __name__ == "__main__":
    # Initialize database
    ensure_db()
    
    # Set webhook on startup
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        print(f"🔗 Setting webhook to: {webhook_url}")
        result = tg_request("setWebhook", {"url": webhook_url})
        print(f"📡 Webhook result: {result}")
    else:
        print("⚠️ WEBHOOK_URL not set — webhook won't be configured automatically")
    
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port)
