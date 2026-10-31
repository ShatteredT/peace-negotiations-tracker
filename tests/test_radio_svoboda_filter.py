import json
import sqlite3
from pathlib import Path

import pandas as pd

from data.RadioSvoboda.filter import article_id_from_url, save_to_file
from scripts.init_db import init_db
from scripts.load_to_db import load_articles


def test_radio_svoboda_ids_are_stable_and_loadable(tmp_path):
    url = "https://www.radiosvoboda.org/a/example/123.html"
    expected_id = article_id_from_url(url)
    assert expected_id == article_id_from_url(url)
    assert expected_id != article_id_from_url(url + "?other=1")

    articles = pd.DataFrame([{
        "url": url,
        "title": "Test article",
        "date": "2026-10-01",
        "text": "Article body",
        "description": "Description",
        "source": "radio_svoboda",
        "tags": ["peace"],
    }])
    output = tmp_path / "filtered_articles"
    save_to_file(articles, str(output))
    records = json.loads(Path(f"{output}.json").read_text(encoding="utf-8"))
    assert records[0]["id"] == expected_id

    db = tmp_path / "articles.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        assert load_articles(conn, records) == (1, 0)
        row = conn.execute(
            "SELECT id, source, url FROM news_articles"
        ).fetchone()
    assert row == (expected_id, "radio_svoboda", url)
