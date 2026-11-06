import sqlite3

import pytest

from src.agents.synthesizer_agent import ensure_tables
from scripts.build_demo_db import build_demo_database
from scripts.init_db import init_db
from src.runtime_db import prepare_demo_database


def test_demo_snapshot_redacts_article_text_and_preserves_app_data(tmp_path):
    source = tmp_path / "source.db"
    snapshot = tmp_path / "demo" / "snapshot.db"
    init_db(source)
    with sqlite3.connect(source) as conn:
        ensure_tables(conn)
        conn.execute(
            """INSERT INTO news_articles (id, source, url, title, date, text)
               VALUES ('a1', 'bbc', 'https://example.test/a1', 'Title', '2024-01-15',
                       'Copyrighted full article text')"""
        )
        conn.execute(
            """INSERT INTO assessment_records
               (article_id, metric_category, score, overall_sentiment, is_compromise_oriented)
               VALUES ('a1', 'diplomacy', 0.4, 0.2, 1)"""
        )
        conn.execute(
            """INSERT INTO expert_notes
               (event_date, metric_category, content, author_id, review_status)
               VALUES ('2024-01-15', 'diplomacy', 'Demo note', 'analyst_1', 'APPROVED')"""
        )
        conn.execute(
            """INSERT INTO period_summaries
               (period, trend, summary, articles_count, avg_sentiment, model, created_at)
               VALUES ('2024-H1', 'stable', 'Demo summary', 1, 0.2, 'test', '2024-07-01T00:00:00Z')"""
        )

    build_demo_database(source, snapshot)

    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT text FROM news_articles WHERE id = 'a1'").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM assessment_records").fetchone()[0] == 1
        assert conn.execute("SELECT content FROM expert_notes").fetchone()[0] == "Demo note"
        assert conn.execute("SELECT summary FROM period_summaries").fetchone()[0] == "Demo summary"

    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT text FROM news_articles WHERE id = 'a1'").fetchone()[0] == (
            "Copyrighted full article text"
        )


def test_runtime_demo_database_is_a_writable_disposable_copy(tmp_path):
    seed = tmp_path / "seed.db"
    runtime_dir = tmp_path / "runtime"
    with sqlite3.connect(seed) as conn:
        conn.execute("CREATE TABLE demo_data (value TEXT)")
        conn.execute("INSERT INTO demo_data VALUES ('seed')")

    runtime_db = prepare_demo_database(seed, runtime_dir)
    with sqlite3.connect(runtime_db) as conn:
        conn.execute("INSERT INTO demo_data VALUES ('runtime change')")

    with sqlite3.connect(seed) as conn:
        assert conn.execute("SELECT value FROM demo_data").fetchall() == [("seed",)]


def test_demo_snapshot_does_not_overwrite_existing_output_without_opt_in(tmp_path):
    source = tmp_path / "source.db"
    output = tmp_path / "demo.db"
    init_db(source)
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        build_demo_database(source, output)

    assert output.read_bytes() == b"existing"
