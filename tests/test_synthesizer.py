import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scripts.init_db import init_db
from scripts.load_to_db import load_articles, load_assessments
from src.agents import synthesizer_agent as agent_module
from src.agents.synthesizer_agent import (
    MAX_ATTEMPTS, RETRY_DELAY_SECONDS, SynthesizerAgent, period_bounds, period_of, select_extremes,
)
from src.agents.synthesizer_schemas import PeriodSynthesis

CATEGORIES = ("diplomacy", "security", "territorial", "humanitarian")
ARTICLES = [
    ("a1", "2023-02-01", 0.8), ("a2", "2023-03-01", 0.2), ("a3", "2023-04-01", -0.3), ("a4", "2023-05-01", -0.9),
    ("a5", "2023-08-01", 0.1), ("a6", "2023-09-01", -0.1),
]


def respond(prompt: str) -> str:
    ids = re.findall(r"^(\S+) — (?:пік|спад)$", prompt, flags=re.M)
    payload = {
        "trend": "mixed",
        "summary": "Період відзначився змішаною динамікою переговорів і безпекових заяв.",
        "annotations": [{"article_id": i, "text": f"Анотація до {i}"} for i in [*ids, "ghost"]],
    }
    return json.dumps(payload, ensure_ascii=False)


class FakeModels:
    def __init__(self, behaviors=()):
        self.behaviors = list(behaviors)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "prompt": contents})
        behavior = self.behaviors.pop(0) if self.behaviors else None
        if isinstance(behavior, Exception):
            raise behavior
        return SimpleNamespace(text=respond(contents))


def make_client(*behaviors):
    return SimpleNamespace(models=FakeModels(behaviors))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "synth.db"
    init_db(str(path))
    with sqlite3.connect(path) as conn:
        load_articles(conn, [{"id": i, "source": "bbc", "title": f"Заголовок {i}", "date": d,
                              "url": f"https://x/{i}", "text": "t"} for i, d, _ in ARTICLES])
        load_assessments(conn, [{
            "article_id": i, "overall_sentiment": o, "is_compromise_oriented": o > 0,
            "categories": {c: {"score": o, "justification": "j"} for c in CATEGORIES},
        } for i, _, o in ARTICLES], "1.0")
        conn.executemany(
            "INSERT INTO expert_notes (event_date, metric_category, content, author_id, status) VALUES (?, ?, ?, ?, ?)",
            [("2023-03-10", "diplomacy", "ОПУБЛІКОВАНА-ЗАМІТКА", "analyst_1", "PUBLISHED"),
             ("2023-03-11", "security", "АРХІВНА-ЗАМІТКА", "analyst_2", "ARCHIVED")],
        )
        conn.commit()
    return path


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(agent_module.time, "sleep", recorded.append)
    return recorded


def query(db, sql, params=()):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


# допоміжні функції
def test_period_helpers():
    assert period_of("2023-06-30") == "2023-H1" and period_of("2023-07-01") == "2023-H2"
    assert period_bounds("2023-H1") == ("2023-01-01", "2023-06-30")
    assert period_bounds("2023-H2") == ("2023-07-01", "2023-12-31")


def test_select_extremes_splits_top_and_bottom_without_overlap():
    items = [{"id": f"x{i}", "overall": v} for i, v in enumerate([0.9, 0.5, 0.0, -0.4, -0.8])]
    assert select_extremes(items) == {"x0": "peak", "x1": "peak", "x3": "drop", "x4": "drop"}
    assert select_extremes(items[:3]) == {"x0": "peak", "x2": "drop"}
    assert select_extremes(items[:1]) == {}


def test_schema_accepts_valid_and_rejects_unknown_trend():
    ok = PeriodSynthesis(trend="stable", summary="Текст резюме.", annotations=[])
    assert ok.trend == "stable"
    with pytest.raises(ValidationError):
        PeriodSynthesis(trend="chaos", summary="x", annotations=[])


# збереження (PT-23)
def test_run_saves_summaries_and_annotations_for_each_period(db):
    client = make_client()
    report = SynthesizerAgent(db, client=client).run()
    assert report == {"done": ["bbc/2023-H1", "bbc/2023-H2"], "failed": [], "skipped": []}
    assert query(db, "SELECT source, period, trend, articles_count, model FROM period_summaries ORDER BY period") == [
        ("bbc", "2023-H1", "mixed", 4, "gemini-3.5-flash-lite"),
        ("bbc", "2023-H2", "mixed", 2, "gemini-3.5-flash-lite")]
    kinds = dict(query(db, "SELECT article_id, kind FROM summary_annotations WHERE period = '2023-H1'"))
    assert kinds == {"a1": "peak", "a2": "peak", "a3": "drop", "a4": "drop"}


def test_unknown_article_ids_from_model_are_dropped(db):
    SynthesizerAgent(db, client=make_client()).run()
    assert query(db, "SELECT COUNT(*) FROM summary_annotations WHERE article_id = 'ghost'") == [(0,)]


def test_uses_configured_model_name(db):
    client = make_client()
    SynthesizerAgent(db, client=client).run(["2023-H1"])
    assert {c["model"] for c in client.models.calls} == {"gemini-3.5-flash-lite"}


def test_prompt_contains_only_period_data_and_published_notes(db):
    client = make_client()
    SynthesizerAgent(db, client=client).run(["2023-H1"])
    prompt = client.models.calls[0]["prompt"]
    assert "a1" in prompt and "Заголовок a4" in prompt and "a1 — пік" in prompt and "a4 — спад" in prompt
    assert "ОПУБЛІКОВАНА-ЗАМІТКА" in prompt
    assert "АРХІВНА-ЗАМІТКА" not in prompt and "a5" not in prompt


# resume / force
def test_second_run_skips_existing_summaries_without_calling_model(db):
    client = make_client()
    agent = SynthesizerAgent(db, client=client)
    agent.run()
    report = agent.run()
    assert report["done"] == [] and report["skipped"] == ["bbc/2023-H1", "bbc/2023-H2"]
    assert len(client.models.calls) == 2


def test_force_regenerates_without_duplicates(db):
    client = make_client()
    agent = SynthesizerAgent(db, client=client)
    agent.run()
    agent.run(["2023-H1"], force=True)
    assert len(client.models.calls) == 3
    assert query(db, "SELECT COUNT(*) FROM period_summaries") == [(2,)]
    assert query(db, "SELECT COUNT(*) FROM summary_annotations WHERE period = '2023-H1'") == [(4,)]


# повтори на 503 (PT-22)
def test_503_is_retried_after_exactly_20_seconds(db, sleeps):
    err = Exception("503 UNAVAILABLE. The model is overloaded")
    client = make_client(err, err)
    report = SynthesizerAgent(db, client=client).run(["2023-H1"])
    assert report["done"] == ["bbc/2023-H1"]
    assert sleeps == [20, 20] and RETRY_DELAY_SECONDS == 20
    assert len(client.models.calls) == 3


def test_gives_up_after_max_attempts_and_reports_failure(db, sleeps):
    client = make_client(*[Exception("503 UNAVAILABLE")] * MAX_ATTEMPTS)
    report = SynthesizerAgent(db, client=client).run(["2023-H1"])
    assert report["failed"] == ["bbc/2023-H1"] and report["done"] == []
    assert sleeps == [20] * (MAX_ATTEMPTS - 1)
    assert query(db, "SELECT COUNT(*) FROM period_summaries") == [(0,)]


def test_non_retryable_error_fails_immediately_without_waiting(db, sleeps):
    report = SynthesizerAgent(db, client=make_client(ValueError("boom"))).run(["2023-H1"])
    assert report["failed"] == ["bbc/2023-H1"] and sleeps == []


def test_failed_period_does_not_stop_the_next_one(db, sleeps):
    client = make_client(ValueError("boom"))
    report = SynthesizerAgent(db, client=client).run()
    assert report["failed"] == ["bbc/2023-H1"] and report["done"] == ["bbc/2023-H2"]


def test_invalid_model_json_is_a_failure_not_a_crash(db, sleeps):
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: SimpleNamespace(text='{"trend": "chaos"}')))
    report = SynthesizerAgent(db, client=client).run(["2023-H1"])
    assert report["failed"] == ["bbc/2023-H1"] and sleeps == []


# середовище й CLI
def test_missing_db_raises_and_is_not_created(tmp_path):
    missing = tmp_path / "gone.db"
    with pytest.raises(FileNotFoundError):
        SynthesizerAgent(missing, client=make_client()).run()
    assert not missing.exists()


def test_cli_returns_zero_on_success_and_one_on_failure(db, monkeypatch, capsys, sleeps):
    monkeypatch.setattr(agent_module, "validate_config", lambda: None)
    clients = iter([make_client(), make_client(ValueError("boom"))])
    monkeypatch.setattr(agent_module.genai, "Client", lambda **kw: next(clients))
    assert agent_module.main(["--db", str(db), "--period", "2023-H1"]) == 0
    assert agent_module.main(["--db", str(db), "--period", "2023-H2"]) == 1
    assert "Не вдалося: bbc/2023-H2" in capsys.readouterr().out


def test_synthesizer_keeps_sources_and_notes_separate(db):
    with sqlite3.connect(db) as conn:
        load_articles(conn, [{
            "id": "r1", "source": "radio_svoboda", "title": "Радіо Свобода",
            "date": "2023-02-01", "url": "https://x/r1", "text": "text",
        }])
        load_assessments(conn, [{
            "article_id": "r1", "overall_sentiment": 0.6, "is_compromise_oriented": True,
            "categories": {c: {"score": 0.6, "justification": "j"} for c in CATEGORIES},
        }], "1.0")
        conn.commit()

    client = make_client()
    report = SynthesizerAgent(db, client=client).run()
    assert report["done"] == ["bbc/2023-H1", "bbc/2023-H2", "radio_svoboda/2023-H1"]
    rows = query(db, "SELECT source, period, articles_count FROM period_summaries ORDER BY source, period")
    assert rows == [
        ("bbc", "2023-H1", 4), ("bbc", "2023-H2", 2), ("radio_svoboda", "2023-H1", 1),
    ]
    assert "Джерело: BBC" in client.models.calls[0]["prompt"]
    radio_prompt = next(call["prompt"] for call in client.models.calls if "Джерело: Радіо Свобода" in call["prompt"])
    assert "ОПУБЛІКОВАНА-ЗАМІТКА" not in radio_prompt
