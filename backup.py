"""
Автобэкап базы данных в отдельную ветку GitHub — через REST API,
без зависимости от установленного бинарника git (на некоторых хостингах,
например при базовом образе python:3.11-slim, git внутри контейнера просто
нет, а тащить его отдельно ради этого не хочется).

Раз в BACKUP_INTERVAL_HOURS часов бот делает консистентный снимок своей
SQLite-базы (через штатный sqlite3 backup API — безопасно даже если в этот
момент идёт запись) и отправляет его в ветку `backup` репозитория через
GitHub Contents API. Если содержимое не изменилось с прошлого раза —
новый коммит не создаётся.

Работает только если заданы переменные окружения:
  GITHUB_BACKUP_TOKEN — GitHub Personal Access Token с правом Contents: Read/Write
                         именно на этот репозиторий
  GITHUB_BACKUP_REPO  — репозиторий вида "владелец/имя", например "EgorVibecop/english_bot"

Если переменные не заданы — автобэкап просто не запускается, остальной
бот работает как обычно.
"""

import base64
import json
import logging
import os
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_BRANCH = "backup"
BACKUP_PATH_IN_REPO = "english_bot.db"
API_BASE = "https://api.github.com"
TIMEOUT = 20


def _safe_snapshot_bytes(db_path: Path) -> bytes:
    """Консистентная копия SQLite-базы через встроенный backup API."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return dest.read_bytes()


def _api_request(method: str, path: str, token: str, data=None, _base=None):
    url = f"{_base or API_BASE}{path}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "english-bot-backup",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        return e.code, payload


def _ensure_branch(token: str, repo: str, _base=None) -> bool:
    status, _ = _api_request("GET", f"/repos/{repo}/branches/{BACKUP_BRANCH}", token, _base=_base)
    if status == 200:
        return True

    status, data = _api_request("GET", f"/repos/{repo}/git/ref/heads/main", token, _base=_base)
    if status != 200:
        logger.error("Бэкап: не удалось получить SHA ветки main: %s %s", status, data)
        return False
    sha = data["object"]["sha"]

    status, data = _api_request(
        "POST", f"/repos/{repo}/git/refs", token,
        {"ref": f"refs/heads/{BACKUP_BRANCH}", "sha": sha}, _base=_base,
    )
    if status not in (200, 201):
        logger.error("Бэкап: не удалось создать ветку %s: %s %s", BACKUP_BRANCH, status, data)
        return False
    logger.info("Бэкап: создана ветка %s", BACKUP_BRANCH)
    return True


def backup_now(db_path: Path, _api_base: str | None = None) -> bool:
    """Сделать снимок базы и отправить его в ветку backup. Возвращает True при успехе.

    _api_base — только для тестов (локальный фейковый сервер вместо api.github.com).
    """
    token = os.getenv("GITHUB_BACKUP_TOKEN")
    repo = os.getenv("GITHUB_BACKUP_REPO")
    if not token or not repo:
        logger.info("GITHUB_BACKUP_TOKEN/GITHUB_BACKUP_REPO не заданы — автобэкап выключен")
        return False

    if not db_path.exists():
        logger.warning("Бэкап пропущен: файл базы %s ещё не создан", db_path)
        return False

    try:
        if not _ensure_branch(token, repo, _base=_api_base):
            return False

        snapshot = _safe_snapshot_bytes(db_path)
        content_b64 = base64.b64encode(snapshot).decode("ascii")

        status, existing = _api_request(
            "GET",
            f"/repos/{repo}/contents/{BACKUP_PATH_IN_REPO}?ref={BACKUP_BRANCH}",
            token,
            _base=_api_base,
        )
        sha = None
        if status == 200 and existing:
            sha = existing.get("sha")
            remote_content = existing.get("content")
            if remote_content and base64.b64decode(remote_content) == snapshot:
                logger.info("Бэкап: база не изменилась с прошлого снимка, пропускаю")
                return True

        payload = {
            "message": f"backup {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "content": content_b64,
            "branch": BACKUP_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        status, data = _api_request(
            "PUT", f"/repos/{repo}/contents/{BACKUP_PATH_IN_REPO}", token, payload, _base=_api_base,
        )
        if status in (200, 201):
            logger.info("Бэкап базы отправлен в ветку %s", BACKUP_BRANCH)
            return True
        logger.error("Бэкап не удался: %s %s", status, data)
        return False

    except Exception:
        logger.exception("Бэкап не удался (непредвиденная ошибка)")
        return False
