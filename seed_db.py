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
  idioms.json               — идиомы и устойчивые выражения

Скрипт идемпотентен: повторный запуск не трогает уже заполненные слова
и не сбрасывает прогресс пользователей. Слова, убранные из JSON, при этом
удаляются и из базы (prune_words) — иначе они остались бы там навсегда.
Запуск вручную:  python seed_db.py
"""

import hashlib
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
IDIOMS_FILE = BASE_DIR / "idioms.json"


def _load(path, default=None):
    if not path.exists():
        if default is None:
            raise SystemExit(f"Не найден обязательный файл: {path.name}")
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CONTENT_FINGERPRINT_KEY = "content_fingerprint"


def _fingerprint():
    """Отпечаток всех файлов-словарей: если не менялись, пересобирать нечего."""
    h = hashlib.sha256()
    for path in (WORDS_FILE, CORE_FILE, CURATED_FILE, EXAMPLES_FILE,
                 POS_FILE, SLANG_FILE, IDIOMS_FILE):
        h.update(path.read_bytes() if path.exists() else b"")
    return h.hexdigest()


def seed(verbose=True, force=False):
    """Заполняет словарь, сленг и идиомы. Возвращает число записей.

    Если содержимое JSON-файлов не менялось с прошлого запуска, работа
    пропускается: иначе бот на каждом старте перезаписывал бы весь словарь.
    """
    db.init_db()

    fingerprint = _fingerprint()
    if not force and db.get_meta(CONTENT_FINGERPRINT_KEY) == fingerprint:
        if verbose:
            print("Словари не менялись — база уже актуальна.")
        return 0

    words = _load(WORDS_FILE)
    core = _load(CORE_FILE)
    curated = _load(CURATED_FILE)
    examples = _load(EXAMPLES_FILE, {})
    pos_map = _load(POS_FILE, {})
    slang = _load(SLANG_FILE, {})
    idioms = _load(IDIOMS_FILE, {})

    # Слова, убранные из словаря (например формы одного и того же слова),
    # надо удалить и из базы: seed только добавляет, сам он их не уберёт.
    pruned = db.prune_words({item["word"] for item in words})

    total = len(words)
    no_translation = []
    rows = []

    for item in words:
        word = item["word"]
        rank = item["rank"]
        order_index = item.get("order", rank)
        level = item.get("level", 1)

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

        rows.append(
            (rank, word, translation, examples.get(word), pos, source,
             order_index, level)
        )

    # Пишем весь словарь разом: нумерация при обновлении меняется, и построчная
    # вставка натыкалась бы на UNIQUE-констрейнт ещё занятых номеров.
    added = db.bulk_upsert_words(rows)

    slang_added = 0
    for i, (term, entry) in enumerate(slang.items(), start=1):
        db.upsert_slang(
            i, term, entry["full"], entry["translation"], entry["example"]
        )
        slang_added += 1

    idioms_added = 0
    for i, (phrase, entry) in enumerate(idioms.items(), start=1):
        db.upsert_idiom(
            i, phrase, entry["translation"], entry.get("literal"), entry["example"]
        )
        idioms_added += 1

    db.set_meta(CONTENT_FINGERPRINT_KEY, fingerprint)

    if verbose:
        print("\n--- Готово ---")
        print(f"Слов записано: {added}")
        if pruned:
            print(f"Удалено устаревших слов из базы: {pruned}")
        print(f"Слов в базе с переводом: "
              f"{db.count_words(only_with_translation=True)} / {db.count_words()}")
        print(f"Сленговых сокращений в базе: {db.count_slang()}")
        print(f"Идиом в базе: {db.count_idioms()}")
        if no_translation:
            print(f"\n⚠ Нет перевода для {len(no_translation)} слов — "
                  f"добавьте их в translations_curated.json:")
            print(", ".join(no_translation[:50]))

    return added + slang_added + idioms_added


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    seed(verbose=True)
