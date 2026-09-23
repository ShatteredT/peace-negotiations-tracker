import os
from dotenv import load_dotenv


load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def validate_config():
    if not GEMINI_API_KEY or "ваш_справжній_ключ" in GEMINI_API_KEY:
        raise ValueError(
            "Помилка: GEMINI_API_KEY не налаштовано або містить шаблонний текст у файлі .env!\n"
            "Отримайте безкоштовний ключ у Google AI Studio та вставте його у файл .env."
        )

# Перевірка при прямому запуску файлу
if __name__ == "__main__":
    try:
        validate_config()
        print("Конфігурація успішна: GEMINI_API_KEY знайдено.")
    except ValueError as e:
        print(e)