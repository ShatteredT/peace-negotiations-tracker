import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "peacetalk.db"

load_dotenv(BASE_DIR / ".env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Моделі винесені в .env, щоб не правити код при зміні версії.
ANALYST_MODEL = os.getenv("ANALYST_MODEL", "gemini-3.5-flash-lite")
SELECTOR_MODEL = os.getenv("SELECTOR_MODEL", "gemini-3.8-flash")
SYNTHESIZER_MODEL = os.getenv("SYNTHESIZER_MODEL", "gemini-3.5-flash-lite")

_PLACEHOLDERS = ("ваш_справжній_ключ", "your_gemini_api_key_here")


def validate_config():
    if not GEMINI_API_KEY or any(p in GEMINI_API_KEY for p in _PLACEHOLDERS):
        raise ValueError(
            "Помилка: GEMINI_API_KEY не налаштовано або містить шаблонний текст у файлі .env!\n"
            "Отримайте безкоштовний ключ у Google AI Studio та вставте його у файл .env."
        )


if __name__ == "__main__":
    try:
        validate_config()
        print("Конфігурація успішна: GEMINI_API_KEY знайдено.")
    except ValueError as e:
        print(e)
