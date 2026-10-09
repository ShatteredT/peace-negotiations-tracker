"""Завантажує результати конвеєра (JSON) у SQLite, звідки їх читає Streamlit-UI.

Запуск з кореня проєкту:
    python -m scripts.load_to_db
    python -m scripts.load_to_db --db data/peacetalk.db --articles data/found_articles.json \
                                 --analyses data/analyzed_articles.json

Скрипт ідемпотентний: повторний запуск оновлює записи, а не дублює їх.
Якщо в оцінці немає confidence (старі дані) — у БД пишеться NULL, а не вигадане число.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from scripts.init_db import DB_PATH, init_db
from src.agents.analyst_schemas import ArticleAnalysis

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CATEGORIES = ("diplomacy", "security", "territorial", "humanitarian")


def load_articles(conn: sqlite3.Connection, articles: list[dict]) -> tuple[int, int]:
    ok = skipped = 0
    for a in articles:
        try:
            conn.execute(
                """
                INSERT INTO news_articles (id, source, url, title, date, description, text, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source, url=excluded.url, title=excluded.title,
                    date=excluded.date, description=excluded.description,
                    text=excluded.text, tags=excluded.tags
                """,
                (
                    a["id"],
                    a.get("source", "unknown"),
                    a["url"],
                    a["title"],
                    a["date"][:10],
                    a.get("description"),
                    a.get("text"),
                    json.dumps(a["tags"], ensure_ascii=False) if a.get("tags") else None,
                ),
            )
            ok += 1
        except (KeyError, sqlite3.IntegrityError) as e:
            print(f"  ! стаття {a.get('id', '?')} пропущена: {e!r}")
            skipped += 1
    return ok, skipped


def load_assessments(conn: sqlite3.Connection, analyses: list[dict], agent_version: str) -> tuple[int, int]:
    known_ids = {r[0] for r in conn.execute("SELECT id FROM news_articles")}
    rows = skipped = 0
    for raw in analyses:
        try:
            item = ArticleAnalysis.model_validate(raw)
        except ValidationError as e:
            print(f"  ! оцінка {raw.get('article_id', '?')} невалідна: {e.error_count()} помилок")
            skipped += 1
            continue
        if item.article_id not in known_ids:
            print(f"  ! оцінка для невідомої статті {item.article_id} пропущена")
            skipped += 1
            continue
        for cat in CATEGORIES:
            c = getattr(item.categories, cat)
            conn.execute(
                """
                INSERT INTO assessment_records
                    (article_id, metric_category, score, justification, confidence,
                     overall_sentiment, is_compromise_oriented, agent_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id, metric_category) DO UPDATE SET
                    score=excluded.score, justification=excluded.justification,
                    confidence=excluded.confidence, overall_sentiment=excluded.overall_sentiment,
                    is_compromise_oriented=excluded.is_compromise_oriented,
                    agent_version=excluded.agent_version
                """,
                (
                    item.article_id, cat, c.score, c.justification, c.confidence,
                    item.overall_sentiment, int(item.is_compromise_oriented), agent_version,
                ),
            )
            rows += 1
    return rows, skipped


def main():
    p = argparse.ArgumentParser(description="JSON → SQLite loader")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--articles", default=str(DATA_DIR / "found_articles.json"))
    p.add_argument("--analyses", default=str(DATA_DIR / "analyzed_articles.json"))
    p.add_argument("--agent-version", default="1.0")
    args = p.parse_args()

    articles = json.loads(Path(args.articles).read_text(encoding="utf-8"))
    analyses = json.loads(Path(args.analyses).read_text(encoding="utf-8"))

    init_db(args.db)
    with sqlite3.connect(args.db) as conn:
        a_ok, a_skip = load_articles(conn, articles)
        r_ok, r_skip = load_assessments(conn, analyses, args.agent_version)
        conn.commit()

    print(f"Статті:  завантажено {a_ok}, пропущено {a_skip}")
    print(f"Оцінки:  записано {r_ok} рядків (по 4 категорії на статтю), статей пропущено {r_skip}")


if __name__ == "__main__":
    main()
