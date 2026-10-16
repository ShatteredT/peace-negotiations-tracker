import shutil
import tempfile
from pathlib import Path


def prepare_demo_database(seed_path: Path, runtime_dir: Path | None = None) -> Path:
    """Copy the tracked read-only seed into a disposable writable database."""
    seed_path = Path(seed_path)
    if not seed_path.is_file():
        raise FileNotFoundError(f"Demo database seed does not exist: {seed_path}")

    if runtime_dir is None:
        runtime_dir = Path(tempfile.mkdtemp(prefix="peacetalk-demo-"))
    else:
        runtime_dir = Path(runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)

    runtime_path = runtime_dir / "peacetalk.db"
    shutil.copy2(seed_path, runtime_path)
    return runtime_path
