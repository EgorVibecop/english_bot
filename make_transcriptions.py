"""
Генерация русской практической транскрипции для словаря бота.

Запускается вручную на своём компьютере, результат кладётся в
transcriptions.json и уезжает в репозиторий. Боту на сервере ни сеть,
ни этот скрипт не нужны.

Почему не по буквам: английское написание не говорит о звучании —
ghost по буквам дало бы «гхост», а звучит «гоуст». Поэтому берём
произношение из CMU Pronouncing Dictionary (фонемы ARPAbet) и
переводим фонемы в русские буквы.

    python make_transcriptions.py
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent
CMUDICT_URL = "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict"
CACHE = BASE_DIR / "cmudict.cache"
OUT = BASE_DIR / "transcriptions.json"

# Согласные: фонема -> русские буквы.
CONSONANTS = {
    "B": "б", "CH": "ч", "D": "д", "DH": "з", "F": "ф", "G": "г",
    "HH": "х", "JH": "дж", "K": "к", "L": "л", "M": "м", "N": "н",
    "NG": "нг", "P": "п", "R": "р", "S": "с", "SH": "ш", "T": "т",
    "TH": "с", "V": "в", "W": "у", "Y": "й", "Z": "з", "ZH": "ж",
}

# Гласные. Часть зависит от ударения, поэтому храним пару:
# (безударный вариант, ударный вариант).
VOWELS = {
    "AA": ("а", "а"),
    "AE": ("э", "э"),
    "AH": ("э", "а"),      # безударная - шва: about -> эбаут; cup -> кап
    "AO": ("о", "о"),
    "AW": ("ау", "ау"),
    "AY": ("ай", "ай"),
    "EH": ("э", "э"),
    "ER": ("эр", "ёр"),    # water -> уотэр, bird -> бёрд
    "EY": ("эй", "эй"),
    "IH": ("и", "и"),
    "IY": ("и", "и"),
    "OW": ("оу", "оу"),
    "OY": ("ой", "ой"),
    "UH": ("у", "у"),
    "UW": ("у", "у"),
}

# После согласной эти сочетания звучат мягко: few -> фью, а не фйу.
SOFT_AFTER_CONSONANT = {"у": "ю", "а": "я", "о": "ё", "э": "е"}

CONSONANT_LETTERS = set("бвгдджзйклмнпрстфхцчшщ")


def load_cmudict():
    if not CACHE.exists():
        print("Скачиваю CMU Pronouncing Dictionary...")
        with urllib.request.urlopen(CMUDICT_URL, timeout=120) as resp:
            CACHE.write_bytes(resp.read())
    out = {}
    for line in CACHE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        word = parts[0]
        # варианты произношения помечены как word(2) - берём основной
        if word.endswith(")"):
            continue
        out.setdefault(word.lower(), parts[1:])
    return out


def to_russian(phonemes):
    """ARPAbet -> русская практическая транскрипция."""
    out = []
    i = 0
    while i < len(phonemes):
        raw = phonemes[i]
        base = re.sub(r"\d", "", raw)
        stressed = raw.endswith(("1", "2"))

        if base == "Y" and i + 1 < len(phonemes):
            # Y перед гласной: после согласной даёт мягкость (few -> фью),
            # в начале слова - «ю» перед U (usually -> южуэли) либо «й».
            nxt = re.sub(r"\d", "", phonemes[i + 1])
            if nxt in VOWELS:
                unstressed_v, stressed_v = VOWELS[nxt]
                letter = stressed_v if phonemes[i + 1].endswith(("1", "2")) else unstressed_v
                soft = SOFT_AFTER_CONSONANT.get(letter)
                after_consonant = bool(out) and out[-1][-1] in CONSONANT_LETTERS
                if soft and after_consonant:
                    out.append("ь" + soft)          # фью, бьюти
                elif soft and not out and letter == "у":
                    out.append(soft)                # в начале слова: usually -> южэуэли
                elif soft and out:
                    out.append(soft)
                else:
                    out.append("й" + letter)
                i += 2
                continue

        if base == "ER" and out and out[-1] == "у":
            # После W звук звучит как «ор»: world -> уорлд, а не уёрлд.
            out.append("ор")
            i += 1
            continue

        if base in VOWELS:
            out.append(VOWELS[base][1] if stressed else VOWELS[base][0])
        elif base in CONSONANTS:
            out.append(CONSONANTS[base])
        i += 1

    text = "".join(out)
    # Косметика: убираем нечитаемые стыки.
    text = text.replace("нгк", "нк").replace("нгг", "нг")  # think -> синк
    text = re.sub(r"([бвгдзклмнпрстфхцчшж])й([аоуэ])", r"\1ь\2", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return text


def main():
    cmu = load_cmudict()
    words = json.load(open(BASE_DIR / "words_top1000.json", encoding="utf-8"))
    needed = [item["word"] for item in words]

    # Слова-подсказки из тренажёра произношения тоже нуждаются в транскрипции:
    # в ответе показываем, что услышали, и как это звучит.
    confusables = BASE_DIR / "confusables.json"
    if confusables.exists():
        extra = json.load(open(confusables, encoding="utf-8"))
        seen = set(needed)
        for similar in extra.values():
            for entry in similar:
                w = entry["word"] if isinstance(entry, dict) else entry
                if w not in seen:
                    seen.add(w)
                    needed.append(w)

    result, missing = {}, []
    for word in needed:
        phonemes = cmu.get(word)
        if not phonemes:
            missing.append(word)
            continue
        result[word] = to_russian(phonemes)

    json.dump(result, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, sort_keys=True)

    print(f"Готово: транскрипция для {len(result)} слов из {len(needed)}")
    if missing:
        print(f"Нет произношения для {len(missing)} слов: {', '.join(missing[:20])}")
    return missing


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
