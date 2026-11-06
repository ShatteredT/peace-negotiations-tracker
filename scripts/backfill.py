import argparse
import json
import sys
import time
from pathlib import Path

from scrapers.sites.bbc import BBC_ArticleParser
from scrapers.sites.radio_svoboda import RadioArticleParser
from src import ops
from src.logger import get_logger

logger = get_logger(__name__)
BASE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"bbc": "BBC", "radio_svoboda": "RadioSvoboda"}


def _build_parser(source: str):
    if source == "bbc":
        return BBC_ArticleParser(
            source_name="bbc", target_years=range(2021, 2027)
        )
    if source == "radio_svoboda":
        return RadioArticleParser()
    raise ValueError(f"Unsupported source: {source}")


def run_backfill(
        source: str, urls_path: Path | None = None,
        output_dir: Path | None = None
) -> dict[str, int]:
    if source not in SOURCE_DIRS:
        raise ValueError(f"Unsupported source: {source}")

    output_dir = Path(
        output_dir or BASE_DIR / "data" / SOURCE_DIRS[source]
    )
    urls_path = Path(
        urls_path or output_dir / f"{source}_failed_urls.json"
    )
    op = f"backfill_{source}"
    started = time.perf_counter()
    metrics = {
        "urls_requested": 0,
        "articles_saved": 0,
        "urls_skipped": 0,
        "urls_failed": 0,
        "failures_pending": 0,
    }
    result, error = "error", None
    try:
        urls = json.loads(urls_path.read_text(encoding="utf-8"))
        invalid_urls = not isinstance(urls, list) or any(
            not isinstance(url, str) or not url.strip() for url in urls
        )
        if invalid_urls:
            raise ValueError(
                "Backfill input must be a JSON list of non-empty URLs"
            )
        urls = list(dict.fromkeys(urls))
        metrics["urls_requested"] = len(urls)
        if not urls:
            result = "ok"
            logger.info(
                "Backfill skipped source=%s reason=no_pending_urls", source
            )
            return metrics

        corpus_metrics = _build_parser(source).scrape_corpus(
            urls, output_dir, merge=True
        )
        keys = (
            "articles_saved", "urls_skipped", "urls_failed",
            "failures_pending",
        )
        for key in keys:
            metrics[key] = corpus_metrics[key]
        result = "error" if metrics["failures_pending"] else "ok"
        logger.info(
            "Backfill complete source=%s requested=%d saved=%d skipped=%d "
            "failed=%d pending=%d",
            source, metrics["urls_requested"], metrics["articles_saved"],
            metrics["urls_skipped"], metrics["urls_failed"],
            metrics["failures_pending"],
        )
        return metrics
    except Exception as exc:
        error = type(exc).__name__
        logger.error(
            "Backfill failed source=%s error=%s", source, error
        )
        raise
    finally:
        ops.record(
            op, result, (time.perf_counter() - started) * 1000,
            error, metrics=metrics,
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Retry failed article URLs and merge recovered results."
    )
    parser.add_argument("source", choices=tuple(SOURCE_DIRS))
    parser.add_argument(
        "--urls", type=Path,
        help="JSON list of URLs; defaults to the source failed-URL file.",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="directory containing the article corpus",
    )
    args = parser.parse_args(argv)

    try:
        metrics = run_backfill(args.source, args.urls, args.output_dir)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Backfill failed ({type(exc).__name__})", file=sys.stderr)
        return 1

    print(
        "Backfill: " + " ".join(
            f"{key}={value}" for key, value in metrics.items()
        )
    )
    return 1 if metrics["failures_pending"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
