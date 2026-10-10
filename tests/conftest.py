import pytest

from src import ops


@pytest.fixture(autouse=True)
def _isolated_ops_log(tmp_path, monkeypatch):
    """Журнал операцій кожного тесту — у тимчасовому файлі, а не в logs/ проєкту."""
    monkeypatch.setattr(ops, "LOG_PATH", tmp_path / "ops.jsonl")
