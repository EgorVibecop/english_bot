"""
Скрипт первоначального заполнения словаря.

Запускается ОДИН раз (или повторно, если что-то не догрузилось — он
пропускает уже готовые слова, так что безопасно перезапускать).

Источники данных (по приоритету):
  1. core_words.json           — вручную проверенные переводы для ~220 самых
                                  частотных служебных слов (артикли, предлоги,
                                  местоимения и т.п. — их плохо переводит
                                  любой автопереводчик).
  2. translations_curated.json — вручную проверенные переводы для остальных
                                  слов словаря (весь топ-1000+).
  3. dictionaryapi.dev + MyMemory — резервный вариант ТОЛЬКО для слов,
                                  которых нет ни в одном из файлов выше
                                  (например, если словарь расширили дальше
                                  через generate_wordlist.py). Качество
                                  перевода у бесплатных API нестабильно,
                                  поэтому такие слова помечаются
                                  source="api_needs_review", и в конце скрипт
                                  печатает их список — стоит проверить и,
                                  если нужно, поправить вручную в
                                  translations_curated.json.

Пример употребления слова (короткая фраза 3-8 слов) берётся из examples.json
для ВСЕХ слов словаря — вручную составленный список, без внешних API.

Часть речи (pos) определяется через WordNet — офлайн-словарь английского
(без сетевых запросов). WordNet требует одноразовой загрузки данных (~10 МБ)
— скрипт скачает их сам при первом запуске, если их ещё нет.
"""

import json
import time
import sys
import urllib.parse
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import db

WORDS_FILE = "words_top1000.json"
CORE_FILE = "core_words.json"
CURATED_FILE = "translations_curated.json"
EXAMPLES_FILE = "examples.json"

DICT_API = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"
TRANSLATE_API = "https://api.mymemory.translated.net/get"

REQUEST_DELAY = 0.4  # пауза между запросами к резервным API


def ensure_wordnet():
    import nltk
    for pkg in ("corpora/wordnet", "corpora/omw-1.4"):
        try:
            nltk.data.find(pkg)
        except LookupError:
            nltk.download(pkg.split("/")[1])


# Частые слова топ-1000, оканчивающиеся на -s, которые в реальном
# употреблении почти всегда глагол (says, works...), а не мн. число
# существительного. Для остальных слов на -s эвристика "это глагол"
# отключена: она ошибочно путала множественное число (parents, terms,
# results, points, schools...) с редкой глагольной формой того же корня,
# которая формально существует в WordNet, но почти никогда не имеется в виду.
VERB_S_WHITELIST = {
    "comes", "gets", "gives", "goes", "knows", "looks", "makes", "means",
    "needs", "says", "seems", "takes", "wants", "works", "includes",
}

POS_MAP = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


def wordnet_pos(word):
    """Часть речи через WordNet (см. VERB_S_WHITELIST про эвристику для -ing/-ed/-s)."""
    from nltk.corpus import wordnet as wn

    is_ing_ed = word.endswith("ing") or word.endswith("ed")
    is_whitelisted_s = word.endswith("s") and word in VERB_S_WHITELIST

    if is_ing_ed or is_whitelisted_s:
        verb_lemma = wn.morphy(word, wn.VERB)
        if verb_lemma and verb_lemma != word:
            if wn.synsets(verb_lemma, pos=wn.VERB):
                return "verb"

    syns = wn.synsets(word)
    return POS_MAP.get(syns[0].pos(), syns[0].pos()) if syns else None


def fetch_definition(word):
    """Резервный вариант через dictionaryapi.dev, для слов вне словарей."""
    try:
        r = requests.get(DICT_API.format(urllib.parse.quote(word)), timeout=10)
        if r.status_code != 200:
            return None, None
        data = r.json()
        meaning = data[0]["meanings"][0]
        pos = meaning.get("partOfSpeech")
        definition = meaning["definitions"][0]["definition"]
        return definition, pos
    except Exception:
        return None, None


def fetch_translation(word):
    """Резервный вариант через MyMemory, для слов вне словарей."""
    try:
        r = requests.get(
            TRANSLATE_API, params={"q": word, "langpair": "en|ru"}, timeout=10
        )
        if r.status_code != 200:
            return None
        data = r.json()
        translated = data.get("responseData", {}).get("translatedText")
        if not translated or translated.strip().lower() == word.lower():
            return None
        return translated.strip().lower()
    except Exception:
        return None


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    ensure_wordnet()
    db.init_db()

    with open(WORDS_FILE, encoding="utf-8") as f:
        words = json.load(f)
    with open(CORE_FILE, encoding="utf-8") as f:
        core = json.load(f)
    with open(CURATED_FILE, encoding="utf-8") as f:
        curated = json.load(f)
    with open(EXAMPLES_FILE, encoding="utf-8") as f:
        examples = json.load(f)

    if limit:
        words = words[:limit]

    total = len(words)
    done = 0
    skipped = 0
    needs_review = []

    for item in words:
        rank, word = item["rank"], item["word"]
        order_index = item.get("order", rank)
        level = item.get("level", 1)

        existing = db.get_word_by_word(word)
        if existing is not None and existing["translation"] is not None:
            skipped += 1
            continue

        example = examples.get(word)

        if word in core:
            entry = core[word]
            db.upsert_word(
                rank, word, entry["translation"], example,
                entry["pos"], "core", order_index, level,
            )
            done += 1
            print(f"[{rank}/{total}] {word} -> {entry['translation']}  (core)")
            continue

        if word in curated:
            translation = curated[word]
            pos = wordnet_pos(word)
            db.upsert_word(rank, word, translation, example, pos, "curated", order_index, level)
            done += 1
            print(f"[{rank}/{total}] {word} -> {translation}  (curated)")
            continue

        # слово за пределами словаря (расширение через generate_wordlist.py) — резервные API
        fallback_definition, pos = fetch_definition(word)
        time.sleep(REQUEST_DELAY)
        translation = fetch_translation(word)
        time.sleep(REQUEST_DELAY)

        source = "api_needs_review" if translation else "api"
        if translation:
            needs_review.append(word)
        db.upsert_word(
            rank, word, translation, example or fallback_definition,
            pos, source, order_index, level,
        )
        done += 1
        status = translation if translation else "⚠ перевод не получен"
        print(f"[{rank}/{total}] {word} -> {status}  (api, требует проверки)")

    print("\n--- Готово ---")
    print(f"Добавлено/обновлено: {done}")
    print(f"Уже было готово (пропущено): {skipped}")
    print(f"Слов в базе с переводом: {db.count_words(only_with_translation=True)} / {db.count_words()}")
    if needs_review:
        print(f"\n⚠ {len(needs_review)} слов переведены автоматическим API (не вручную) "
              f"— стоит перепроверить их перевод в боте и при желании поправить "
              f"в translations_curated.json:")
        print(", ".join(needs_review[:50]) + (" ..." if len(needs_review) > 50 else ""))


if __name__ == "__main__":
    main()
