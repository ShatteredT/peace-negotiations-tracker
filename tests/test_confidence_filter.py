"""PT-76: автотести порогу «Мін. впевненість» (модульні + e2e UI) на даних із demo_db.

У репозиторному demo_db confidence може бути порожнім (NULL, старі дані), тому тест бере його
копію й детерміновано проставляє confidence кожному запису. Так тест однаково працює і до, і
після перегенерації оцінок (PT-67/68).
"""
import shutil
import sqlite3
import textwrap
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import src.ui.app as app

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "data" / "demo" / "peacetalk_demo.db"
START, END = "2022-02-24", "2030-01-01"


def _assign_confidence(conn: sqlite3.Connection) -> None:
    # 0.00 … 1.00 з кроком 0.01, детерміновано за rowid: у кожного порога є й видимі, і приховані
    conn.execute("UPDATE assessment_records SET confidence = ROUND((rowid * 37 % 101) / 100.0, 2)")
    conn.commit()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    assert DEMO_DB.is_file(), f"немає {DEMO_DB}"
    db = tmp_path / "demo_copy.db"
    shutil.copy2(DEMO_DB, db)
    with sqlite3.connect(db) as conn:
        _assign_confidence(conn)
    monkeypatch.setattr(app, "DB_PATH", db)
    st.cache_resource.clear()
    st.cache_data.clear()
    yield db
    st.cache_resource.clear()
    st.cache_data.clear()


def _load(min_conf, cats=(), srcs=(), start=START, end=END):
    return app.load_assessments(start, end, tuple(cats), tuple(srcs), min_conf)


def _counts(min_conf, cats=(), srcs=(), start=START, end=END):
    return app.count_hidden_assessments(start, end, tuple(cats), tuple(srcs), min_conf)


# модульні тести (PT-74)
def test_threshold_keeps_only_confident_rows(demo):
    for thr in (0.0, 0.3, 0.5, 0.8, 1.0):
        df = _load(thr)
        assert (df["confidence"] >= thr).all()


def test_higher_threshold_never_adds_rows(demo):
    sizes = [len(_load(t)) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] > sizes[2] > sizes[-1] >= 0  # поріг реально щось відсіює


def test_cache_key_includes_min_confidence(demo):
    """Без st.cache_data.clear() між викликами: інший поріг не має віддавати кеш попереднього."""
    low = len(_load(0.0))
    high = len(_load(0.9))
    assert high < low
    assert len(_load(0.0)) == low  # повернення до порога 0 — знову повний набір


def test_threshold_is_inclusive(demo):
    with sqlite3.connect(demo) as conn:
        value = conn.execute("SELECT confidence FROM assessment_records WHERE confidence BETWEEN 0.3 AND 0.7 "
                             "LIMIT 1").fetchone()[0]
        exact = conn.execute("SELECT COUNT(*) FROM assessment_records WHERE confidence >= ?", (value,)).fetchone()[0]
    assert len(_load(value)) == exact
    assert (_load(value)["confidence"] == value).any()


def test_null_confidence_is_always_visible(demo):
    with sqlite3.connect(demo) as conn:
        conn.execute("UPDATE assessment_records SET confidence = NULL WHERE rowid % 5 = 0")
        nulls = conn.execute("SELECT COUNT(*) FROM assessment_records WHERE confidence IS NULL").fetchone()[0]
    assert nulls > 0
    assert _load(1.0)["confidence"].isna().sum() == nulls


# лічильник «приховано N із M» (PT-75)
def test_hidden_plus_visible_equals_total(demo):
    total0 = _counts(0.0)[1]
    assert total0 == len(_load(0.0))
    for thr in (0.0, 0.25, 0.5, 0.9, 1.0):
        hidden, total = _counts(thr)
        assert total == total0  # M не залежить від порога
        assert total - hidden == len(_load(thr))


def test_hidden_is_zero_at_zero_threshold(demo):
    assert _counts(0.0)[0] == 0


def test_counter_respects_other_filters(demo):
    cat = "diplomacy"
    hidden, total = _counts(0.5, cats=[cat])
    df = _load(0.5, cats=[cat])
    assert total == _counts(0.0, cats=[cat])[1] < _counts(0.0)[1]
    assert total - hidden == len(df)
    assert set(df["metric_category"]) <= {cat}


def test_counter_respects_date_range(demo):
    assert _counts(0.5, start="2999-01-01", end="2999-12-31") == (0, 0)


# e2e UI: слайдер у бічній панелі (PT-76)
SCRIPT = textwrap.dedent('''
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import streamlit as st
    import src.ui.app as app
    app.DB_PATH = Path(os.environ["PEACETALK_DB"])
    app.bootstrap()
    df, arts = app.current_filtered_data()
    st.text("ROWS=" + str(len(df)))
''')


@pytest.fixture
def ui(demo, tmp_path, monkeypatch):
    monkeypatch.setenv("PEACETALK_DB", str(demo))
    script = tmp_path / "page.py"
    script.write_text(SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _rows(at):
    return int(next(t.value for t in at.text if t.value.startswith("ROWS=")).split("=")[1])


def _counter(at):
    return next(c.value for c in at.sidebar.caption if c.value.startswith("Приховано"))


def test_slider_changes_data_and_counter(ui):
    at = ui
    all_rows = _rows(at)
    assert all_rows > 0
    assert _counter(at) == f"Приховано **0** із **{all_rows}** оцінок"

    at.slider(key="f_conf").set_value(0.8).run()
    assert not at.exception, [e.value for e in at.exception]
    shown = _rows(at)
    assert 0 < shown < all_rows
    assert _counter(at) == f"Приховано **{all_rows - shown}** із **{all_rows}** оцінок"

    at.slider(key="f_conf").set_value(0.0).run()  # повернення — усе знову видно
    assert _rows(at) == all_rows


def test_slider_at_max_shows_all_hidden_hint(ui, demo):
    at = ui
    with sqlite3.connect(demo) as conn:
        conn.execute("UPDATE assessment_records SET confidence = 0.5")
    st.cache_data.clear()
    at.slider(key="f_conf").set_value(0.95).run()
    assert _rows(at) == 0
    assert "усі приховано" in _counter(at)
