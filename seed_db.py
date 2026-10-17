"""
Наполнение словаря бота в базе данных.

Работает ПОЛНОСТЬЮ ОФЛАЙН и без тяжёлых зависимостей: все данные лежат
в JSON-файлах рядом, никаких сетевых запросов и словарей NLTK на сервере
не требуется. Благодаря этому бот может собрать себе базу сам при первом
запуске на хостинге (см. ensure_seeded в bot.py).

Файлы-источники:
  words_top1000.json        — список слов: rank (частота), order, level
  core_words.json           — переводы служебных слов (артикли, предлоги...)
  translations_curated.json — переводы остальных слов
  examples.json             — короткая фраза-пример для каждого слова
  pos.json                  — часть речи (заранее посчитана через WordNet)
  slang.json                — отдельный словарь сленговых сокращений

Скрипт идемпотентен: повторный запуск не трогает уже заполненные слова
и не сбрасывает прогресс пользователей. Запуск вручную:  python seed_db.py
"""

import json
import sys
from pathlib import Path

import db

BASE_DIR = Path(__file__).parent

WORDS_FILE = BASE_DIR / "words_top1000.json"
CORE_FILE = BASE_DIR / "core_words.json"
CURATED_FILE = BASE_DIR / "translations_curated.json"
EXAMPLES_FILE = BASE_DIR / "examples.json"
POS_FILE = BASE_DIR / "pos.json"
SLANG_FILE = BASE_DIR / "slang.json"


def _load(path, default=None):
    if not path.exists():
        if default is None:
            raise SystemExit(f"Не найден обязательный файл: {path.name}")
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def seed(verbose=True):
    """Заполняет словарь и сленг. Возвращает число добавленных записей."""
    db.init_db()

    words = _load(WORDS_FILE)
    core = _load(CORE_FILE)
    curated = _load(CURATED_FILE)
    examples = _load(EXAMPLES_FILE, {})
    pos_map = _load(POS_FILE, {})
    slang = _load(SLANG_FILE, {})

    total = len(words)
    added = skipped = 0
    no_translation = []

    for item in words:
        word = item["word"]
        rank = item["rank"]
        order_index = item.get("order", rank)
        level = item.get("level", 1)

        existing = db.get_word_by_word(word)
        if existing is not None and existing["translation"] is not None:
            skipped += 1
            continue

        if word in core:
            translation = core[word]["translation"]
            pos = core[word].get("pos") or pos_map.get(word)
            source = "core"
        elif word in curated:
            translation = curated[word]
            pos = pos_map.get(word)
            source = "curated"
        else:
            no_translation.append(word)
            continue

        db.upsert_word(
            rank, word, translation, examples.get(word), pos, source,
            order_index, level,
        )
        added += 1
        if verbose:
            print(f"[{rank}/{total}] {word} -> {translation}  ({source})")

    slang_added = 0
    for i, (term, entry) in enumerate(slang.items(), start=1):
        existing = db.get_slang_by_term(term)
        if existing is not None:
            continue
        db.upsert_slang(
            i, term, entry["full"], entry["translation"], entry["example"]
        )
        slang_added += 1

    if verbose:
        print("\n--- Готово ---")
        print(f"Слов добавлено: {added}, пропущено (уже были): {skipped}")
        print(f"Слов в базе с переводом: "
              f"{db.count_words(only_with_translation=True)} / {db.count_words()}")
        print(f"Сленговых сокращений добавлено: {slang_added} "
              f"(всего в базе: {db.count_slang()})")
        if no_translation:
            print(f"\n⚠ Нет перевода для {len(no_translation)} слов — "
                  f"добавьте их в translations_curated.json:")
            print(", ".join(no_translation[:50]))

    return added + slang_added


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    seed(verbose=True)
