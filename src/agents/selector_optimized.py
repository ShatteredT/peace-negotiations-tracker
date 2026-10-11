import json
import time
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from src.config import DATA_DIR, GEMINI_API_KEY, SELECTOR_MODEL, validate_config
from src.logger import get_logger

try:
    from google import genai
    from google.genai import types

    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

logger = get_logger("selector_optimized")

WAR_START = "2022-02-24"
TARGET_PER_PERIOD = 20  # ~20 статей за 6 місяців
PERIOD_MONTHS = 6  # період в місяцях

SELECTOR_PROMPT = """
Ти — експерт з відбору новин про МИРНІ ПЕРЕГОВОРИ Україна-Росія (2022-2026).

Дано: список заголовків з датами за ПЕРІОД (6 місяців).
Завдання: обери ТІЛЬКИ найрелевантніші про МИРНІ ПЕРЕГОВОРИ.

Критерії TRUE (миротворчі треки):
- Прямі переговори/зустрічі делегацій (Стамбул, Женева, Білорусь)
- Припинення вогню, перемир'я, гуманітарні коридори
- Обмін полоненими, "всіх на всіх"
- Безпекові гарантії, нейтральний статус, неблокований статус
- Територіальні угоди/поступки, кордони 1991
- Заяви Зеленського/Путіна/МЗС про мир/переговори
- Мирні ініціативи від США, ЄС, Китаю, Африки, арабських країн
- Відмови від переговорів або імітація переговорів

Критерії FALSE (НЕ брати):
- Лише бої/удари/атаки БЕЗ згадування переговорів
- Постачання зброї, санкції, економіка
- Внутрішня політика, вибори, судові процеси
- Розслідування воєнних злочинів, Бучa, Маріуполь (якщо без мирного контексту)

Поверни JSON:
{
  "selected_indices": [0, 5, 12, ...],  // індекси з 0 в масиві titles
  "reasoning": "коротко чому обрано саме ці"
}
"""


@dataclass
class SelectedArticle:
    id: str
    title: str
    date: str
    source: str
    url: str
    text: str
    period: str
    llm_reason: str


class OptimizedSelectorAgent:
    def __init__(self, db_path: str = str(DATA_DIR / "selector_opt.db"), max_llm_calls_per_period: int = 1):
        self.db_path = db_path
        self.max_llm_calls_per_period = max_llm_calls_per_period
        self._init_db()
        if HAS_GENAI:
            validate_config()
            self.client = genai.Client(api_key=GEMINI_API_KEY)
            self.model_name = SELECTOR_MODEL
        else:
            self.client = None

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source TEXT,
                    period TEXT,
                    processed_indices TEXT,
                    selected_count INTEGER,
                    PRIMARY KEY (source, period)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS selected_articles (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    period TEXT,
                    title TEXT,
                    date TEXT,
                    url TEXT,
                    text TEXT,
                    llm_reason TEXT,
                    selected_at TEXT
                )
            """)

    def _get_period_key(self, date_str: str) -> str:
        """Convert YYYY-MM to 6-month period key (YYYY-H1 or YYYY-H2)."""
        year = int(date_str[:4])
        month = int(date_str[5:7])
        half = "H1" if month <= 6 else "H2"
        return f"{year}-{half}"

    def _group_by_period(self, articles: list[dict]) -> dict[str, list[dict]]:
        """Групує статті за 6-місячними періодами (YYYY-H1/H2), фільтрує від 24.02.2022"""
        grouped = {}
        for i, a in enumerate(articles):
            date_str = a.get("date", "")[:10]
            if not date_str or date_str < WAR_START:
                continue
            period = self._get_period_key(date_str)
            a["_orig_idx"] = i
            grouped.setdefault(period, []).append(a)
        return grouped

    def _load_checkpoint(self, source: str, period: str) -> tuple[set, int]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT processed_indices, selected_count FROM checkpoints WHERE source=? AND period=?", (source, period)
            ).fetchone()
            if row:
                return set(json.loads(row[0])), row[1]
        return set(), 0

    def _save_checkpoint(self, source: str, period: str, processed: set, selected: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (source, period, processed_indices, selected_count)
                VALUES (?, ?, ?, ?)
            """,
                (source, period, json.dumps(list(processed)), selected),
            )

    def _save_selected(self, art: SelectedArticle):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO selected_articles
                (id, source, period, title, date, url, text, llm_reason, selected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    art.id,
                    art.source,
                    art.period,
                    art.title,
                    art.date,
                    art.url,
                    art.text,
                    art.llm_reason,
                    datetime.now().isoformat(),
                ),
            )

    def _call_llm_all(
        self, titles_with_dates: list[tuple[str, str]], period: str, max_retries: int = 5, target_count: int = 60
    ) -> dict:
        """Надсилає ВСІ заголовки періоду в ОДНОМУ запиті, просить повернути топ-N найрелевантніших."""
        items = [f"{i}. [{d}] {t}" for i, (t, d) in enumerate(titles_with_dates)]
        prompt = (
            f"Період: {period}\n"
            f"Всього статей: {len(titles_with_dates)}\n"
            f"Потрібно відібрати ТІЛЬКИ {target_count} найрелевантніших про МИРНІ ПЕРЕГОВОРИ.\n\n" + "\n".join(items)
        )

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SELECTOR_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                return json.loads(response.text)

            except Exception as e:
                err_str = str(e)
                is_retryable = "429" in err_str or "503" in err_str or "UNAVAILABLE" in err_str

                if is_retryable and attempt < max_retries:
                    wait_time = 25  # фіксована пауза 25 секунд
                    logger.warning(f"[{period}] Rate limit/error. Attempt {attempt}/{max_retries}. Wait {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"[{period}] LLM failed: {e}")
                    raise

        return {"selected_indices": [], "reasoning": "LLM failed after retries"}

    def select_single_period(self, articles: list[dict], source: str, target_period: str) -> list[SelectedArticle]:
        """Один LLM-запит на один піврічний період; повністю підтримує resume через checkpoints."""
        if not self.client:
            raise RuntimeError("google-genai not available")

        grouped = self._group_by_period(articles)
        if target_period not in grouped:
            raise ValueError(f"Period {target_period} not found in data. Available: {list(grouped.keys())}")

        processed, selected_count = self._load_checkpoint(source, target_period)
        quota = TARGET_PER_PERIOD
        if selected_count >= quota:
            logger.info("[%s/%s] Quota met (%d/%d), skipping", source, target_period, selected_count, quota)
            return []

        remaining = [a for a in grouped[target_period] if a["_orig_idx"] not in processed]
        if not remaining:
            logger.info("[%s/%s] No remaining articles to process", source, target_period)
            return []

        need = quota - selected_count
        logger.info("[%s/%s] %d articles in SINGLE CALL, need %d", source, target_period, len(remaining), need)

        titles_dates = [(a.get("title", ""), a.get("date", "")[:10]) for a in remaining]
        try:
            result = self._call_llm_all(titles_dates, target_period, target_count=need)
        except Exception as e:
            logger.error("[%s/%s] SINGLE CALL failed: %s", source, target_period, e)
            return []  # checkpoint не змінюємо — період буде повторено при наступному запуску

        reasoning = result.get("reasoning", "")
        # валідні унікальні індекси, не більше за потрібну кількість
        seen, indices = set(), []
        for idx in result.get("selected_indices", []):
            if isinstance(idx, int) and 0 <= idx < len(remaining) and idx not in seen:
                seen.add(idx)
                indices.append(idx)
        indices = indices[:need]

        chosen = []
        for idx in indices:
            art = remaining[idx]
            selected = SelectedArticle(
                id=art.get("id", art.get("url", f"{source}_{art['_orig_idx']}")),
                title=art.get("title", ""),
                date=art.get("date", "")[:10],
                source=source,
                url=art.get("url", ""),
                text=art.get("text", ""),
                period=target_period,
                llm_reason=reasoning,
            )
            self._save_selected(selected)
            chosen.append(selected)

        # якщо усі remaining вже розглянуті моделлю - позначаємо їх оброблені,
        # інакше resume повторював би той самий запит.
        processed.update(a["_orig_idx"] for a in remaining)
        self._save_checkpoint(source, target_period, processed, selected_count + len(chosen))
        logger.info(
            "[%s/%s] Selected %d (total %d/%d)", source, target_period, len(chosen), selected_count + len(chosen), quota
        )
        return chosen

    def select_from_source(self, articles: list[dict], source: str) -> list[SelectedArticle]:
        all_selected = []
        for period in sorted(self._group_by_period(articles)):
            all_selected.extend(self.select_single_period(articles, source, period))
        return all_selected

    def export_results(self, output_path: str):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT id, source, period, title, date, url, text, llm_reason
                FROM selected_articles ORDER BY source, period, date
            """).fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "id": row[0],
                    "source": row[1],
                    "period": row[2],
                    "title": row[3],
                    "date": row[4],
                    "url": row[5],
                    "text": row[6],
                    "llm_reason": row[7],
                }
            )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"Exported {len(results)} articles to {output_path}")


def load_bbc_articles() -> list[dict]:
    """Спершу шукає data/BBC/filtered.json локально; якщо немає — бере з git (FETCH_HEAD)."""
    local = DATA_DIR / "BBC" / "filtered.json"
    if local.exists():
        data = json.loads(local.read_text(encoding="utf-8"))
    else:
        result = subprocess.run(
            ["git", "show", "FETCH_HEAD:data/BBC/filtered.json"],
            capture_output=True,
        )
        if result.returncode != 0:
            raise FileNotFoundError(
                f"Не знайдено {local} і не вдалося прочитати FETCH_HEAD:data/BBC/filtered.json. "
                "Покладіть корпус у data/BBC/filtered.json або зробіть git fetch потрібної гілки."
            )
        data = json.loads(result.stdout.decode("utf-8", errors="ignore"))
    for a in data:
        a["source"] = "bbc"
    return data


def list_periods(articles: list[dict]):
    """Show available periods and stats."""
    WAR_START = "2022-02-24"
    periods = set()
    for a in articles:
        d = a.get("date", "")[:10]
        if d and d >= WAR_START:
            year = int(d[:4])
            month = int(d[5:7])
            periods.add(f"{year}-{'H1' if month <= 6 else 'H2'}")

    periods = sorted(periods)
    print("\nДоступні періоди:")
    for i, p in enumerate(periods):
        print(f"  {i+1}. {p}")
    return periods


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Peacetalk Tracker - Article Selector (single call per period)")
    parser.add_argument("--period", "-p", type=str, help="Specific period to process (e.g., 2022-H1)")
    parser.add_argument("--list", "-l", action="store_true", help="List available periods and exit")
    parser.add_argument("--source", "-s", type=str, default="bbc", help="Source to process (default: bbc)")
    args = parser.parse_args()

    if not HAS_GENAI:
        print("ERROR: google-genai not installed")
        exit(1)

    print("=" * 60)
    print("OPTIMIZED SELECTOR: title+date only, 6-month periods, SINGLE CALL")
    print(f"Target: {TARGET_PER_PERIOD} articles per {PERIOD_MONTHS}-month period")
    print("=" * 60)

    agent = OptimizedSelectorAgent()

    # BBC
    print("\nLoading BBC articles...")
    bbc = load_bbc_articles()
    print(f"Total BBC: {len(bbc)}")

    if args.list:
        list_periods(bbc)
        exit(0)

    if args.period:
        print(f"\nProcessing single period: {args.period}")
        selected = agent.select_single_period(bbc, args.source, args.period)
        print(f"\nPeriod {args.period}: selected {len(selected)} articles")
    else:
        print("\nProcessing ALL periods...")
        selected = agent.select_from_source(bbc, args.source)
        print(f"\nTotal selected: {len(selected)}")

    agent.export_results(str(DATA_DIR / "filtered_corpus_optimized.json"))
    print("Done! Check data/filtered_corpus_optimized.json")
