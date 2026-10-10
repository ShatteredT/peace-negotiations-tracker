"""Звіт про метрики операцій і перевірка правила сповіщення (вимога 8.2 лабораторної).

Запуск з кореня проєкту:
    python -m scripts.ops_report                       # метрики за весь журнал + перевірка ALERT
    python -m scripts.ops_report --probe               # спершу перевірити БД (healthcheck), потім звіт
    python -m scripts.ops_report --window 5 --max-errors 3

Правило сповіщення: ALERT, якщо технічних збоїв (result = error) за останні --window хвилин
не менше ніж --max-errors. Код завершення: 0 — норма, 2 — ALERT.

Як простежити невдалу операцію: у UI при технічному збої показується «Код для пошуку в
журналі: <request_id>» -> знайдіть цей request_id у logs/ops.jsonl (рядок містить час,
операцію, клас помилки й тривалість).
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

from src import ops

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "peacetalk.db"


def probe_db(db_path) -> bool:
    """Перевірка доступності БД: читання таблиці expert_notes. Пише подію healthcheck у журнал."""
    started = time.perf_counter()
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=3)
        try:
            conn.execute("SELECT COUNT(*) FROM expert_notes").fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        ops.record("healthcheck", "error", (time.perf_counter() - started) * 1000, type(exc).__name__)
        return False
    ops.record("healthcheck", "ok", (time.perf_counter() - started) * 1000)
    return True


def print_summary(summary: dict) -> None:
    header = f"{'операція':<16}{'всього':>7}{'ok':>6}{'відмов':>8}{'збоїв':>7}{'p50 мс':>9}{'p95 мс':>9}{'max мс':>9}"
    print(header)
    print("-" * len(header))
    for op, m in sorted(summary.items()):
        print(f"{op:<16}{m['total']:>7}{m['ok']:>6}{m['rejected']:>8}{m['error']:>7}"
              f"{m['p50_ms']:>9.1f}{m['p95_ms']:>9.1f}{m['max_ms']:>9.1f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Звіт про метрики операцій та перевірка ALERT")
    parser.add_argument("--log", default=str(ops.LOG_PATH), help="шлях до журналу JSONL")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="БД для --probe")
    parser.add_argument("--probe", action="store_true", help="виконати healthcheck БД перед звітом")
    parser.add_argument("--window", type=int, default=5, help="вікно правила сповіщення, хвилин")
    parser.add_argument("--max-errors", type=int, default=3, help="поріг технічних збоїв у вікні")
    args = parser.parse_args(argv)

    ops.LOG_PATH = Path(args.log)  # запис probe і читання звіту працюють з одним файлом
    if args.probe:
        print("healthcheck БД:", "OK" if probe_db(args.db) else "ЗБІЙ")

    events = ops.read_events(ops.LOG_PATH)
    if not events:
        print(f"Журнал порожній або відсутній: {ops.LOG_PATH}")
        return 0
    print_summary(ops.summarize(events))

    fired, count = ops.evaluate_alert(events, window_minutes=args.window, max_errors=args.max_errors)
    status = "ALERT" if fired else "норма"
    print(f"\nПравило: >= {args.max_errors} збоїв за {args.window} хв. Збоїв у вікні: {count} -> {status}")
    return 2 if fired else 0


if __name__ == "__main__":
    sys.exit(main())
