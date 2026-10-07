import sqlite3
import pytest
from pathlib import Path

DB_PATH = Path("data/peacetalk.db")


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure clean database for each test."""
    import sqlite3
    # Clear tables
    with sqlite3.connect("data/peacetalk.db") as conn:
        conn.execute("DELETE FROM expert_notes")
        conn.execute("DELETE FROM news_articles")
        conn.execute("DELETE FROM assessment_records")
        conn.commit()
    yield
    # Cleanup
    with sqlite3.connect("data/peacetalk.db") as conn:
        conn.execute("DELETE FROM expert_notes")
        conn.execute("DELETE FROM news_articles")
        conn.execute("DELETE FROM assessment_records")
        conn.commit()


class TestDBConstraints:
    """PT-16: Unit test for duplicate entry rejection."""

    def test_duplicate_note_same_user_same_date_same_category_rejected(self):
        """PT-16: Duplicate note for same user/date/category should be rejected."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            # Insert first note
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "First note", "user1", "COMMENT", "PUBLISHED"))
            conn.commit()

            # Attempt duplicate - should raise IntegrityError
            with pytest.raises(sqlite3.IntegrityError) as exc_info:
                conn.execute("""
                    INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ("2024-01-15", "diplomacy", "Duplicate note", "user1", "COMMENT", "PUBLISHED"))
                conn.commit()
            
            assert "UNIQUE constraint failed" in str(exc_info.value)
            assert "expert_notes.author_id" in str(exc_info.value)
            assert "event_date" in str(exc_info.value)
            assert "metric_category" in str(exc_info.value)

    def test_different_user_same_date_category_allowed(self):
        """Different user can create note for same date/category."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "User 1 note", "user1", "COMMENT", "PUBLISHED"))
            
            # Different user - should work
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "User 2 note", "user2", "COMMENT", "PUBLISHED"))
            conn.commit()
            
            # Verify both exist
            count = conn.execute("SELECT COUNT(*) FROM expert_notes WHERE event_date = '2024-01-15' AND metric_category = 'diplomacy'").fetchone()[0]
            assert count == 2

    def test_same_user_different_date_allowed(self):
        """Same user can create notes for different dates."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "Note 1", "user1", "COMMENT", "PUBLISHED"))
            
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-16", "diplomacy", "Note 2", "user1", "COMMENT", "PUBLISHED"))
            conn.commit()
            
            count = conn.execute("SELECT COUNT(*) FROM expert_notes WHERE author_id = 'user1' AND metric_category = 'diplomacy'").fetchone()[0]
            assert count == 2

    def test_same_user_different_category_allowed(self):
        """Same user can create notes for different categories on same date."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "diplomacy", "Diplomacy note", "user1", "COMMENT", "PUBLISHED"))
            
            conn.execute("""
                INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("2024-01-15", "security", "Security note", "user1", "COMMENT", "PUBLISHED"))
            conn.commit()
            
            count = conn.execute("SELECT COUNT(*) FROM expert_notes WHERE author_id = 'user1' AND event_date = '2024-01-15'").fetchone()[0]
            assert count == 2

    def test_assessment_records_unique_article_category(self):
        """assessment_records should have unique constraint on (article_id, metric_category)."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            # Insert article
            conn.execute("""
                INSERT INTO news_articles (id, source, url, title, date, text)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("art1", "bbc", "http://test.com", "Test", "2024-01-15", "Content"))
            
            # Insert first assessment
            conn.execute("""
                INSERT INTO assessment_records (article_id, metric_category, score, justification, confidence, overall_sentiment, is_compromise_oriented)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("art1", "diplomacy", 0.5, "Test", 0.8, 0.3, 1))
            
            # Duplicate should fail
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO assessment_records (article_id, metric_category, score, justification, confidence, overall_sentiment, is_compromise_oriented)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ("art1", "diplomacy", 0.6, "Test 2", 0.7, 0.4, 0))
                conn.commit()

    def test_news_articles_unique_url(self):
        """news_articles should have unique constraint on url."""
        with sqlite3.connect("data/peacetalk.db") as conn:
            conn.execute("""
                INSERT INTO news_articles (id, source, url, title, date, text)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("art1", "bbc", "http://test.com", "Test", "2024-01-15", "Content"))
            
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO news_articles (id, source, url, title, date, text)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ("art2", "bbc", "http://test.com", "Test 2", "2024-01-16", "Content 2"))
                conn.commit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])