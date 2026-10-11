"""Спостереження за сервісом: журнал операцій, метрики та правило сповіщення.

Журнал — файл JSON Lines (logs/ops.jsonl), по одному рядку на операцію:
    {"ts": "2026-10-10T12:00:00Z", "op": "create_note", "result": "ok",
     "error": null, "ms": 1.4, "request_id": "a1b2c3d4"}

* ``result``: ``ok`` — успіх; ``rejected`` — очікувана відмова (валідація / конфлікт / права);
  ``error`` — технічний збій.
* ``request_id`` — безпечний ідентифікатор: він показується користувачу при технічній помилці
  і дозволяє знайти відповідний рядок у журналі. Текст замітки в журнал НЕ потрапляє.
* Час скрізь у UTC (ISO 8601 із суфіксом Z).
"""
import functools
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.errors import NoteError, TechnicalFailure

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = Path(os.getenv("OPS_LOG_PATH", str(BASE_DIR / "logs" / "ops.jsonl")))
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_lock = threading.Lock()


# час (UTC)
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Єдиний формат часу для зберігання: 2026-10-10T12:00:00Z."""
    return utc_now().strftime(TS_FORMAT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


# запис
def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def record(op: str, result: str, ms: float = 0.0, error: Optional[str] = None,
           request_id: Optional[str] = None) -> str:
    """Дописує один рядок у журнал і повертає request_id. Збій запису журналу не ламає операцію."""
    rid = request_id or new_request_id()
    entry = {"ts": utc_now_iso(), "op": op, "result": result, "error": error,
             "ms": round(ms, 2), "request_id": rid}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rid


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def observed(op: str):
    """Декоратор: вимірює тривалість, класифікує результат і пише рядок у журнал.

    NoteError пробрасується як є (``rejected``); будь-який інший виняток стає
    ``TechnicalFailure`` із кодом request_id (``error``).
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            rid = new_request_id()
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except NoteError as exc:
                record(op, "rejected", _elapsed_ms(started), type(exc).__name__, rid)
                raise
            except Exception as exc:
                record(op, "error", _elapsed_ms(started), type(exc).__name__, rid)
                raise TechnicalFailure(rid) from exc
            record(op, "ok", _elapsed_ms(started), None, rid)
            return result
        return wrapper
    return decorator


# читання й метрики
def read_events(path: Optional[Path] = None) -> list:
    path = Path(path or LOG_PATH)
    if not path.exists():
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # пошкоджений рядок не має ламати звіт
    return events


def percentile(values: list, p: float) -> float:
    """Перцентиль за методом найближчого рангу."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(events: list) -> dict:
    """Метрики за операціями: кількість, успіхи/відмови/збої, p50/p95/max тривалості."""
    by_op: dict = {}
    for e in events:
        by_op.setdefault(e["op"], []).append(e)
    summary = {}
    for op, items in by_op.items():
        durations = [e["ms"] for e in items]
        total = len(items)
        errors = sum(1 for e in items if e["result"] == "error")
        summary[op] = {
            "total": total,
            "ok": sum(1 for e in items if e["result"] == "ok"),
            "rejected": sum(1 for e in items if e["result"] == "rejected"),
            "error": errors,
            "error_rate": errors / total,
            "p50_ms": percentile(durations, 50),
            "p95_ms": percentile(durations, 95),
            "max_ms": max(durations),
        }
    return summary


def evaluate_alert(events: list, now: Optional[datetime] = None,
                   window_minutes: int = 5, max_errors: int = 3) -> tuple:
    """Правило сповіщення: ALERT, якщо технічних збоїв за останні N хвилин >= max_errors.

    Повертає (спрацювало, кількість_збоїв_у_вікні).
    """
    now = now or utc_now()
    since = now - timedelta(minutes=window_minutes)
    count = sum(1 for e in events if e["result"] == "error" and parse_ts(e["ts"]) >= since)
    return count >= max_errors, count
