import sqlite3

import pytest

from src.runtime_db import prepare_demo_database, restore_database_from_backup


def test_demo_database_is_backed_up_before_ui_startup(tmp_path):
    seed = tmp_path / "seed.db"
    runtime_dir = tmp_path / "runtime"
    with sqlite3.connect(seed) as conn:
        conn.execute("CREATE TABLE demo_data (value TEXT)")
        conn.execute("INSERT INTO demo_data VALUES ('seed')")

    runtime_db = prepare_demo_database(seed, runtime_dir)

    backup_files = list((runtime_dir / "backups").glob("peacetalk_before_ui_*.db"))
    assert len(backup_files) == 1
    with sqlite3.connect(backup_files[0]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM demo_data").fetchall() == [("seed",)]
    assert runtime_db.exists()

    runtime_db.write_bytes(b"corrupted SQLite runtime database")
    conn = sqlite3.connect(runtime_db)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()

    restore_database_from_backup(backup_files[0], runtime_db)

    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM demo_data").fetchall() == [("seed",)]
