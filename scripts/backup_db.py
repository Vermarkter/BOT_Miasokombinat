from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_settings

logger = logging.getLogger("backup_db")


def _resolve_sqlite_path(database_url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("DATABASE_URL must be sqlite+aiosqlite:///...")

    raw_path = database_url[len(prefix) :]
    if raw_path.startswith("./"):
        return Path(raw_path[2:])
    if raw_path.startswith("/"):
        return Path(raw_path)
    return Path(raw_path)


def _cleanup_old_backups(backup_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for file_path in backup_dir.glob("meat_bot_*.db"):
        try:
            modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                file_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to cleanup backup file: %s", file_path)


def _run_backup(source_db: Path, target_db: Path) -> None:
    # SQLite backup API provides a safe snapshot while the app is running.
    with sqlite3.connect(source_db) as source_conn:
        with sqlite3.connect(target_db) as backup_conn:
            source_conn.backup(backup_conn)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    settings = get_settings()
    source_db = _resolve_sqlite_path(settings.database_url).resolve()

    if not source_db.exists():
        raise FileNotFoundError(f"Database file not found: {source_db}")

    backup_dir = Path(settings.backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_db = backup_dir / f"meat_bot_{timestamp}.db"

    _run_backup(source_db, target_db)
    _cleanup_old_backups(backup_dir, settings.backup_retention_days)
    logger.info("Backup completed: %s", target_db)


if __name__ == "__main__":
    main()
