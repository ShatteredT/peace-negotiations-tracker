import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_init")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "peacetalk.db"
SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- NewsArticles: сирі статті зі скраперів
CREATE TABLE IF NOT EXISTS news_articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    text TEXT,
    tags TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- AssessmentRecords
CREATE TABLE IF NOT EXISTS assessment_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES news_articles(id),
    metric_category TEXT NOT NULL,
    score REAL NOT NULL CHECK (score >= -1.0 AND score <= 1.0),
    justification TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    overall_sentiment REAL NOT NULL CHECK (overall_sentiment >= -1.0 AND overall_sentiment <= 1.0),
    is_compromise_oriented INTEGER NOT NULL CHECK (is_compromise_oriented IN (0, 1)),
    agent_version TEXT DEFAULT '1.0',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(article_id, metric_category)
);

-- ExpertNotes: оновлена схема v2.0
CREATE TABLE IF NOT EXISTS expert_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    metric_category TEXT NOT NULL,
    content TEXT NOT NULL,
    author_id TEXT NOT NULL,
    importance_type TEXT NOT NULL DEFAULT 'COMMENT',
    status TEXT NOT NULL DEFAULT 'PUBLISHED',
    review_status TEXT DEFAULT 'APPROVED',
    reviewed_by TEXT DEFAULT NULL,
    reviewed_at TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_note
    ON expert_notes(author_id, event_date, metric_category)
    WHERE status = 'PUBLISHED';

CREATE INDEX IF NOT EXISTS idx_news_articles_date ON news_articles(date);
CREATE INDEX IF NOT EXISTS idx_expert_notes_date ON expert_notes(event_date);
"""

def init_db(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    logger.info("Database initialized at %s", db_path)

if __name__ == "__main__":
    init_db()
