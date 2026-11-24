from datetime import UTC, datetime
from pathlib import Path

from book_agent.evals.backups import back_up, backup_dir, retire_run, run_judgment_files


def test_backups_keep_relative_paths_outside_the_case_set(tmp_path: Path) -> None:
    folder = backup_dir(tmp_path, "set_v1", datetime(2026, 11, 24, 15, 4, 5, tzinfo=UTC))
    assert folder == tmp_path / "_backups" / "set_v1" / "20261124T150405Z"
    source = tmp_path / "set_v1" / "cfg" / "case__run1.json"
    source.parent.mkdir(parents=True)
    source.write_text("old")
    copied = back_up(source, tmp_path, folder)
    assert copied == folder / "set_v1" / "cfg" / "case__run1.json" and source.exists()
    assert back_up(tmp_path / "missing.json", tmp_path, folder) is None


def test_retiring_a_run_copies_its_record_and_moves_every_judges_judgments(tmp_path: Path) -> None:
    record = tmp_path / "set_v1" / "cfg" / "case__run2.json"
    judgments = [
        tmp_path / "set_v1" / "judgments" / j / "cfg" / "case__run2.json" for j in ("a", "b")
    ]
    other = tmp_path / "set_v1" / "judgments" / "a" / "cfg" / "case__run1.json"
    for path in (record, *judgments, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name)
    assert run_judgment_files(tmp_path, "set_v1", "cfg", "case", 2) == judgments
    saved = retire_run(record, tmp_path, "set_v1", "cfg", "case", 2, tmp_path / "bk")
    assert len(saved) == 3 and record.exists() and other.exists()
    assert not any(p.exists() for p in judgments)
