import logging
import os

# Створюємо директорію для логів, якщо вона відсутня
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    handlers=[
        logging.FileHandler("logs/ai_pipeline.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def get_logger(name: str):
    return logging.getLogger(name)

if __name__ == "__main__":
    logger = get_logger("test_logger")
    logger.info("Система логування працює коректно!")