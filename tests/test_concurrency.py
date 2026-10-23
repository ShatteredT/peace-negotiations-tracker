import sqlite3
import threading

import pytest

from scripts.init_db import init_db
from src.errors import ConflictError
from src.ui import app

THREADS = 8
ROUNDS = 5


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "race.db"
    init_db(str(path))
    monkeypatch.setattr(app, "DB_PATH", path)
    return path


def race(slot_date):
    barrier = threading.Barrier(THREADS)
    outcomes = []

    def worker(i):
        barrier.wait()  # усі потоки стартують одночасно
        try:
            app.create_note("analyst_1", slot_date, "diplomacy", f"race {i}")
            outcomes.append("ok")
        except ConflictError:
            outcomes.append("conflict")
        except Exception as exc:  # будь-що інше — дефект
            outcomes.append(f"unexpected:{type(exc).__name__}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


def test_parallel_creates_on_same_slot_yield_exactly_one_active_note(db):
    for day in range(1, ROUNDS + 1):
        slot_date = f"2024-02-{day:02d}"
        outcomes = race(slot_date)
        assert outcomes.count("ok") == 1, outcomes
        assert outcomes.count("conflict") == THREADS - 1, outcomes
        with sqlite3.connect(db) as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM expert_notes WHERE event_date = ? AND status = 'PUBLISHED'", (slot_date,)
            ).fetchone()[0]
        assert active == 1
