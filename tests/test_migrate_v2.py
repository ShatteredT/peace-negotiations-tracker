import sqlite3

from scripts.init_db import init_db
from scripts.migrate_v2 import migrate_database


V1_SCHEMA = """
CREATE TABLE news_articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    text TEXT,
    tags TEXT,
    created_at TEXT
);
CREATE TABLE assessment_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES news_articles(id),
    metric_category TEXT NOT NULL,
    score REAL NOT NULL,
    justification TEXT,
    confidence REAL,
    overall_sentiment REAL NOT NULL,
    is_compromise_oriented INTEGER NOT NULL,
    agent_version TEXT DEFAULT '1.0',
    created_at TEXT,
    UNIQUE(article_id, metric_category)
);
CREATE TABLE expert_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    metric_category TEXT NOT NULL,
    content TEXT NOT NULL,
    author_id TEXT NOT NULL,
    importance_type TEXT NOT NULL DEFAULT 'COMMENT',
    status TEXT NOT NULL DEFAULT 'PUBLISHED',
    created_at TEXT,
    updated_at TEXT
);
CREATE UNIQUE INDEX uq_active_note
    ON expert_notes(author_id, event_date, metric_category)
    WHERE status = 'PUBLISHED';
PRAGMA user_version = 1;
"""


def create_v1_database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(V1_SCHEMA)
        conn.execute(
            """INSERT INTO news_articles (id, source, url, title, date, text)
               VALUES ('article-1', 'bbc', 'https://example.test/1', 'Article', '2024-01-15', 'Body')"""
        )
        conn.execute(
            """INSERT INTO assessment_records
               (article_id, metric_category, score, overall_sentiment, is_compromise_oriented)
               VALUES ('article-1', 'diplomacy', 0.4, 0.2, 1)"""
        )
        conn.executemany(
            """INSERT INTO expert_notes
               (event_date, metric_category, content, author_id, importance_type, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("2024-01-15", "diplomacy", "Existing public comment", "analyst_1",
                 "COMMENT", "PUBLISHED", "2024-01-16T10:00:00Z", "2024-01-16T10:00:00Z"),
                ("2024-01-16", "security", "Existing official note", "analyst_2",
                 "OFFICIALSTATEMENT", "PUBLISHED", "2024-01-17T10:00:00Z", "2024-01-17T10:00:00Z"),
                ("2024-01-17", "territorial", "Previously archived", "analyst_1",
                 "COMMENT", "ARCHIVED", "2024-01-18T10:00:00Z", "2024-01-19T10:00:00Z"),
            ],
        )


def test_migration_preserves_v1_data_and_approves_existing_notes(tmp_path):
    db_path = tmp_path / "v1.db"
    create_v1_database(db_path)

    migrate_database(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT id, title, text FROM news_articles").fetchall() == [
            ("article-1", "Article", "Body")
        ]
        assert conn.execute(
            "SELECT article_id, metric_category, score FROM assessment_records"
        ).fetchall() == [("article-1", "diplomacy", 0.4)]
        assert conn.execute(
            """SELECT event_date, metric_category, content, author_id, importance_type,
                      status, review_status, reviewed_by, reviewed_at, created_at, updated_at
               FROM expert_notes ORDER BY id"""
        ).fetchall() == [
            ("2024-01-15", "diplomacy", "Existing public comment", "analyst_1",
             "COMMENT", "PUBLISHED", "APPROVED", None, None,
             "2024-01-16T10:00:00Z", "2024-01-16T10:00:00Z"),
            ("2024-01-16", "security", "Existing official note", "analyst_2",
             "OFFICIALSTATEMENT", "PUBLISHED", "APPROVED", None, None,
             "2024-01-17T10:00:00Z", "2024-01-17T10:00:00Z"),
            ("2024-01-17", "territorial", "Previously archived", "analyst_1",
             "COMMENT", "ARCHIVED", "APPROVED", None, None,
             "2024-01-18T10:00:00Z", "2024-01-19T10:00:00Z"),
        ]


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "v1.db"
    create_v1_database(db_path)

    migrate_database(db_path)
    migrate_database(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(expert_notes)")}
        assert {"review_status", "reviewed_by", "reviewed_at"} <= columns
        assert conn.execute("SELECT COUNT(*) FROM expert_notes").fetchone()[0] == 3


def test_init_db_migrates_existing_v1_database(tmp_path):
    db_path = tmp_path / "v1.db"
    create_v1_database(db_path)

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute(
            "SELECT content, status, review_status FROM expert_notes WHERE id = 2"
        ).fetchone() == ("Existing official note", "PUBLISHED", "APPROVED")
