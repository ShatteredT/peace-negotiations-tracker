import datetime
import sqlite3
import textwrap
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from scripts.init_db import init_db
from src.ui import charts

CATS = charts.CATEGORIES
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def df():
    """Два місяці. У 2023-03 по дві статті на категорію, у 2023-04 лише diplomacy (порожні клітинки)."""
    rows = []
    for i, (cat, score) in enumerate([("diplomacy", 0.8), ("diplomacy", 0.2), ("security", -0.4), ("security", -0.6),
                                      ("territorial", 0.0), ("territorial", 0.1), ("humanitarian", 0.5),
                                      ("humanitarian", 0.7)]):
        rows.append((f"a{i // 2}", "bbc", f"T{i // 2}", pd.Timestamp("2023-03-01") + pd.Timedelta(days=i),
                     "https://x/" + str(i // 2), cat, score, f"j{i}", 0.5 + i / 20))
    rows.append(("a9", "radio", "T9", pd.Timestamp("2023-04-12"), "https://x/9", "diplomacy", -0.3, "j9", None))
    out = pd.DataFrame(rows, columns=["article_id", "source", "title", "date", "url", "metric_category", "score",
                                      "justification", "confidence"])
    out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()
    return out


# PT-80: articles_for_cell
def test_cell_articles_are_exactly_the_rows_behind_the_cell_colour(df):
    fig = charts.heatmap_figure(df)
    z = pd.DataFrame(fig.data[0].z, index=[charts.CAT_LABEL[c] for c in CATS], columns=fig.data[0].x)
    checked = 0
    for label, row in z.iterrows():
        cat = next(k for k, v in charts.CAT_LABEL.items() if v == label)
        for month, value in row.items():
            rows = charts.articles_for_cell(df, cat, month)
            if pd.isna(value):
                assert rows.empty
            else:
                assert rows["score"].mean() == pytest.approx(value)
                checked += 1
    assert checked == 5  # 4 категорії у 2023-03 + diplomacy у 2023-04


def test_cell_articles_filter_by_category_and_month(df):
    rows = charts.articles_for_cell(df, "security", pd.Timestamp("2023-03-01"))
    assert list(rows["score"]) == [-0.4, -0.6]
    assert set(rows["article_id"]) <= {"a1"}
    assert charts.articles_for_cell(df, "diplomacy", pd.Timestamp("2023-04-01"))["article_id"].tolist() == ["a9"]


def test_cell_articles_are_sorted_by_date_and_keep_display_columns(df):
    rows = charts.articles_for_cell(df, "diplomacy", pd.Timestamp("2023-03-01"))
    assert rows["date"].is_monotonic_increasing
    assert list(rows.columns) == charts.CELL_COLUMNS


@pytest.mark.parametrize("month", [pd.Timestamp("2023-03-17"), "2023-03-01", "2023-03-01 00:00:00",
                                   datetime.datetime(2023, 3, 31)])
def test_month_argument_is_normalised_to_month_start(df, month):
    assert len(charts.articles_for_cell(df, "diplomacy", month)) == 2


def test_empty_or_bad_cell_gives_empty_frame_with_same_columns(df):
    for category, month in (("security", "2023-04-01"), ("diplomacy", "2030-01-01"), ("diplomacy", None),
                            ("diplomacy", "не дата")):
        rows = charts.articles_for_cell(df, category, month)
        assert rows.empty and list(rows.columns) == charts.CELL_COLUMNS


# PT-79: customdata та шар для кліку
def test_heatmap_customdata_matches_every_cell(df):
    fig = charts.heatmap_figure(df)
    heat = fig.data[0]
    assert len(heat.customdata) == len(heat.z) and len(heat.customdata[0]) == len(heat.z[0])
    for i, cat in enumerate(c for c in CATS if c in set(df["metric_category"])):
        for j, month in enumerate(heat.x):
            assert list(heat.customdata[i][j]) == [cat, pd.Timestamp(month).strftime("%Y-%m-%d")]


def test_click_layer_covers_every_cell_including_empty_ones(df):
    """Spike PT-78: go.Heatmap не підтримує вибір точок, клік приймає scatter-шар із customdata клітинок."""
    fig = charts.heatmap_figure(df)
    heat, layer = fig.data
    assert len(layer.x) == len(heat.z) * len(heat.z[0]) == 8  # 4 категорії × 2 місяці, і порожні теж
    assert [list(c[:2]) for c in layer.customdata] == [list(c) for row in heat.customdata for c in row]
    assert layer.hoverinfo != "skip"  # "skip" вимикає події кліку: регресія, знайдена в spike
    assert fig.layout.hoverdistance == -1 and fig.layout.clickmode == "event+select"
    assert fig.layout.dragmode is None  # зум перетягуванням, як було до кліків, не вимикаємо
    # Маркерний шар лише приймає клік; виділення малюється фігурами на всю клітинку.
    assert layer.marker.size <= 6
    assert layer.selected.marker.size == layer.marker.size
    assert layer.selected.marker.opacity == 0
    assert len(fig.layout.shapes) == 0


def test_selected_cell_uses_full_cell_inset_overlay_not_center_marker(df):
    fig = charts.heatmap_figure(df, selected_cell=("humanitarian", pd.Timestamp("2023-03-01")))
    layer = fig.data[1]
    assert len(fig.layout.shapes) == 5  # основне затемнення + чотири внутрішні краї
    assert all(shape.type == "rect" and shape.layer == "above" for shape in fig.layout.shapes)
    assert all(shape.xref == "x" and shape.yref == "y" for shape in fig.layout.shapes)
    # Перша фігура затемнює всю клітинку; решта чотири — тонкі внутрішні краї.
    assert fig.layout.shapes[0].y0 == pytest.approx(2.5)
    assert fig.layout.shapes[0].y1 == pytest.approx(3.5)
    assert all(2.5 <= shape.y0 <= 3.5 and 2.5 <= shape.y1 <= 3.5 for shape in fig.layout.shapes)
    assert layer.selected.marker.opacity == 0 and layer.selected.marker.size == 6


def test_invalid_or_missing_selected_cell_adds_no_overlay(df):
    assert len(charts.heatmap_figure(df).layout.shapes) == 0
    fig = charts.heatmap_figure(df, selected_cell=("humanitarian", pd.Timestamp("2030-01-01")))
    assert len(fig.layout.shapes) == 0


def test_axes_ranges_equal_the_cell_edges_so_the_map_fills_the_plot(df):
    fig = charts.heatmap_figure(df)
    lo, hi = (pd.Timestamp(v) for v in fig.layout.xaxis.range)
    assert lo == pd.Timestamp("2023-03-01") - (pd.Timestamp("2023-04-01") - pd.Timestamp("2023-03-01")) / 2
    assert hi == pd.Timestamp("2023-04-01") + (pd.Timestamp("2023-04-01") - pd.Timestamp("2023-03-01")) / 2
    assert tuple(fig.layout.yaxis.range) == (3.5, -0.5)  # 4 категорії, перша зверху


def test_single_month_heatmap_still_renders(df):
    one = df[df["month"] == pd.Timestamp("2023-03-01")]
    fig = charts.heatmap_figure(one)
    assert len(fig.data[1].x) == 4 and fig.layout.xaxis.range is None


def test_tooltip_comes_from_the_layer_and_shows_the_cell_value(df):
    """Шар через hoverdistance=-1 перехоплює наведення, тож підказку показує він (інакше вона зникає)."""
    fig = charts.heatmap_figure(df)
    heat, layer = fig.data
    assert heat.hoverinfo == "skip"
    assert "%{y}" in layer.hovertemplate and "%{x|%m.%Y}" in layer.hovertemplate
    assert "%{customdata[2]}" in layer.hovertemplate
    z = [v for row in heat.z for v in row]
    for value, cd in zip(z, layer.customdata):
        assert cd[2] == ("немає даних" if pd.isna(value) else f"{value:+.2f}")
    assert {cd[2] for cd in layer.customdata} >= {"+0.50", "немає даних"}  # diplomacy 2023-03: (0.8+0.2)/2


def test_every_layer_point_resolves_back_to_its_own_cell(df):
    layer = charts.heatmap_figure(df).data[1]
    cells = set()
    for x, y, cd in zip(layer.x, layer.y, layer.customdata):
        point = {"x": pd.Timestamp(x).strftime("%Y-%m-%d"), "y": y, "customdata": list(cd)}
        cell = charts.cell_from_point(point)
        assert cell == (cd[0], pd.Timestamp(cd[1]))
        cells.add(cell)
    assert len(cells) == 8


# PT-80: розбір події кліку
REAL_EVENT = {  # сирий результат st.plotly_chart(on_select=...), знятий у spike PT-78
    "selection": {
        "points": [{"curve_number": 1, "point_number": 8, "point_index": 8, "x": "2023-03-01", "y": "Безпека",
                    "customdata": ["security", "2023-03-01"]}],
        "point_indices": [8], "box": [], "lasso": [],
    }
}


def test_event_from_real_browser_click_is_parsed():
    assert charts.cell_from_event(REAL_EVENT) == ("security", pd.Timestamp("2023-03-01"))


def test_axis_labels_are_used_when_customdata_is_missing():
    point = {"x": "2023-03-01", "y": "Безпека"}
    assert charts.cell_from_point(point) == ("security", pd.Timestamp("2023-03-01"))


@pytest.mark.parametrize("event", [None, {}, {"selection": {}}, {"selection": {"points": []}},
                                   {"selection": {"points": [{"x": "2023-03-01", "y": "Невідома"}]}},
                                   {"selection": {"points": [{"customdata": ["nope", "2023-03-01"]}]}},
                                   {"selection": {"points": [{"customdata": ["security", "не дата"]}]}}])
def test_no_cell_for_empty_or_garbage_events(event):
    assert charts.cell_from_event(event) is None


# PT-77: секція в інтерфейсі
SCRIPT = textwrap.dedent('''
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import streamlit as st
    import src.ui.app as app
    app.DB_PATH = Path(os.environ["PEACETALK_DB"])
    app.cell_from_event = lambda event: event if isinstance(event, tuple) else None
    if "_fake_click" in st.session_state:
        app._apply_heatmap_selection(st.session_state.pop("_fake_click"))
    app.bootstrap()
    d, _ = app.current_filtered_data()
    app.heatmap_section(d)
''')


@pytest.fixture
def ui(tmp_path, monkeypatch):
    db = tmp_path / "heat.db"
    init_db(str(db))
    with sqlite3.connect(db) as conn:
        for art in (("a1", "bbc", "Перші переговори", "2023-03-02"), ("a2", "radio", "Другі переговори", "2023-03-20"),
                    ("a3", "bbc", "Квітневий огляд", "2023-04-10")):
            conn.execute("INSERT INTO news_articles (id, source, url, title, date) VALUES (?, ?, ?, ?, ?)",
                         (art[0], art[1], f"https://x/{art[0]}", art[2], art[3]))

        def rate(article, cat, score, conf):
            conn.execute("INSERT INTO assessment_records (article_id, metric_category, score, justification, "
                         "confidence, overall_sentiment, is_compromise_oriented) VALUES (?, ?, ?, ?, ?, 0.1, 1)",
                         (article, cat, score, f"Обґрунтування {article}/{cat}", conf))
        for art in ("a1", "a2"):
            for cat in CATS:
                rate(art, cat, 0.6 if art == "a1" else 0.2, 0.9)
        rate("a3", "diplomacy", -0.5, None)  # у квітні оцінена лише diplomacy: решта клітинок порожні
    monkeypatch.setenv("PEACETALK_DB", str(db))
    st.cache_resource.clear()
    st.cache_data.clear()
    script = tmp_path / "page.py"
    script.write_text(SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    yield at
    st.cache_resource.clear()
    st.cache_data.clear()


def shown(at):
    return " ".join(m.value for m in at.markdown)


def pick(at, cat, month):
    at.selectbox(key="heat_cat").select(cat)
    at.selectbox(key="heat_month").select(pd.Timestamp(month))
    return at.run()


def test_nothing_is_listed_until_a_cell_is_chosen(ui):
    assert not ui.exception
    assert any("Клікніть клітинку" in c.value for c in ui.caption)
    assert "Перші переговори" not in shown(ui)


def test_choosing_a_cell_lists_its_articles_with_scores(ui):
    at = pick(ui, "diplomacy", "2023-03-01")
    assert not at.exception, [e.value for e in at.exception]
    text = shown(at)
    assert "Перші переговори" in text and "Другі переговори" in text and "Квітневий огляд" not in text
    assert "Дипломатія · 03.2023" in text and "статей 2" in text and "+0.40" in text  # (0.6 + 0.2) / 2
    assert any("Обґрунтування a1/diplomacy" in c.value for c in at.caption)
    assert "впевненість 0.90" in text


def test_cell_with_other_month_shows_only_that_month(ui):
    text = shown(pick(ui, "diplomacy", "2023-04-01"))
    assert "Квітневий огляд" in text and "Перші переговори" not in text


def test_empty_cell_says_there_are_no_articles(ui):
    at = pick(ui, "security", "2023-04-01")
    assert not at.exception, [e.value for e in at.exception]
    assert any("немає статей" in i.value for i in at.info)


def test_click_fills_the_choice_and_manual_change_is_not_overwritten(ui):
    ui.session_state["_fake_click"] = ("security", pd.Timestamp("2023-03-01"))
    at = ui.run()
    assert at.selectbox(key="heat_cat").value == "security"
    assert at.selectbox(key="heat_month").value == pd.Timestamp("2023-03-01")
    assert "Безпека · 03.2023" in shown(at)

    at.selectbox(key="heat_cat").select("diplomacy")  # те саме виділення лишається на карті
    at = at.run()
    assert at.selectbox(key="heat_cat").value == "diplomacy"
    assert "Дипломатія · 03.2023" in shown(at)

    at.session_state["_fake_click"] = ("humanitarian", pd.Timestamp("2023-03-01"))  # новий клік діє
    assert ui.run().selectbox(key="heat_cat").value == "humanitarian"


def test_same_cell_can_be_clicked_again_after_manual_change_and_deselect(ui):
    x = ("security", pd.Timestamp("2023-03-01"))
    ui.session_state["_fake_click"] = x
    at = ui.run()
    at.selectbox(key="heat_cat").select("diplomacy")  # вручну вибрали іншу клітинку
    at = at.run()
    assert at.selectbox(key="heat_cat").value == "diplomacy"

    at.session_state["_fake_click"] = None  # виділення на карті зняли
    at = at.run()
    assert at.selectbox(key="heat_cat").value == "diplomacy"  # зняття виділення вибір не чіпає

    at.session_state["_fake_click"] = x  # той самий клік знову: має спрацювати
    at = at.run()
    assert at.selectbox(key="heat_cat").value == "security"


def test_cell_that_disappears_after_filter_change_is_reset_without_error(ui):
    at = pick(ui, "diplomacy", "2023-03-01")
    assert "Перші переговори" in shown(at)
    at.slider(key="f_conf").set_value(1.0).run()  # оцінки статей a1/a2 (0.9) приховано, лишається лише a3
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="heat_month").value is None
    assert "Перші переговори" not in shown(at)
