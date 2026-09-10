"""
Проверка произношения: пользователь наговаривает слово, бот слушает.

Как устроено:
  1. Голосовое из Telegram приходит в OGG/Opus. Раскодируем его через
     soundfile (libsndfile умеет Opus) — системный ffmpeg не нужен, а в
     контейнере python:3.11-slim его и нет.
  2. Приводим к 16 кГц моно — этого ждёт распознаватель.
  3. Узнаём слово моделью Vosk, но не со всем словарём, а только из
     нескольких вариантов: само слово плюс похоже звучащие (ship/sheep).
     Со свободным словарём малая модель на одиночных словах путается —
     записанное «sheep» слышит как «cheap». С ограничением выбирает верно,
     и сразу видно, на что похоже сказанное.

Модель (~40 МБ) скачивается при первом обращении в DATA_DIR и остаётся
там. Пока её нет, раздел просто сообщает, что готовится.

Память: модель занимает около 150 МБ и грузится один раз, лениво — если
разделом не пользуются, она не занимает ничего.
"""

import io
import json
import logging
import os
import threading
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_NAME = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000

# Порог уверенности: ниже — считаем, что слово узнано, но звучит нечётко.
CONFIDENT = 0.75

DATA_DIR = Path(os.getenv("DATA_DIR") or os.getenv("VOSK_DIR")
                or (Path(__file__).parent / "models"))

_model = None
_model_lock = threading.Lock()
_download_started = False


def deps_ok():
    """Установлены ли библиотеки для распознавания."""
    try:
        import soundfile  # noqa: F401
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False


def model_path():
    return DATA_DIR / MODEL_NAME


def model_ready():
    return model_path().exists()


def download_model():
    """Скачать и распаковать модель. Возвращает True, если она на месте."""
    global _download_started
    if model_ready():
        return True
    with _model_lock:
        if model_ready():
            return True
        if _download_started:
            return False
        _download_started = True
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Скачиваю модель распознавания речи (~40 МБ)...")
        with urllib.request.urlopen(MODEL_URL, timeout=300) as resp:
            payload = resp.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(DATA_DIR)
        logger.info("Модель распознавания готова: %s", model_path())
        return model_ready()
    except Exception:
        logger.exception("Не удалось скачать модель распознавания")
        return False
    finally:
        globals()["_download_started"] = False


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            import vosk
            vosk.SetLogLevel(-1)
            _model = vosk.Model(str(model_path()))
            logger.info("Модель распознавания загружена в память")
    return _model


def _resample(samples, src_rate, dst_rate):
    import numpy as np
    if src_rate == dst_rate:
        return samples
    count = int(len(samples) * dst_rate / src_rate)
    if count <= 0:
        return samples
    return np.interp(
        np.linspace(0, len(samples), count, endpoint=False),
        np.arange(len(samples)),
        samples,
    )


def decode_voice(raw):
    """OGG/Opus из Telegram -> PCM 16 бит, 16 кГц, моно."""
    import numpy as np
    import soundfile as sf

    data, rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    mono = _resample(mono, rate, SAMPLE_RATE)
    duration = len(mono) / SAMPLE_RATE
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    return (mono * 32767).astype("int16").tobytes(), duration, peak


def check(raw, target, confusables=()):
    """Разобрать голосовое и сравнить с целевым словом.

    Возвращает словарь:
      status  — ok / unclear / wrong / not_heard / too_quiet / too_short / error
      heard   — что услышали
      conf    — уверенность распознавателя (0..1)
    """
    target = (target or "").strip().lower()
    try:
        pcm, duration, peak = decode_voice(raw)
    except Exception:
        logger.exception("Не удалось раскодировать голосовое")
        return {"status": "error", "heard": "", "conf": 0.0}

    if duration < 0.25:
        return {"status": "too_short", "heard": "", "conf": 0.0}
    if peak < 0.02:
        return {"status": "too_quiet", "heard": "", "conf": 0.0}

    try:
        import vosk
        options = [target] + [w for w in confusables if w != target]
        grammar = json.dumps(options + ["[unk]"])
        rec = vosk.KaldiRecognizer(_get_model(), SAMPLE_RATE, grammar)
        rec.SetWords(True)
        rec.AcceptWaveform(pcm)
        result = json.loads(rec.FinalResult())
    except Exception:
        logger.exception("Распознавание не удалось")
        return {"status": "error", "heard": "", "conf": 0.0}

    words = [w for w in result.get("result", []) if w.get("word") != "[unk]"]
    if not words:
        return {"status": "not_heard", "heard": "", "conf": 0.0}

    # Берём самое уверенное слово: люди часто прихватывают лишний звук.
    best = max(words, key=lambda w: w.get("conf", 0.0))
    heard, conf = best.get("word", ""), float(best.get("conf", 0.0))

    if heard != target:
        return {"status": "wrong", "heard": heard, "conf": conf}
    if conf < CONFIDENT:
        return {"status": "unclear", "heard": heard, "conf": conf}
    return {"status": "ok", "heard": heard, "conf": conf}


# Подсказки по звукам: что именно не так, если услышали другое слово.
# Ключ — пара фонем ARPAbet, которыми слова различаются.
SOUND_HINTS = {
    ("IY", "IH"): "в целевом слове звук долгий и напряжённый, у тебя вышел короткий",
    ("IH", "IY"): "здесь звук короткий и расслабленный, у тебя вышел долгий",
    ("UW", "UH"): "нужен долгий «у», у тебя короткий",
    ("UH", "UW"): "нужен короткий «у», у тебя долгий",
    ("AE", "EH"): "нужен широкий звук между «э» и «а», у тебя вышло чистое «э»",
    ("EH", "AE"): "нужно чистое «э», у тебя вышло ближе к «а»",
    ("AA", "AH"): "нужен открытый «а», у тебя получился короткий",
    ("AH", "AA"): "нужен короткий звук, у тебя вышел слишком открытый «а»",
    ("TH", "S"): "нужен межзубный звук: кончик языка между зубами, а не «с»",
    ("S", "TH"): "здесь обычный «с», а не межзубный",
    ("TH", "T"): "нужен межзубный звук, а не «т»",
    ("DH", "Z"): "нужен звонкий межзубный, кончик языка между зубами, а не «з»",
    ("DH", "D"): "нужен звонкий межзубный, а не «д»",
    ("V", "W"): "нужен «в» — верхние зубы касаются нижней губы",
    ("W", "V"): "нужен губной «у», зубы не участвуют",
    ("NG", "N"): "нужен носовой «нг» задней частью языка, а не обычный «н»",
    ("N", "NG"): "нужен обычный «н», без носового «нг» в конце",
    ("AE", "AA"): "нужен звук между «э» и «а», у тебя вышло чистое «а»",
    ("AA", "AE"): "нужен открытый «а», у тебя вышло ближе к «э»",
}


def sound_hint(target_phoneme, heard_phoneme):
    return SOUND_HINTS.get((target_phoneme, heard_phoneme))
