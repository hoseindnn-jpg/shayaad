from flask import Flask, request, jsonify
import sqlite3
import random
import string
import os
import requests
import json
from datetime import datetime

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL")
BOT_USERNAME = os.environ.get("BOT_USERNAME")
DB_PATH = os.environ.get("DB_PATH", "bot.db")
QUESTIONS_FILE = os.environ.get("QUESTIONS_FILE", "questions.json")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# =========================
# Helpers
# =========================

def now():
    return datetime.utcnow().isoformat()


def normalize_text(text):
    if not text:
        return ""

    text = text.strip().lower()

    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "آ": "ا",
        "‌": " ",
        "\u200c": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = " ".join(text.split())
    return text


def generate_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def load_questions():
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            questions = json.load(f)

        valid_questions = []
        for q in questions:
            if q.get("question") and q.get("answer"):
                valid_questions.append(q)

        return valid_questions

    except Exception as e:
        print("Load questions error:", e)
        return []


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# Telegram API
# =========================

def tg_request(method, data=None):
    url = f"{TELEGRAM_API}/{method}"

    try:
        response = requests.post(url, json=data or {}, timeout=10)
        if not response.ok:
            print("Telegram API error:", response.status_code, response.text)
            return None
        return response.json()

    except requests.RequestException as e:
        print("Telegram request exception:", e)
        return None


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        data["reply_markup"] = reply_markup

    return tg_request("sendMessage", data)


def answer_callback(callback_query_id, text=None, show_alert=False):
    data = {
        "callback_query_id": callback_query_id,
        "show_alert": show_alert,
    }

    if text:
        data["text"] = text

    return tg_request("answerCallbackQuery", data)


def inline_keyboard(rows):
    return {
        "inline_keyboard": rows
    }


def button(text, callback_data):
    return {
        "text": text,
        "callback_data": callback_data
    }


# =========================
# Database Init
# =========================

def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                owner_user_id INTEGER NOT NULL,
                owner_chat_id INTEGER NOT NULL,
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
                normalized_name TEXT,
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
                round_no INTEGER NOT NULL,
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
                has_answered INTEGER NOT NULL DEFAULT 0,
                can_vote INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                penalty INTEGER NOT NULL DEFAULT 0,
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
                normalized_answer TEXT NOT NULL,
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
                normalized_answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL DEFAULT 0,
                player_id INTEGER,
                UNIQUE(round_id, option_no),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(player_id) REFERENCES players(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                voter_player_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(round_id, voter_player_id),
                FOREIGN KEY(round_id) REFERENCES rounds(id),
                FOREIGN KEY(voter_player_id) REFERENCES players(id),
                FOREIGN KEY(option_id) REFERENCES options(id)
            )
        """)

        # Migration برای دیتابیس‌های قدیمی که ستون normalized_name ندارند
        try:
            conn.execute("ALTER TABLE players ADD COLUMN normalized_name TEXT")
        except sqlite3.OperationalError:
            pass

        # Migration: اضافه کردن ستون score و penalty به round_players
        try:
            conn.execute("ALTER TABLE round_players ADD COLUMN score INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE round_players ADD COLUMN penalty INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        conn.commit()


init_db()

# ═══════════════════════════════════════════
# Penalty System — State
# ═══════════════════════════════════════════

# دیکشنری موقت: mapping عدد → player_id برای انتخاب بازیکن جهت جریمه
# ساختار: {round_id: {1: player_id, 2: player_id, ...}}
_penalty_mappings = {}


# =========================
# State Management
# =========================

def set_user_state(user_id, state, data=None):
    with db() as conn:
        conn.execute("""
            INSERT INTO user_states(user_id, state, data, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET state = excluded.state, data = excluded.data, updated_at = excluded.updated_at
        """, (
            user_id,
            state,
            json.dumps(data or {}, ensure_ascii=False),
            now()
        ))
        conn.commit()


def get_user_state(user_id):
    with db() as conn:
        row = conn.execute("""
            SELECT * FROM user_states
            WHERE user_id = ?
        """, (user_id,)).fetchone()

    if not row:
        return None

    try:
        data = json.loads(row["data"] or "{}")
    except Exception:
        data = {}

    return {
        "state": row["state"],
        "data": data
    }


def clear_user_state(user_id):
    with db() as conn:
        conn.execute("""
            DELETE FROM user_states
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()


# =========================
# Game Queries
# =========================

def get_game_by_code(code):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM games
            WHERE code = ?
        """, (code,)).fetchone()


def get_game_by_id(game_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM games
            WHERE id = ?
        """, (game_id,)).fetchone()


def get_player(game_id, user_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM players
            WHERE game_id = ? AND user_id = ?
        """, (game_id, user_id)).fetchone()


def get_player_by_id(player_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM players
            WHERE id = ?
        """, (player_id,)).fetchone()


def get_players(game_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM players
            WHERE game_id = ?
            ORDER BY id ASC
        """, (game_id,)).fetchall()


def get_active_round(game_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM rounds
            WHERE game_id = ? AND status != 'finished'
            ORDER BY id DESC
            LIMIT 1
        """, (game_id,)).fetchone()


def get_round(round_id):
    with db() as conn:
        return conn.execute("""
            SELECT * FROM rounds
            WHERE id = ?
        """, (round_id,)).fetchone()


def is_owner(game, user_id):
    return int(game["owner_user_id"]) == int(user_id)


def join_link(game_code):
    return f"https://t.me/{BOT_USERNAME}?start=join_{game_code}"


# =========================
# Game Creation / Join
# =========================

def create_game(user_id, chat_id):
    if not BOT_USERNAME:
        send_message(chat_id, "BOT_USERNAME داخل Environment Variables تنظیم نشده.")
        return

    code = generate_code()

    with db() as conn:
        while conn.execute("SELECT id FROM games WHERE code = ?", (code,)).fetchone():
            code = generate_code()

        conn.execute("""
            INSERT INTO games(code, owner_user_id, owner_chat_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (code, user_id, chat_id, now()))
        conn.commit()

    link = join_link(code)

    send_message(
        chat_id,
        "بازی جدید ساخته شد.\n\n"
        f"کد بازی:\n<code>{code}</code>\n\n"
        f"لینک عضویت:\n{link}\n\n"
        "این لینک رو برای بچه‌ها بفرست. هرکس وارد بشه، ربات ازش اسم بازی می‌پرسه.",
        reply_markup=inline_keyboard([
            [button("شروع دور جدید", f"start_round:{code}")]
        ])
    )


def join_game_start(user_id, chat_id, game_code):
    game = get_game_by_code(game_code)

    if not game:
        send_message(chat_id, "این لینک عضویت معتبر نیست یا بازی پیدا نشد.")
        return

    existing_player = get_player(game["id"], user_id)

    if existing_player:
        send_message(
            chat_id,
            f"تو قبلاً عضو این بازی شدی با اسم <b>{existing_player['display_name']}</b>."
        )
        return

    set_user_state(user_id, "awaiting_name", {
        "game_code": game_code
    })

    send_message(
        chat_id,
        "خوش اومدی!\n"
        "برای اینکه وارد بازی بشی، یه اسم برای خودت انتخاب کن و همینجا بفرست.\n\n"
        "این اسم توی امتیازها و نتایج نمایش داده می‌شه."
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

    normalized_name = normalize_text(display_name)

    try:
        with db() as conn:
            duplicate_name = conn.execute("""
                SELECT id FROM players
                WHERE game_id = ? AND normalized_name = ?
            """, (game["id"], normalized_name)).fetchone()

            if duplicate_name:
                send_message(chat_id, "این اسم توی این بازی قبلاً انتخاب شده. یه اسم دیگه بفرست.")
                return

            existing_player = conn.execute("""
                SELECT id FROM players
                WHERE game_id = ? AND user_id = ?
            """, (game["id"], user_id)).fetchone()

            if existing_player:
                clear_user_state(user_id)
                send_message(chat_id, "تو قبلاً عضو این بازی شدی.")
                return

            conn.execute("""
                INSERT INTO players(
                    game_id,
                    user_id,
                    chat_id,
                    display_name,
                    normalized_name,
                    score,
                    joined_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?)
            """, (
                game["id"],
                user_id,
                chat_id,
                display_name,
                normalized_name,
                now()
            ))

            conn.commit()

    except Exception as e:
        print("Save player name error:", e)
        send_message(chat_id, "یه خطا موقع ثبت اسمت پیش اومد. لطفاً دوباره امتحان کن.")
        return

    clear_user_state(user_id)

    send_message(
        chat_id,
        f"ثبت شد! از این به بعد توی این بازی با اسم <b>{display_name}</b> هستی.\n"
        "وقتی مدیر دور جدید رو شروع کنه، سوال برات ارسال می‌شه."
    )

    send_message(
        game["owner_chat_id"],
        f"بازیکن جدید عضو شد:\n<b>{display_name}</b>"
    )


# =========================
# Round Logic
# =========================

def start_new_round(user_id, chat_id, game_code):
    game = get_game_by_code(game_code)

    if not game:
        send_message(chat_id, "بازی پیدا نشد.")
        return

    if not is_owner(game, user_id):
        send_message(chat_id, "فقط مدیر بازی می‌تونه دور جدید رو شروع کنه.")
        return

    existing_round = get_active_round(game["id"])
    if existing_round:
        send_message(chat_id, "یه دور هنوز تموم نشده. اول همون رو کامل کن.")
        return

    players = get_players(game["id"])

    if len(players) < 3:
        send_message(chat_id, "برای شروع بازی حداقل ۳ بازیکن لازم داریم.")
        return

    questions = load_questions()

    if not questions:
        send_message(chat_id, "فایل سوال‌ها خالیه یا درست خونده نمی‌شه.")
        return

    selected = random.choice(questions)

    with db() as conn:
        last_round = conn.execute("""
            SELECT MAX(round_no) AS max_no
            FROM rounds
            WHERE game_id = ?
        """, (game["id"],)).fetchone()

        round_no = (last_round["max_no"] or 0) + 1

        cur = conn.execute("""
            INSERT INTO rounds(
                game_id,
                round_no,
                question,
                correct_answer,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 'collecting', ?)
        """, (
            game["id"],
            round_no,
            selected["question"],
            selected["answer"],
            now()
        ))

        round_id = cur.lastrowid

        for p in players:
            conn.execute("""
                INSERT INTO round_players(round_id, player_id, has_answered, can_vote, score, penalty)
                VALUES (?, ?, 0, 0, 0, 0)
            """, (round_id, p["id"]))

        conn.commit()

    for p in players:
        send_message(
            p["chat_id"],
            f"دور {round_no} شروع شد!\n\n"
            f"سوال:\n<b>{selected['question']}</b>\n\n"
            "یه جواب بفرست که هم به نظرت درست باشه، هم بقیه رو بندازه تو شک!\n"
            "حتی اگه جواب درست رو می‌دونی، یه جواب دیگه بگو — اگه جوابت شبیه جواب درست باشه، "
            "مدیر می‌تونه جریمه‌ات کنه و امتیاز این دورت صفر بشه.\n\n"
            "تا قبل از بسته‌شدن ارسال جواب‌ها، اگر دوباره جواب بفرستی، جواب قبلیت آپدیت می‌شه."
        )

    send_message(
        game["owner_chat_id"],
        f"دور {round_no} شروع شد.\n\n"
        f"سوال برای {len(players)} نفر ارسال شد.",
        reply_markup=inline_keyboard([
            [button("پایان ارسال جواب‌ها", f"end_answers:{round_id}")]
        ])
    )


def handle_answer_message(user_id, chat_id, text):
    with db() as conn:
        row = conn.execute("""
            SELECT
                r.*,
                p.id AS player_id,
                p.display_name AS display_name
            FROM rounds r
            JOIN round_players rp ON rp.round_id = r.id
            JOIN players p ON p.id = rp.player_id
            WHERE p.user_id = ?
              AND r.status = 'collecting'
            ORDER BY r.id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

        if not row:
            return False

        round_id = row["id"]
        player_id = row["player_id"]
        answer_text = text.strip()

        if len(answer_text) < 1:
            send_message(chat_id, "جوابت خالیه. یه چیزی بفرست.")
            return True

        if len(answer_text) > 300:
            send_message(chat_id, "جوابت خیلی طولانیه. کوتاه‌ترش کن.")
            return True

        normalized_answer = normalize_text(answer_text)
        normalized_correct = normalize_text(row["correct_answer"])

        if normalized_answer == normalized_correct:
            send_message(
                chat_id,
                "این جواب دقیقاً شبیه جواب درسته. یه جواب دیگه بفرست — "
                "اگه جواب درست رو می‌دونی، یه جواب گمراه‌کننده بده."
            )
            return True

        duplicate = conn.execute("""
            SELECT a.id
            FROM answers a
            WHERE a.round_id = ?
              AND a.normalized_answer = ?
              AND a.player_id != ?
        """, (round_id, normalized_answer, player_id)).fetchone()

        if duplicate:
            send_message(
                chat_id,
                "این جواب تکراری یا خیلی شبیه جواب یکی دیگه‌ست. یه جواب دیگه بفرست."
            )
            return True

        existing = conn.execute("""
            SELECT id
            FROM answers
            WHERE round_id = ? AND player_id = ?
        """, (round_id, player_id)).fetchone()

        if existing:
            conn.execute("""
                UPDATE answers
                SET answer_text = ?, normalized_answer = ?, updated_at = ?
                WHERE round_id = ? AND player_id = ?
            """, (
                answer_text,
                normalized_answer,
                now(),
                round_id,
                player_id
            ))

            message = "جوابت آپدیت شد."
        else:
            conn.execute("""
                INSERT INTO answers(
                    round_id,
                    player_id,
                    answer_text,
                    normalized_answer,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                round_id,
                player_id,
                answer_text,
                normalized_answer,
                now(),
                now()
            ))

            conn.execute("""
                UPDATE round_players
                SET has_answered = 1
                WHERE round_id = ? AND player_id = ?
            """, (round_id, player_id))

            message = "جوابت ثبت شد."

        conn.commit()

    send_message(chat_id, message)
    return True


def get_missing_answer_players(round_id):
    with db() as conn:
        return conn.execute("""
            SELECT p.*
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ?
              AND rp.has_answered = 0
            ORDER BY p.id ASC
        """, (round_id,)).fetchall()


def close_answers_and_prepare_options(round_id):
    round_row = get_round(round_id)

    if not round_row:
        return

    game = get_game_by_id(round_row["game_id"])

    with db() as conn:
        conn.execute("""
            UPDATE round_players
            SET can_vote = 1
            WHERE round_id = ?
              AND has_answered = 1
        """, (round_id,))

        existing_options = conn.execute("""
            SELECT id
            FROM options
            WHERE round_id = ?
            LIMIT 1
        """, (round_id,)).fetchone()

        if not existing_options:
            answers = conn.execute("""
                SELECT *
                FROM answers
                WHERE round_id = ?
            """, (round_id,)).fetchall()

            option_items = []

            for a in answers:
                option_items.append({
                    "answer_text": a["answer_text"],
                    "normalized_answer": a["normalized_answer"],
                    "is_correct": 0,
                    "player_id": a["player_id"]
                })

            option_items.append({
                "answer_text": round_row["correct_answer"],
                "normalized_answer": normalize_text(round_row["correct_answer"]),
                "is_correct": 1,
                "player_id": None
            })

            random.shuffle(option_items)

            for index, item in enumerate(option_items, start=1):
                conn.execute("""
                    INSERT INTO options(
                        round_id,
                        option_no,
                        answer_text,
                        normalized_answer,
                        is_correct,
                        player_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    round_id,
                    index,
                    item["answer_text"],
                    item["normalized_answer"],
                    item["is_correct"],
                    item["player_id"]
                ))

        conn.execute("""
            UPDATE rounds
            SET status = 'reviewing'
            WHERE id = ?
        """, (round_id,))

        conn.commit()

    options = get_options(round_id)
    options_text = format_options(options)

    send_message(
        game["owner_chat_id"],
        "ارسال جواب‌ها بسته شد.\n\n"
        "این لیست جواب‌هاست. اسم فرستنده‌ها رو نشون نمی‌دم.\n"
        "جواب‌ها رو برای بچه‌ها بلند بخون، بعد بزن روی شروع رای‌گیری:\n\n"
        f"{options_text}",
        reply_markup=inline_keyboard([
            [button("شروع رای‌گیری", f"start_voting:{round_id}")]
        ])
    )


def get_options(round_id):
    with db() as conn:
        return conn.execute("""
            SELECT *
            FROM options
            WHERE round_id = ?
            ORDER BY option_no ASC
        """, (round_id,)).fetchall()


def format_options(options):
    lines = []
    for o in options:
        lines.append(f"{o['option_no']}. {o['answer_text']}")
    return "\n".join(lines)


def request_end_answers(user_id, chat_id, round_id):
    round_row = get_round(round_id)

    if not round_row:
        send_message(chat_id, "این دور پیدا نشد.")
        return

    game = get_game_by_id(round_row["game_id"])

    if not is_owner(game, user_id):
        send_message(chat_id, "فقط مدیر بازی می‌تونه ارسال جواب‌ها رو ببنده.")
        return

    if round_row["status"] != "collecting":
        send_message(chat_id, "الان مرحله ارسال جواب‌ها نیست.")
        return

    missing = get_missing_answer_players(round_id)

    if missing:
        names = "\n".join([f"- {p['display_name']}" for p in missing])

        send_message(
            chat_id,
            "هنوز اینا جواب ندادن:\n\n"
            f"{names}\n\n"
            "می‌خوای با همین وضعیت ارسال جواب‌ها رو ببندم؟\n"
            "کسایی که جواب ندادن، این دور حق رای ندارن و امتیاز هم نمی‌گیرن.",
            reply_markup=inline_keyboard([
                [
                    button("آره، تموم کن", f"force_end_answers:{round_id}"),
                    button("نه، صبر می‌کنم", f"cancel_action:{round_id}")
                ]
            ])
        )
        return

    close_answers_and_prepare_options(round_id)


def start_voting(user_id, chat_id, round_id):
    round_row = get_round(round_id)

    if not round_row:
        send_message(chat_id, "این دور پیدا نشد.")
        return

    game = get_game_by_id(round_row["game_id"])

    if not is_owner(game, user_id):
        send_message(chat_id, "فقط مدیر بازی می‌تونه رای‌گیری رو شروع کنه.")
        return

    if round_row["status"] != "reviewing":
        send_message(chat_id, "هنوز نوبت شروع رای‌گیری نیست.")
        return

    options = get_options(round_id)

    if not options:
        send_message(chat_id, "گزینه‌ای برای رای‌گیری پیدا نشد.")
        return

    options_text = format_options(options)

    with db() as conn:
        voters = conn.execute("""
            SELECT p.*
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ?
              AND rp.can_vote = 1
            ORDER BY p.id ASC
        """, (round_id,)).fetchall()

        conn.execute("""
            UPDATE rounds
            SET status = 'voting'
            WHERE id = ?
        """, (round_id,))

        conn.commit()

    for p in voters:
        send_message(
            p["chat_id"],
            "رای‌گیری شروع شد.\n\n"
            "عدد گزینه‌ای که فکر می‌کنی جواب درست است رو بفرست.\n"
            "حواست باشه نمی‌تونی به جواب خودت رای بدی.\n\n"
            f"{options_text}"
        )

    send_message(
        game["owner_chat_id"],
        f"رای‌گیری برای {len(voters)} نفر شروع شد.",
        reply_markup=inline_keyboard([
            [button("پایان رای‌گیری", f"end_voting:{round_id}")]
        ])
    )


def handle_vote_message(user_id, chat_id, text):
    if not text.strip().isdigit():
        return False

    selected_no = int(text.strip())

    with db() as conn:
        row = conn.execute("""
            SELECT
                r.id AS round_id,
                r.status AS status,
                p.id AS player_id,
                p.display_name AS display_name
            FROM rounds r
            JOIN round_players rp ON rp.round_id = r.id
            JOIN players p ON p.id = rp.player_id
            WHERE p.user_id = ?
              AND r.status = 'voting'
              AND rp.can_vote = 1
            ORDER BY r.id DESC
            LIMIT 1
        """, (user_id,)).fetchone()

        if not row:
            return False

        round_id = row["round_id"]
        voter_player_id = row["player_id"]

        option = conn.execute("""
            SELECT *
            FROM options
            WHERE round_id = ?
              AND option_no = ?
        """, (round_id, selected_no)).fetchone()

        if not option:
            send_message(chat_id, "همچین گزینه‌ای نداریم. فقط عدد یکی از گزینه‌ها رو بفرست.")
            return True

        if option["player_id"] and int(option["player_id"]) == int(voter_player_id):
            send_message(chat_id, "نمی‌تونی به جواب خودت رای بدی. یه گزینه دیگه انتخاب کن.")
            return True

        existing_vote = conn.execute("""
            SELECT id
            FROM votes
            WHERE round_id = ?
              AND voter_player_id = ?
        """, (round_id, voter_player_id)).fetchone()

        if existing_vote:
            conn.execute("""
                UPDATE votes
                SET option_id = ?, created_at = ?
                WHERE round_id = ?
                  AND voter_player_id = ?
            """, (
                option["id"],
                now(),
                round_id,
                voter_player_id
            ))

            message = "رایت آپدیت شد."
        else:
            conn.execute("""
                INSERT INTO votes(
                    round_id,
                    voter_player_id,
                    option_id,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                round_id,
                voter_player_id,
                option["id"],
                now()
            ))

            message = "رایت ثبت شد."

        conn.commit()

    send_message(chat_id, message)
    return True


def get_missing_vote_players(round_id):
    with db() as conn:
        return conn.execute("""
            SELECT p.*
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            LEFT JOIN votes v
                ON v.round_id = rp.round_id
               AND v.voter_player_id = p.id
            WHERE rp.round_id = ?
              AND rp.can_vote = 1
              AND v.id IS NULL
            ORDER BY p.id ASC
        """, (round_id,)).fetchall()


def request_end_voting(user_id, chat_id, round_id):
    round_row = get_round(round_id)

    if not round_row:
        send_message(chat_id, "این دور پیدا نشد.")
        return

    game = get_game_by_id(round_row["game_id"])

    if not is_owner(game, user_id):
        send_message(chat_id, "فقط مدیر بازی می‌تونه رای‌گیری رو تموم کنه.")
        return

    if round_row["status"] != "voting":
        send_message(chat_id, "الان مرحله رای‌گیری نیست.")
        return

    missing = get_missing_vote_players(round_id)

    if missing:
        names = "\n".join([f"- {p['display_name']}" for p in missing])

        send_message(
            chat_id,
            "هنوز اینا رای ندادن:\n\n"
            f"{names}\n\n"
            "می‌خوای با همین وضعیت رای‌گیری رو تموم کنم؟",
            reply_markup=inline_keyboard([
                [
                    button("آره، تموم کن", f"force_end_voting:{round_id}"),
                    button("نه، صبر می‌کنم", f"cancel_action:{round_id}")
                ]
            ])
        )
        return

    finalize_round(round_id)


def finalize_round(round_id):
    round_row = get_round(round_id)

    if not round_row:
        return

    game = get_game_by_id(round_row["game_id"])
    players = get_players(game["id"])

    with db() as conn:
        correct_option = conn.execute("""
            SELECT *
            FROM options
            WHERE round_id = ?
              AND is_correct = 1
            LIMIT 1
        """, (round_id,)).fetchone()

        correct_voters = conn.execute("""
            SELECT p.*
            FROM votes v
            JOIN players p ON p.id = v.voter_player_id
            WHERE v.round_id = ?
              AND v.option_id = ?
            ORDER BY p.display_name ASC
        """, (round_id, correct_option["id"])).fetchall()

        # دیکشنری امتیازات این دور (player_id → round_score)
        round_scores = {}

        # رأی‌دهندگان درست: +1
        for p in correct_voters:
            round_scores[p["id"]] = round_scores.get(p["id"], 0) + 1
            conn.execute("""
                UPDATE players
                SET score = score + 1
                WHERE id = ?
            """, (p["id"],))

        fake_results = conn.execute("""
            SELECT
                o.id AS option_id,
                o.answer_text AS answer_text,
                o.player_id AS answer_owner_id,
                p.display_name AS owner_name,
                COUNT(v.id) AS vote_count
            FROM options o
            JOIN players p ON p.id = o.player_id
            LEFT JOIN votes v ON v.option_id = o.id
            WHERE o.round_id = ?
              AND o.is_correct = 0
            GROUP BY o.id
            ORDER BY vote_count DESC, o.option_no ASC
        """, (round_id,)).fetchall()

        for item in fake_results:
            vote_count = int(item["vote_count"] or 0)
            if vote_count > 0:
                round_scores[item["answer_owner_id"]] = (
                    round_scores.get(item["answer_owner_id"], 0) + vote_count
                )
                conn.execute("""
                    UPDATE players
                    SET score = score + ?
                    WHERE id = ?
                """, (
                    vote_count,
                    item["answer_owner_id"]
                ))

        # ذخیره امتیازات هر بازیکن در round_players
        for player_id, score in round_scores.items():
            conn.execute("""
                UPDATE round_players
                SET score = ?
                WHERE round_id = ? AND player_id = ?
            """, (score, round_id, player_id))

        conn.execute("""
            UPDATE rounds
            SET status = 'finished',
                finished_at = ?
            WHERE id = ?
        """, (now(), round_id))

        conn.commit()

        scoreboard = conn.execute("""
            SELECT *
            FROM players
            WHERE game_id = ?
            ORDER BY score DESC, display_name ASC
        """, (game["id"],)).fetchall()

    # ── ساخت متن نتایج ──
    if correct_voters:
        correct_names = "\n".join([f"- {p['display_name']}" for p in correct_voters])
    else:
        correct_names = "هیچ‌کس جواب درست رو انتخاب نکرد."

    fake_lines = []
    for item in fake_results:
        fake_lines.append(
            f"- {item['answer_text']}\n"
            f"  فرستنده: <b>{item['owner_name']}</b> | رای: {item['vote_count']}"
        )

    if not fake_lines:
        fake_lines_text = "جواب اشتباهی ثبت نشده بود."
    else:
        fake_lines_text = "\n\n".join(fake_lines)

    result_text = (
        f"نتیجه دور {round_row['round_no']}\n\n"
        f"جواب درست:\n<b>{round_row['correct_answer']}</b>\n\n"
        "کسایی که جواب درست رو پیدا کردن:\n"
        f"{correct_names}\n\n"
        "--------------------\n\n"
        "جواب‌های اشتباه به ترتیب بیشترین رای:\n\n"
        f"{fake_lines_text}"
    )

    score_lines = []
    for index, p in enumerate(scoreboard, start=1):
        score_lines.append(f"{index}. {p['display_name']} — {p['score']} امتیاز")

    scoreboard_text = (
        "جدول امتیازها:\n\n"
        + "\n".join(score_lines)
    )

    # ── ارسال به بازیکن‌ها ──
    admin_id = int(game["owner_user_id"])
    admin_is_player = any(int(p["user_id"]) == admin_id for p in players)

    for p in players:
        send_message(p["chat_id"], result_text)
        send_message(p["chat_id"], scoreboard_text)

    # ── ارسال به مدیر ──
    owner_keyboard_rows = [
        [button("🚀 شروع دور جدید", f"start_round:{game['code']}")],
        [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")]
    ]

    if admin_is_player:
        # مدیر خودش بازیکنه — دکمه جریمه رو فقط برای مدیر جداگانه بفرست
        admin_player = next(p for p in players if int(p["user_id"]) == admin_id)
        send_message(
            admin_player["chat_id"],
            "🔍 **پنل مدیریت:**\nمی‌تونی بازیکنی که جوابش شبیه جواب درست بوده رو جریمه کنی.",
            reply_markup=inline_keyboard(owner_keyboard_rows)
        )
    else:
        # مدیر بازیکن نیست — نتایج + دکمه‌ها رو براش بفرست
        send_message(
            game["owner_chat_id"],
            f"🔍 **نتایج دور (نظارت مدیر):**\n\n{result_text}\n\n{scoreboard_text}",
            reply_markup=inline_keyboard(owner_keyboard_rows)
        )


# ═══════════════════════════════════════════
# Penalty System — Functions
# ═══════════════════════════════════════════

def show_penalty_player_list(round_id, admin_id, chat_id):
    """لیست شماره‌دار بازیکنان رو به مدیر نشون میده برای انتخاب جهت جریمه"""
    with db() as conn:
        round_players = conn.execute("""
            SELECT rp.player_id, p.display_name, rp.score, rp.penalty
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ?
            ORDER BY rp.id ASC
        """, (round_id,)).fetchall()

    if not round_players:
        send_message(chat_id, "⚠️ هیچ بازیکنی برای جریمه یافت نشد.")
        return

    # فقط کسایی که هنوز جریمه نَشدن و امتیاز دارن
    eligible = [rp for rp in round_players if not rp["penalty"] and rp["score"] > 0]

    if not eligible:
        send_message(chat_id, "✅ همه بازیکن‌های امتیازدار قبلاً جریمه شدن یا امتیازی برای صفر کردن ندارن.")
        return

    # ساخت mapping: عدد → player_id
    mapping = {}
    lines = ["🚫 **ثبت جریمه**\n", "کدوم بازیکن رو می‌خوای جریمه کنی؟ عددش رو بفرست:\n"]

    for i, rp in enumerate(eligible, 1):
        mapping[i] = rp["player_id"]
        lines.append(f"{i}️⃣ {rp['display_name']} — {rp['score']} امتیاز این دور")

    lines.append("\n❌ برای لغو، کلمه «انصراف» رو بفرست.")

    # ذخیره mapping در مموری
    _penalty_mappings[round_id] = mapping

    # تنظیم state برای مدیر
    set_user_state(admin_id, f"penalty_waiting:{round_id}")

    send_message(chat_id, "\n".join(lines))


def apply_penalty(round_id, penalized_player_id):
    """
    امتیاز یک بازیکن در این دور رو صفر می‌کنه.
    مقدار round_score ذخیره‌شده در round_players رو از cumulative score کم می‌کنه.
    """
    with db() as conn:
        # گرفتن امتیاز این دور بازیکن
        rp = conn.execute("""
            SELECT rp.score, rp.penalty, p.display_name, p.id AS player_id
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ? AND rp.player_id = ?
        """, (round_id, penalized_player_id)).fetchone()

        if not rp:
            return None

        if rp["penalty"]:
            return None  # قبلاً جریمه شده

        round_score = rp["score"]

        # صفر کردن امتیاز این دور در round_players
        conn.execute("""
            UPDATE round_players
            SET penalty = 1, score = 0
            WHERE round_id = ? AND player_id = ?
        """, (round_id, penalized_player_id))

        # کم کردن از امتیاز کلی بازیکن
        if round_score > 0:
            conn.execute("""
                UPDATE players
                SET score = MAX(0, score - ?)
                WHERE id = ?
            """, (round_score, penalized_player_id))

        conn.commit()

        print(f"🚫 PENALTY applied: {rp['display_name']} in round {round_id} "
              f"(-{round_score} points)")

        return rp["display_name"]


def recalculate_and_broadcast(round_id):
    """
    بعد از جریمه، جدول امتیازات رو دوباره می‌سازه و برای همه (بازیکن‌ها + مدیر) می‌فرسته.
    """
    round_row = get_round(round_id)
    if not round_row:
        return

    game = get_game_by_id(round_row["game_id"])
    players = get_players(game["id"])
    admin_id = int(game["owner_user_id"])

    with db() as conn:
        scoreboard = conn.execute("""
            SELECT *
            FROM players
            WHERE game_id = ?
            ORDER BY score DESC, display_name ASC
        """, (game["id"],)).fetchall()

        # گرفتن اطلاعات جریمه‌شده‌ها برای نمایش
        penalized = conn.execute("""
            SELECT p.display_name
            FROM round_players rp
            JOIN players p ON p.id = rp.player_id
            WHERE rp.round_id = ? AND rp.penalty = 1
        """, (round_id,)).fetchall()

    penalized_names = [p["display_name"] for p in penalized]

    # ساخت متن جدول امتیازات
    score_lines = ["📊 **جدول امتیازها (بروز شده):**\n"]
    for index, p in enumerate(scoreboard, start=1):
        score_lines.append(f"{index}. {p['display_name']} — {p['score']} امتیاز")

    if penalized_names:
        score_lines.append(
            f"\n🚫 جریمه‌شده‌ها: {', '.join(penalized_names)}"
        )

    scoreboard_text = "\n".join(score_lines)

    # ── ارسال به بازیکن‌ها ──
    admin_is_player = any(int(p["user_id"]) == admin_id for p in players)

    for p in players:
        if int(p["user_id"]) == admin_id:
            # مدیر-بازیکن: دکمه جریمه هم داره
            keyboard = inline_keyboard([
                [button("🚀 شروع دور جدید", f"start_round:{game['code']}")],
                [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")]
            ])
        else:
            keyboard = inline_keyboard([
                [button("🚀 شروع دور جدید", f"start_round:{game['code']}")]
            ])

        send_message(p["chat_id"], scoreboard_text, reply_markup=keyboard)

    # ── ارسال به مدیر (اگر بازیکن نیست) ──
    if not admin_is_player:
        admin_keyboard = inline_keyboard([
            [button("🚀 شروع دور جدید", f"start_round:{game['code']}")],
            [button("🚫 ثبت جریمه", f"penalty_start:{round_id}")]
        ])
        send_message(
            game["owner_chat_id"],
            f"🔍 **پنل نظارت — {scoreboard_text}**",
            reply_markup=admin_keyboard
        )


# =========================
# Callback Handler
# =========================

def handle_callback(callback):
    callback_id = callback["id"]
    data = callback.get("data", "")
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]

    answer_callback(callback_id)

    try:
        action, value = data.split(":", 1)
    except ValueError:
        send_message(chat_id, "دکمه نامعتبره.")
        return

    if action == "start_round":
        # پاک کردن penalty mappings قدیمی
        _penalty_mappings.clear()
        start_new_round(user_id, chat_id, value)

    elif action == "penalty_start":
        round_id = int(value)
        show_penalty_player_list(round_id, user_id, chat_id)

    elif action == "end_answers":
        request_end_answers(user_id, chat_id, int(value))

    elif action == "force_end_answers":
        round_id = int(value)
        round_row = get_round(round_id)
        if not round_row:
            send_message(chat_id, "این دور پیدا نشد.")
            return

        game = get_game_by_id(round_row["game_id"])
        if not is_owner(game, user_id):
            send_message(chat_id, "فقط مدیر بازی اجازه این کار رو داره.")
            return

        close_answers_and_prepare_options(round_id)

    elif action == "start_voting":
        start_voting(user_id, chat_id, int(value))

    elif action == "end_voting":
        request_end_voting(user_id, chat_id, int(value))

    elif action == "force_end_voting":
        round_id = int(value)
        round_row = get_round(round_id)
        if not round_row:
            send_message(chat_id, "این دور پیدا نشد.")
            return

        game = get_game_by_id(round_row["game_id"])
        if not is_owner(game, user_id):
            send_message(chat_id, "فقط مدیر بازی اجازه این کار رو داره.")
            return

        finalize_round(round_id)

    elif action == "cancel_action":
        send_message(chat_id, "باشه، فعلاً ادامه می‌دیم.")

    else:
        send_message(chat_id, "این عملیات شناخته‌شده نیست.")


# =========================
# Message Handler
# =========================

def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})

    chat_id = chat.get("id")
    user_id = user.get("id")
    text = message.get("text", "")

    if not chat_id or not user_id or not text:
        return

    text = text.strip()

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)

        if len(parts) == 2 and parts[1].startswith("join_"):
            game_code = parts[1].replace("join_", "", 1).strip()
            join_game_start(user_id, chat_id, game_code)
            return

        send_message(
            chat_id,
            "سلام! من ربات بازی جواب‌های گمراه‌کننده‌ام.\n\n"
            "اگر مدیر بازی هستی، با دستور /newgame یه بازی جدید بساز.\n"
            "اگر بازیکنی، باید با لینک عضویت مدیر وارد بشی."
        )
        return

    if text == "/newgame":
        create_game(user_id, chat_id)
        return

    if text == "/help":
        send_message(
            chat_id,
            "دستورها:\n\n"
            "/newgame ساخت بازی جدید\n"
            "/start شروع کار با ربات"
        )
        return

    state = get_user_state(user_id)

    # ═══ STATE: در حال انتخاب بازیکن برای جریمه ═══
    if state and state["state"].startswith("penalty_waiting:"):
        round_id = int(state["state"].split(":")[1])

        # لغو
        if text == "انصراف" or text == "/cancel":
            clear_user_state(user_id)
            send_message(chat_id, "❌ جریمه لغو شد.")
            return

        # اعتبارسنجی: باید عدد باشه
        if not text.isdigit():
            send_message(chat_id, "⚠️ لطفاً عدد بازیکن رو بفرست (مثلاً 2).")
            return

        player_number = int(text)
        mapping = _penalty_mappings.get(round_id, {})

        if player_number not in mapping:
            send_message(chat_id, f"⚠️ عدد {player_number} معتبر نیست. دوباره سعی کن.")
            return

        penalized_player_id = mapping[player_number]

        # اعمال جریمه
        player_name = apply_penalty(round_id, penalized_player_id)

        if player_name:
            send_message(
                chat_id,
                f"🚫 **{player_name}** جریمه شد!\n"
                f"امتیاز این دورش صفر شد و از مجموع امتیازاتش کم شد."
            )
            # اطلاع‌رسانی به خود بازیکن جریمه‌شده
            penalized_player = get_player_by_id(penalized_player_id)
            if penalized_player:
                send_message(
                    penalized_player["chat_id"],
                    "🚫 **شما توسط مدیر جریمه شدید!**\n\n"
                    "امتیاز شما در این دور صفر شد.\n"
                    "احتمالاً جواب شما خیلی شبیه جواب درست بوده.\n"
                    "دفعه بعد حتی اگه جواب رو می‌دونی، یه جواب گمراه‌کننده بده."
                )
        else:
            send_message(chat_id, "⚠️ این بازیکن قبلاً جریمه شده یا پیدا نشد.")

        # پاک کردن state
        clear_user_state(user_id)

        # محاسبه مجدد و ارسال نتایج جدید به همه
        recalculate_and_broadcast(round_id)

        return

    if state and state["state"] == "awaiting_name":
        game_code = state["data"].get("game_code")
        save_player_name(user_id, chat_id, text, game_code)
        return

    if handle_answer_message(user_id, chat_id, text):
        return

    if handle_vote_message(user_id, chat_id, text):
        return

    send_message(
        chat_id,
        "الان منتظر این پیام نبودم.\n"
        "اگر می‌خوای وارد بازی بشی، از لینک عضویت استفاده کن."
    )


# =========================
# Flask Routes
# =========================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "service": "telegram-game-bot",
        "time": now()
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

    webhook_url = f"{BASE_URL.rstrip('/')}/telegram/webhook"

    result = tg_request("setWebhook", {
        "url": webhook_url,
        "drop_pending_updates": True
    })

    return jsonify({
        "ok": True,
        "webhook_url": webhook_url,
        "telegram_result": result
    })


@app.route("/delete-webhook", methods=["GET"])
def delete_webhook():
    result = tg_request("deleteWebhook", {
        "drop_pending_updates": True
    })

    return jsonify({
        "ok": True,
        "telegram_result": result
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
