"""CRUD експертних заміток + права доступу (src/ui/app.py). Працює з тимчасовою БД."""
import sqlite3

import pytest

from scripts.init_db import init_db
from src.ui import app


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "notes.db"
    init_db(str(db))
    connection = sqlite3.connect(db)
    monkeypatch.setattr(app, "get_connection", lambda: connection)
    yield connection
    connection.close()


def status_of(conn, note_id):
    return conn.execute("SELECT status FROM expert_notes WHERE id = ?", (note_id,)).fetchone()[0]


def test_empty_content_rejected(conn):
    ok, _ = app.create_note("analyst_1", "2024-01-01", "diplomacy", "   ", "COMMENT")
    assert not ok


def test_duplicate_note_rejected(conn):
    assert app.create_note("analyst_1", "2024-01-01", "diplomacy", "a", "COMMENT")[0]
    ok, msg = app.create_note("analyst_1", "2024-01-01", "diplomacy", "b", "COMMENT")
    assert not ok and "вже є" in msg


def test_comment_is_published_official_goes_to_moderation(conn):
    app.create_note("analyst_1", "2024-01-01", "diplomacy", "a", "COMMENT")
    app.create_note("analyst_1", "2024-01-02", "security", "b", "OFFICIALSTATEMENT")
    assert status_of(conn, 1) == "PUBLISHED"
    assert status_of(conn, 2) == "PENDING_APPROVAL"


def test_other_analyst_cannot_edit_or_delete(conn):
    app.create_note("analyst_1", "2024-01-01", "diplomacy", "a", "COMMENT")
    assert not app.update_note(1, "analyst_2", "Analyst", "hack")[0]
    assert not app.delete_note(1, "analyst_2", "Analyst")[0]
    assert conn.execute("SELECT content FROM expert_notes WHERE id = 1").fetchone()[0] == "a"


def test_delete_of_missing_note_reports_failure(conn):
    """Регресія: раніше перевірялось conn.total_changes, і «успіх» повертався завжди."""
    app.create_note("analyst_1", "2024-01-01", "diplomacy", "a", "COMMENT")
    assert not app.delete_note(999, "analyst_1", "Analyst")[0]


def test_admin_can_edit_and_delete_any_note(conn):
    app.create_note("analyst_1", "2024-01-01", "diplomacy", "a", "COMMENT")
    assert app.update_note(1, "admin_1", "Admin", "edited")[0]
    assert app.delete_note(1, "admin_1", "Admin")[0]


def test_only_admin_can_moderate(conn):
    app.create_note("analyst_1", "2024-01-01", "security", "b", "OFFICIALSTATEMENT")
    assert not app.moderate_note(1, "analyst_1", "Analyst", "approve")[0]
    assert status_of(conn, 1) == "PENDING_APPROVAL"


def test_moderation_saves_comment_and_cannot_repeat(conn):
    app.create_note("analyst_1", "2024-01-01", "security", "b", "OFFICIALSTATEMENT")
    assert app.moderate_note(1, "admin_1", "Admin", "reject", "Потрібне джерело")[0]
    row = conn.execute("SELECT status, moderated_by, moderator_comment FROM expert_notes").fetchone()
    assert row == ("REJECTED", "admin_1", "Потрібне джерело")
    assert not app.moderate_note(1, "admin_1", "Admin", "approve")[0]


def test_author_editing_official_statement_requires_new_moderation(conn):
    app.create_note("analyst_1", "2024-01-01", "security", "b", "OFFICIALSTATEMENT")
    app.moderate_note(1, "admin_1", "Admin", "approve")
    assert status_of(conn, 1) == "APPROVED"
    assert app.update_note(1, "analyst_1", "Analyst", "changed text")[0]
    assert status_of(conn, 1) == "PENDING_APPROVAL"
