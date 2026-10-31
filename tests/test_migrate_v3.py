import sqlite3

from scripts.migrate_v2 import migrate_database as migrate_v2
from scripts.migrate_v3 import migrate_database as migrate_v3

from tests.test_migrate_v2 import create_v1_database


def test_migration_separates_legacy_summaries_and_notes_by_source(tmp_path):
    db_path = tmp_path / "v2.db"
    create_v1_database(db_path)
    migrate_v2(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE period_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL UNIQUE,
                trend TEXT NOT NULL,
                summary TEXT NOT NULL,
                articles_count INTEGER NOT NULL,
                avg_sentiment REAL NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE summary_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,
                article_id TEXT NOT NULL REFERENCES news_articles(id),
                kind TEXT NOT NULL CHECK (kind IN ('peak', 'drop')),
                text TEXT NOT NULL,
                UNIQUE(period, article_id)
            );
            """
        )
        conn.execute(
            """INSERT INTO period_summaries
               (period, trend, summary, articles_count, avg_sentiment, model, created_at)
               VALUES ('2024-H1', 'stable', 'old summary', 1, 0.2, 'test', 'now')"""
        )
        conn.execute(
            """INSERT INTO summary_annotations (period, article_id, kind, text)
               VALUES ('2024-H1', 'article-1', 'peak', 'old annotation')"""
        )
        conn.execute("PRAGMA user_version = 2")

    migrate_v3(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT source, period, summary FROM period_summaries").fetchall() == [
            ("bbc", "2024-H1", "old summary")
        ]
        assert conn.execute(
            "SELECT source, period, article_id, text FROM summary_annotations"
        ).fetchall() == [("bbc", "2024-H1", "article-1", "old annotation")]
        assert conn.execute("SELECT source FROM expert_notes WHERE id = 1").fetchone() == ("bbc",)
        conn.execute(
            """INSERT INTO period_summaries
               (source, period, trend, summary, articles_count, avg_sentiment, model, created_at)
               VALUES ('radio_svoboda', '2024-H1', 'mixed', 'separate', 1, 0.3, 'test', 'now')"""
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM period_summaries WHERE period = '2024-H1'").fetchone()[0] == 2


def test_migration_preserves_existing_source_specific_summary_source(tmp_path):
    db_path = tmp_path / "v2-sources.db"
    create_v1_database(db_path)
    migrate_v2(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE period_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, period TEXT NOT NULL,
                trend TEXT NOT NULL, summary TEXT NOT NULL, articles_count INTEGER NOT NULL,
                avg_sentiment REAL NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE summary_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, period TEXT NOT NULL,
                article_id TEXT NOT NULL REFERENCES news_articles(id),
                kind TEXT NOT NULL CHECK (kind IN ('peak', 'drop')), text TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """INSERT INTO period_summaries
               (source, period, trend, summary, articles_count, avg_sentiment, model, created_at)
               VALUES ('bbc', '2024-H1', 'stable', 'existing', 1, 0.2, 'test', 'now')"""
        )
        conn.execute("PRAGMA user_version = 2")

    migrate_v3(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT source, summary FROM period_summaries").fetchall() == [("bbc", "existing")]
