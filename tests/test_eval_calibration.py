from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_agent.domain.recommendations import Rule
from book_agent.evals.calibration import (
    HELD_OUT,
    TUNING,
    Agreement,
    CalibrationSample,
    CalibrationStore,
    Label,
    LabelSet,
    SampleItem,
    agreement,
    draw_sample,
    eligible_items,
    label_session,
    render_calibration,
)
from book_agent.evals.judge import (
    BookJudgment,
    BookRef,
    Criterion,
    CriterionJudgment,
    JudgeConfig,
    Verdict,
)
from book_agent.evals.records import EvalRecord, RunConfig
from book_agent.prompts.judge_v1 import JudgeInput
from tests.judge_data import AGENT, BASELINE, checked, stored

YES, PARTIAL, NO = Verdict.YES, Verdict.PARTIAL, Verdict.NO
JUDGE = JudgeConfig(prompt_id="judge_v1", model="m")
NOW = datetime(2026, 11, 22, tzinfo=UTC)


def three_books(
    case_id: str,
    config: RunConfig = AGENT,
    run_index: int = 1,
    violations: Sequence[tuple[Rule, int]] = (),
) -> EvalRecord:
    books = [checked(f"{case_id} {run_index} {i}") for i in range(3)]
    return stored(case_id, books, config=config, run_index=run_index, violations=violations)


def test_books_in_the_library_are_not_eligible() -> None:
    record = three_books(
        "c", violations=[(Rule.ALREADY_READ, 0), (Rule.ON_TO_READ, 2), (Rule.MAX_PAGES, 1)]
    )
    assert eligible_items(record) == [1]


RECORDS = [
    three_books(case, config, run)
    for case in ("a", "b", "c", "d", "e", "f")
    for config in (AGENT, BASELINE)
    for run in (1, 2, 3)
]


def test_the_sample_is_balanced_capped_and_reproducible() -> None:
    items = draw_sample(RECORDS, seed=7, per_system=8, per_group=2)
    assert len(items) == 16
    by_system = Counter(i.system for i in items)
    assert by_system == {"agent": 8, "baseline": 8}
    per_group = Counter((i.system, i.ref.case_id) for i in items)
    assert max(per_group.values()) == 2 and len(per_group) == 12  # every case, both systems
    halves = Counter((i.system, i.split) for i in items)
    assert set(halves.values()) == {4}
    # Each case with two books puts one in each half.
    for (system, case), count in per_group.items():
        if count == 2:
            splits = {i.split for i in items if (i.system, i.ref.case_id) == (system, case)}
            assert splits == {TUNING, HELD_OUT}
    assert draw_sample(RECORDS, seed=7, per_system=8) == items
    assert draw_sample(RECORDS, seed=8, per_system=8) != items


def sample(items: list[SampleItem]) -> CalibrationSample:
    return CalibrationSample(
        version="cal_v1", case_set="set_v1", harness=2, seed=1, created_at=NOW, items=items
    )


def test_samples_are_frozen_and_labels_tied_to_them(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path)
    store.save_sample(sample(draw_sample(RECORDS, seed=1, per_system=4)))
    with pytest.raises(ValueError, match="frozen"):
        store.save_sample(sample([]))
    loaded, sha = store.load_sample("cal_v1")
    labels = store.load_labels("cal_v1", sha)
    assert labels.labels == []
    store.save_labels(labels)
    with pytest.raises(ValueError, match="changed after it was labeled"):
        store.load_labels("cal_v1", "other-sha")
    assert len(loaded.items) == 8


def ref(n: int) -> BookRef:
    return BookRef(config_id="c", case_id=f"case-{n}", run_index=1, item_index=0)


def card(has_request: bool = True) -> JudgeInput:
    return JudgeInput(
        request="a desert epic" if has_request else None, preference=None, title="Sand",
        author="A", year=None, categories=[], description=None, reason="R", cited=[],
    )  # fmt: skip


def test_labeling_asks_skips_quits_and_saves_after_each(tmp_path: Path) -> None:
    items = [SampleItem(ref=ref(n), system="agent", split=TUNING) for n in range(4)]
    inputs = {ref(0): card(), ref(1): card(), ref(2): card(has_request=False), ref(3): card()}
    answers = iter(["y", "x", "p", "n", "liked it",  # book 1, with one invalid answer
                    "s",                             # book 2 skipped
                    "n", "y", "",                    # book 3: no request, so two questions
                    "q"])  # fmt: skip
    shown: list[str] = []
    saves: list[int] = []
    labels = LabelSet(sample_version="cal_v1", sample_sha256="s")
    count = label_session(
        sample(items), inputs, labels,
        save=lambda ls: saves.append(len(ls.labels)), ask=lambda _: next(answers),
        show=shown.append,
    )  # fmt: skip
    assert count == 2 and saves == [1, 2]
    first, third = labels.labels
    assert (first.fits_request, first.reason_quality, first.would_read) == (YES, PARTIAL, NO)
    assert first.note == "liked it"
    assert (third.ref, third.fits_request, third.reason_quality) == (ref(2), None, NO)
    assert third.note is None
    assert not any("agent" in line for line in shown)  # blind to the system


def test_agreement_counts_direction() -> None:
    a = Agreement([(YES, YES), (YES, PARTIAL), (YES, NO), (NO, PARTIAL), (PARTIAL, PARTIAL)])
    assert (a.n, a.exact, a.within_one, a.lenient, a.strict) == (5, 2, 4, 2, 1)
    assert a.matrix[(YES, NO)] == 1


def judgment(fits: Verdict | None, quality: Verdict) -> BookJudgment:
    def c(v: Verdict) -> CriterionJudgment:
        return CriterionJudgment(justification=f"because {v.value}", verdict=v)

    return BookJudgment(
        item_index=0, title="Sand", author="A", has_request=fits is not None,
        had_description=True, fits_request=c(fits) if fits else None, reason_quality=c(quality),
    )  # fmt: skip


def label(r: BookRef, fits: Verdict | None, quality: Verdict) -> Label:
    return Label(ref=r, fits_request=fits, reason_quality=quality, would_read=YES,
                 note="too generic", labeled_at=NOW)  # fmt: skip


ITEMS = [
    SampleItem(ref=ref(0), system="agent", split=TUNING),
    SampleItem(ref=ref(1), system="baseline", split=TUNING),
    SampleItem(ref=ref(2), system="agent", split=HELD_OUT),
]
LABELS = {ref(0): label(ref(0), YES, PARTIAL), ref(1): label(ref(1), None, NO),
          ref(2): label(ref(2), NO, YES)}  # fmt: skip
JUDGMENTS = {ref(0): judgment(YES, YES), ref(1): judgment(None, NO),
             ref(2): judgment(YES, YES)}  # fmt: skip


def test_agreement_skips_criteria_without_both_verdicts() -> None:
    tuning = [i for i in ITEMS if i.split == TUNING]
    assert agreement(tuning, LABELS, JUDGMENTS, Criterion.FITS_REQUEST).pairs == [(YES, YES)]
    quality = agreement(tuning, LABELS, JUDGMENTS, Criterion.REASON_QUALITY)
    assert quality.pairs == [(YES, PARTIAL), (NO, NO)] and quality.lenient == 1


def test_the_tuning_report_lists_disagreements() -> None:
    markdown = render_calibration(sample(ITEMS), TUNING, JUDGE, LABELS, JUDGMENTS)
    assert "| reason_quality | 2 | 1/2 (50%) | 2/2 (100%) | 1 | 0 |" in markdown
    assert (
        "- Sand (case-0), reason_quality: judge yes, you partial. "
        "Judge: 'because yes'. Your note: too generic" in markdown
    )


def test_the_held_out_report_shows_only_aggregates(tmp_path: Path) -> None:
    looks = len(CalibrationStore(tmp_path).log_held_out_look("cal_v1", JUDGE).looks)
    markdown = render_calibration(
        sample(ITEMS), HELD_OUT, JUDGE, LABELS, JUDGMENTS, held_out_looks=looks
    )
    assert "| fits_request | 1 | 0/1 (0%) | 0/1 (0%) | 1 | 0 |" in markdown
    assert "Held-out looks so far: 1." in markdown
    assert "Disagreements" not in markdown and "because" not in markdown
    assert (
        CalibrationStore(tmp_path).log_held_out_look("cal_v1", JUDGE).looks[-1].judge_id == JUDGE.id
    )
