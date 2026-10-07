import sqlite3
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_init")

DB_PATH = Path("data/peacetalk.db")

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
CREATE TABLE IF NOT EXISTS assessment_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES news_articles(id),
    metric_category TEXT NOT NULL,  -- diplomacy, security, territorial, humanitarian
    score REAL NOT NULL CHECK (score >= -1.0 AND score <= 1.0),
    justification TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
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

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_news_articles_date ON news_articles(date);
CREATE INDEX IF NOT EXISTS idx_news_articles_source ON news_articles(source);
CREATE INDEX IF NOT EXISTS idx_assessment_article ON assessment_records(article_id);
CREATE INDEX IF NOT EXISTS idx_assessment_date ON assessment_records(created_at);
CREATE INDEX IF NOT EXISTS idx_expert_notes_date ON expert_notes(event_date);
CREATE INDEX IF NOT EXISTS idx_expert_notes_author ON expert_notes(author_id);
CREATE INDEX IF NOT EXISTS idx_expert_notes_status ON expert_notes(status);
"""

def init_db(db_path: str = "data/peacetalk.db"):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    
    logger.info(f"Database initialized at {db_path}")

def verify_schema(db_path: str = "data/peacetalk.db"):
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        print("Tables:")
        for t in tables:
            print(f"  - {t[0]}")
            cols = conn.execute(f"PRAGMA table_info({t[0]})").fetchall()
            for c in cols:
                pk = " PK" if c[5] else ""
                print(f"    {c[1]}: {c[2]}{pk}")

def test_constraints(db_path: str = "data/peacetalk.db"):
    """Test unique constraint on expert_notes"""
    import datetime
    with sqlite3.connect(db_path) as conn:
        # Insert first note
        conn.execute("""
            INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2024-01-15", "diplomacy", "Test note 1", "user1", "COMMENT", "PUBLISHED"))
        
        # Try duplicate - should fail
        try:
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "Test note 2", "user1", "COMMENT", "PUBLISHED"))
            print("ERROR: Duplicate was allowed!")
        except sqlite3.IntegrityError as e:
            print(f"OK: Duplicate correctly rejected: {e}")
        
        # Different user - should work
        conn.execute("""
            INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2024-01-15", "diplomacy", "Test note 3", "user2", "COMMENT", "PUBLISHED"))
        print("OK: Different user allowed")
        
        # Different date - should work
        conn.execute("""
            INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2024-01-16", "diplomacy", "Test note 4", "user1", "COMMENT", "PUBLISHED"))
        print("OK: Different date allowed")
        
        conn.commit()

if __name__ == "__main__":
    init_db()
    verify_schema()
    test_constraints()
    print("\nAll tests passed!")