import shutil
import textwrap
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "data" / "demo" / "peacetalk_demo.db"

SCRIPT = textwrap.dedent('''
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import src.ui.app as app
    app.DB_PATH = Path(os.environ["PEACETALK_DB"])
    app.bootstrap()
    app.compare_page()
''')


@pytest.fixture
def page(tmp_path, monkeypatch):
    """Сторінка «Порівняння» над тимчасовою копією демонстраційної БД (оригінал не змінюється)."""
    db = tmp_path / "compare.db"
    shutil.copy2(DEMO_DB, db)
    monkeypatch.setenv("PEACETALK_DB", str(db))
    st.cache_resource.clear()
    st.cache_data.clear()
    script = tmp_path / "page.py"
    script.write_text(SCRIPT.format(root=str(ROOT)), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]
    yield at
    st.cache_resource.clear()
    st.cache_data.clear()


def group(at, key):
    return next(b for b in at.button_group if b.key == key)


def shown(at):
    return [m.value for m in at.markdown]


def test_page_shows_head_to_head_cards_tabs_and_highlights(page):
    labels = [m.label for m in page.metric]
    assert labels == ["Середній тон · різниця", "Орієнтовані на компроміс · різниця", "Розкид тону · різниця",
                      "Впевненість моделі · різниця", "Узгодженість динаміки"]
    assert [t.label.split(": ", 1)[-1] for t in page.tabs] == [
        "Динаміка", "Категорії", "Теплова карта", "Розподіли", "Півріччя поруч"]
    assert any(v == "**Головне**" for v in shown(page))
    assert any("Найбільше джерела розходяться" in v for v in shown(page))


def test_every_control_runs_without_errors(page):
    page.toggle(key="cmp_dots").set_value(True).run()
    for key, value in (("cmp_gran", "Місяць"), ("cmp_gran", "Квартал"), ("cmp_criterion", "intensity"),
                       ("cmp_criterion", "compromise"), ("cmp_criterion", "confidence"),
                       ("cmp_heat_mode", "BBC"), ("cmp_heat_mode", "Радіо Свобода"), ("cmp_scope", "Загальний тон")):
        group(page, key).set_value(value).run()
        assert not page.exception, (key, value, [e.value for e in page.exception])
    page.select_slider(key="cmp_period").set_value("2023-H1").run()
    assert not page.exception


def test_picking_a_heatmap_cell_lists_articles_of_both_sources(page):
    assert any("Клікніть клітинку" in c.value for c in page.caption)
    page.selectbox(key="cmp_heat_cat").select("territorial").run()
    page.selectbox(key="cmp_heat_period").select("2022-H2").run()
    text = shown(page)
    assert "**Території · 2022-H2**: статей 35" in text
    assert any(v.startswith(":blue-badge[BBC] статей 20") for v in text)
    assert any(v.startswith(":orange-badge[Радіо Свобода] статей 15") for v in text)
    assert sum(1 for v in text if v.startswith("[")) == 35, "кожна стаття — окрема картка із посиланням"


def test_one_source_selected_asks_for_both(page):
    group(page, "f_srcs").set_value(["bbc"]).run()
    assert not page.exception and not page.tabs
    assert any("потрібні два джерела" in i.value for i in page.info)


def test_narrow_period_degrades_gracefully(page):
    group(page, "f_preset").set_value("2026").run()
    assert not page.exception, [e.value for e in page.exception]
    assert dict((m.label, m.value) for m in page.metric)["Узгодженість динаміки"] == "—"
