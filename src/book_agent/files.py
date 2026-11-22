"""Small file helpers shared by the caches and the eval records."""

import os
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file and a rename: a crash mid-write never leaves a broken
    file, and readers see either the old content or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
