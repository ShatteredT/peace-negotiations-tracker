"""Наскрізна перевірка через інтерфейс: створення -> перегляд -> архівація, права, помилки.

Використовує streamlit.testing (AppTest): сторінка «Замітки» запускається як справжній застосунок
над тимчасовою БД; дії виконуються кліками та введенням у віджети.
"""
import sqlite3
import textwrap

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from scripts.init_db import init_db
from scripts.load_to_db import load_articles, load_assessments

CONTENT = "Стамбульські переговори: перший предметний контакт"

SCRIPT = textwrap.dedent('''
    import os
    import sys
    from pathlib import Path
    sys.path.insert(0, {root!r})
    import src.ui.app as app
    app.DB_PATH = Path(os.environ["PEACETALK_DB"])
    app.bootstrap()
    app.notes_page()
''')


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """Порожній UI-застосунок над тимчасовою БД із однією статтею (як у реальних даних)."""
    db = tmp_path / "e2e.db"
    init_db(str(db))
    with sqlite3.connect(db) as conn:
        load_articles(conn, [{"id": "a1", "source": "bbc", "title": "T", "date": "2023-05-01",
                              "url": "https://x/1", "text": "body"}])
        cat = {"score": 0.1, "justification": "j"}
        load_assessments(conn, [{"article_id": "a1", "overall_sentiment": 0.1, "is_compromise_oriented": True,
                                 "categories": {k: dict(cat) for k in
                                                ("diplomacy", "security", "territorial", "humanitarian")}}], "1.0")
        conn.commit()
    monkeypatch.setenv("PEACETALK_DB", str(db))
    st.cache_resource.clear()
    st.cache_data.clear()
    script = tmp_path / "page.py"
    root = str(__import__("pathlib").Path(__file__).resolve().parents[1])
    script.write_text(SCRIPT.format(root=root), encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    yield at, db
    st.cache_resource.clear()
    st.cache_data.clear()


def add_note(at, text=CONTENT):
    at.text_area(key="nn_content").set_value(text)
    next(b for b in at.button if b.label == "Додати замітку").click()
    return at.run()


def switch_account(at, account_id):
    at.selectbox(key="account_select").select(account_id)
    return at.run()


def shown(at):
    return [m.value for m in at.markdown]


def db_notes(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT id, author_id, status FROM expert_notes ORDER BY id").fetchall()


def test_ui_startup_creates_verified_database_backup(ui):
    at, db = ui
    assert not at.exception

    backups = list((db.parent / "backups").glob("e2e_before_ui_*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 1


def test_full_flow_create_view_archive(ui):
    at, db = ui

    # 1) створення
    at = add_note(at)
    assert not at.exception
    assert db_notes(db) == [(1, "analyst_1", "PUBLISHED")]

    # 2) перегляд: автор бачить замітку зі станом
    assert CONTENT in shown(at)
    assert any("Опубліковано" in v for v in shown(at))

    # 3) повторне створення на той самий слот — конфлікт, а не збій
    at = add_note(at, "дубль")
    assert any("вже є активна замітка" in w.value for w in at.warning)
    assert not at.error
    assert len(db_notes(db)) == 1

    # 4) скасування (архівація) автором
    next(b for b in at.button if b.key == "archive_1").click()
    at.run()
    assert db_notes(db) == [(1, "analyst_1", "ARCHIVED")]
    assert CONTENT not in shown(at), "архівна замітка зникає зі списку «Усі»"

    # 5) слот звільнено — нова замітка на ту саму дату й категорію створюється
    at = add_note(at, "нова після архівації")
    assert [s for _, _, s in db_notes(db)] == ["ARCHIVED", "PUBLISHED"]


def test_other_user_sees_note_but_cannot_change_it(ui):
    at, db = ui
    add_note(at)
    at = switch_account(at, "analyst_2")          # Дослідник Б
    assert CONTENT in shown(at), "активну замітку бачать усі"
    assert not [b for b in at.button if (b.key or "").startswith(("archive_", "edit_save_"))], \
        "кнопок керування чужою заміткою немає"
    assert db_notes(db) == [(1, "analyst_1", "PUBLISHED")]


def test_admin_can_archive_any_note(ui, monkeypatch):
    at, db = ui
    add_note(at)
    monkeypatch.setenv("PEACETALK_ADMIN_PASSWORD", "test-admin-password")
    at.run()
    at = switch_account(at, "admin_1")
    at.text_input(key="admin_password_input").set_value("test-admin-password")
    next(b for b in at.button if b.label == "Увійти як адміністратор").click()
    at = at.run()
    next(b for b in at.button if b.key == "archive_1").click()
    at.run()
    assert db_notes(db) == [(1, "analyst_1", "ARCHIVED")]


def test_admin_login_requires_configured_password(ui, monkeypatch):
    at, _ = ui
    monkeypatch.setenv("PEACETALK_ADMIN_PASSWORD", "test-admin-password")
    at.run()
    at = switch_account(at, "admin_1")
    at.text_input(key="admin_password_input").set_value("wrong-password")
    next(b for b in at.button if b.label == "Увійти як адміністратор").click()
    at = at.run()
    assert any("Неправильний пароль" in e.value for e in at.error)
    assert not any("Адміністратор" == e.value for e in at.success)


def test_admin_password_field_is_shown_only_for_admin(ui, monkeypatch):
    at, _ = ui
    monkeypatch.delenv("PEACETALK_ADMIN_PASSWORD", raising=False)
    at = switch_account(at, "analyst_1")
    assert not any(item.key == "admin_password_input" for item in at.text_input)
    at = switch_account(at, "analyst_2")
    assert not any(item.key == "admin_password_input" for item in at.text_input)
    at = switch_account(at, "admin_1")
    assert any(item.key == "admin_password_input" for item in at.text_input)
    login_button = next(b for b in at.button if b.label == "Увійти як адміністратор")
    assert login_button.disabled


def test_empty_text_is_validation_message_not_technical_error(ui):
    at, db = ui
    at = add_note(at, "   ")
    assert any("не може бути порожнім" in w.value for w in at.warning)
    assert not at.error and not at.exception
    assert db_notes(db) == []
