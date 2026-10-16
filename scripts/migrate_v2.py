import sqlite3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.config import DB_PATH

SCHEMA_VERSION = 2
MODERATION_COLUMNS = (
    ("review_status", "TEXT DEFAULT 'APPROVED'"),
    ("reviewed_by", "TEXT DEFAULT NULL"),
    ("reviewed_at", "TEXT DEFAULT NULL"),
)
V1_NOTE_COLUMNS = {
    "id",
    "event_date",
    "metric_category",
    "content",
    "author_id",
    "importance_type",
    "status",
    "created_at",
    "updated_at",
}


def migrate_database(db_path=DB_PATH):
    """Migrate an existing v1.0 database to the v2.0 moderation schema."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "expert_notes" not in tables:
            raise RuntimeError("Cannot migrate database: expert_notes table is missing")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema version {version} is newer than supported version 2")

        columns = {row[1] for row in conn.execute("PRAGMA table_info(expert_notes)")}
        missing_v1_columns = V1_NOTE_COLUMNS - columns
        if missing_v1_columns:
            missing = ", ".join(sorted(missing_v1_columns))
            raise RuntimeError(f"Cannot migrate expert_notes: required v1 columns are missing: {missing}")

        conn.execute("BEGIN IMMEDIATE")
        for column_name, definition in MODERATION_COLUMNS:
            if column_name not in columns:
                conn.execute(f"ALTER TABLE expert_notes ADD COLUMN {column_name} {definition}")

        conn.execute(
            "UPDATE expert_notes SET review_status = 'APPROVED' WHERE review_status IS NULL"
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    print(f"[+] Database migrated to schema v{SCHEMA_VERSION}: {db_path}")


if __name__ == "__main__":
    migrate_database()
