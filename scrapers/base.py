import json
import os
import tempfile
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src import ops
from src.logger import get_logger

logger = get_logger(__name__)


def _write_json(path: Path, payload) -> None:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as output:
            temp_path = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _merge_articles(existing: list[dict], new: list[dict]) -> list[dict]:
    merged = []
    seen_urls = set()
    for article in existing + new:
        url = article.get("url")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        merged.append(article)
    return merged


class BaseScraper():
    def __init__(self) -> None:
        self.HEADERS = {"User-Agent": "Mozilla/5.0"}

    def get_response(self, url):
        """
        Завантажує вебсторінку за URL.
        """
        response = requests.get(url, headers=self.HEADERS, timeout=30)
        response.raise_for_status()
        return response.content

    def get_sitemap_urls(self):
        """
        Отримує URL усіх sitemap-ів з головного sitemap index
        """
        pass

    def get_article_urls(self, sitemap_url):
        """
        Витягує URL новин із конкретного sitemap
        """
        pass

    def scrape(self):
        source = getattr(self, "source_name", type(self).__name__.lower())
        op = f"scrape_urls_{source}"
        started = time.perf_counter()
        metrics = {"sitemaps": 0, "urls_found": 0}
        result, error = "error", None
        try:
            logger.info("Starting sitemap scan source=%s", source)
            sitemap_urls = self.get_sitemap_urls()
            metrics["sitemaps"] = len(sitemap_urls)
            all_articles = {}
            for index, sitemap_url in enumerate(sitemap_urls, start=1):
                urls = list(dict.fromkeys(self.get_article_urls(sitemap_url)))
                all_articles[sitemap_url] = urls
                metrics["urls_found"] += len(urls)
                logger.debug(
                    "Sitemap processed source=%s index=%d total=%d urls=%d",
                    source, index, len(sitemap_urls), len(urls),
                )
            result = "ok"
            logger.info(
                "Sitemap scan complete source=%s sitemaps=%d urls=%d",
                source, metrics["sitemaps"], metrics["urls_found"],
            )
            return all_articles
        except Exception as exc:
            error = type(exc).__name__
            logger.error(
                "Sitemap scan failed source=%s error=%s", source, error
            )
            raise
        finally:
            ops.record(
                op, result, (time.perf_counter() - started) * 1000,
                error, metrics=metrics,
            )

    def save_urls(self, all_articles, save_path):
        """
        Зберігає відфільтрований корпус url
        """

        filtered_articles = dict()

        for sitemap_url, articles_url_list in all_articles.items():
            if articles_url_list:
                filtered_articles[sitemap_url] = articles_url_list

        with open(save_path, "w", encoding="utf-8") as file:
            json.dump(filtered_articles, file, ensure_ascii=False, indent=2)


class BaseParser():
    def get_response(self, url):
        """
        Завантажує вебсторінку за URL. Повертає soup або None у разі помилки.
        """
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            return soup
        except requests.exceptions.RequestException as exc:
            source = getattr(self, "source_name", type(self).__name__)
            logger.warning(
                "Article request failed source=%s error=%s",
                source, type(exc).__name__,
            )
            raise

    def _get_article_date(self, soup):
        pass

    def _get_article_title(self, soup):
        pass

    def _get_article_text(self, soup):
        pass

    def _get_tags(self, soup):
        pass

    def _get_description(self, soup):
        pass

    def scrape_article(self, article_url):
        """
        Витягує дату, заголовок, опис, теги, текст зі статті
        """
        article_soup = self.get_response(article_url)
        if not article_soup:
            return None

        article_date = self._get_article_date(article_soup)
        if not article_date:
            return None

        if article_date.year in self.target_years:  # year filter

            article_title = self._get_article_title(article_soup)
            article_text = self._get_article_text(article_soup)
            article_tags = self._get_tags(article_soup)
            article_descr = self._get_description(article_soup)

            entry = {
                "title": article_title,
                "date": article_date.date().isoformat(),
                "description": article_descr,
                "tags": article_tags,
                "url": article_url,
                "text": article_text,
                "source": self.source_name
            }
            return entry
        return None

    def scrape_corpus(
            self, urls: list, save_dir, *, merge: bool = False
    ) -> dict[str, int]:
        source = self.source_name
        op = f"scrape_corpus_{source}"
        started = time.perf_counter()
        metrics = {
            "urls_attempted": 0,
            "articles_saved": 0,
            "urls_skipped": 0,
            "urls_failed": 0,
            "failures_pending": 0,
        }
        result, error = "error", None
        save_dir = Path(save_dir)
        save_path = save_dir / f"{source}_articles.json"
        failed_path = save_dir / f"{source}_failed_urls.json"
        articles, failed_urls = [], []
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            existing = []
            if merge and save_path.exists():
                existing = json.loads(save_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    raise ValueError("Expected an article list")

            total = len(urls)
            logger.info(
                "Scraping article corpus source=%s urls=%d merge=%s",
                source, total, merge,
            )
            for index, url in enumerate(urls, start=1):
                metrics["urls_attempted"] += 1
                try:
                    article = self.scrape_article(url)
                except Exception as exc:
                    error = error or type(exc).__name__
                    metrics["urls_failed"] += 1
                    failed_urls.append(url)
                    logger.warning(
                        "Article scrape failed source=%s index=%d error=%s",
                        source, index, type(exc).__name__,
                    )
                else:
                    if article:
                        articles.append(article)
                        metrics["articles_saved"] += 1
                    else:
                        metrics["urls_skipped"] += 1
                        failed_urls.append(url)

                if index % 50 == 0:
                    saved = (
                        _merge_articles(existing, articles)
                        if merge else articles
                    )
                    _write_json(save_path, saved)
                    _write_json(failed_path, failed_urls)
                    logger.info(
                        "Scrape progress source=%s processed=%d/%d saved=%d "
                        "skipped=%d failed=%d",
                        source, index, total, metrics["articles_saved"],
                        metrics["urls_skipped"], metrics["urls_failed"],
                    )
                time.sleep(0.5)

            saved = (
                _merge_articles(existing, articles)
                if merge else articles
            )
            _write_json(save_path, saved)
            _write_json(failed_path, failed_urls)
            metrics["failures_pending"] = len(failed_urls)
            result = "error" if metrics["failures_pending"] else "ok"
            logger.info(
                "Scrape complete source=%s attempted=%d saved=%d skipped=%d "
                "failed=%d pending=%d",
                source, metrics["urls_attempted"], metrics["articles_saved"],
                metrics["urls_skipped"], metrics["urls_failed"],
                metrics["failures_pending"],
            )
            return metrics
        except Exception as exc:
            error = type(exc).__name__
            logger.error(
                "Corpus scrape failed source=%s error=%s", source, error
            )
            raise
        finally:
            ops.record(
                op, result, (time.perf_counter() - started) * 1000,
                error, metrics=metrics,
            )


class BaseFilter():
    def filter(self, articles_file_path, save_path):
        pass
