from flask import Flask, request, jsonify
import sqlite3
import random
import string
import os
import json
import requests
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "").replace("@", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "questions.json")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# -----------------------------
# Telegram Helpers
# -----------------------------

def tg_request(method, payload=None):
    url = f"{TELEGRAM_API}/{method}"
    try:
        r = requests.post(url, json=payload or {}, timeout=10)
        if not r.ok:
            print("Telegram API Error:", method, r.status_code, r.text)
        return r.json()
    except requests.RequestException as e:
        print("Telegram request failed:", method, e)
        return None


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("sendMessage", payload)


def answer_callback(callback_query_id, text=None, show_alert=False):
    payload = {
        "callback_query_id": callback_query_id,
        "show_alert": show_alert
    }
    if text:
        payload["text"] = text
    return tg_request("answerCallbackQuery", payload)


def inline_keyboard(buttons):
    return {"inline_keyboard": buttons}


# -----------------------------
# DB Helpers
# -----------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                owner_chat_id INTEGER NOT NULL,
                game_code TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                UNIQUE(game_id, user_id),
                FOREIGN KEY(game_id) REFERENCES games(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                data TEXT,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY(game_id) REFERENCES games(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS round_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                eligible INTEGER NOT NULL DEFAULT 0,
                UNIQUE(round_id, player_id),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(round_id, player_id),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                option_no INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                is_correct INTEGER NOT NULL DEFAULT 0,
                owner_player_id INTEGER,
                UNIQUE(round_id, option_no),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(owner_player_id) REFERENCES players(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                voter_player_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(round_id, voter_player_id),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(voter_player_id) REFERENCES players(id),
                FOREIGN KEY(option_id) REFERENCES options(id)
            )
        """)

        conn.commit()


# -----------------------------
# Utility
# -----------------------------

def now():
    return datetime.utcnow().isoformat()


def generate_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def normalize_text(text):
    if not text:
        return ""
    text = text.strip().lower()
    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "أ": "ا",
        "إ": "ا",
        "آ": "ا"
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    for ch in ["‌", "\u200c", ".", "،", ",", "!", "؟", "?", ":", ";", "؛", "-", "_", "\"", "'", "«", "»", "(", ")"]:
        text = text.replace(ch, " ")

    return " ".join(text.split())


def load_questions():
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        valid = []
        for item in data:
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if q and a:
                valid.append({"question": q, "answer": a})
        return valid
    except Exception as e:
        print("Questions load error:", e)
        return []


def get_join_link(game_code):
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start=join_{game_code}"
    return f"لطفاً BOT_USERNAME را در Render تنظیم کن. کد بازی: {game_code}"


def set_user_state(user_id, state, data=None):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_states(user_id, state, data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                state = excluded.state,
                data = excluded.data,
                updated_at = excluded.updated_at
        """, (user_id, state, json.dumps(data or {}, ensure_ascii=False), now()))
        conn.commit()


def get_user_state(user_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM user_states WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {
            "state": row["state"],
            "data": json.loads(row["data"] or "{}")
        }


def clear_user_state(user_id):
    with db() as conn:
        conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
        conn.commit()


def get_game_by_code(game_code):
    with db() as conn:
        return conn.execute("SELECT * FROM games WHERE game_code = ?", (game_code,)).fetchone()


def get_game(game_id):
    with db() as conn:
        return conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def is_game_owner(game_id, user_id):
    game = get_game(game_id)
    return game and int(game["owner_user_id"]) == int(user_id)


def get_player(game_id, user_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM players WHERE game_id = ? AND user_id = ?
        """, (game_id, user_id)).fetchone()


def get_player_by_id(player_id):
    with db() as conn:
        return conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()


def get_game_players(game_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM players WHERE game_id = ? ORDER BY joined_at ASC
        """, (game_id,)).fetchall()


def get_active_round_for_game(game_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM rounds
            WHERE game_id = ? AND status IN ('collecting', 'reviewing', 'voting')
            ORDER BY id DESC
            LIMIT 1
        """, (game_id,)).fetchone()


def get_latest_user_active_round(user_id):
    with db() as conn:
        return conn.execute("""
            SELECT 
                r.*,
                p.id AS player_id,
                p.display_name AS display_name,
                p.game_id AS player_game_id
            FROM rounds r
            JOIN round_players rp ON rp.round_id = r.id
            JOIN players p ON p.id = rp.player_id
            WHERE p.user_id = ?
              AND r.status IN ('collecting', 'voting')
            ORDER BY r.id DESC
            LIMIT 1
        """, (user_id,)).fetchone()


# -----------------------------
# Menus
# -----------------------------

def main_menu_for_user(user_id):
    buttons = [
        [{"text": "🎮 ساخت بازی جدید", "callback_data": "create_game"}]
    ]

    with db() as conn:
        games = conn.execute("""
            SELECT * FROM games WHERE owner_user_id = ? ORDER BY id DESC LIMIT 10
        """, (user_id,)).fetchall()

    for g in games:
        buttons.append([
            {"text": f"شروع دور جدید | {g['game_code']}", "callback_data": f"start_round:{g['id']}"}
        ])

    return inline_keyboard(buttons)


def admin_round_keyboard(round_id, stage):
    if stage == "collecting":
        return inline_keyboard([
            [{"text": "✅ پایان ارسال جواب‌ها", "callback_data": f"end_answers:{round_id}"}]
        ])

    if stage == "confirm_end_answers":
        return inline_keyboard([
            [{"text": "✅ آره، تموم کن", "callback_data": f"confirm_end_answers:{round_id}"}],
            [{"text": "❌ نه، صبر می‌کنم", "callback_data": f"cancel_end_answers:{round_id}"}]
        ])

    if stage == "reviewing":
        return inline_keyboard([
            [{"text": "🗳 شروع رای‌گیری", "callback_data": f"start_voting:{round_id}"}]
        ])

    if stage == "voting":
        return inline_keyboard([
            [{"text": "🏁 پایان رای‌گیری", "callback_data": f"end_voting:{round_id}"}]
        ])

    if stage == "confirm_end_voting":
        return inline_keyboard([
            [{"text": "✅ آره، رای‌گیری رو تموم کن", "callback_data": f"confirm_end_voting:{round_id}"}],
            [{"text": "❌ نه، ادامه بدیم", "callback_data": f"cancel_end_voting:{round_id}"}]
        ])

    return None


# -----------------------------
# Game Actions
# -----------------------------

def create_game(owner_user_id, owner_chat_id):
    game_code = generate_code()
    with db() as conn:
        while conn.execute("SELECT id FROM games WHERE game_code = ?", (game_code,)).fetchone():
            game_code = generate_code()

        conn.execute("""
            INSERT INTO games(owner_user_id, owner_chat_id, game_code, created_at)
            VALUES (?, ?, ?, ?)
        """, (owner_user_id, owner_chat_id, game_code, now()))
        conn.commit()

    link = get_join_link(game_code)

    text = (
        "🎮 بازی جدید ساخته شد!\n\n"
        f"کد بازی: <code>{game_code}</code>\n\n"
        "لینک عضویت بازیکن‌ها:\n"
        f"{link}\n\n"
        "این لینک رو برای بقیه بفرست تا بتونند عضو بازی بشند.\n\n"
        "وقتی حداقل ۳ نفر عضو شدن، می‌تونی دور جدید رو شروع کنی."
    )
    send_message(owner_chat_id, text, main_menu_for_user(owner_user_id))


def join_game_start(user_id, chat_id, game_code):
    game = get_game_by_code(game_code)
    if not game:
        send_message(chat_id, "این لینک بازی معتبر نیست یا بازی پیدا نشد.")
        return

    existing = get_player(game["id"], user_id)
    if existing:
        send_message(
            chat_id,
            f"تو قبلاً عضو این بازی شدی با اسم: <b>{existing['display_name']}</b>\n"
            "از دور بعدی که مدیر شروع کنه می‌تونی بازی کنی."
        )
        return

    set_user_state(user_id, "waiting_display_name", {"game_code": game_code})
    send_message(
        chat_id,
        "خوش اومدی 😄\n\n"
        "اسمت رو همینجا بفرست.\n"
        "امتیازهات و نتایج با همین اسم نمایش داده می‌شن."
    )


def save_player_name(user_id, chat_id, display_name, game_code):
    display_name = display_name.strip()
    if len(display_name) < 2:
        send_message(chat_id, "اسمت خیلی کوتاهه. یه اسم حداقل ۲ حرفی بفرست.")
        return

    if len(display_name) > 40:
        send_message(chat_id, "اسمت خیلی طولانیه. لطفاً کوتاه‌ترش کن.")
        return

    game = get_game_by_code(game_code)
    if not game:
        clear_user_state(user_id)
        send_message(chat_id, "بازی پیدا نشد. دوباره با لینک عضویت وارد شو.")
        return

    with db() as conn:
        duplicate_name = conn.execute("""
            SELECT id FROM players
            WHERE game_id = ? AND normalized_name = ?
        """, (game["id"], normalize_text(display_name))).fetchone()

    # اگر ستون normalized_name وجود نداشت، با try پایین هندل می‌کنیم.
    try:
        with db() as conn:
            conn.execute("""
                ALTER TABLE players ADD COLUMN normalized_name TEXT
            """)
            conn.commit()
    except sqlite3.OperationalError:
        pass

    with db() as conn:
        duplicate_name = conn.execute("""
            SELECT id FROM players
            WHERE game_id = ? AND normalized_name = ?
        """, (game["id"], normalize_text(display_name))).fetchone()

        if duplicate_name:
            send_message(chat_id, "این اسم توی این بازی قبلاً انتخاب شده. یه اسم دیگه بفرست.")
            return

        conn.execute("""
            INSERT OR IGNORE INTO players(
                game_id, user_id, chat_id, display_name, normalized_name, score, joined_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (game["id"], user_id, chat_id, display_name, normalize_text(display_name), now()))
        conn.commit()

    clear_user_state(user_id)

    send_message(
        chat_id,
        f"ثبت شد! از این به بعد توی این بازی با اسم <b>{display_name}</b> هستی.\n"
        "وقتی مدیر دور جدید رو شروع کنه، سوال برات ارسال می‌شه."
    )

    send_message(
        game["owner_chat_id"],
        f"👤 بازیکن جدید عضو شد:\n<b>{display_name}</b>"
    )


def start_new_round(game_id, admin_user_id, admin_chat_id):
    if not is_game_owner(game_id, admin_user_id):
        send_message(admin_chat_id, "این دکمه مخصوص مدیر همین بازیه.")
        return

    active = get_active_round_for_game(game_id)
    if active:
        send_message(admin_chat_id, "یه دور هنوز تموم نشده. اول همون رو کامل کن.")
        return

    players = get_game_players(game_id)
    if len(players) < 3:
        send_message(
            admin_chat_id,
            f"تعداد بازیکن‌ها کمه. حداقل ۳ نفر لازمه.\n"
            f"الان تعداد اعضا: {len(players)}"
        )
        return

    questions = load_questions()
    if not questions:
        send_message(admin_chat_id, "فایل سوال‌ها خالیه یا درست خونده نشد. questions.json رو چک کن.")
        return

    q = random.choice(questions)

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO rounds(game_id, question, correct_answer, status, created_at)
            VALUES (?, ?, ?, 'collecting', ?)
        """, (game_id, q["question"], q["answer"], now()))
        round_id = cur.lastrowid

        for p in players:
            conn.execute("""
                INSERT INTO round_players(round_id, player_id, eligible)
                VALUES (?, ?, 0)
            """, (round_id, p["id"]))

        conn.commit()

    question_text = (
        "🎲 دور جدید شروع شد!\n\n"
        f"سوال:\n<b>{q['question']}</b>\n\n"
        "حالا یه جواب بامزه، عجیب و گمراه‌کننده بفرست؛ جوری که بقیه فکر کنن جواب درست همونه 😈\n\n"
        "تا وقتی مدیر مرحله جواب‌دهی رو نبسته، می‌تونی جوابت رو عوض کنی."
    )

    for p in players:
        send_message(p["chat_id"], question_text)

    send_message(
        admin_chat_id,
        "دور جدید شروع شد و سوال برای همه اعضای فعلی ارسال شد.\n\n"
        "هر وقت خواستی مرحله ارسال جواب‌ها رو ببندی، دکمه زیر رو بزن.",
        admin_round_keyboard(round_id, "collecting")
    )


def submit_fake_answer(user_id, chat_id, text):
    active = get_latest_user_active_round(user_id)

    if not active:
        send_message(chat_id, "الان دور فعالی برای جواب دادن یا رای دادن نداری.")
        return

    if active["status"] == "collecting":
        submit_answer_collecting(active, user_id, chat_id, text)
        return

    if active["status"] == "voting":
        submit_vote(active, user_id, chat_id, text)
        return

    send_message(chat_id, "الان نمی‌تونی چیزی ثبت کنی. صبر کن مدیر مرحله بعدی رو شروع کنه.")


def submit_answer_collecting(active_round, user_id, chat_id, text):
    answer_text = text.strip()
    if len(answer_text) < 1:
        send_message(chat_id, "یه جواب متنی بفرست.")
        return

    if len(answer_text) > 500:
        send_message(chat_id, "جوابت خیلی طولانیه. لطفاً کوتاه‌ترش کن.")
        return

    round_id = active_round["id"]
    player_id = active_round["player_id"]
    normalized = normalize_text(answer_text)
    correct_normalized = normalize_text(active_round["correct_answer"])

    if normalized == correct_normalized:
        send_message(chat_id, "این جواب تکراریه. یه جواب دیگه بفرست.")
        return

    with db() as conn:
        duplicate = conn.execute("""
            SELECT a.*
            FROM answers a
            WHERE a.round_id = ?
              AND a.normalized_text = ?
              AND a.player_id != ?
        """, (round_id, normalized, player_id)).fetchone()

        if duplicate:
            send_message(chat_id, "این جواب تکراریه. یه جواب دیگه بفرست.")
            return

        existing = conn.execute("""
            SELECT id FROM answers WHERE round_id = ? AND player_id = ?
        """, (round_id, player_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE answers
                SET answer_text = ?, normalized_text = ?, updated_at = ?
                WHERE round_id = ? AND player_id = ?
            """, (answer_text, normalized, now(), round_id, player_id))
            msg = "جوابت آپدیت شد ✅"
        else:
            conn.execute("""
                INSERT INTO answers(round_id, player_id, answer_text, normalized_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (round_id, player_id, answer_text, normalized, now(), now()))
            msg = "جوابت ثبت شد ✅"

        conn.execute("""
            UPDATE round_players SET eligible = 1
            WHERE round_id = ? AND player_id = ?
        """, (round_id, player_id))

        conn.commit()

    send_message(chat_id, msg + "\nحالا صبر کن تا مدیر مرحله جواب‌دهی رو ببنده.")


def end_answers(round_id, admin_user_id, admin_chat_id, force=False):
    with db() as conn:
        r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()

    if not r:
        send_message(admin_chat_id, "این دور پیدا نشد.")
        return

    if not is_game_owner(r["game_id"], admin_user_id):
        send_message(admin_chat_id, "این دکمه مخصوص مدیر بازیه.")
        return

    if r["status"] != "collecting":
        send_message(admin_chat_id, "مرحله جواب‌دهی الان فعال نیست.")
        return

    with db() as conn:
        missing = conn.execute("""
            SELECT p.display_name
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            LEFT JOIN answers a ON a.round_id = rp.round_id AND a.player_id = rp.player_id
            WHERE rp.round_id = ? AND a.id IS NULL
            ORDER BY p.display_name ASC
        """, (round_id,)).fetchall()

        answer_count = conn.execute("""
            SELECT COUNT(*) AS c FROM answers WHERE round_id = ?
        """, (round_id,)).fetchone()["c"]

    if answer_count < 2:
        send_message(admin_chat_id, "حداقل باید ۲ نفر جواب جعلی فرستاده باشن تا بتونی بری مرحله بعد.")
        return

    if missing and not force:
        names = "\n".join([f"- {m['display_name']}" for m in missing])
        send_message(
            admin_chat_id,
            "هنوز اینا جواب ندادن:\n"
            f"{names}\n\n"
            "می‌خوای با همین وضعیت ارسال جواب‌ها رو تموم کنی؟",
            admin_round_keyboard(round_id, "confirm_end_answers")
        )
        return

    prepare_options_and_show_to_admin(round_id, admin_chat_id)


def prepare_options_and_show_to_admin(round_id, admin_chat_id):
    with db() as conn:
        r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()

        existing_options = conn.execute("""
            SELECT COUNT(*) AS c FROM options WHERE round_id = ?
        """, (round_id,)).fetchone()["c"]

        if existing_options == 0:
            answers = conn.execute("""
                SELECT * FROM answers WHERE round_id = ?
            """, (round_id,)).fetchall()

            option_items = []

            option_items.append({
                "answer_text": r["correct_answer"],
                "is_correct": 1,
                "owner_player_id": None
            })

            for a in answers:
                option_items.append({
                    "answer_text": a["answer_text"],
                    "is_correct": 0,
                    "owner_player_id": a["player_id"]
                })

            random.shuffle(option_items)

            for idx, item in enumerate(option_items, start=1):
                conn.execute("""
                    INSERT INTO options(round_id, option_no, answer_text, is_correct, owner_player_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    round_id,
                    idx,
                    item["answer_text"],
                    item["is_correct"],
                    item["owner_player_id"]
                ))

        conn.execute("""
            UPDATE rounds SET status = 'reviewing'
            WHERE id = ?
        """, (round_id,))

        options = conn.execute("""
            SELECT * FROM options WHERE round_id = ? ORDER BY option_no ASC
        """, (round_id,)).fetchall()

        conn.commit()

    lines = [
        "📋 لیست جواب‌ها برای خوندن بلند:",
        "",
        "اسم‌ها عمداً نمایش داده نمی‌شن."
    ]

    for o in options:
        lines.append("")
        lines.append(f"{o['option_no']}) {o['answer_text']}")

    lines.append("")
    lines.append("بعد از اینکه جواب‌ها رو برای همه خوندی، دکمه شروع رای‌گیری رو بزن.")

    send_message(
        admin_chat_id,
        "\n".join(lines),
        admin_round_keyboard(round_id, "reviewing")
    )


def start_voting(round_id, admin_user_id, admin_chat_id):
    with db() as conn:
        r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()

    if not r:
        send_message(admin_chat_id, "این دور پیدا نشد.")
        return

    if not is_game_owner(r["game_id"], admin_user_id):
        send_message(admin_chat_id, "این دکمه مخصوص مدیر بازیه.")
        return

    if r["status"] != "reviewing":
        send_message(admin_chat_id, "الان وقت شروع رای‌گیری نیست.")
        return

    with db() as conn:
        conn.execute("UPDATE rounds SET status = 'voting' WHERE id = ?", (round_id,))
        options = conn.execute("""
            SELECT * FROM options WHERE round_id = ? ORDER BY option_no ASC
        """, (round_id,)).fetchall()

        eligible_players = conn.execute("""
            SELECT p.*
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ? AND rp.eligible = 1
            ORDER BY p.display_name ASC
        """, (round_id,)).fetchall()

        conn.commit()

    lines = [
        "🗳 رای‌گیری شروع شد!",
        "",
        "به نظرت جواب درست کدومه؟ فقط عدد گزینه رو بفرست.",
        "",
        "نکته: نمی‌تونی به جواب خودت رای بدی.",
        ""
    ]

    for o in options:
        lines.append(f"{o['option_no']}) {o['answer_text']}")

    vote_text = "\n".join(lines)

    for p in eligible_players:
        send_message(p["chat_id"], vote_text)

    send_message(
        admin_chat_id,
        "رای‌گیری برای کسانی که جواب جعلی فرستاده بودن شروع شد.\n\n"
        "هر وقت خواستی پایان رای‌گیری رو بزنی، از دکمه زیر استفاده کن.",
        admin_round_keyboard(round_id, "voting")
    )


def submit_vote(active_round, user_id, chat_id, text):
    vote_text = text.strip()

    if not vote_text.isdigit():
        send_message(chat_id, "برای رای دادن فقط عدد گزینه رو بفرست.")
        return

    option_no = int(vote_text)
    round_id = active_round["id"]
    voter_player_id = active_round["player_id"]

    with db() as conn:
        eligible = conn.execute("""
            SELECT eligible FROM round_players
            WHERE round_id = ? AND player_id = ?
        """, (round_id, voter_player_id)).fetchone()

        if not eligible or eligible["eligible"] != 1:
            send_message(chat_id, "تو این دور جواب نفرستادی، پس نمی‌تونی تو رای‌گیری شرکت کنی.")
            return

        option = conn.execute("""
            SELECT * FROM options
            WHERE round_id = ? AND option_no = ?
        """, (round_id, option_no)).fetchone()

        if not option:
            send_message(chat_id, "این شماره گزینه وجود نداره. یه عدد درست بفرست.")
            return

        if option["owner_player_id"] and int(option["owner_player_id"]) == int(voter_player_id):
            send_message(chat_id, "نمی‌تونی به جواب خودت رای بدی 😄 یه گزینه دیگه انتخاب کن.")
            return

        existing = conn.execute("""
            SELECT id FROM votes WHERE round_id = ? AND voter_player_id = ?
        """, (round_id, voter_player_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE votes
                SET option_id = ?, updated_at = ?
                WHERE round_id = ? AND voter_player_id = ?
            """, (option["id"], now(), round_id, voter_player_id))
            msg = "رایت آپدیت شد ✅"
        else:
            conn.execute("""
                INSERT INTO votes(round_id, voter_player_id, option_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (round_id, voter_player_id, option["id"], now(), now()))
            msg = "رایت ثبت شد ✅"

        conn.commit()

    send_message(chat_id, msg + "\nصبر کن تا مدیر رای‌گیری رو تموم کنه.")


def end_voting(round_id, admin_user_id, admin_chat_id, force=False):
    with db() as conn:
        r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()

    if not r:
        send_message(admin_chat_id, "این دور پیدا نشد.")
        return

    if not is_game_owner(r["game_id"], admin_user_id):
        send_message(admin_chat_id, "این دکمه مخصوص مدیر بازیه.")
        return

    if r["status"] != "voting":
        send_message(admin_chat_id, "رای‌گیری الان فعال نیست.")
        return

    with db() as conn:
        missing = conn.execute("""
            SELECT p.display_name
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            LEFT JOIN votes v ON v.round_id = rp.round_id AND v.voter_player_id = rp.player_id
            WHERE rp.round_id = ?
              AND rp.eligible = 1
              AND v.id IS NULL
            ORDER BY p.display_name ASC
        """, (round_id,)).fetchall()

    if missing and not force:
        names = "\n".join([f"- {m['display_name']}" for m in missing])
        send_message(
            admin_chat_id,
            "هنوز اینا رای ندادن:\n"
            f"{names}\n\n"
            "می‌خوای بدون رای اینا رای‌گیری رو تموم کنی؟",
            admin_round_keyboard(round_id, "confirm_end_voting")
        )
        return

    finalize_round(round_id, admin_chat_id)


def finalize_round(round_id, admin_chat_id):
    with db() as conn:
        r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()

        correct_option = conn.execute("""
            SELECT * FROM options WHERE round_id = ? AND is_correct = 1
        """, (round_id,)).fetchone()

        votes = conn.execute("""
            SELECT 
                v.*,
                o.is_correct,
                o.owner_player_id,
                o.answer_text,
                o.option_no,
                p.display_name AS voter_name
            FROM votes v
            JOIN options o ON o.id = v.option_id
            JOIN players p ON p.id = v.voter_player_id
            WHERE v.round_id = ?
        """, (round_id,)).fetchall()

        round_points = {}

        correct_voters = []
        for v in votes:
            if v["is_correct"] == 1:
                round_points[v["voter_player_id"]] = round_points.get(v["voter_player_id"], 0) + 1
                correct_voters.append(v["voter_name"])
            else:
                if v["owner_player_id"]:
                    round_points[v["owner_player_id"]] = round_points.get(v["owner_player_id"], 0) + 1

        for player_id, pts in round_points.items():
            conn.execute("""
                UPDATE players SET score = score + ?
                WHERE id = ?
            """, (pts, player_id))

        wrong_options = conn.execute("""
            SELECT 
                o.*,
                p.display_name AS owner_name,
                COUNT(v.id) AS vote_count
            FROM options o
            LEFT JOIN votes v ON v.option_id = o.id
            LEFT JOIN players p ON p.id = o.owner_player_id
            WHERE o.round_id = ? AND o.is_correct = 0
            GROUP BY o.id
            HAVING vote_count > 0
            ORDER BY vote_count DESC, o.option_no ASC
        """, (round_id,)).fetchall()

        scoreboard = conn.execute("""
            SELECT * FROM players
            WHERE game_id = ?
            ORDER BY score DESC, display_name ASC
        """, (r["game_id"],)).fetchall()

        all_players = conn.execute("""
            SELECT * FROM players
            WHERE game_id = ?
        """, (r["game_id"],)).fetchall()

        conn.execute("""
            UPDATE rounds
            SET status = 'finished', finished_at = ?
            WHERE id = ?
        """, (now(), round_id))

        conn.commit()

    result_lines = [
        "🏁 نتیجه این دور",
        "",
        f"✅ جواب درست:\n<b>{correct_option['answer_text']}</b>",
        "",
        "کسایی که جواب درست رو انتخاب کردن:"
    ]

    if correct_voters:
        for name in correct_voters:
            result_lines.append(f"- {name}")
    else:
        result_lines.append("- هیچ‌کس 😄")

    result_lines.append("")
    result_lines.append("────────────")
    result_lines.append("")
    result_lines.append("جواب‌های اشتباهی که بقیه رو گول زدن:")

    if wrong_options:
        for wo in wrong_options:
            result_lines.append("")
            result_lines.append(
                f"گزینه {wo['option_no']} | {wo['vote_count']} رای\n"
                f"متن: {wo['answer_text']}\n"
                f"فرستنده: {wo['owner_name']}"
            )
    else:
        result_lines.append("- هیچ جواب اشتباهی رای نگرفت.")

    scores_lines = [
        "🏆 جدول امتیازها",
        ""
    ]

    for idx, p in enumerate(scoreboard, start=1):
        scores_lines.append(f"{idx}. {p['display_name']} — {p['score']} امتیاز")

    result_text = "\n".join(result_lines)
    scores_text = "\n".join(scores_lines)

    for p in all_players:
        send_message(p["chat_id"], result_text)
        send_message(p["chat_id"], scores_text)

    game = get_game(r["game_id"])
    send_message(
        game["owner_chat_id"],
        "اگه خواستی دور بعدی رو شروع کنی، از دکمه زیر استفاده کن.",
        inline_keyboard([
            [{"text": "🎲 شروع دور جدید", "callback_data": f"start_round:{r['game_id']}"}]
        ])
    )


# -----------------------------
# Update Handlers
# -----------------------------

def handle_start(message, args):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    if args and args.startswith("join_"):
        game_code = args.replace("join_", "", 1).strip().upper()
        join_game_start(user_id, chat_id, game_code)
        return

    text = (
        "سلام 😄\n"
        "به بازی شیاد خوش اومدی.\n\n"
        "یه بازی هیجان انگیز و شاد برای دورهمی ها.\n"
        "برای ساخت بازی به عنوان مدیر روی دکمه زیر بزن."
    )
    send_message(chat_id, text, main_menu_for_user(user_id))


def handle_message(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if not text:
        return

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        handle_start(message, args)
        return

    state = get_user_state(user_id)
    if state and state["state"] == "waiting_display_name":
        game_code = state["data"].get("game_code")
        save_player_name(user_id, chat_id, text, game_code)
        return

    submit_fake_answer(user_id, chat_id, text)


def handle_callback(callback):
    callback_id = callback["id"]
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_id = callback["from"]["id"]

    answer_callback(callback_id)

    if data == "create_game":
        create_game(user_id, chat_id)
        return

    if data.startswith("start_round:"):
        game_id = int(data.split(":")[1])
        start_new_round(game_id, user_id, chat_id)
        return

    if data.startswith("end_answers:"):
        round_id = int(data.split(":")[1])
        end_answers(round_id, user_id, chat_id, force=False)
        return

    if data.startswith("confirm_end_answers:"):
        round_id = int(data.split(":")[1])
        end_answers(round_id, user_id, chat_id, force=True)
        return

    if data.startswith("cancel_end_answers:"):
        round_id = int(data.split(":")[1])
        send_message(
            chat_id,
            "باشه، هنوز فرصت جواب دادن هست.\nهر وقت خواستی دوباره پایان ارسال جواب‌ها رو بزن.",
            admin_round_keyboard(round_id, "collecting")
        )
        return

    if data.startswith("start_voting:"):
        round_id = int(data.split(":")[1])
        start_voting(round_id, user_id, chat_id)
        return

    if data.startswith("end_voting:"):
        round_id = int(data.split(":")[1])
        end_voting(round_id, user_id, chat_id, force=False)
        return

    if data.startswith("confirm_end_voting:"):
        round_id = int(data.split(":")[1])
        end_voting(round_id, user_id, chat_id, force=True)
        return

    if data.startswith("cancel_end_voting:"):
        round_id = int(data.split(":")[1])
        send_message(
            chat_id,
            "باشه، رای‌گیری هنوز ادامه داره.\nهر وقت خواستی دوباره پایان رای‌گیری رو بزن.",
            admin_round_keyboard(round_id, "voting")
        )
        return

    send_message(chat_id, "دستور ناشناخته بود.")


# -----------------------------
# Flask Routes
# -----------------------------

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "ok": True,
        "service": "creative-game-telegram-bot",
        "webhook": "/telegram/webhook"
    })


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        print("Webhook handling error:", e)

    return jsonify({"ok": True})


@app.route("/set-webhook", methods=["GET"])
def set_webhook():
    if not BASE_URL:
        return jsonify({
            "ok": False,
            "error": "BASE_URL is not set"
        }), 400

    webhook_url = f"{BASE_URL}/telegram/webhook"
    result = tg_request("setWebhook", {"url": webhook_url})

    return jsonify({
        "ok": True,
        "webhook_url": webhook_url,
        "telegram_result": result
    })


@app.route("/delete-webhook", methods=["GET"])
def delete_webhook():
    result = tg_request("deleteWebhook", {})
    return jsonify({
        "ok": True,
        "telegram_result": result
    })


init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
