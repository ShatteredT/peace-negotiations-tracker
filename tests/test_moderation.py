import sqlite3

import pytest

from src.errors import AccessDenied, ConflictError
from src.ui import app
from scripts.init_db import init_db

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "notes.db"
    init_db(str(path))
    monkeypatch.setattr(app, "DB_PATH", path)
    return path

def test_moderation_logic(db):
    comment_id = app.create_note("analyst_1", "2024-01-15", "diplomacy", "A regular expert comment")
    official_id = app.create_note(
        "analyst_2", "2024-01-16", "diplomacy", "An official statement for review", "OFFICIALSTATEMENT"
    )
    rejected_id = app.create_note(
        "analyst_1", "2024-01-17", "security", "Another official statement", "OFFICIALSTATEMENT"
    )

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT review_status FROM expert_notes WHERE id = ?", (comment_id,)
        ).fetchone()[0] == "APPROVED"
        assert conn.execute(
            "SELECT review_status FROM expert_notes WHERE id = ?", (official_id,)
        ).fetchone()[0] == "PENDING_APPROVAL"

    with pytest.raises(AccessDenied):
        app.approve_note(official_id, "analyst_1", "Analyst")

    app.approve_note(official_id, "admin_1", "Admin")
    app.reject_note(rejected_id, "admin_1", "Admin")

    with sqlite3.connect(db) as conn:
        approved = conn.execute(
            "SELECT review_status, reviewed_by, reviewed_at FROM expert_notes WHERE id = ?",
            (official_id,),
        ).fetchone()
        rejected = conn.execute(
            "SELECT review_status, reviewed_by, reviewed_at FROM expert_notes WHERE id = ?",
            (rejected_id,),
        ).fetchone()

    assert approved[0] == "APPROVED"
    assert approved[1] == "admin_1"
    assert approved[2]
    assert rejected[0] == "REJECTED"
    assert rejected[1] == "admin_1"
    assert rejected[2]
    with pytest.raises(ConflictError):
        app.reject_note(official_id, "admin_1", "Admin")
