"""Ініціалізація SQLite-бази Peacetalk Tracker.

Запуск з кореня проєкту:  python -m scripts.init_db
"""
import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_init")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "peacetalk.db"

SCHEMA_SQL = """
-- NewsArticles: raw articles from scrapers
CREATE TABLE IF NOT EXISTS news_articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,  -- ISO format YYYY-MM-DD
    description TEXT,
    text TEXT,
    tags TEXT,  -- JSON array
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- AssessmentRecords: Agent 1 (Analyst) outputs
-- confidence може бути NULL (старі записи, де модель її не повертала)
CREATE TABLE IF NOT EXISTS assessment_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES news_articles(id),
    metric_category TEXT NOT NULL,  -- diplomacy, security, territorial, humanitarian
    score REAL NOT NULL CHECK (score >= -1.0 AND score <= 1.0),
    justification TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    overall_sentiment REAL NOT NULL CHECK (overall_sentiment >= -1.0 AND overall_sentiment <= 1.0),
    is_compromise_oriented INTEGER NOT NULL CHECK (is_compromise_oriented IN (0, 1)),
    agent_version TEXT DEFAULT '1.0',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, metric_category)
);

-- ExpertNotes: main domain entity (FR-03, FR-04, v2.0 moderation)
CREATE TABLE IF NOT EXISTS expert_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,  -- ISO format YYYY-MM-DD
    metric_category TEXT NOT NULL,  -- diplomacy, security, territorial, humanitarian
    content TEXT NOT NULL,
    author_id TEXT NOT NULL,
    importance_type TEXT NOT NULL DEFAULT 'COMMENT',  -- COMMENT, OFFICIALSTATEMENT
    status TEXT NOT NULL DEFAULT 'DRAFT',  -- DRAFT, PUBLISHED, PENDING_APPROVAL, APPROVED, REJECTED, ARCHIVED
    moderated_by TEXT,
    moderated_at TEXT,
    moderator_comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    -- Domain invariant: one active note per user per date per category
    UNIQUE(author_id, event_date, metric_category)
);

CREATE INDEX IF NOT EXISTS idx_news_articles_date ON news_articles(date);
CREATE INDEX IF NOT EXISTS idx_news_articles_source ON news_articles(source);
CREATE INDEX IF NOT EXISTS idx_assessment_article ON assessment_records(article_id);
CREATE INDEX IF NOT EXISTS idx_assessment_date ON assessment_records(created_at);
CREATE INDEX IF NOT EXISTS idx_expert_notes_date ON expert_notes(event_date);
CREATE INDEX IF NOT EXISTS idx_expert_notes_author ON expert_notes(author_id);
CREATE INDEX IF NOT EXISTS idx_expert_notes_status ON expert_notes(status);
"""


def init_db(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    logger.info("Database initialized at %s", db_path)


def verify_schema(db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        print("Tables:")
        for (name,) in tables:
            print(f"  - {name}")
            for c in conn.execute(f"PRAGMA table_info({name})").fetchall():
                pk = " PK" if c[5] else ""
                print(f"    {c[1]}: {c[2]}{pk}")


if __name__ == "__main__":
    init_db()
    verify_schema()
