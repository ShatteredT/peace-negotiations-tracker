"""PT-16: обмеження цілісності БД (схема v1.0).

Кожен тест працює з власною тимчасовою БД (tmp_path), тому не залежить від
`python scripts/init_db.py` і не чіпає робочу data/peacetalk.db.
"""
import re
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
UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_peacetalk.db"
    init_db(str(db_path))
    connection = sqlite3.connect(db_path)
    yield connection
    connection.close()


def add_note(conn, event_date, category, author, content="note", status="PUBLISHED"):
    conn.execute(NOTE_SQL, (event_date, category, content, author, "COMMENT", status))
    conn.commit()


def count_notes(conn):
    return conn.execute("SELECT COUNT(*) FROM expert_notes").fetchone()[0]


class TestExpertNotes:
    def test_duplicate_active_note_same_user_date_category_rejected(self, conn):
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

    def test_archived_note_frees_the_slot(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1", status="ARCHIVED")
        add_note(conn, "2024-01-15", "diplomacy", "user1")  # слот вільний
        assert count_notes(conn) == 2

    def test_archiving_active_note_frees_the_slot(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        conn.execute("UPDATE expert_notes SET status = 'ARCHIVED'")
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        assert count_notes(conn) == 2

    def test_many_archived_notes_in_same_slot_allowed(self, conn):
        for text in ("a", "b", "c"):
            add_note(conn, "2024-01-15", "diplomacy", "user1", content=text, status="ARCHIVED")
        assert count_notes(conn) == 3


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


class TestSchemaV2:
    def test_timestamps_default_to_utc_iso(self, conn):
        add_note(conn, "2024-01-15", "diplomacy", "user1")
        created, updated = conn.execute("SELECT created_at, updated_at FROM expert_notes").fetchone()
        assert UTC_ISO.match(created) and UTC_ISO.match(updated)

    def test_default_status_is_published(self, conn):
        conn.execute("INSERT INTO expert_notes (event_date, metric_category, content, author_id) "
                     "VALUES ('2024-01-15', 'diplomacy', 'x', 'user1')")
        assert conn.execute("SELECT status FROM expert_notes").fetchone()[0] == "PUBLISHED"

    def test_v2_schema_has_moderation_columns(self, conn):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(expert_notes)")}
        assert {"review_status", "reviewed_by", "reviewed_at"} <= columns

    def test_legacy_notes_default_to_approved(self, conn):
        conn.execute(
            "INSERT INTO expert_notes (event_date, metric_category, content, author_id) "
            "VALUES ('2024-01-15', 'diplomacy', 'legacy note', 'user1')"
        )
        review_status, reviewed_by, reviewed_at = conn.execute(
            "SELECT review_status, reviewed_by, reviewed_at FROM expert_notes"
        ).fetchone()
        assert (review_status, reviewed_by, reviewed_at) == ("APPROVED", None, None)

    def test_schema_version_is_2(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
