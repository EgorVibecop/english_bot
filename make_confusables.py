"""
Подбор похоже звучащих слов (минимальных пар) для тренажёра произношения.

Зачем: распознаватель с открытым словарём на одиночных словах ошибается —
записанное «sheep» он слышит как «cheap». Если же дать ему на выбор только
слово-цель и несколько похожих, он выбирает уверенно, а мы заодно видим,
на какое именно слово похоже сказанное. Это и есть полезная обратная связь:
«услышал ship вместо sheep».

Минимальная пара — слова, различающиеся ровно одним звуком: ship/sheep,
think/sink, bad/bed. Считаются по фонемам из CMU Pronouncing Dictionary.

Запускается вручную, результат (confusables.json) уезжает в репозиторий:
    python make_confusables.py
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from make_transcriptions import load_cmudict

BASE_DIR = Path(__file__).parent
OUT = BASE_DIR / "confusables.json"
MAX_PER_WORD = 4

# Пары звуков, которые русскоговорящим даются тяжелее всего: их и подставляем
# в первую очередь, чтобы тренажёр бил по настоящим слабым местам.
TRICKY_PAIRS = [
    {"IH", "IY"},    # ship / sheep
    {"AE", "EH"},    # bad / bed
    {"AH", "AA"},    # cup / cop
    {"UH", "UW"},    # full / fool
    {"TH", "S"},     # think / sink
    {"TH", "T"},     # thin / tin
    {"DH", "Z"},     # they / zay
    {"DH", "D"},     # then / den
    {"V", "W"},      # vest / west
    {"W", "V"},
    {"NG", "N"},     # sing / sin
    {"AE", "AA"},    # cat / cot
    {"ER", "EH"},
]


def strip_stress(phonemes):
    return [re.sub(r"\d", "", p) for p in phonemes]


def one_sound_apart(a, b):
    """Различаются ли записи ровно одним звуком. Возвращает пару звуков."""
    if len(a) == len(b):
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        if len(diff) == 1:
            return diff[0]
        return None
    # разница в один звук: вставка или пропуск
    if abs(len(a) - len(b)) != 1:
        return None
    long_, short = (a, b) if len(a) > len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return (long_[i], "")
    return None


def main():
    cmu = load_cmudict()
    words = json.load(open(BASE_DIR / "words_top1000.json", encoding="utf-8"))
    our = [w["word"] for w in words]

    # Кандидаты в «похожие» — наш словарь плюс оксфордский список: так
    # подсказка остаётся из знакомых слов, а не из редких.
    pool = set(our)
    oxford = BASE_DIR / "oxford_cefr.json"
    if oxford.exists():
        pool |= set(json.load(open(oxford, encoding="utf-8")))
    pool = {w for w in pool if w in cmu and w.isalpha() and len(w) > 1}

    by_length = defaultdict(list)
    for word in pool:
        by_length[len(strip_stress(cmu[word]))].append(word)

    result = {}
    for word in our:
        if word not in cmu:
            continue
        target = strip_stress(cmu[word])
        tricky, plain = [], []
        for length in (len(target) - 1, len(target), len(target) + 1):
            for other in by_length.get(length, ()):
                if other == word:
                    continue
                pair = one_sound_apart(target, strip_stress(cmu[other]))
                if not pair:
                    continue
                entry = {"word": other, "pair": [pair[0], pair[1]]}
                if set(pair) in TRICKY_PAIRS:
                    tricky.append(entry)
                else:
                    plain.append(entry)
        chosen = tricky[:MAX_PER_WORD] + plain[: MAX_PER_WORD - len(tricky[:MAX_PER_WORD])]
        if chosen:
            result[word] = chosen

    json.dump(result, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, sort_keys=True)
    total = sum(len(v) for v in result.values())
    print(f"Слов с похожими: {len(result)} из {len(our)} (всего пар: {total})")
    for sample in ("ship", "sheep", "think", "bad", "cup", "full", "ghost"):
        if sample in result:
            pairs = ", ".join(f"{e['word']}({e['pair'][0]}/{e['pair'][1]})"
                              for e in result[sample])
            print(f"   {sample} <-> {pairs}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
