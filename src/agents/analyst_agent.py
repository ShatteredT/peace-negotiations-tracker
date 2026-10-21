import json
import time
from pathlib import Path

from google import genai
from google.genai import types

from src.agents.analyst_schemas import BatchAnalysis
from src.config import ANALYST_MODEL, DATA_DIR, GEMINI_API_KEY, validate_config
from src.logger import get_logger

logger = get_logger("analyst_agent")

SYSTEM_PROMPT = """
Ти — аналітик з мирних переговорів. Твоє завдання — оцінити пачку новин за шкалою від -1.0 до +1.0:
-1.0: максимальна ескалація, агресія, повна відмова від переговорів.
 0.0: нейтральна риторика, констатація фактів без явних сигналів.
+1.0: готовність до компромісів, конструктивні мирні ініціативи, підписання угод.

Оціни обов'язково 4 аспекти для КОЖНОЇ статті:
1. дипломатичні контакти
2. безпекові гарантії / припинення вогню
3. територіальні поступки чи непохитність кордонів
4. гуманітарні питання, як-от обмін полоненими

Для кожного з 4 аспектів ОБОВ'ЯЗКОВО вкажи confidence від 0.0 до 1.0 — наскільки текст
статті дає підстав для цієї оцінки. Не пропускай це поле й не повертай null:
0.0 — текст не дає підстав, аспект не згадується, оцінка здогадкова;
0.5 — підстави непрямі (натяки, контекст, цитати з посиланням на інших);
1.0 — пряма заява в тексті, оцінка однозначно випливає з нього.

Поверни РІВНО ОДИН результат на кожну статтю, з тим самим article_id, що в запиті.
Повертай відповідь СУВОРО за наданою JSON-схемою (BatchAnalysis).
"""

RETRYABLE_MARKERS = ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED")


class AnalystAgent:
    def __init__(self, output_file: Path = DATA_DIR / "analyzed_articles.json"):
        validate_config()
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = ANALYST_MODEL
        self.batch_size = 15
        self.output_file = Path(output_file)

    # prompt
    @staticmethod
    def _create_batch_prompt(articles: list) -> str:
        parts = []
        for i, article in enumerate(articles):
            parts.append(f"=== ARTICLE {i} ===")
            parts.append(f"ID: {article['id']}")
            parts.append(f"Title: {article['title']}")
            parts.append(f"Date: {article['date']}")
            parts.append(f"Text: {article['text'][:2000]}")
            parts.append("")
        return "\n".join(parts)

    # LLM
    def _call_llm_batch(self, prompt: str, max_retries: int = 5) -> BatchAnalysis:
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=BatchAnalysis,
                        temperature=0.1,
                    ),
                )
                return BatchAnalysis.model_validate_json(response.text)
            except Exception as err:
                retryable = any(m in str(err) for m in RETRYABLE_MARKERS)
                if retryable and attempt < max_retries:
                    wait_time = 20 * attempt  # лінійний backoff: 20, 40, 60, 80 с
                    logger.warning(
                        "Rate limit/unavailable. Attempt %d/%d. Wait %ds...",
                        attempt, max_retries, wait_time,
                    )
                    time.sleep(wait_time)
                else:
                    logger.error("Batch LLM failed: %s", err)
                    raise
        raise RuntimeError("unreachable")  # цикл завжди повертає або піднімає виняток

    # storage
    def _load_existing_results(self) -> list:
        if self.output_file.exists():
            try:
                with open(self.output_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                logger.info("Loaded %d existing results from %s", len(existing), self.output_file)
                return existing
            except Exception as e:
                logger.warning("Could not load existing results: %s", e)
        return []

    def _save_results(self, results: list):
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.output_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        tmp.replace(self.output_file)  # атомарно: файл не буде напівзаписаним
        logger.info("Saved %d results to %s", len(results), self.output_file)

    # main loop
    def analyze_batch(self, articles: list, delay_seconds: int = 30) -> list:
        results = self._load_existing_results()
        processed_ids = {item["article_id"] for item in results}

        todo = [a for a in articles if a["id"] not in processed_ids]
        logger.info(
            "Total articles: %d, already processed: %d, to process: %d",
            len(articles), len(processed_ids), len(todo),
        )

        for start in range(0, len(todo), self.batch_size):
            batch = todo[start:start + self.batch_size]
            batch_ids = {a["id"] for a in batch}
            logger.info("Batch %d: %d articles", start // self.batch_size + 1, len(batch))

            analyses = self._call_llm_batch(self._create_batch_prompt(batch)).analyses

            added = 0
            for analysis in analyses:
                if analysis.article_id not in batch_ids:
                    logger.warning("Unknown article_id in response: %s", analysis.article_id)
                    continue
                if analysis.article_id in processed_ids:
                    logger.warning("Duplicate article_id in response: %s", analysis.article_id)
                    continue
                results.append(analysis.model_dump())
                processed_ids.add(analysis.article_id)
                added += 1

            missing = batch_ids - processed_ids
            if missing:
                # ці статті підуть у наступний запуск (resume)
                logger.warning("Model skipped %d article(s): %s", len(missing), sorted(missing))

            self._save_results(results)

            if start + self.batch_size < len(todo):
                logger.info("Delay %ds before next batch...", delay_seconds)
                time.sleep(delay_seconds)

        return results


if __name__ == "__main__":
    input_file = DATA_DIR / "found_articles.json"
    if not input_file.exists():
        print(f"Файл не знайдено: {input_file}")
        raise SystemExit(1)

    with open(input_file, "r", encoding="utf-8") as f:
        articles = json.load(f)

    print(f"Починаємо обробку {len(articles)} статей через Gemini...")
    agent = AnalystAgent()
    results = agent.analyze_batch(articles, delay_seconds=30)
    print(f"\nГотово! Оброблено: {len(results)} статей → {agent.output_file}")
