"""
Бонус-скрипт: пересобрать words_top1000.json с другим размером словаря
(например, топ-3000 или топ-5000 самых частотных английских слов),
если захочешь расширить бота позже.

Сохраняет две вещи, настроенные вручную поверх чистой частоты:
  - "i", "love", "katya" всегда идут первыми тремя словами;
  - остальные слова перемешаны (поле "order"), чтобы в боте не шли подряд
    одни предлоги/артикли — "rank" при этом хранит настоящую частоту
    (пригодится для уровней/фильтров в будущем).

Использование:
    python generate_wordlist.py 3000
    python seed_db.py            # затем доучить переводы для новых слов
"""

import json
import random
import re
import sys

from wordfreq import top_n_list

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
PINNED = ["i", "love", "katya"]

raw = top_n_list("en", N * 3)  # с запасом, т.к. часть слов отфильтруется
clean = [w for w in raw if (re.fullmatch(r"[a-z]+", w) and len(w) > 1) or w in ("a", "i")]
clean = [w for w in clean if w not in PINNED]
top = clean[: N - 1]  # -1, т.к. "katya" не из wordfreq и добавляется отдельно

words = [{"rank": i + 1, "word": w} for i, w in enumerate(top)]
words.append({"rank": len(words) + 1, "word": "katya"})  # не настоящее англ. слово, персональное

pinned_items = [w for w in words if w["word"] in PINNED]
rest_items = [w for w in words if w["word"] not in PINNED]

random.seed(42)
random.shuffle(rest_items)

order = 1
for w in sorted(pinned_items, key=lambda w: PINNED.index(w["word"])):
    w["order"] = order
    order += 1
for w in rest_items:
    w["order"] = order
    order += 1

words.sort(key=lambda w: w["order"])

with open("words_top1000.json", "w", encoding="utf-8") as f:
    json.dump(words, f, ensure_ascii=False, indent=2)

print(f"Сохранено {len(words)} слов в words_top1000.json (i/love/katya закреплены первыми, остальное перемешано)")
print("Теперь запусти: python seed_db.py  (он доучит только новые слова)")
