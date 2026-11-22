from pathlib import Path

from book_agent.files import write_atomic


def test_write_atomic_creates_folders_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "file.json"
    write_atomic(path, "first")
    write_atomic(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    assert [p.name for p in path.parent.iterdir()] == ["file.json"]
