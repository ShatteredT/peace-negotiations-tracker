import argparse
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from src.agents.synthesizer_schemas import PeriodSynthesis
from src.config import DB_PATH, GEMINI_API_KEY, SYNTHESIZER_MODEL, validate_config
from src.logger import get_logger
from src.ops import utc_now_iso

logger = get_logger("synthesizer_agent")

RETRY_DELAY_SECONDS = 20
MAX_ATTEMPTS = 5
RETRYABLE = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")
EXTREMES_PER_SIDE = 2
MAX_NOTES = 10
CATEGORIES = ("diplomacy", "security", "territorial", "humanitarian")

SYSTEM_PROMPT = """
Ти — аналітик мирних переговорів Україна–Росія. Тобі дають оцінки новин за піврічний період
(шкала від -1.0 ескалація до +1.0 компроміс) та експертні замітки.

Напиши українською:
1. trend: escalation, compromise, stable або mixed.
2. summary: 3-5 речень про тенденцію, ключові події та суперечності.
3. annotations: рівно по одній анотації (1-2 речення) на кожен id зі списку «Статті для анотацій»:
   що сталося і чому тон змінився.

Спирайся лише на наведені дані, нічого не вигадуй. Поверни відповідь за JSON-схемою.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS period_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'bbc',
    period TEXT NOT NULL,
    trend TEXT NOT NULL,
    summary TEXT NOT NULL,
    articles_count INTEGER NOT NULL,
    avg_sentiment REAL NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source, period)
);

CREATE TABLE IF NOT EXISTS summary_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'bbc',
    period TEXT NOT NULL,
    article_id TEXT NOT NULL REFERENCES news_articles(id),
    kind TEXT NOT NULL CHECK (kind IN ('peak', 'drop')),
    text TEXT NOT NULL,
    UNIQUE(source, period, article_id)
);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    if db_path and conn.execute("PRAGMA user_version").fetchone()[0] < 3:
        conn.commit()
        from scripts.migrate_v3 import migrate_database

        migrate_database(db_path)


def period_of(day: str) -> str:
    return f"{day[:4]}-H{1 if int(day[5:7]) <= 6 else 2}"


def period_bounds(period: str) -> tuple:
    year, half = period.split("-H")
    return (f"{year}-01-01", f"{year}-06-30") if half == "1" else (f"{year}-07-01", f"{year}-12-31")


def load_period_articles(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT a.id, a.source, a.date, a.title, ar.metric_category, ar.score, ar.overall_sentiment
           FROM news_articles a JOIN assessment_records ar ON ar.article_id = a.id
           ORDER BY a.date, a.id"""
    ).fetchall()
    articles: dict = {}
    for article_id, source, day, title, category, score, overall in rows:
        item = articles.setdefault(
            article_id, {
                "id": article_id, "source": source, "date": day[:10],
                "title": title, "overall": overall, "scores": {},
            }
        )
        item["scores"][category] = score
    grouped: dict = {}
    for item in articles.values():
        grouped.setdefault((item["source"], period_of(item["date"])), []).append(item)
    return grouped


def load_period_notes(conn: sqlite3.Connection, period: str, source: str) -> list:
    start, end = period_bounds(period)
    return conn.execute(
        """SELECT event_date, metric_category, content FROM expert_notes
           WHERE status = 'PUBLISHED' AND event_date BETWEEN ? AND ? AND source = ?
           ORDER BY event_date LIMIT ?""",
        (start, end, source, MAX_NOTES),
    ).fetchall()


def select_extremes(items: list, per_side: int = EXTREMES_PER_SIDE) -> dict:
    k = min(per_side, len(items) // 2)
    if k == 0:
        return {}
    ranked = sorted(items, key=lambda a: (a["overall"], a["id"]))
    extremes = {a["id"]: "drop" for a in ranked[:k]}
    extremes.update({a["id"]: "peak" for a in ranked[-k:]})
    return extremes


SOURCE_LABELS = {"bbc": "BBC", "radio_svoboda": "Радіо Свобода", "all": "Усі джерела"}


def build_prompt(period: str, source: str, items: list, extremes: dict, notes: list) -> str:
    average = sum(a["overall"] for a in items) / len(items)
    lines = [
        f"Період: {period}",
        f"Джерело: {SOURCE_LABELS.get(source, source)}",
        f"Статей: {len(items)}, середній тон: {average:+.2f}",
        "",
        f"Статті (id | дата | загальний | {'/'.join(CATEGORIES)} | заголовок):",
    ]
    for a in items:
        scores = "/".join(f"{a['scores'].get(c, 0):+.1f}" for c in CATEGORIES)
        title = a["title"].removesuffix(" - BBC News Україна")
        lines.append(f"{a['id']} | {a['date']} | {a['overall']:+.2f} | {scores} | {title}")
    lines += ["", "Статті для анотацій:"]
    lines += [f"{article_id} — {'пік' if kind == 'peak' else 'спад'}" for article_id, kind in extremes.items()]
    if notes:
        lines += ["", "Експертні замітки періоду:"]
        lines += [f"{day} [{category}]: {content[:300]}" for day, category, content in notes]
    return "\n".join(lines)


class SynthesizerAgent:
    def __init__(self, db_path=DB_PATH, client=None):
        if client is None:
            validate_config()
            client = genai.Client(api_key=GEMINI_API_KEY)
        self.client = client
        self.model_name = SYNTHESIZER_MODEL
        self.db_path = Path(db_path)

    def _call_llm(self, prompt: str) -> PeriodSynthesis:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=PeriodSynthesis,
                        temperature=0.2,
                    ),
                )
                return PeriodSynthesis.model_validate_json(response.text)
            except Exception as err:
                if attempt < MAX_ATTEMPTS and any(marker in str(err) for marker in RETRYABLE):
                    logger.warning("Спроба %d/%d: %s. Пауза %d с...", attempt, MAX_ATTEMPTS, err, RETRY_DELAY_SECONDS)
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                logger.error("Запит до моделі не вдався: %s", err)
                raise
        raise RuntimeError("unreachable")

    def synthesize_period(self, conn: sqlite3.Connection, source: str, period: str, items: list) -> tuple:
        extremes = select_extremes(items)
        result = self._call_llm(
            build_prompt(period, source, items, extremes, load_period_notes(conn, period, source))
        )
        annotations: dict = {}
        for annotation in result.annotations:
            text = annotation.text.strip()
            if annotation.article_id in extremes and text and annotation.article_id not in annotations:
                annotations[annotation.article_id] = (extremes[annotation.article_id], text)
        return result, annotations

    def save_summary(self, conn: sqlite3.Connection, source: str, period: str, items: list,
                     result: PeriodSynthesis, annotations: dict) -> None:
        average = sum(a["overall"] for a in items) / len(items)
        with conn:
            conn.execute(
                "DELETE FROM summary_annotations WHERE source = ? AND period = ?", (source, period)
            )
            conn.execute(
                """INSERT INTO period_summaries
                   (source, period, trend, summary, articles_count, avg_sentiment, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, period) DO UPDATE SET trend = excluded.trend, summary = excluded.summary,
                       articles_count = excluded.articles_count, avg_sentiment = excluded.avg_sentiment,
                       model = excluded.model, created_at = excluded.created_at""",
                (source, period, result.trend, result.summary.strip(), len(items), average, self.model_name, utc_now_iso()),
            )
            conn.executemany(
                """INSERT INTO summary_annotations (source, period, article_id, kind, text)
                   VALUES (?, ?, ?, ?, ?)""",
                [(source, period, article_id, kind, text) for article_id, (kind, text) in annotations.items()],
            )

    def run(self, periods: Optional[list] = None, force: bool = False, delay_seconds: int = 0,
            sources: Optional[list] = None) -> dict:
        if not self.db_path.exists():
            raise FileNotFoundError(f"БД не знайдено: {self.db_path}. Спершу виконайте python -m scripts.load_to_db")
        with closing(sqlite3.connect(self.db_path)) as conn:
            ensure_tables(conn)
            grouped = load_period_articles(conn)
            existing = {
                (row[0], row[1])
                for row in conn.execute("SELECT source, period FROM period_summaries")
            }
            targets = [
                (source, period)
                for source, period in sorted(grouped)
                if (not periods or period in periods)
                and (not sources or source in sources)
                and (force or (source, period) not in existing)
            ]
            skipped = [
                f"{source}/{period}"
                for source, period in sorted(grouped)
                if (not periods or period in periods)
                and (not sources or source in sources)
                and not force
                and (source, period) in existing
            ]
            report: dict = {"done": [], "failed": [], "skipped": skipped}
            for index, (source, period) in enumerate(targets):
                try:
                    result, annotations = self.synthesize_period(conn, source, period, grouped[(source, period)])
                    self.save_summary(conn, source, period, grouped[(source, period)], result, annotations)
                    report["done"].append(f"{source}/{period}")
                    logger.info("Джерело %s, період %s: резюме збережено, анотацій: %d",
                                source, period, len(annotations))
                except Exception as err:
                    report["failed"].append(f"{source}/{period}")
                    logger.error("Джерело %s, період %s пропущено: %s", source, period, err)
                if delay_seconds and index < len(targets) - 1:
                    time.sleep(delay_seconds)
        return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Агент-Синтезатор: резюме та анотації за піврічними періодами")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--period", action="append", help="напр. 2023-H1 (можна кілька разів)")
    parser.add_argument("--force", action="store_true", help="перегенерувати вже наявні резюме")
    parser.add_argument("--source", action="append", help="джерело для обробки (можна кілька разів)")
    parser.add_argument("--delay", type=int, default=0, help="пауза між періодами, секунд")
    args = parser.parse_args(argv)

    report = SynthesizerAgent(args.db).run(args.period, args.force, args.delay, args.source)
    print(f"Готово: {len(report['done'])} · пропущено: {len(report['skipped'])} · помилок: {len(report['failed'])}")
    if report["failed"]:
        print("Не вдалося:", ", ".join(report["failed"]), "— запустіть ще раз, збережені періоди не повторюються")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
