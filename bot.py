"""
Telegram-бот для изучения английского: словарь по частотности + грамматика.

Два раздела:
  📚 Словарь — /word, /review, /quiz (см. bot.py как раньше)
  ⏳ Времена — тренажёр на 12 английских времён: бот присылает предложение
               с пропуском, пользователь отвечает текстом, бот проверяет.

Команды:
  /start    - приветствие и главное меню
  /word     - показать следующее новое слово
  /review   - повторить слово, которое пора повторить (интервальный повтор)
  /quiz     - мини-тест по уже изученным словам
  /grammar  - тренажёр времён
  /progress - статистика прогресса
  /help     - справка

Перед первым запуском обязательно выполните: python seed_db.py
"""

import asyncio
import json
import logging
import os
import random
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import backup
import db

BACKUP_INTERVAL_SECONDS = 6 * 60 * 60  # каждые 6 часов

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

with open("grammar_exercises.json", encoding="utf-8") as f:
    GRAMMAR_EXERCISES = json.load(f)

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["📚 Словарь", "⏳ Времена"],
        ["💬 Сленг", "📊 Прогресс"],
        ["❓ Помощь"],
    ],
    resize_keyboard=True,
)

SLANG_MENU = ReplyKeyboardMarkup(
    [
        ["💬 Новое сокращение"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)

VOCAB_MENU = ReplyKeyboardMarkup(
    [
        ["📖 Новое слово", "🔁 Повторить"],
        ["🧠 Квиз", "🎚 Уровень"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)

LEVELS = {
    1: "Уровень 1 (база, 843 слова)",
    2: "Уровень 2 (средний, 540 слов)",
    3: "Уровень 3 (продвинутый, 689 слов)",
}

GRAMMAR_MENU = ReplyKeyboardMarkup(
    [
        ["🏁 Закончить тренировку"],
        ["⬅️ Назад"],
    ],
    resize_keyboard=True,
)


def word_card_text(row, header="📖 Новое слово"):
    pos = f" _({row['pos']})_" if row["pos"] else ""
    text = (
        f"{header}\n\n"
        f"*{row['word']}*{pos}   `#{row['rank']}`\n\n"
        f"🇷🇺 {row['translation']}\n"
    )
    if row["definition"]:
        text += f"\n✏️ _{row['definition']}_"
    return text


def know_buttons(word_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Знаю", callback_data=f"know:{word_id}"),
                InlineKeyboardButton("❌ Не знаю", callback_data=f"dontknow:{word_id}"),
            ]
        ]
    )


def slang_card_text(row):
    return (
        f"💬 Сленговое сокращение\n\n"
        f"*{row['term']}* — {row['full_form']}\n\n"
        f"🇷🇺 {row['translation']}\n"
        f"\n✏️ _{row['example']}_"
    )


def slang_buttons(slang_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Знаю", callback_data=f"sknow:{slang_id}"),
                InlineKeyboardButton("❌ Не знаю", callback_data=f"sdont:{slang_id}"),
            ]
        ]
    )


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)
    context.user_data.pop("grammar_active", None)
    await update.message.reply_text(
        "Привет! 👋 Я помогу выучить английский.\n\n"
        "📚 *Словарь* — частотные слова с переводом и примером употребления, "
        "три уровня сложности, интервальный повтор и квиз.\n"
        "⏳ *Времена* — тренажёр на все 12 английских времён: предложение "
        "с пропуском, ты вписываешь ответ текстом.\n"
        "💬 *Сленг* — современные сокращения из переписки: btw, ngl, iykyk "
        "и другие.\n\n"
        "Выбери раздел на клавиатуре ниже.",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 Словарь: после каждого нового слова отметь, знал(а) ты его или "
        "нет — от этого зависит, когда бот покажет его на повторении снова. "
        "Слова идут в случайном порядке и не повторяются раньше, чем через "
        "200 других слов. Кнопка «🎚 Уровень» меняет сложность слов "
        "(1 — база, 2 — средний, 3 — продвинутый).\n\n"
        "⏳ Времена: бот присылает предложение с пропуском (___), впиши "
        "пропущенное слово (или несколько слов, например «will have») "
        "обычным текстовым сообщением.\n\n"
        "💬 Сленг: сокращения из реальной переписки — расшифровка, перевод "
        "и пример.",
        reply_markup=MAIN_MENU,
    )


async def cmd_vocab_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("grammar_active", None)
    await update.message.reply_text("📚 Раздел «Словарь». Выбери действие:", reply_markup=VOCAB_MENU)


async def cmd_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("grammar_active", None)
    await update.message.reply_text("Главное меню:", reply_markup=MAIN_MENU)


async def send_next_slang(send_func, user_id):
    row = db.get_next_slang(user_id)
    if row is None:
        await send_func(
            "Словарь сокращений пуст. Запусти seed_db.py, чтобы его заполнить.",
            reply_markup=SLANG_MENU,
        )
        return
    await send_func(
        slang_card_text(row),
        reply_markup=slang_buttons(row["id"]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_slang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)
    context.user_data.pop("grammar_active", None)
    await send_next_slang(update.message.reply_text, user.id)


async def cmd_slang_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("grammar_active", None)
    s = db.get_slang_stats(update.effective_user.id)
    await update.message.reply_text(
        f"💬 Раздел «Сленг» — современные сокращения из переписки и соцсетей "
        f"(btw, ngl, iykyk...).\n\n"
        f"Всего в словаре: {s['total']} · пройдено: {s['seen']} · выучено: {s['known']}",
        reply_markup=SLANG_MENU,
    )


async def send_next_word(send_func, user_id):
    level = db.get_user_level(user_id)
    row = db.get_next_new_word(user_id, level)
    if row is None:
        await send_func(
            f"🎉 Ты прошёл(шла) все слова уровня {level}!\n"
            "Используй /review для повторения и /quiz для проверки, "
            "или переключи «🎚 Уровень», если он доступен.",
            reply_markup=VOCAB_MENU,
        )
        return
    await send_func(
        word_card_text(row),
        reply_markup=know_buttons(row["id"]),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)
    await send_next_word(update.message.reply_text, user.id)


async def cmd_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)
    current = db.get_user_level(user.id)
    buttons = [
        [InlineKeyboardButton(
            ("✅ " if lvl == current else "") + label, callback_data=f"level:{lvl}"
        )]
        for lvl, label in LEVELS.items()
    ]
    await update.message.reply_text(
        f"🎚 Текущий уровень: {LEVELS[current]}\n\nВыбери уровень:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = db.get_due_reviews(user.id, limit=1)
    if not rows:
        stats = db.get_stats(user.id, db.get_user_level(user.id))
        await update.message.reply_text(
            f"Сейчас нет слов для повторения. Выучено: {stats['known']}, "
            f"в процессе: {stats['learning']}.\nЗагляни позже 🙂",
            reply_markup=VOCAB_MENU,
        )
        return
    row = rows[0]
    context.user_data["reviewing"] = row["id"]
    await update.message.reply_text(
        f"🔁 Вспомни перевод слова:\n\n*{row['word']}*",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("👁 Показать ответ", callback_data=f"reveal:{row['id']}")]]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    level = db.get_user_level(user.id)
    s = db.get_stats(user.id, level)
    percent = round(100 * s["seen"] / s["total"]) if s["total"] else 0
    g = db.get_grammar_stats(user.id)
    g_percent = round(100 * g["correct"] / g["total"]) if g["total"] else 0
    sl = db.get_slang_stats(user.id)
    await update.message.reply_text(
        f"📊 *Твой прогресс*\n\n"
        f"📚 *Словарь* ({LEVELS[level]})\n"
        f"Всего слов в словаре: {s['total']}\n"
        f"Просмотрено: {s['seen']} ({percent}%)\n"
        f"✅ Выучено: {s['known']}\n"
        f"📘 В процессе: {s['learning']}\n"
        f"🔁 Ждут повторения сейчас: {s['due']}\n\n"
        f"⏳ *Времена*\n"
        f"Отвечено вопросов: {g['total']}\n"
        f"Правильно: {g['correct']} ({g_percent}%)\n\n"
        f"💬 *Сленг*\n"
        f"Всего сокращений: {sl['total']}\n"
        f"Пройдено: {sl['seen']} · ✅ выучено: {sl['known']}",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    pool = db.get_quiz_pool(user.id, limit=8)
    if len(pool) < 4:
        await update.message.reply_text(
            "Пока маловато слов со статусом «не знаю» для квиза (нужно минимум 4). "
            "Квиз тренирует именно те слова, где ты нажимал(а) «❌ Не знаю» на "
            "«📖 Новое слово» или «🔁 Повторить».",
            reply_markup=VOCAB_MENU,
        )
        return

    questions = []
    for row in pool:
        distractors = db.get_random_translations(
            row["id"], 3, pool="seen", user_id=user.id
        )
        if len(distractors) < 3:
            distractors = db.get_random_translations(row["id"], 3, pool="all")
        options = distractors + [row["translation"]]
        random.shuffle(options)
        questions.append(
            {
                "word": row["word"],
                "correct": row["translation"],
                "options": options,
            }
        )

    context.user_data["quiz"] = {"questions": questions, "index": 0, "score": 0}
    await send_quiz_question(update.message.reply_text, context)


async def send_quiz_question(send_func, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data["quiz"]
    q = quiz["questions"][quiz["index"]]
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"quiz:{i}")]
        for i, opt in enumerate(q["options"])
    ]
    n = quiz["index"] + 1
    total = len(quiz["questions"])
    await send_func(
        f"🧠 Вопрос {n}/{total}\n\nКак переводится слово *{q['word']}*?",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- callback (inline buttons) ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("level:"):
        level = int(data.split(":")[1])
        db.set_user_level(user_id, level)
        await query.edit_message_text(f"🎚 Установлен: {LEVELS[level]}")
        return

    if data.startswith("sknow:") or data.startswith("sdont:"):
        known = data.startswith("sknow:")
        slang_id = int(data.split(":")[1])
        db.record_slang_answer(user_id, slang_id, known)
        await query.edit_message_reply_markup(reply_markup=None)
        await send_next_slang(query.message.reply_text, user_id)
        return

    if data.startswith("know:") or data.startswith("dontknow:"):
        known = data.startswith("know:")
        word_id = int(data.split(":")[1])
        db.record_answer(user_id, word_id, known)
        await query.edit_message_reply_markup(reply_markup=None)
        await send_next_word(query.message.reply_text, user_id)

    elif data.startswith("reveal:"):
        word_id = int(data.split(":")[1])
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        conn.close()
        await query.edit_message_text(
            word_card_text(row, header="🔁 Повторение"),
            parse_mode=ParseMode.MARKDOWN,
        )
        await query.message.reply_text(
            "Вспомнил(а)?",
            reply_markup=know_buttons(word_id),
        )

    elif data.startswith("quiz:"):
        quiz = context.user_data.get("quiz")
        if not quiz:
            await query.edit_message_text("Квиз уже завершён. Начни новый: /quiz")
            return
        idx = int(data.split(":")[1])
        q = quiz["questions"][quiz["index"]]
        chosen = q["options"][idx]
        correct = chosen == q["correct"]
        if correct:
            quiz["score"] += 1
            result = f"✅ Верно! *{q['word']}* — {q['correct']}"
        else:
            result = f"❌ Неверно. *{q['word']}* — {q['correct']} (ты выбрал(а): {chosen})"
        await query.edit_message_text(result, parse_mode=ParseMode.MARKDOWN)

        quiz["index"] += 1
        if quiz["index"] >= len(quiz["questions"]):
            score = quiz["score"]
            total = len(quiz["questions"])
            del context.user_data["quiz"]
            await query.message.reply_text(
                f"🏁 Квиз завершён! Результат: {score}/{total}",
                reply_markup=VOCAB_MENU,
            )
        else:
            await send_quiz_question(query.message.reply_text, context)


# ---------- grammar (tenses) ----------

TENSE_EXPLANATIONS = {
    "Present Simple": "факты, привычки, регулярные действия («every day», «usually»).",
    "Present Continuous": "действие происходит прямо сейчас или в этот период времени.",
    "Past Simple": "законченное действие в прошлом с известным моментом времени.",
    "Past Continuous": "действие было в процессе в конкретный момент прошлого (часто его прерывает другое действие).",
    "Present Perfect": "прошлое действие важно результатом сейчас — точное время не указывается.",
    "Past Perfect": "«прошлое до прошлого» — действие завершилось раньше другого момента в прошлом.",
    "Future Simple": "решение в момент речи, обещание, предсказание («will»).",
    "Future Continuous": "действие будет в процессе в конкретный момент будущего.",
    "Present Perfect Continuous": "действие началось в прошлом и продолжается до сих пор, важна длительность.",
    "Past Perfect Continuous": "действие длилось вплоть до определённого момента в прошлом.",
    "Future Perfect": "действие завершится к определённому моменту в будущем.",
    "Future Perfect Continuous": "действие будет длиться вплоть до определённого момента в будущем.",
}


def normalize_answer(text):
    text = text.strip().lower()
    text = re.sub(r"[.!?,;:]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


async def send_grammar_exercise(send_func, context: ContextTypes.DEFAULT_TYPE):
    retry_tense = context.user_data.get("grammar_retry_tense")
    if retry_tense:
        pool = [e for e in GRAMMAR_EXERCISES if e["tense"] == retry_tense]
    else:
        pool = GRAMMAR_EXERCISES

    previous = context.user_data.get("grammar_current")
    if previous is not None and len(pool) > 1:
        pool = [e for e in pool if e["sentence"] != previous["sentence"]]

    exercise = random.choice(pool)
    context.user_data["grammar_active"] = True
    context.user_data["grammar_current"] = exercise
    session = context.user_data.setdefault("grammar_session", {"total": 0, "correct": 0})
    n = session["total"] + 1
    # без parse_mode: предложения содержат "___", что ломает Markdown-разметку
    # время в вопросе не пишем — его нужно определить по смыслу самому
    await send_func(
        f"⏳ Вопрос {n}\n\n"
        f"{exercise['sentence']}\n\n"
        f"Впиши пропущенное слово (или несколько слов).",
        reply_markup=GRAMMAR_MENU,
    )


async def cmd_grammar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)
    context.user_data["grammar_session"] = {"total": 0, "correct": 0}
    context.user_data.pop("grammar_retry_tense", None)
    await update.message.reply_text(
        "⏳ *Тренажёр времён*\n\n"
        "Присылаю предложение с пропуском — впиши недостающее слово "
        "(или слова, например «will have») текстом. Времена идут вперемешку.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=GRAMMAR_MENU,
    )
    await send_grammar_exercise(update.message.reply_text, context)


async def cmd_grammar_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = context.user_data.pop("grammar_session", {"total": 0, "correct": 0})
    context.user_data.pop("grammar_active", None)
    context.user_data.pop("grammar_current", None)
    context.user_data.pop("grammar_retry_tense", None)
    stats = db.get_grammar_stats(user.id)
    percent = round(100 * stats["correct"] / stats["total"]) if stats["total"] else 0
    await update.message.reply_text(
        f"🏁 Тренировка окончена. В этот раз: {session['correct']}/{session['total']}.\n"
        f"Всего за всё время: {stats['correct']}/{stats['total']} ({percent}%).",
        reply_markup=MAIN_MENU,
    )


async def check_grammar_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    exercise = context.user_data.get("grammar_current")
    if not exercise:
        await send_grammar_exercise(update.message.reply_text, context)
        return

    given = normalize_answer(update.message.text)
    accepted = {normalize_answer(a) for a in exercise["answers"]}
    correct = given in accepted

    session = context.user_data.setdefault("grammar_session", {"total": 0, "correct": 0})
    session["total"] += 1
    if correct:
        session["correct"] += 1
    db.log_grammar_answer(user_id, exercise["tense"], correct)

    translation = exercise.get("translation", "")
    if correct:
        reply = (
            f"✅ Верно! *{exercise['tense_ru']}*\n"
            f"🇷🇺 {translation}"
        )
        context.user_data.pop("grammar_retry_tense", None)
    else:
        explanation = TENSE_EXPLANATIONS.get(exercise["tense"], "")
        reply = (
            f"❌ Неверно. Правильный ответ: *{exercise['answers'][0]}*\n"
            f"*{exercise['tense_ru']}* — {explanation}\n"
            f"🇷🇺 {translation}"
        )
        context.user_data["grammar_retry_tense"] = exercise["tense"]
    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    await send_grammar_exercise(update.message.reply_text, context)


# ---------- reply-keyboard text buttons ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    mapping = {
        "📚 Словарь": cmd_vocab_menu,
        "⏳ Времена": cmd_grammar,
        "💬 Сленг": cmd_slang_menu,
        "💬 Новое сокращение": cmd_slang,
        "⬅️ Назад": cmd_back_to_main,
        "🏁 Закончить тренировку": cmd_grammar_stop,
        "📖 Новое слово": cmd_word,
        "🔁 Повторить": cmd_review,
        "🧠 Квиз": cmd_quiz,
        "🎚 Уровень": cmd_level,
        "📊 Прогресс": cmd_progress,
        "❓ Помощь": help_cmd,
    }
    handler = mapping.get(text)
    if handler:
        await handler(update, context)
    elif context.user_data.get("grammar_active"):
        await check_grammar_answer(update, context)
    else:
        await update.message.reply_text(
            "Не понял(а) команду. Используй меню ниже или /help.",
            reply_markup=MAIN_MENU,
        )


def ensure_seeded():
    """Собрать базу при первом запуске.

    Нужно для хостинга: файл базы намеренно НЕ хранится в git (иначе передеплой
    затирал бы прогресс), поэтому на новом сервере бот наполняет её сам из
    JSON-файлов. Операция идемпотентная — уже заполненные слова не трогаются,
    прогресс не сбрасывается.
    """
    import seed_db

    db.init_db()
    words_before = db.count_words(only_with_translation=True)
    slang_before = db.count_slang()
    if words_before and slang_before:
        logger.info("База готова: слов %s, сокращений %s", words_before, slang_before)
        return

    logger.info("База пуста или неполная — наполняю из JSON-файлов...")
    seed_db.seed(verbose=False)
    logger.info(
        "Готово: слов %s, сокращений %s",
        db.count_words(only_with_translation=True), db.count_slang(),
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💾 Делаю бэкап базы...")
    ok = await asyncio.to_thread(backup.backup_now, db.DB_PATH)
    if ok:
        await update.message.reply_text("✅ Готово — свежий снимок в ветке backup на GitHub.")
    else:
        await update.message.reply_text(
            "⚠ Бэкап не настроен или не удался. Проверь, что на хостинге заданы "
            "переменные GITHUB_BACKUP_TOKEN и GITHUB_BACKUP_REPO, и посмотри логи."
        )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID or str(update.effective_user.id) != str(ADMIN_ID):
        return
    s = db.get_admin_stats()
    await update.message.reply_text(
        f"📊 Статистика бота\n\n"
        f"Всего пользователей: {s['total_users']}\n"
        f"Активных за {s['active_days']} дн.: {s['active_users']}\n"
        f"Новых сегодня: {s['new_today']}\n\n"
        f"Слов выучено (известно): {s['words_known']}\n"
        f"Слов в процессе: {s['words_learning']}\n"
        f"Ответов на грамматику: {s['grammar_answers']}\n"
        f"Сленга выучено: {s['slang_known']}"
    )


async def _backup_loop():
    """Раз в BACKUP_INTERVAL_SECONDS отправляет снимок базы в GitHub.
    Ошибки не приводят к падению бота — backup.backup_now сама их логирует."""
    await asyncio.sleep(60)  # дать боту спокойно подняться перед первым бэкапом
    while True:
        await asyncio.to_thread(backup.backup_now, db.DB_PATH)
        await asyncio.sleep(BACKUP_INTERVAL_SECONDS)


async def _post_init(app: Application):
    asyncio.create_task(_backup_loop())


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "Не найден BOT_TOKEN. Скопируйте .env.example в .env и вставьте "
            "туда токен, полученный от @BotFather (либо задайте переменную "
            "окружения BOT_TOKEN в панели хостинга)."
        )

    ensure_seeded()

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("word", cmd_word))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("grammar", cmd_grammar))
    app.add_handler(CommandHandler("slang", cmd_slang))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Бот запущен. Останови через Ctrl+C.")
    app.run_polling()


if __name__ == "__main__":
    main()
