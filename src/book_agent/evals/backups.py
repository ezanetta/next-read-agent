"""Backups of results that a rerun replaces (D-042).

Rerunning a run lost to an outage replaces its record. The old record, and every judge's
judgments of that run (they describe books the new run may not have), are moved to a
timestamped folder outside the case set:

    data/evals/_backups/<case set>/<UTC timestamp>/<path relative to the results root>

so nothing that reads results (records, judgments, reports) ever sees them, and each
replaced file can still be found and compared.
"""

import shutil
from datetime import datetime
from pathlib import Path

BACKUPS = "_backups"


def backup_dir(results_root: Path, case_set: str, now: datetime) -> Path:
    """The folder one rerun command backs up into; created on first use."""
    return results_root / BACKUPS / case_set / now.strftime("%Y%m%dT%H%M%SZ")


def back_up(path: Path, results_root: Path, backup: Path, *, move: bool = False) -> Path | None:
    """Copy (or move) `path` into `backup`, keeping its path relative to `results_root`.
    Returns the backup's path, or None when `path` doesn't exist."""
    if not path.exists():
        return None
    target = backup / path.relative_to(results_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(path, target)
    else:
        shutil.copy2(path, target)
    return target


def run_judgment_files(
    results_root: Path, case_set: str, config_id: str, case_id: str, run_index: int
) -> list[Path]:
    """Every judge's judgment file for one run."""
    judgments = results_root / case_set / "judgments"
    return sorted(judgments.glob(f"*/{config_id}/{case_id}__run{run_index}.json"))


def retire_run(
    record_path: Path,
    results_root: Path,
    case_set: str,
    config_id: str,
    case_id: str,
    run_index: int,
    backup: Path,
) -> list[Path]:
    """Before a rerun's record is saved: copy the old record to the backup (the new one
    overwrites it) and move the run's judgments there (the new run has to be judged
    again). Returns the backed-up files."""
    saved = [back_up(record_path, results_root, backup)]
    for path in run_judgment_files(results_root, case_set, config_id, case_id, run_index):
        saved.append(back_up(path, results_root, backup, move=True))
    return [p for p in saved if p is not None]
