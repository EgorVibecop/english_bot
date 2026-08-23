"""
Работа с базой данных SQLite для бота.
Хранит словарь (words), сленговые сокращения (slang) и прогресс
каждого пользователя (progress, slang_progress).

Путь к базе можно переопределить переменной окружения DB_PATH — это нужно
на хостинге, чтобы положить базу на постоянный диск и не терять прогресс
при передеплое.
"""

import os
import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH") or (Path(__file__).parent / "english_bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Интервалы повторения по системе Лейтнера (в днях), индекс = номер "коробки"
LEITNER_INTERVALS = {0: 0, 1: 1, 2: 2, 3: 4, 4: 7, 5: 14, 6: 30}
MAX_BOX = max(LEITNER_INTERVALS.keys())
KNOWN_BOX_THRESHOLD = 3  # начиная с этой коробки слово считается "известным"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank INTEGER UNIQUE NOT NULL,
            order_index INTEGER UNIQUE,
            word TEXT UNIQUE NOT NULL,
            translation TEXT,
            definition TEXT,
            pos TEXT,
            source TEXT,
            level INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TEXT,
            level INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER NOT NULL,
            word_id INTEGER NOT NULL,
            box INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            next_review TEXT,
            last_seen TEXT,
            PRIMARY KEY (user_id, word_id),
            FOREIGN KEY (word_id) REFERENCES words (id)
        );

        CREATE TABLE IF NOT EXISTS grammar_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tense TEXT NOT NULL,
            correct INTEGER NOT NULL,
            answered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS slang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_index INTEGER,
            term TEXT UNIQUE NOT NULL,
            full_form TEXT,
            translation TEXT,
            example TEXT
        );

        CREATE TABLE IF NOT EXISTS slang_progress (
            user_id INTEGER NOT NULL,
            slang_id INTEGER NOT NULL,
            box INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            next_review TEXT,
            last_seen TEXT,
            PRIMARY KEY (user_id, slang_id),
            FOREIGN KEY (slang_id) REFERENCES slang (id)
        );
        """
    )
    conn.commit()
    conn.close()


# ---------- words ----------

def upsert_word(rank, word, translation, definition, pos, source, order_index=None, level=1):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO words (rank, order_index, word, translation, definition, pos, source, level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(word) DO UPDATE SET
            order_index=excluded.order_index,
            translation=excluded.translation,
            definition=excluded.definition,
            pos=excluded.pos,
            source=excluded.source,
            level=excluded.level
        """,
        (rank, order_index if order_index is not None else rank, word,
         translation, definition, pos, source, level),
    )
    conn.commit()
    conn.close()


def get_word_by_word(word):
    conn = get_conn()
    row = conn.execute("SELECT * FROM words WHERE word = ?", (word,)).fetchone()
    conn.close()
    return row


def count_words(only_with_translation=False):
    conn = get_conn()
    if only_with_translation:
        n = conn.execute(
            "SELECT COUNT(*) FROM words WHERE translation IS NOT NULL"
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    conn.close()
    return n


# ---------- users ----------

def ensure_user(user_id, username):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, created_at, level) VALUES (?, ?, ?, 1)",
        (user_id, username, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_user_level(user_id):
    conn = get_conn()
    row = conn.execute("SELECT level FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["level"] if row and row["level"] else 1


def get_admin_stats(active_days=7):
    conn = get_conn()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    cutoff = (datetime.utcnow() - timedelta(days=active_days)).isoformat()
    active_users = conn.execute(
        """
        SELECT COUNT(DISTINCT user_id) FROM (
            SELECT user_id, last_seen AS ts FROM progress
            UNION ALL
            SELECT user_id, answered_at AS ts FROM grammar_log
            UNION ALL
            SELECT user_id, last_seen AS ts FROM slang_progress
        )
        WHERE ts >= ?
        """,
        (cutoff,),
    ).fetchone()[0]

    new_today = conn.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= ?",
        (datetime.utcnow().strftime("%Y-%m-%d"),),
    ).fetchone()[0]

    words_known = conn.execute(
        "SELECT COUNT(*) FROM progress WHERE status = 'known'"
    ).fetchone()[0]
    words_learning = conn.execute(
        "SELECT COUNT(*) FROM progress WHERE status = 'learning'"
    ).fetchone()[0]
    grammar_answers = conn.execute("SELECT COUNT(*) FROM grammar_log").fetchone()[0]
    slang_known = conn.execute(
        "SELECT COUNT(*) FROM slang_progress WHERE status = 'known'"
    ).fetchone()[0]

    conn.close()
    return {
        "total_users": total_users,
        "active_users": active_users,
        "active_days": active_days,
        "new_today": new_today,
        "words_known": words_known,
        "words_learning": words_learning,
        "grammar_answers": grammar_answers,
        "slang_known": slang_known,
    }


def set_user_level(user_id, level):
    conn = get_conn()
    conn.execute("UPDATE users SET level = ? WHERE user_id = ?", (level, user_id))
    conn.commit()
    conn.close()


# ---------- progress ----------

# Сколько последних показанных слов не предлагать повторно.
NO_REPEAT_WINDOW = 200

# Фиксированное начало для нового пользователя, дальше — случайный порядок.
PINNED_FIRST = ("i", "love", "katya")


def get_next_new_word(user_id, level=1):
    """Случайное слово выбранного уровня, исключая NO_REPEAT_WINDOW последних
    показанных этому пользователю.

    Порядок именно случайный, а не фиксированный: прогресс на хостинге
    сбрасывается при передеплое, и при фиксированном порядке после каждого
    сброса шли бы одни и те же первые слова. Слово может встретиться снова,
    но не раньше, чем через NO_REPEAT_WINDOW других слов.
    """
    conn = get_conn()

    seen_count = conn.execute(
        "SELECT COUNT(*) FROM progress WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if level == 1 and seen_count < len(PINNED_FIRST):
        pinned = conn.execute(
            "SELECT * FROM words WHERE word = ? AND level = ?",
            (PINNED_FIRST[seen_count], level),
        ).fetchone()
        if pinned is not None:
            conn.close()
            return pinned

    row = conn.execute(
        """
        SELECT w.* FROM words w
        WHERE w.translation IS NOT NULL
          AND w.level = ?
          AND w.id NOT IN (
              SELECT word_id FROM progress
              WHERE user_id = ?
              ORDER BY last_seen DESC
              LIMIT ?
          )
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (level, user_id, NO_REPEAT_WINDOW),
    ).fetchone()

    # Подстраховка: если в уровне слов меньше окна, окно исключит вообще всё —
    # тогда выдаём просто случайное слово уровня.
    if row is None:
        row = conn.execute(
            """
            SELECT w.* FROM words w
            WHERE w.translation IS NOT NULL AND w.level = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (level,),
        ).fetchone()

    conn.close()
    return row


def get_due_reviews(user_id, limit=1):
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        """
        SELECT w.*, p.box, p.status, p.next_review FROM progress p
        JOIN words w ON w.id = p.word_id
        WHERE p.user_id = ? AND p.next_review <= ?
        ORDER BY p.next_review ASC
        LIMIT ?
        """,
        (user_id, now, limit),
    ).fetchall()
    conn.close()
    return rows


def record_answer(user_id, word_id, known: bool):
    """Обновить прогресс по слову после ответа пользователя (Лейтнер)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT box FROM progress WHERE user_id = ? AND word_id = ?",
        (user_id, word_id),
    ).fetchone()
    current_box = row["box"] if row else 0

    if known:
        new_box = min(current_box + 1, MAX_BOX)
    else:
        new_box = 1

    status = "known" if new_box >= KNOWN_BOX_THRESHOLD else "learning"
    next_review = (
        datetime.utcnow() + timedelta(days=LEITNER_INTERVALS[new_box])
    ).isoformat()
    now = datetime.utcnow().isoformat()

    conn.execute(
        """
        INSERT INTO progress (user_id, word_id, box, status, next_review, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, word_id) DO UPDATE SET
            box=excluded.box, status=excluded.status,
            next_review=excluded.next_review, last_seen=excluded.last_seen
        """,
        (user_id, word_id, new_box, status, next_review, now),
    )
    conn.commit()
    conn.close()


def get_stats(user_id, level=1):
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM words WHERE translation IS NOT NULL AND level = ?", (level,)
    ).fetchone()[0]
    seen = conn.execute(
        """SELECT COUNT(*) FROM progress p JOIN words w ON w.id = p.word_id
           WHERE p.user_id = ? AND w.level = ?""",
        (user_id, level),
    ).fetchone()[0]
    known = conn.execute(
        """SELECT COUNT(*) FROM progress p JOIN words w ON w.id = p.word_id
           WHERE p.user_id = ? AND p.status = 'known' AND w.level = ?""",
        (user_id, level),
    ).fetchone()[0]
    learning = conn.execute(
        """SELECT COUNT(*) FROM progress p JOIN words w ON w.id = p.word_id
           WHERE p.user_id = ? AND p.status = 'learning' AND w.level = ?""",
        (user_id, level),
    ).fetchone()[0]
    due = conn.execute(
        "SELECT COUNT(*) FROM progress WHERE user_id = ? AND next_review <= ?",
        (user_id, datetime.utcnow().isoformat()),
    ).fetchone()[0]
    conn.close()
    return {
        "total": total,
        "seen": seen,
        "known": known,
        "learning": learning,
        "due": due,
    }


def get_random_translations(exclude_word_id, n, pool="seen", user_id=None):
    """Случайные варианты перевода-'обманки' для квиза."""
    conn = get_conn()
    if pool == "seen" and user_id is not None:
        rows = conn.execute(
            """
            SELECT w.translation FROM progress p
            JOIN words w ON w.id = p.word_id
            WHERE p.user_id = ? AND w.id != ? AND w.translation IS NOT NULL
            """,
            (user_id, exclude_word_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT translation FROM words WHERE id != ? AND translation IS NOT NULL",
            (exclude_word_id,),
        ).fetchall()
    conn.close()
    options = [r["translation"] for r in rows]
    random.shuffle(options)
    return options[:n]


# ---------- grammar (tenses) ----------

# ---------- slang (сленговые сокращения) ----------

def upsert_slang(order_index, term, full_form, translation, example):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO slang (order_index, term, full_form, translation, example)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(term) DO UPDATE SET
            order_index=excluded.order_index,
            full_form=excluded.full_form,
            translation=excluded.translation,
            example=excluded.example
        """,
        (order_index, term, full_form, translation, example),
    )
    conn.commit()
    conn.close()


def get_slang_by_term(term):
    conn = get_conn()
    row = conn.execute("SELECT * FROM slang WHERE term = ?", (term,)).fetchone()
    conn.close()
    return row


def count_slang():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM slang").fetchone()[0]
    conn.close()
    return n


def get_next_slang(user_id):
    """Случайное сокращение, исключая последние показанные (см. get_next_new_word)."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM slang").fetchone()[0]
    window = min(NO_REPEAT_WINDOW, max(1, total // 2))
    row = conn.execute(
        """
        SELECT s.* FROM slang s
        WHERE s.id NOT IN (
            SELECT slang_id FROM slang_progress
            WHERE user_id = ?
            ORDER BY last_seen DESC
            LIMIT ?
        )
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (user_id, window),
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM slang ORDER BY RANDOM() LIMIT 1").fetchone()
    conn.close()
    return row


def record_slang_answer(user_id, slang_id, known: bool):
    conn = get_conn()
    prev = conn.execute(
        "SELECT box FROM slang_progress WHERE user_id = ? AND slang_id = ?",
        (user_id, slang_id),
    ).fetchone()
    current_box = prev["box"] if prev else 0
    new_box = min(current_box + 1, MAX_BOX) if known else 1
    status = "known" if new_box >= KNOWN_BOX_THRESHOLD else "learning"
    now = datetime.utcnow()
    conn.execute(
        """
        INSERT INTO slang_progress (user_id, slang_id, box, status, next_review, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, slang_id) DO UPDATE SET
            box=excluded.box, status=excluded.status,
            next_review=excluded.next_review, last_seen=excluded.last_seen
        """,
        (
            user_id, slang_id, new_box, status,
            (now + timedelta(days=LEITNER_INTERVALS[new_box])).isoformat(),
            now.isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_slang_stats(user_id):
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM slang").fetchone()[0]
    seen = conn.execute(
        "SELECT COUNT(*) FROM slang_progress WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    known = conn.execute(
        "SELECT COUNT(*) FROM slang_progress WHERE user_id = ? AND status = 'known'",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return {"total": total, "seen": seen, "known": known}


# ---------- grammar (tenses) ----------

def log_grammar_answer(user_id, tense, correct: bool):
    conn = get_conn()
    conn.execute(
        "INSERT INTO grammar_log (user_id, tense, correct, answered_at) VALUES (?, ?, ?, ?)",
        (user_id, tense, 1 if correct else 0, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_grammar_stats(user_id):
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) FROM grammar_log WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    correct = conn.execute(
        "SELECT COUNT(*) FROM grammar_log WHERE user_id = ? AND correct = 1", (user_id,)
    ).fetchone()[0]
    conn.close()
    return {"total": total, "correct": correct}


def get_quiz_pool(user_id, limit=8):
    """Слова со статусом 'learning' — те, где пользователь ответил 'не знаю'
    (именно они и должны тренироваться квизом, а не уже выученные/новые)."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT w.* FROM progress p
        JOIN words w ON w.id = p.word_id
        WHERE p.user_id = ? AND p.status = 'learning' AND w.translation IS NOT NULL
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows
