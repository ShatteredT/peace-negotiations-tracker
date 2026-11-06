import json

import pytest

from scripts import backfill
from scrapers.base import BaseParser, BaseScraper
from src import ops


class FixtureScraper(BaseScraper):
    source_name = "fixture"

    def get_sitemap_urls(self):
        return ["sitemap-1", "sitemap-2"]

    def get_article_urls(self, sitemap_url):
        if sitemap_url == "sitemap-1":
            return ["article-1", "article-1"]
        return ["article-2"]


class FixtureParser(BaseParser):
    source_name = "bbc"

    def scrape_article(self, url):
        if url.endswith("/skip"):
            return None
        if url.endswith("/error"):
            raise RuntimeError("sensitive exception details")
        return {"url": url, "title": "fixture"}


def test_sitemap_metrics_and_url_deduplication(tmp_path, monkeypatch):
    log_path = tmp_path / "ops.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log_path)

    result = FixtureScraper().scrape()

    assert result == {
        "sitemap-1": ["article-1"],
        "sitemap-2": ["article-2"],
    }
    (event,) = ops.read_events(log_path)
    assert (
        event["op"] == "scrape_urls_fixture"
        and event["result"] == "ok"
    )
    assert event["metrics"] == {"sitemaps": 2, "urls_found": 2}


def test_backfill_merges_results_and_records_metrics(tmp_path, monkeypatch):
    log_path = tmp_path / "ops.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log_path)
    monkeypatch.setattr(
        "scrapers.base.time.sleep", lambda _: None
    )
    parser = FixtureParser()
    monkeypatch.setattr(backfill, "_build_parser", lambda source: parser)

    output_dir = tmp_path / "corpus"
    output_dir.mkdir()
    (output_dir / "bbc_articles.json").write_text(
        json.dumps([{"url": "old-article"}]), encoding="utf-8"
    )
    urls_path = tmp_path / "failed.json"
    urls_path.write_text(
        json.dumps(["new-article", "article/skip", "article/error"]),
        encoding="utf-8",
    )

    metrics = backfill.run_backfill("bbc", urls_path, output_dir)

    assert metrics == {
        "urls_requested": 3,
        "articles_saved": 1,
        "urls_skipped": 1,
        "urls_failed": 1,
        "failures_pending": 2,
    }
    saved = json.loads(
        (output_dir / "bbc_articles.json").read_text(encoding="utf-8")
    )
    assert [article["url"] for article in saved] == [
        "old-article",
        "new-article",
    ]
    failed = json.loads(
        (output_dir / "bbc_failed_urls.json").read_text(encoding="utf-8")
    )
    assert failed == ["article/skip", "article/error"]
    events = ops.read_events(log_path)
    assert [event["op"] for event in events] == [
        "scrape_corpus_bbc", "backfill_bbc"
    ]
    assert events[0]["result"] == events[1]["result"] == "error"
    log_content = log_path.read_text(encoding="utf-8")
    assert "sensitive exception details" not in log_content


def test_backfill_rejects_invalid_url_input(tmp_path, monkeypatch):
    log_path = tmp_path / "ops.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log_path)
    urls_path = tmp_path / "failed.json"
    urls_path.write_text(
        json.dumps({"url": "not-a-list"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="JSON list"):
        backfill.run_backfill("bbc", urls_path, tmp_path)

    (event,) = ops.read_events(log_path)
    assert (
        event["op"] == "backfill_bbc"
        and event["result"] == "error"
    )
    assert event["error"] == "ValueError"


def test_backfill_clears_the_failed_url_queue(tmp_path, monkeypatch):
    log_path = tmp_path / "ops.jsonl"
    monkeypatch.setattr(ops, "LOG_PATH", log_path)
    monkeypatch.setattr(
        "scrapers.base.time.sleep", lambda _: None
    )
    monkeypatch.setattr(
        backfill, "_build_parser", lambda source: FixtureParser()
    )
    output_dir = tmp_path / "corpus"
    output_dir.mkdir()
    urls_path = tmp_path / "failed.json"
    urls_path.write_text(
        json.dumps(["article/recovered"]), encoding="utf-8"
    )

    metrics = backfill.run_backfill("bbc", urls_path, output_dir)

    assert (
        metrics["articles_saved"] == 1
        and metrics["failures_pending"] == 0
    )
    assert json.loads(
        (output_dir / "bbc_failed_urls.json").read_text(encoding="utf-8")
    ) == []
