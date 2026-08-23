"""
Автобэкап базы данных в отдельную ветку GitHub.

Раз в BACKUP_INTERVAL_HOURS часов бот делает консистентный снимок своей
SQLite-базы (через штатный sqlite3 backup API — безопасно даже если в этот
момент идёт запись) и отправляет его в ветку `backup` репозитория одним
всегда актуальным коммитом (commit --amend + force-push), чтобы история
репозитория не росла бесконечно.

Работает только если заданы переменные окружения:
  GITHUB_BACKUP_TOKEN — GitHub Personal Access Token с правом Contents: Read/Write
                         именно на этот репозиторий
  GITHUB_BACKUP_REPO  — репозиторий вида "владелец/имя", например "EgorVibecop/english_bot"

Если переменные не заданы — автобэкап просто не запускается, остальной
бот работает как обычно.
"""

import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_BRANCH = "backup"


def _clone_dir(db_path: Path) -> Path:
    return db_path.parent / "_backup_clone"


def _run(args, cwd):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True, timeout=60
    )


def _safe_snapshot(db_path: Path, dest_path: Path):
    """Консистентная копия SQLite-базы через встроенный backup API."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_now(db_path: Path, _remote_url: str | None = None) -> bool:
    """Сделать снимок базы и отправить его в ветку backup. Возвращает True при успехе.

    _remote_url — только для тестов (например, локальный bare-репозиторий);
    в обычной работе всегда строится из GITHUB_BACKUP_TOKEN/GITHUB_BACKUP_REPO.
    """
    remote_url = _remote_url
    if remote_url is None:
        token = os.getenv("GITHUB_BACKUP_TOKEN")
        repo = os.getenv("GITHUB_BACKUP_REPO")
        if not token or not repo:
            logger.info("GITHUB_BACKUP_TOKEN/GITHUB_BACKUP_REPO не заданы — автобэкап выключен")
            return False
        remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"

    if not db_path.exists():
        logger.warning("Бэкап пропущен: файл базы %s ещё не создан", db_path)
        return False

    clone_dir = _clone_dir(db_path)

    try:
        if not (clone_dir / ".git").exists():
            clone_dir.mkdir(parents=True, exist_ok=True)
            _run(["git", "init", "-q"], clone_dir)
            _run(["git", "checkout", "-q", "--orphan", BACKUP_BRANCH], clone_dir)
            _run(["git", "config", "user.email", "backup@bot.local"], clone_dir)
            _run(["git", "config", "user.name", "English Bot Backup"], clone_dir)

        snapshot_path = clone_dir / "english_bot.db"
        _safe_snapshot(db_path, snapshot_path)

        _run(["git", "add", "english_bot.db"], clone_dir)

        # если содержимое не изменилось с прошлого раза — коммитить нечего
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=clone_dir, capture_output=True, timeout=30,
        )
        if diff.returncode == 0:
            logger.info("Бэкап: база не изменилась с прошлого снимка, пропускаю")
            return True

        message = f"backup {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
        has_commit = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=clone_dir, capture_output=True, timeout=30,
        ).returncode == 0
        if has_commit:
            _run(["git", "commit", "-q", "--amend", "-m", message], clone_dir)
        else:
            _run(["git", "commit", "-q", "-m", message], clone_dir)

        _run(["git", "push", "-q", "-f", remote_url, f"HEAD:{BACKUP_BRANCH}"], clone_dir)
        logger.info("Бэкап базы отправлен в ветку %s", BACKUP_BRANCH)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("Бэкап не удался: %s\n%s", e, e.stderr)
        return False
    except Exception:
        logger.exception("Бэкап не удался (непредвиденная ошибка)")
        return False
