"""PT-16: обмеження цілісності БД.

Кожен тест працює з власною тимчасовою БД (tmp_path), тому:
  * не залежить від попереднього `python scripts/init_db.py`;
  * не чіпає робочу data/peacetalk.db.
"""
import sqlite3

import pytest

from scripts.init_db import init_db

NOTE_SQL = (
    "INSERT INTO expert_notes "
    "(event_date, metric_category, content, author_id, importance_type, status) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
ARTICLE_SQL = (
    "INSERT INTO news_articles (id, source, url, title, date, text) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
ASSESSMENT_SQL = (
    "INSERT INTO assessment_records "
    "(article_id, metric_category, score, justification, confidence, "
    "overall_sentiment, is_compromise_oriented) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_peacetalk.db"
    init_db(str(db_path))
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def add_note(conn, event_date, category, author, content="note"):
    conn.execute(NOTE_SQL, (event_date, category, content, author, "COMMENT", "PUBLISHED"))
    conn.commit()


def count_notes(conn):
    return conn.execute("SELECT COUNT(*) FROM expert_notes").fetchone()[0]


class TestExpertNotes:
    def test_duplicate_note_same_user_date_category_rejected(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1", "First note")
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            add_note(conn, "2024-01-15", "diplomacy", "user1", "Duplicate note")
        assert "metric_category" in str(exc_info.value)
        assert count_notes(conn) == 1

    def test_different_user_same_date_category_allowed(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        add_note(conn, "2024-01-15", "diplomacy", "user2")
        assert count_notes(conn) == 2

    def test_same_user_different_date_allowed(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        add_note(conn, "2024-01-16", "diplomacy", "user1")
        assert count_notes(conn) == 2

    def test_same_user_different_category_allowed(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        add_note(conn, "2024-01-15", "security", "user1")
        assert count_notes(conn) == 2


class TestArticlesAndAssessments:
    def test_news_articles_url_is_unique(self, conn):
        conn.execute(ARTICLE_SQL, ("art1", "bbc", "http://test.com", "Test", "2024-01-15", "Content"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ARTICLE_SQL, ("art2", "bbc", "http://test.com", "Test 2", "2024-01-16", "Content 2"))

    def test_assessment_unique_per_article_and_category(self, conn):
        conn.execute(ARTICLE_SQL, ("art1", "bbc", "http://test.com", "Test", "2024-01-15", "Content"))
        conn.execute(ASSESSMENT_SQL, ("art1", "diplomacy", 0.5, "Test", 0.8, 0.3, 1))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ASSESSMENT_SQL, ("art1", "diplomacy", 0.6, "Test 2", 0.7, 0.4, 0))

    def test_score_out_of_range_rejected(self, conn):
        conn.execute(ARTICLE_SQL, ("art1", "bbc", "http://test.com", "Test", "2024-01-15", "Content"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ASSESSMENT_SQL, ("art1", "diplomacy", 1.5, "Test", 0.8, 0.3, 1))
