import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = Path(os.getenv(
    "AI_PIPELINE_LOG_PATH", str(BASE_DIR / "logs" / "ai_pipeline.log")
))
_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(_FORMAT)
        file_handler = RotatingFileHandler(
            LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.propagate = False
    return logger


if __name__ == "__main__":
    logger = get_logger("test_logger")
    logger.info("Система логування працює коректно!")
