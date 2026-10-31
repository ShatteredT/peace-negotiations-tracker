import json

import pytest

from src.agents import selector_optimized


def test_load_radio_svoboda_articles_sets_source_and_preserves_ids(tmp_path, monkeypatch):
    corpus = tmp_path / "RadioSvoboda" / "filtered_articles.json"
    corpus.parent.mkdir()
    article = {"id": "radio-svoboda-1", "url": "https://example.test/1", "title": "Article"}
    corpus.write_text(json.dumps([article]), encoding="utf-8")
    monkeypatch.setattr(selector_optimized, "DATA_DIR", tmp_path)

    loaded = selector_optimized.load_radio_svoboda_articles()

    assert loaded == [{**article, "source": "radio_svoboda"}]


def test_load_radio_svoboda_articles_requires_committed_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(selector_optimized, "DATA_DIR", tmp_path)

    with pytest.raises(FileNotFoundError, match="корпус Радіо Свобода"):
        selector_optimized.load_radio_svoboda_articles()
