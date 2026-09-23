import json
import time
from google import genai
from google.genai import types
from pathlib import Path
from src.config import GEMINI_API_KEY, validate_config
from src.logger import get_logger
from src.agents.analyst_schemas import ArticleAnalysis

logger = get_logger("analyst_agent")
SYSTEM_PROMPT = """
Ти — аналітик з мирних переговорів. Твоє завдання — оцінити новину за шкалою від -1.0 до +1.0:
-1.0: максимальна ескалація, агресія, повна відмова від переговорів.
 0.0: нейтральна риторика, констатація фактів без явних сигналів.
+1.0: готовність до компромісів, конструктивні мирні ініціативи, підписання угод.

Оціни обов'язково 4 аспекти:
1. дипломатичні контакти
2. безпекові гарантії / припинення вогню
3. територіальні поступки чи непохитність кордонів
4. гуманітарні питання, як-от обмін полоненими

Повертай відповідь СУВОРО за наданою JSON-схемою.
"""


class AnalystAgent:
    def __init__(self):
        validate_config()
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-3.6-flash"

    def analyze_article(self, article_id: str, title: str, text: str, max_retries: int = 3) -> ArticleAnalysis:
        logger.info(f"Аналіз статті ID: {article_id} | '{title[:35]}...'")
        prompt_content = f"Title: {title}\nText: {text}"

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt_content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=ArticleAnalysis,
                        temperature=0.1,
                    ),
                )
                parsed_data = ArticleAnalysis.model_validate_json(response.text)
                logger.info(f"Успішно оброблено ID {article_id}: score = {parsed_data.overall_sentiment}")
                return parsed_data

            except Exception as err:
                err_str = str(err)
                # Якщо сервер тимчасово перевантажений (503) або обмежив частоту (429)
                if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str) and attempt < max_retries:
                    wait_time = attempt * 5  # чекаємо 5с, потім 10с
                    logger.warning(f"Сервер перевантажений (503). Спроба {attempt}/{max_retries}. Очікування {wait_time} с...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Помилка обробки ID {article_id}: {err}")
                    raise err


if __name__ == "__main__":
    # Знаходимо корінь проєкту: піднімаємося на 2 рівні вгору від src/agents/
    BASE_DIR = Path(__file__).resolve().parent.parent.parent

    mock_file = BASE_DIR / "data" / "raw_mock_articles.json"
    output_file = BASE_DIR / "data" / "analyzed_articles.json"

    if not mock_file.exists():
        print(f"Файл не знайдено за шляхом: {mock_file}")
        exit(1)

    with open(mock_file, "r", encoding="utf-8") as f:
        articles = json.load(f)

    print(f"Починаємо обробку {len(articles)} статей через Gemini...")
    agent = AnalystAgent()
    results = []

    for item in articles:
        try:
            analysis = agent.analyze_article(
                article_id=item["id"],
                title=item["title"],
                text=item["text"]
            )
            results.append(analysis.model_dump())

            # Затримка між викликами за регламентом (Free Tier захист)
            time.sleep(3)
        except Exception as e:
            print(f"Пропуск {item['id']} через помилку: {e}")

    # Зберігаємо результат розмітки
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nГотово! Результати збережено у: {output_file}")


