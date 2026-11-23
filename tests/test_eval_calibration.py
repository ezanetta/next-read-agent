import dataclasses
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_agent.domain.recommendations import Filters, Rule
from book_agent.evals.calibration import (
    HELD_OUT,
    TUNING,
    Agreement,
    CalibrationSample,
    CalibrationStore,
    Label,
    LabelSet,
    SampleItem,
    Split,
    agreement,
    correct_label,
    draw_sample,
    eligible_items,
    fits_left_out,
    label_session,
    render_calibration,
    render_card,
    shared_questions,
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
        request="a desert epic" if has_request else None, preference=None,
        filters=Filters(), title="Sand", author="A", year=None, pages=None, categories=[],
        description=None, reason="R", cited=[],
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


def test_the_card_shows_pages_and_filters_like_the_judge_prompt() -> None:
    item = JudgeInput(
        request="algo corto", preference="algo corto", filters=Filters(language="es"),
        title="Sand", author="A", year=2001, pages=None, categories=["Fiction"],
        description="D", reason="R", cited=[],
    )  # fmt: skip
    text = render_card(1, 1, item)
    assert "Filters checked by code: language: es" in text
    assert "2001 | pages unknown | Fiction" in text
    with_pages = render_card(1, 1, dataclasses.replace(item, pages=96))
    assert "2001 | 96 pages | Fiction" in with_pages


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
        "- #1 Sand (case-0), reason_quality: judge yes, you partial. "
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


def labeled_set() -> LabelSet:
    return LabelSet(sample_version="cal_v1", sample_sha256="s", labels=list(LABELS.values()))


def test_a_correction_keeps_the_original_in_history() -> None:
    labels = labeled_set()
    before, after = correct_label(
        sample(ITEMS), labels, 1, note="  misread the cited book  ", reason_quality=NO
    )
    assert (before.reason_quality, after.reason_quality) == (PARTIAL, NO)
    assert (after.fits_request, after.would_read, after.note) == (YES, YES, "too generic")
    (correction,) = after.corrections
    assert correction.before == before.values and correction.after == after.values
    assert correction.note == "misread the cited book"
    assert labels.by_ref()[ref(0)] == after  # replaced in place, nothing appended
    assert len(labels.labels) == 3

    # A second correction stacks on the first, so the original stays first in history.
    _, again = correct_label(sample(ITEMS), labels, 1, note="checked again", fits_request=NO)
    assert [c.before.reason_quality for c in again.corrections] == [PARTIAL, NO]
    assert again.values == again.corrections[-1].after


@pytest.mark.parametrize(
    ("position", "changes", "note", "error"),
    [
        (3, {"reason_quality": NO}, "n", "held-out half"),
        (1, {"reason_quality": NO}, "   ", "needs a note"),
        (1, {"reason_quality": PARTIAL}, "n", "nothing to correct"),
        (1, {}, "n", "nothing to correct"),
        (2, {"fits_request": YES}, "n", "no request text"),
        (4, {"reason_quality": NO}, "n", "no book #4"),
    ],
)
def test_corrections_are_refused_outside_their_rules(
    position: int, changes: dict[str, Verdict], note: str, error: str
) -> None:
    labels = labeled_set()
    with pytest.raises(ValueError, match=error):
        correct_label(sample(ITEMS), labels, position, note=note, **changes)
    assert labels.labels == list(LABELS.values())


def test_a_correction_needs_a_label_first() -> None:
    labels = LabelSet(sample_version="cal_v1", sample_sha256="s")
    with pytest.raises(ValueError, match="no label yet"):
        correct_label(sample(ITEMS), labels, 1, note="n", reason_quality=NO)


def test_the_tuning_report_lists_every_correction_and_scores_the_corrected_label() -> None:
    labels = labeled_set()
    correct_label(sample(ITEMS), labels, 1, note="misread it", reason_quality=YES)
    markdown = render_calibration(sample(ITEMS), TUNING, JUDGE, labels.by_ref(), JUDGMENTS)
    assert "- #1 Sand (case-0), reason_quality partial -> yes on " in markdown
    assert "Note: misread it\n" in markdown
    assert "| reason_quality | 2 | 2/2 (100%) |" in markdown  # the corrected label counts
    untouched = render_calibration(sample(ITEMS), TUNING, JUDGE, LABELS, JUDGMENTS)
    assert "## Corrected labels\n\nNone." in untouched


def test_labels_without_corrections_still_load() -> None:
    stored = (
        '{"ref": {"config_id": "c", "case_id": "x", "run_index": 1, "item_index": 0}, '
        '"fits_request": "yes", "reason_quality": "no", "would_read": "yes", '
        '"labeled_at": "2026-11-22T10:00:00Z"}'
    )
    assert Label.model_validate_json(stored).corrections == []


def test_the_card_number_is_the_book_position_even_after_skips() -> None:
    items = [SampleItem(ref=ref(n), system="agent", split=TUNING) for n in range(3)]
    labels = LabelSet(sample_version="cal_v1", sample_sha256="s", labels=[LABELS[ref(0)]])
    answers = iter(["s", "y", "y", "y", ""])  # skip book 2, label book 3
    shown: list[str] = []
    label_session(
        sample(items), {r.ref: card() for r in items}, labels,
        save=lambda _: None, ask=lambda _: next(answers), show=shown.append,
    )  # fmt: skip
    cards = [text.splitlines()[0] for text in shown if text.startswith("--- Book")]
    assert cards == ["--- Book 2 of 3 ---", "--- Book 3 of 3 ---"]


def same_book_sample() -> tuple[CalibrationSample, dict[BookRef, JudgeInput]]:
    """#1/#2: one book twice on tuning. #3/#4: one book on both halves. #5: alone."""

    def r(case: str, run: int) -> BookRef:
        return BookRef(config_id="c", case_id=case, run_index=run, item_index=0)

    refs = [r("a", 1), r("a", 2), r("b", 1), r("b", 2), r("b", 3)]
    splits: list[Split] = [TUNING, TUNING, TUNING, HELD_OUT, HELD_OUT]
    items = [
        SampleItem(ref=x, system="agent", split=sp) for x, sp in zip(refs, splits, strict=True)
    ]
    titles = ["Sand", "sand", "Rain", "Rain", "Snow"]  # the match ignores case
    inputs = {x: dataclasses.replace(card(), title=t) for x, t in zip(refs, titles, strict=True)}
    return sample(items), inputs


def test_the_same_book_for_the_same_case_shares_a_question() -> None:
    s, inputs = same_book_sample()
    shared = shared_questions(s, inputs)
    found = [(q.case_id, q.title, len(q.refs)) for q in shared]
    assert found == [("a", "Sand", 2), ("b", "Rain", 2)]
    assert fits_left_out(s, shared) == {s.items[3].ref}  # held-out twin of a tuning book


def test_the_same_title_in_another_case_is_a_different_question() -> None:
    s, inputs = same_book_sample()
    inputs[s.items[4].ref] = dataclasses.replace(inputs[s.items[4].ref], title="Sand")
    assert [len(q.refs) for q in shared_questions(s, inputs)] == [2, 2]


def test_held_out_agreement_leaves_out_only_the_twins_fits_request() -> None:
    s, inputs = same_book_sample()
    labels = {i.ref: label(i.ref, NO, NO) for i in s.items}
    judgments = {i.ref: judgment(YES, YES) for i in s.items}
    markdown = render_calibration(
        s, HELD_OUT, JUDGE, labels, judgments, shared=shared_questions(s, inputs)
    )
    assert "| fits_request | 1 |" in markdown and "| reason_quality | 2 |" in markdown
    assert "fits_request leaves out 1 book(s) whose question is also asked" in markdown
    assert "Rain" not in markdown and "#4" not in markdown  # held-out stays anonymous
    plain = render_calibration(s, HELD_OUT, JUDGE, labels, judgments)
    assert "| fits_request | 2 |" in plain and "leaves out" not in plain


def test_the_tuning_report_notes_repeated_questions() -> None:
    s, inputs = same_book_sample()
    labels = {i.ref: label(i.ref, NO, NO) for i in s.items}
    judgments = {i.ref: judgment(YES, YES) for i in s.items}
    markdown = render_calibration(
        s, TUNING, JUDGE, labels, judgments, shared=shared_questions(s, inputs)
    )
    assert (
        "- #1 and #2: Sand (a) ask the same fits_request question, so it counts 2 times "
        "in tuning agreement." in markdown
    )
    assert (
        "- #3: Rain (b) shares its fits_request question with 1 held-out book(s), whose "
        "fits_request is left out of held-out agreement." in markdown
    )
    assert "| fits_request | 3 |" in markdown  # tuning keeps every pair


def test_two_level_agreement_merges_partial_on_either_side() -> None:
    a = Agreement([(YES, YES), (YES, PARTIAL), (PARTIAL, NO), (NO, YES), (PARTIAL, YES)])
    at_yes = a.yes_vs_rest()
    assert at_yes.pairs == [(YES, YES), (YES, NO), (NO, NO), (NO, YES), (NO, YES)]
    assert (at_yes.exact, at_yes.lenient, at_yes.strict) == (2, 1, 2)
    at_no = a.no_vs_rest()
    assert at_no.pairs == [(YES, YES), (YES, YES), (YES, NO), (NO, YES), (YES, YES)]
    assert (at_no.exact, at_no.lenient, at_no.strict) == (3, 1, 1)


def test_the_calibration_table_has_both_two_level_rows() -> None:
    # Tuning pairs (judge, you): (yes, partial) and (no, no).
    tuning = render_calibration(sample(ITEMS), TUNING, JUDGE, LABELS, JUDGMENTS)
    assert "| reason_quality, yes vs partial or no | 2 | 1/2 (50%) | - | 1 | 0 |" in tuning
    assert "| reason_quality, no vs partial or yes | 2 | 2/2 (100%) | - | 0 | 0 |" in tuning
    held_out = render_calibration(sample(ITEMS), HELD_OUT, JUDGE, LABELS, JUDGMENTS)
    assert "| reason_quality, yes vs partial or no | 1 | 1/1 (100%) | - | 0 | 0 |" in held_out
    assert "| reason_quality, no vs partial or yes | 1 | 1/1 (100%) | - | 0 | 0 |" in held_out
