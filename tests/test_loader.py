import sqlite3

from scripts.init_db import init_db
from scripts.load_to_db import load_articles, load_assessments

ARTICLE = {"id": "a1", "source": "bbc", "title": "T", "date": "2023-05-01T10:00:00",
           "url": "https://x/1", "text": "body"}


def _analysis(aid="a1", conf=None):
    cat = {"score": 0.5, "justification": "j"}
    if conf is not None:
        cat["confidence"] = conf
    return {"article_id": aid, "overall_sentiment": 0.1, "is_compromise_oriented": True,
            "categories": {k: dict(cat) for k in ("diplomacy", "security", "territorial", "humanitarian")}}


def _conn(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    return sqlite3.connect(db)


def test_loader_is_idempotent_and_normalizes_date(tmp_path):
    conn = _conn(tmp_path)
    for _ in range(2):
        load_articles(conn, [ARTICLE])
        load_assessments(conn, [_analysis()], "1.0")
    assert conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM assessment_records").fetchone()[0] == 4
    assert conn.execute("SELECT date FROM news_articles").fetchone()[0] == "2023-05-01"


def test_missing_confidence_stored_as_null(tmp_path):
    conn = _conn(tmp_path)
    load_articles(conn, [ARTICLE])
    load_assessments(conn, [_analysis()], "1.0")
    assert conn.execute("SELECT COUNT(*) FROM assessment_records WHERE confidence IS NULL").fetchone()[0] == 4


def test_confidence_is_kept_when_present(tmp_path):
    conn = _conn(tmp_path)
    load_articles(conn, [ARTICLE])
    load_assessments(conn, [_analysis(conf=0.9)], "1.0")
    assert conn.execute("SELECT DISTINCT confidence FROM assessment_records").fetchall() == [(0.9,)]


def test_assessment_for_unknown_article_is_skipped(tmp_path):
    conn = _conn(tmp_path)
    rows, skipped = load_assessments(conn, [_analysis("ghost")], "1.0")
    assert (rows, skipped) == (0, 1)


def test_invalid_score_is_skipped(tmp_path):
    conn = _conn(tmp_path)
    load_articles(conn, [ARTICLE])
    bad = _analysis()
    bad["categories"]["security"]["score"] = 5
    rows, skipped = load_assessments(conn, [bad], "1.0")
    assert (rows, skipped) == (0, 1)
