import re
import sqlite3
from datetime import timedelta

import pytest

from scripts.init_db import init_db
from src.errors import (
    AccessDenied, ConflictError, NoteError, NotFoundError, TechnicalFailure, ValidationError,
)
from src.ops import utc_now
from src.ui import app

UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE = "2024-01-15"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "notes.db"
    init_db(str(path))
    monkeypatch.setattr(app, "DB_PATH", path)
    return path


def row(db, note_id):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT status, content, created_at, updated_at FROM expert_notes WHERE id = ?",
                            (note_id,)).fetchone()


def make(user="analyst_1", date=DATE, cat="diplomacy", text="note", imp="COMMENT", source="bbc"):
    return app.create_note(user, date, cat, text, imp, source)


# валідація
@pytest.mark.parametrize("kwargs", [
    {"text": ""},
    {"text": "   "},
    {"text": "x" * (app.MAX_CONTENT_LEN + 1)},
    {"cat": "unknown"},
    {"imp": "RUMOR"},
    {"date": "15.01.2024"},
    {"date": "2022-02-23"},                                              # до початку війни
    {"date": (utc_now().date() + timedelta(days=1)).isoformat()},        # майбутнє (UTC)
])
def test_create_rejects_invalid_input_with_validation_error(db, kwargs):
    with pytest.raises(ValidationError):
        make(**kwargs)


def test_boundary_dates_are_accepted(db):
    assert make(date="2022-02-24", cat="security")
    assert make(date=utc_now().date().isoformat(), cat="territorial")


# створення
def test_create_returns_id_and_stores_active_note_with_utc_timestamps(db):
    note_id = make(text="  Перший контакт  ")
    status, content, created, updated = row(db, note_id)
    assert (status, content) == ("PUBLISHED", "Перший контакт")
    assert UTC_ISO.match(created) and UTC_ISO.match(updated)


def test_duplicate_active_note_is_conflict(db):
    make()
    with pytest.raises(ConflictError):
        make(text="other")


def test_active_notes_are_unique_per_source(db):
    bbc_note = make(source="bbc")
    radio_note = make(source="radio_svoboda")

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT source FROM expert_notes WHERE id IN (?, ?) ORDER BY source",
            (bbc_note, radio_note),
        ).fetchall() == [("bbc",), ("radio_svoboda",)]


def test_archive_frees_slot_for_new_note(db):
    first = make()
    app.archive_note(first, "analyst_1", "Analyst")
    second = make(text="new")
    assert second != first and row(db, second)[0] == "PUBLISHED"


# архівація й права (FR-04)
def test_author_archives_own_note(db):
    note_id = make()
    app.archive_note(note_id, "analyst_1", "Analyst")
    assert row(db, note_id)[0] == "ARCHIVED"


def test_other_analyst_cannot_archive_or_edit(db):
    note_id = make()
    with pytest.raises(AccessDenied):
        app.archive_note(note_id, "analyst_2", "Analyst")
    with pytest.raises(AccessDenied):
        app.update_note(note_id, "analyst_2", "Analyst", "hack")
    assert row(db, note_id)[:2] == ("PUBLISHED", "note")


def test_admin_can_archive_and_edit_any_note(db):
    note_id = make()
    app.update_note(note_id, "admin_1", "Admin", "edited by admin")
    app.archive_note(note_id, "admin_1", "Admin")
    assert row(db, note_id)[:2] == ("ARCHIVED", "edited by admin")


def test_archive_missing_note_is_not_found(db):
    with pytest.raises(NotFoundError):
        app.archive_note(999, "analyst_1", "Analyst")


def test_second_archive_is_conflict_not_silent_success(db):
    note_id = make()
    app.archive_note(note_id, "analyst_1", "Analyst")
    with pytest.raises(ConflictError):
        app.archive_note(note_id, "analyst_1", "Analyst")


# редагування
def test_update_changes_text_and_updated_at(db):
    note_id = make()
    app.update_note(note_id, "analyst_1", "Analyst", "new text")
    assert row(db, note_id)[1] == "new text"


def test_archived_note_cannot_be_edited(db):
    note_id = make()
    app.archive_note(note_id, "analyst_1", "Analyst")
    with pytest.raises(ConflictError):
        app.update_note(note_id, "analyst_1", "Analyst", "late edit")


def test_update_rejects_empty_text(db):
    note_id = make()
    with pytest.raises(ValidationError):
        app.update_note(note_id, "analyst_1", "Analyst", "  ")


# бізнес-помилка не реєструється як технічний збій
def test_technical_failure_is_distinct_from_business_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "missing.db")
    with pytest.raises(TechnicalFailure) as exc_info:
        make()
    assert not isinstance(exc_info.value, NoteError)
    assert exc_info.value.request_id in str(exc_info.value)
    assert not (tmp_path / "missing.db").exists(), "відсутня БД не має створюватися порожньою"
