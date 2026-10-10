"""Ініціалізація SQLite-бази Peacetalk Tracker (схема v1.0).

Запуск з кореня проєкту:  python -m scripts.init_db

Схема v1.0 НЕ містить модерації заміток: колонки review_status / reviewed_by / reviewed_at
і нові статуси додаються міграцією у v2.0 (PT-41…PT-44).

Час зберігається в UTC у форматі ISO 8601 з суфіксом Z (2026-10-10T12:00:00Z).
event_date — календарна дата події без часу (YYYY-MM-DD).
"""
import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_init")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "peacetalk.db"
SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- NewsArticles: сирі статті зі скраперів
CREATE TABLE IF NOT EXISTS news_articles (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,  -- ISO YYYY-MM-DD
    description TEXT,
    text TEXT,
    tags TEXT,  -- JSON-масив
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- AssessmentRecords: результати Agent 1 (Analyst); confidence може бути NULL
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
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(article_id, metric_category)
);

-- ExpertNotes: експертні замітки (v1.0). Життєвий цикл: PUBLISHED -> ARCHIVED.
-- Статуси перевіряються в прикладній логіці, щоб міграція v2.0 була простим ALTER TABLE.
CREATE TABLE IF NOT EXISTS expert_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,  -- ISO YYYY-MM-DD, без часу
    metric_category TEXT NOT NULL,  -- diplomacy, security, territorial, humanitarian
    content TEXT NOT NULL,
    author_id TEXT NOT NULL,
    importance_type TEXT NOT NULL DEFAULT 'COMMENT',  -- COMMENT, OFFICIALSTATEMENT
    status TEXT NOT NULL DEFAULT 'PUBLISHED',  -- PUBLISHED, ARCHIVED
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Інваріант домену (PT-16): одна АКТИВНА замітка на автора, дату та категорію.
-- Архівація звільняє «слот»; саме БД гарантує, що два паралельні запити не створять дубль.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_note
    ON expert_notes(author_id, event_date, metric_category)
    WHERE status = 'PUBLISHED';

CREATE INDEX IF NOT EXISTS idx_news_articles_date ON news_articles(date);
CREATE INDEX IF NOT EXISTS idx_news_articles_source ON news_articles(source);
CREATE INDEX IF NOT EXISTS idx_assessment_article ON assessment_records(article_id);
CREATE INDEX IF NOT EXISTS idx_expert_notes_date ON expert_notes(event_date);
CREATE INDEX IF NOT EXISTS idx_expert_notes_author ON expert_notes(author_id);
CREATE INDEX IF NOT EXISTS idx_expert_notes_status ON expert_notes(status);
"""


def init_db(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        is_new = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='expert_notes'"
        ).fetchone()[0] == 0
        conn.executescript(SCHEMA_SQL)
        if is_new:
            # PRAGMA user_version зручно використати у міграції v2.0 для перевірки версії схеми
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    logger.info("Database initialized at %s", db_path)


def verify_schema(db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        print("Schema version (PRAGMA user_version):", conn.execute("PRAGMA user_version").fetchone()[0])
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
