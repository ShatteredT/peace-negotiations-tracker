import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _check_integrity(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise sqlite3.DatabaseError(f"SQLite integrity check failed for {db_path}: {result}")


def _copy_sqlite_database(source_path: Path, destination_path: Path) -> None:
    _check_integrity(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        with closing(sqlite3.connect(source_path)) as source:
            with closing(sqlite3.connect(temp_path)) as destination:
                source.backup(destination)
        _check_integrity(temp_path)
        os.replace(temp_path, destination_path)
    finally:
        temp_path.unlink(missing_ok=True)


def create_database_backup(db_path: Path, backup_dir: Path | None = None) -> Path:
    """Create a verified SQLite backup before the UI starts."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")

    if backup_dir is None:
        backup_dir = db_path.parent / "backups"
    backup_dir = Path(backup_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"{db_path.stem}_before_ui_{timestamp}.db"
    _copy_sqlite_database(db_path, backup_path)
    return backup_path


def restore_database_from_backup(backup_path: Path, db_path: Path) -> None:
    """Restore a SQLite database atomically from a verified backup."""
    backup_path = Path(backup_path)
    db_path = Path(db_path)
    if not backup_path.is_file():
        raise FileNotFoundError(f"Database backup does not exist: {backup_path}")
    if backup_path.resolve() == db_path.resolve():
        raise ValueError("Database backup and restore target must be different files")
    _copy_sqlite_database(backup_path, db_path)


def prepare_demo_database(seed_path: Path, runtime_dir: Path | None = None) -> Path:
    """Copy the tracked read-only seed into a disposable writable database."""
    seed_path = Path(seed_path)
    if not seed_path.is_file():
        raise FileNotFoundError(f"Demo database seed does not exist: {seed_path}")

    if runtime_dir is None:
        runtime_dir = Path(tempfile.mkdtemp(prefix="peacetalk-demo-"))
    else:
        runtime_dir = Path(runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)

    runtime_path = runtime_dir / "peacetalk.db"
    shutil.copy2(seed_path, runtime_path)
    create_database_backup(runtime_path, runtime_dir / "backups")
    return runtime_path
