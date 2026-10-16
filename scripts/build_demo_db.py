import argparse
import sqlite3
from pathlib import Path

from scripts.init_db import SCHEMA_VERSION
from scripts.migrate_v2 import migrate_database

BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DB = BASE_DIR / "data" / "peacetalk.db"
DEMO_DB = BASE_DIR / "data" / "demo" / "peacetalk_demo.db"


def build_demo_database(source_path=SOURCE_DB, output_path=DEMO_DB, overwrite=False):
    source_path = Path(source_path)
    output_path = Path(output_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if source_path.resolve() == output_path.resolve():
        raise ValueError("Source and output database paths must be different")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing demo database: {output_path}")
    if output_path.exists():
        output_path.unlink()

    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(output_path) as demo:
            source.backup(demo)
        with sqlite3.connect(output_path) as demo:
            version = demo.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            migrate_database(output_path)
        elif version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Expected database schema v{SCHEMA_VERSION}, found v{version}"
            )

        with sqlite3.connect(output_path) as demo:
            tables = {
                row[0]
                for row in demo.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            required_tables = {
                "news_articles",
                "assessment_records",
                "expert_notes",
                "period_summaries",
                "summary_annotations",
            }
            missing = required_tables - tables
            if missing:
                raise RuntimeError(f"Source database is missing tables: {sorted(missing)}")

            demo.execute("UPDATE news_articles SET text = NULL")
            result = demo.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"Demo database integrity check failed: {result}")
            remaining_text = demo.execute(
                "SELECT COUNT(*) FROM news_articles WHERE text IS NOT NULL"
            ).fetchone()[0]
            if remaining_text:
                raise RuntimeError(
                    f"Expected article text to be removed; found {remaining_text} rows"
                )
    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise

    print(f"Demo database created: {output_path}")
    print(f"Size: {output_path.stat().st_size} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a sanitized demo SQLite snapshot.")
    parser.add_argument("--source", type=Path, default=SOURCE_DB)
    parser.add_argument("--output", type=Path, default=DEMO_DB)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the exact output file if it already exists.",
    )
    args = parser.parse_args()
    build_demo_database(args.source, args.output, overwrite=args.overwrite)
