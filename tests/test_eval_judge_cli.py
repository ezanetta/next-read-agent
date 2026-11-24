from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_agent.config import get_settings
from book_agent.evals import __main__ as evals_cli
from book_agent.evals.calibration import CalibrationStore
from book_agent.evals.judge import (
    DEFAULT_JUDGE_PROMPT,
    BookJudgment,
    CriterionJudgment,
    JudgeConfig,
    JudgmentStore,
    RecordJudgments,
    Verdict,
)
from book_agent.evals.records import RecordStore
from tests.judge_data import AGENT, BASELINE, checked, stored
from tests.library_data import PROFILE

# The CLI defaults to the frozen judge (judge_v3, D-039).
JUDGE = JudgeConfig(prompt_id=DEFAULT_JUDGE_PROMPT.prompt_id, model="gemini-3.5-flash-lite")


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def results(tmp_path: Path) -> RecordStore:
    store = RecordStore(tmp_path / "results")
    store.save_profile("set_v1", PROFILE)
    for config in (AGENT, BASELINE):
        for case in ("a", "b"):
            books = [checked(f"{config.system} {case} {i}") for i in range(2)]
            store.save(stored(case, books, config=config, request=None if case == "b" else "x"))
    return store


def run(tmp_path: Path, *argv: str) -> int:
    paths = ["--results", str(tmp_path / "results")]
    if argv[0] in ("sample", "label", "relabel", "calibrate"):
        paths += ["--calibration", str(tmp_path / "cal")]
    return evals_cli.main([*argv, "--cases" if argv[0] in ("sample", "judge") else "--sample",
                           "set_v1" if argv[0] in ("sample", "judge") else "calibration_v1",
                           *paths])  # fmt: skip


def test_sample_label_judge_plan_calibrate_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = results(tmp_path)
    assert run(tmp_path, "sample", "--per-system", "2") == evals_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "- agent: 2 books from 2 cases, 1 tuning / 1 held-out" in out
    assert run(tmp_path, "sample") == evals_cli.EXIT_ERROR  # frozen: no overwrite
    capsys.readouterr()

    # Label every book: "y" to each question, no note.
    sample, _ = CalibrationStore(tmp_path / "cal").load_sample("calibration_v1")

    def answer(prompt: str) -> str:
        return "" if prompt.startswith("Note") else "y"

    monkeypatch.setattr("builtins.input", answer)
    assert run(tmp_path, "label") == evals_cli.EXIT_OK
    assert "4 of 4 in total" in capsys.readouterr().out

    # Judge plan without --yes: nothing runs, nothing is written.
    assert run(tmp_path, "judge") == evals_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Matched books: 8, 0 already judged, 8 to judge" in out
    assert out.rstrip().endswith("Add --yes to start.")
    assert not (tmp_path / "results" / "set_v1" / "judgments").exists()

    # With stored judgments, calibrate needs no LLM call.
    judgments = JudgmentStore(store)
    for item in sample.items:
        ref = item.ref
        verdict = CriterionJudgment(justification="j", verdict=Verdict.PARTIAL)
        has_request = ref.case_id == "a"
        judgments.save(
            RecordJudgments(
                case_set="set_v1", config_id=ref.config_id, case_id=ref.case_id,
                run_index=ref.run_index, judge=JUDGE, judged_at=datetime.now(UTC),
                judgments=[BookJudgment(
                    item_index=ref.item_index, title="T", author="A", has_request=has_request,
                    had_description=True, fits_request=verdict if has_request else None,
                    reason_quality=verdict,
                )],
            )
        )  # fmt: skip
    assert run(tmp_path, "calibrate", "--split", "tuning") == evals_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "| reason_quality | 2 | 0/2 (0%) | 2/2 (100%) | 0 | 2 |" in out
    assert run(tmp_path, "calibrate", "--split", "held-out") == evals_cli.EXIT_OK
    assert "Held-out looks so far: 1." in capsys.readouterr().out

    # Correct one tuning label; a held-out one is refused and nothing changes.
    positions = sample.positions()
    tuning = next(positions[i.ref] for i in sample.items if i.split == "tuning")
    held_out = next(positions[i.ref] for i in sample.items if i.split == "held_out")
    fix = ("--quality", "n", "--note", "the reason misdescribes the cited book")
    assert run(tmp_path, "relabel", "--book", str(tuning), *fix) == evals_cli.EXIT_OK
    assert "- reason_quality: yes -> no" in capsys.readouterr().out
    assert run(tmp_path, "relabel", "--book", str(held_out), *fix) == evals_cli.EXIT_ERROR
    assert "held-out half" in capsys.readouterr().out
    assert run(tmp_path, "calibrate", "--split", "tuning") == evals_cli.EXIT_OK
    out = capsys.readouterr().out
    assert f"- #{tuning} T (" in out and "reason_quality yes -> no on " in out

    code = evals_cli.main(
        ["report", "--cases", "set_v1", "--results", str(tmp_path / "results"),
         "--judge", JUDGE.id]
    )  # fmt: skip
    assert code == evals_cli.EXIT_OK
    assert f"## Judge scores ({JUDGE.id})" in capsys.readouterr().out
