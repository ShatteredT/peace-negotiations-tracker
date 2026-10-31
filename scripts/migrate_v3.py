import sqlite3
from pathlib import Path

from scripts.migrate_v2 import migrate_database as migrate_v2

SCHEMA_VERSION = 3


def _period_sources(conn: sqlite3.Connection, period: str) -> list[str]:
    year, half = period.split("-H")
    start, end = (f"{year}-01-01", f"{year}-06-30") if half == "1" else (f"{year}-07-01", f"{year}-12-31")
    return [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT source FROM news_articles WHERE date BETWEEN ? AND ? ORDER BY source",
            (start, end),
        )
    ]


def _migrate_summaries(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'period_summaries'"
    ).fetchone():
        return

    columns = {row[1] for row in conn.execute("PRAGMA table_info(period_summaries)")}
    if "source" in columns:
        rows = conn.execute(
            """SELECT id, source, period, trend, summary, articles_count, avg_sentiment, model, created_at
               FROM period_summaries ORDER BY id"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, period, trend, summary, articles_count, avg_sentiment, model, created_at
               FROM period_summaries ORDER BY id"""
        ).fetchall()
    conn.execute("DROP TABLE IF EXISTS period_summaries_v3")
    conn.execute(
        """CREATE TABLE period_summaries_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            period TEXT NOT NULL,
            trend TEXT NOT NULL,
            summary TEXT NOT NULL,
            articles_count INTEGER NOT NULL,
            avg_sentiment REAL NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source, period)
        )"""
    )
    for row in rows:
        if "source" in columns:
            summary_id, source, period, *summary_fields = row
        else:
            summary_id, period, *summary_fields = row
            source = None
        if not source:
            sources = _period_sources(conn, period)
            source = sources[0] if len(sources) == 1 else "all"
        conn.execute(
            """INSERT INTO period_summaries_v3
               (id, source, period, trend, summary, articles_count, avg_sentiment, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (summary_id, source, period, *summary_fields),
        )
    conn.execute("DROP TABLE period_summaries")
    conn.execute("ALTER TABLE period_summaries_v3 RENAME TO period_summaries")


def _migrate_annotations(conn: sqlite3.Connection) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'summary_annotations'"
    ).fetchone():
        return

    columns = {row[1] for row in conn.execute("PRAGMA table_info(summary_annotations)")}
    source_column = "s.source" if "source" in columns else "a.source"
    rows = conn.execute(
        f"""SELECT s.id, s.period, s.article_id, s.kind, s.text, {source_column}
            FROM summary_annotations s
            LEFT JOIN news_articles a ON a.id = s.article_id
            ORDER BY s.id"""
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS summary_annotations_v3")
    conn.execute(
        """CREATE TABLE summary_annotations_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            period TEXT NOT NULL,
            article_id TEXT NOT NULL REFERENCES news_articles(id),
            kind TEXT NOT NULL CHECK (kind IN ('peak', 'drop')),
            text TEXT NOT NULL,
            UNIQUE(source, period, article_id)
        )"""
    )
    conn.executemany(
        """INSERT INTO summary_annotations_v3 (id, source, period, article_id, kind, text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(row[0], row[5] or "bbc", row[1], row[2], row[3], row[4]) for row in rows],
    )
    conn.execute("DROP TABLE summary_annotations")
    conn.execute("ALTER TABLE summary_annotations_v3 RENAME TO summary_annotations")


def migrate_database(db_path) -> None:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}")
        if version < 2:
            migrate_v2(db_path)
        with sqlite3.connect(db_path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if "expert_notes" not in tables:
                raise RuntimeError("Cannot migrate database: expert_notes table is missing")

            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(expert_notes)")}
            if "source" not in columns:
                conn.execute("ALTER TABLE expert_notes ADD COLUMN source TEXT NOT NULL DEFAULT 'bbc'")
            conn.execute("DROP INDEX IF EXISTS uq_active_note")
            conn.execute(
                """CREATE UNIQUE INDEX uq_active_note
                   ON expert_notes(author_id, source, event_date, metric_category)
                   WHERE status = 'PUBLISHED'"""
            )
            _migrate_summaries(conn)
            _migrate_annotations(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    print(f"[+] Database migrated to schema v{SCHEMA_VERSION}: {db_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate a database to schema v3.")
    parser.add_argument("database", type=Path)
    migrate_database(parser.parse_args().database)
