import sqlite3
import textwrap
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from scripts.init_db import init_db
from scripts.load_to_db import load_articles, load_assessments
from src.agents.synthesizer_agent import SynthesizerAgent, ensure_tables, load_period_articles
from src.agents.synthesizer_schemas import PeriodSynthesis
from src.ui import app, charts

CATEGORIES = ("diplomacy", "security", "territorial", "humanitarian")
SUMMARY = "Період відзначився змішаною динамікою переговорів."
ANNOTATION = "Стамбульські контакти підняли тон"

SCRIPT = textwrap.dedent('''
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import src.ui.app as app
    app.DB_PATH = Path(os.environ["PEACETALK_DB"])
    app.bootstrap()
    app.{page}()
''')


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    init_db(str(path))
    items = [("a1", "2023-02-01", 0.8), ("a2", "2023-05-01", -0.7)]
    with sqlite3.connect(path) as conn:
        load_articles(conn, [{"id": i, "source": "bbc", "title": f"Заголовок {i}", "date": d,
                              "url": f"https://x/{i}", "text": "t"} for i, d, _ in items])
        load_assessments(conn, [{
            "article_id": i, "overall_sentiment": o, "is_compromise_oriented": o > 0,
            "categories": {c: {"score": o, "justification": "j"} for c in CATEGORIES},
        } for i, _, o in items], "1.0")
        conn.commit()
    monkeypatch.setenv("PEACETALK_DB", str(path))
    st.cache_resource.clear()
    st.cache_data.clear()
    yield path
    st.cache_resource.clear()
    st.cache_data.clear()


def save_summary(path):
    agent = SynthesizerAgent(path, client=object())
    with sqlite3.connect(path) as conn:
        ensure_tables(conn)
        grouped = load_period_articles(conn)
        result = PeriodSynthesis(trend="mixed", summary=SUMMARY, annotations=[])
        annotations = {"a1": ("peak", ANNOTATION), "a2": ("drop", "Ескалація після заяви")}
        agent.save_summary(conn, "2023-H1", grouped["2023-H1"], result, annotations)


def open_page(tmp_path, page):
    script = tmp_path / f"{page}.py"
    root = str(Path(__file__).resolve().parents[1])
    script.write_text(SCRIPT.format(root=root, page=page), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(at):
    return [m.value for m in at.markdown]


def test_summary_page_shows_saved_summary_and_annotations(db, tmp_path):
    save_summary(db)
    at = open_page(tmp_path, "summary_page")
    assert SUMMARY in texts(at)
    assert any(ANNOTATION in t and "пік" in t for t in texts(at))
    assert any("Ескалація після заяви" in t and "спад" in t for t in texts(at))
    assert any("2023-H1" in t and "Змішано" in t for t in texts(at))


def test_summary_page_without_summaries_shows_how_to_generate_them(db, tmp_path):
    at = open_page(tmp_path, "summary_page")
    assert any("Резюме ще не згенеровано" in i.value for i in at.info)
    assert not at.error


def test_dashboard_renders_with_agent_annotations_on_the_chart(db, tmp_path):
    save_summary(db)
    at = open_page(tmp_path, "dashboard_page")
    assert at.toggle(key="trend_ann").value is True
    at.toggle(key="trend_ann").set_value(False).run()
    assert not at.exception


def test_annotation_traces_are_split_into_peak_and_drop_legend_entries(db, monkeypatch):
    save_summary(db)
    monkeypatch.setattr(app, "DB_PATH", db)
    peak, drop = charts.annotation_traces(app.load_annotations())
    assert (peak.name, list(peak.y), peak.marker.symbol) == ("Анотація: пік", [0.8], "triangle-up")
    assert (drop.name, list(drop.y), drop.marker.symbol) == ("Анотація: спад", [-0.7], "triangle-down")
    assert ANNOTATION in peak.customdata[0][0]


def test_only_annotations_of_visible_articles_are_drawn(db, monkeypatch):
    save_summary(db)
    monkeypatch.setattr(app, "DB_PATH", db)
    arts = app.load_annotations()[["article_id"]].iloc[:1]
    assert list(app._visible_annotations(arts)["article_id"]) == ["a1"]


def test_load_helpers_return_empty_frames_when_tables_do_not_exist(db, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", db)
    assert app.load_summaries().empty and app.load_annotations().empty
