import json
from datetime import timedelta

import pytest

from scripts import ops_report
from scripts.init_db import init_db
from src import ops
from src.errors import ConflictError, TechnicalFailure
from src.ui import app


def events():
    return ops.read_events()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ops.db"
    init_db(str(path))
    monkeypatch.setattr(app, "DB_PATH", path)
    return path


# декоратор і журнал
def test_successful_operation_is_logged_as_ok(db):
    app.create_note("analyst_1", "2024-01-15", "diplomacy", "text")
    (event,) = events()
    assert event["op"] == "create_note" and event["result"] == "ok" and event["error"] is None
    assert event["ms"] >= 0 and len(event["request_id"]) == 8
    assert event["ts"].endswith("Z")


def test_business_refusal_is_logged_as_rejected_with_error_class(db):
    app.create_note("analyst_1", "2024-01-15", "diplomacy", "a")
    with pytest.raises(ConflictError):
        app.create_note("analyst_1", "2024-01-15", "diplomacy", "b")
    assert [(e["result"], e["error"]) for e in events()] == [("ok", None), ("rejected", "ConflictError")]


def test_technical_failure_is_logged_and_request_id_matches_user_message(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "missing.db")
    with pytest.raises(TechnicalFailure) as exc_info:
        app.create_note("analyst_1", "2024-01-15", "diplomacy", "text")
    (event,) = events()
    assert event["result"] == "error" and event["error"] == "OperationalError"
    assert event["request_id"] == exc_info.value.request_id, "код з UI знаходить рядок у журналі"


def test_note_text_never_reaches_the_log(db):
    secret = "СЕКРЕТНИЙ-ТЕКСТ-ЗАМІТКИ"
    app.create_note("analyst_1", "2024-01-15", "diplomacy", secret)
    assert secret not in ops.LOG_PATH.read_text(encoding="utf-8")


def test_corrupted_log_line_does_not_break_reading(db):
    app.create_note("analyst_1", "2024-01-15", "diplomacy", "a")
    with open(ops.LOG_PATH, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    assert len(events()) == 1


# метрики
def make_event(result="ok", ms=1.0, op="create_note", ago_minutes=0):
    ts = (ops.utc_now() - timedelta(minutes=ago_minutes)).strftime(ops.TS_FORMAT)
    return {"ts": ts, "op": op, "result": result, "error": None, "ms": ms, "request_id": "r"}


def test_percentiles_use_nearest_rank():
    values = [float(i) for i in range(1, 101)]
    assert ops.percentile(values, 50) == 50.0
    assert ops.percentile(values, 95) == 95.0
    assert ops.percentile([], 95) == 0.0


def test_summary_counts_results_and_durations():
    data = [make_event("ok", 10), make_event("ok", 20), make_event("rejected", 5), make_event("error", 40)]
    s = ops.summarize(data)["create_note"]
    assert (s["total"], s["ok"], s["rejected"], s["error"]) == (4, 2, 1, 1)
    assert s["error_rate"] == 0.25 and s["max_ms"] == 40


# правило сповіщення
def test_alert_fires_at_threshold_and_not_below():
    two = [make_event("error"), make_event("error")]
    three = two + [make_event("error")]
    assert ops.evaluate_alert(two, window_minutes=5, max_errors=3) == (False, 2)
    assert ops.evaluate_alert(three, window_minutes=5, max_errors=3) == (True, 3)


def test_alert_ignores_old_failures_and_business_refusals():
    data = [make_event("error", ago_minutes=30)] * 5 + [make_event("rejected")] * 5
    assert ops.evaluate_alert(data, window_minutes=5, max_errors=3) == (False, 0)


# CLI-звіт і healthcheck
def write_log(path, items):
    path.write_text("\n".join(json.dumps(i) for i in items) + "\n", encoding="utf-8")


def test_report_exit_code_0_when_healthy(tmp_path, capsys):
    log = tmp_path / "healthy.jsonl"
    write_log(log, [make_event("ok", 3), make_event("rejected", 2)])
    assert ops_report.main(["--log", str(log)]) == 0
    assert "create_note" in capsys.readouterr().out


def test_report_exit_code_2_on_alert(tmp_path, capsys):
    log = tmp_path / "incident.jsonl"
    write_log(log, [make_event("error")] * 3)
    assert ops_report.main(["--log", str(log), "--max-errors", "3"]) == 2
    assert "ALERT" in capsys.readouterr().out


def test_probe_reports_ok_for_healthy_db_and_failure_for_missing_db(db, tmp_path):
    assert ops_report.probe_db(db) is True
    assert ops_report.probe_db(tmp_path / "gone.db") is False
    assert [(e["op"], e["result"]) for e in events()] == [("healthcheck", "ok"), ("healthcheck", "error")]
    assert not (tmp_path / "gone.db").exists(), "probe не створює порожню БД"
