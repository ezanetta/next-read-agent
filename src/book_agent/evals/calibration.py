"""Calibrating the judge against the user's labels (D-034).

- A frozen sample of recommended books, drawn once with a seed from stored records,
  split into a tuning half and a held-out half.
- Blind labeling: one book at a time, the same fields the judge sees, never the system.
- Agreement per criterion, with the direction of each disagreement. The tuning half
  lists every disagreement; the held-out half only gives aggregates, and each look at
  it is logged with the judge configuration, so the prompt is never tuned on it.

Everything lives in data/evals/calibration/ (private: it points at the library, D-024).
"""

import hashlib
import random
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from book_agent.domain.recommendations import Rule
from book_agent.evals.judge import (
    BookJudgment,
    BookRef,
    Criterion,
    JudgeConfig,
    Verdict,
    judgeable_items,
    ref_of,
)
from book_agent.evals.records import EvalRecord
from book_agent.files import write_atomic
from book_agent.prompts.judge_v1 import JudgeInput

Split = Literal["tuning", "held_out"]
TUNING: Split = "tuning"
HELD_OUT: Split = "held_out"

DEFAULT_SAMPLE = "calibration_v1"
CALIBRATION_DIR = Path("data/evals/calibration")
PER_SYSTEM = 20
PER_GROUP = 2  # at most this many books per (system, case)

# A book the reader has read, plans to read or is reading: "would I read it" makes no
# sense for it, so it is left out of the sample (it is still judged in the full run).
_LIBRARY_RULES = {Rule.ALREADY_READ, Rule.ON_TO_READ, Rule.CURRENTLY_READING}


class SampleItem(BaseModel, frozen=True):
    ref: BookRef
    system: str  # stored for the balance check; never shown while labeling
    split: Split


class CalibrationSample(BaseModel, frozen=True):
    version: str
    case_set: str
    harness: int
    seed: int
    created_at: datetime
    items: list[SampleItem]  # in labeling order (shuffled)

    def split(self, split: Split) -> list[SampleItem]:
        return [i for i in self.items if i.split == split]


class Label(BaseModel, frozen=True):
    ref: BookRef
    fits_request: Verdict | None  # None when the run had no request text
    reason_quality: Verdict
    would_read: Verdict  # only the user answers this one
    note: str | None = None
    labeled_at: datetime


class LabelSet(BaseModel):
    sample_version: str
    sample_sha256: str
    labels: list[Label] = []

    def by_ref(self) -> dict[BookRef, Label]:
        return {label.ref: label for label in self.labels}


class HeldOutLook(BaseModel, frozen=True):
    judge_id: str
    at: datetime


class HeldOutLog(BaseModel):
    looks: list[HeldOutLook] = []


# --- Sampling -------------------------------------------------------------------------


def eligible_items(record: EvalRecord) -> list[int]:
    """Matched books without a library violation (already read, to read, reading)."""
    if record.final_validation is None:
        return []
    in_library = {v.index for v in record.final_validation.violations if v.rule in _LIBRARY_RULES}
    return [i for i in judgeable_items(record) if i not in in_library]


def draw_sample(
    records: Iterable[EvalRecord],
    seed: int,
    per_system: int = PER_SYSTEM,
    per_group: int = PER_GROUP,
) -> list[SampleItem]:
    """Up to `per_system` books per system, at most `per_group` per (system, case),
    spread over as many cases as possible, then split half and half.

    Round robin over cases (in a seeded random order): one book from every case, then
    a second from each, until the system's quota is met. Within each system the books
    alternate between tuning and held-out in case order, so a case with two books puts
    one in each half. The result is shuffled into labeling order.
    """
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[BookRef]] = {}
    for record in sorted(records, key=lambda r: (r.config.id, r.case_id, r.run_index)):
        for index in eligible_items(record):
            key = (record.config.system, record.case_id)
            groups.setdefault(key, []).append(ref_of(record, index))
    for refs in groups.values():
        rng.shuffle(refs)

    items: list[SampleItem] = []
    for system in sorted({system for system, _ in groups}):
        cases = sorted(case for s, case in groups if s == system)
        rng.shuffle(cases)
        picked: list[tuple[str, int, BookRef]] = []  # (case, round, ref)
        for round_ in range(per_group):
            for case in cases:
                refs = groups[(system, case)]
                if len(picked) < per_system and round_ < len(refs):
                    picked.append((case, round_, refs[round_]))
        picked.sort(key=lambda p: (p[0], p[1]))
        for number, (_, _, ref) in enumerate(picked):
            split: Split = TUNING if number % 2 == 0 else HELD_OUT
            items.append(SampleItem(ref=ref, system=system, split=split))
    rng.shuffle(items)
    return items


# --- Storage --------------------------------------------------------------------------


class CalibrationStore:
    def __init__(self, root: Path = CALIBRATION_DIR) -> None:
        self.root = root

    def sample_path(self, version: str) -> Path:
        return self.root / f"{version}.json"

    def save_sample(self, sample: CalibrationSample) -> Path:
        """Write a new sample. Refuses to overwrite one: samples are frozen (D-034)."""
        path = self.sample_path(sample.version)
        if path.exists():
            raise ValueError(f"{path} exists; samples are frozen. Use a new version.")
        write_atomic(path, sample.model_dump_json(indent=1) + "\n")
        return path

    def load_sample(self, version: str) -> tuple[CalibrationSample, str]:
        """The sample and the sha256 of its file."""
        content = self.sample_path(version).read_bytes()
        sample = CalibrationSample.model_validate_json(content)
        return sample, hashlib.sha256(content).hexdigest()

    def labels_path(self, version: str) -> Path:
        return self.root / f"{version}_labels.json"

    def load_labels(self, version: str, sample_sha256: str) -> LabelSet:
        """Stored labels, or an empty set. Raises ValueError if the sample file changed
        after labeling started."""
        path = self.labels_path(version)
        if not path.exists():
            return LabelSet(sample_version=version, sample_sha256=sample_sha256)
        labels = LabelSet.model_validate_json(path.read_bytes())
        if labels.sample_sha256 != sample_sha256:
            raise ValueError(f"{version} changed after it was labeled; it is frozen.")
        return labels

    def save_labels(self, labels: LabelSet) -> None:
        write_atomic(
            self.labels_path(labels.sample_version), labels.model_dump_json(indent=1) + "\n"
        )

    def log_held_out_look(self, version: str, judge: JudgeConfig) -> HeldOutLog:
        """Record that `judge` was scored on the held-out half; returns the whole log."""
        path = self.root / f"{version}_held_out_log.json"
        log = HeldOutLog.model_validate_json(path.read_bytes()) if path.exists() else HeldOutLog()
        log.looks.append(HeldOutLook(judge_id=judge.id, at=datetime.now(UTC)))
        write_atomic(path, log.model_dump_json(indent=1) + "\n")
        return log


# --- Labeling -------------------------------------------------------------------------

Ask = Callable[[str], str]
Show = Callable[[str], None]

_ANSWERS = {"y": Verdict.YES, "p": Verdict.PARTIAL, "n": Verdict.NO}


class _Skip(Exception):
    pass


class _Quit(Exception):
    pass


def render_card(number: int, total: int, item: JudgeInput) -> str:
    """One book as the labeler sees it: the judge's inputs, nothing about the system."""
    lines = [f"--- Book {number} of {total} ---", ""]
    if item.request is not None:
        lines += [f"Request: {item.request}", f"Preference: {item.preference or '(none)'}"]
    else:
        lines += ["Request: (none: library only)"]
    lines += ["", f"{item.title} by {item.author}"]
    details = [str(item.year)] if item.year is not None else []
    details += [", ".join(item.categories)] if item.categories else []
    if details:
        lines.append(" | ".join(details))
    lines += ["", item.description or "(no description available)", ""]
    lines += [f"Reason: {item.reason}", ""]
    if item.cited:
        lines.append("Cited from your library:")
        for book in item.cited:
            rating = f"{book.rating:g}/5" if book.rating is not None else "not rated"
            lines.append(f"  - {book.title} by {book.author} ({rating}, {book.shelf})")
    else:
        lines.append("Cited from your library: none")
    return "\n".join(lines)


def label_session(
    sample: CalibrationSample,
    inputs: dict[BookRef, JudgeInput],
    labels: LabelSet,
    *,
    save: Callable[[LabelSet], None],
    ask: Ask = input,
    show: Show = print,
) -> int:
    """Ask for labels on every unlabeled book, saving after each. Returns how many were
    labeled. Answers: y / p / n; s skips the book (it comes back next time); q quits."""
    done = labels.by_ref()
    todo = [i for i in sample.items if i.ref not in done]
    show(f"{len(done)} labeled, {len(todo)} to go. Answer y (yes), p (partial), n (no); "
         "s skips the book, q quits.")  # fmt: skip
    count = 0
    for number, entry in enumerate(todo, start=len(done) + 1):
        item = inputs[entry.ref]
        show("")
        show(render_card(number, len(sample.items), item))
        try:
            fits = _verdict(ask, "Fits the request?") if item.has_request else None
            quality = _verdict(ask, "Reason quality (specific, real connection, nothing invented)?")
            would_read = _verdict(ask, "Would you read it?")
        except _Skip:
            continue
        except _Quit:
            break
        note = ask("Note (optional, Enter to skip): ").strip() or None
        labels.labels.append(
            Label(
                ref=entry.ref,
                fits_request=fits,
                reason_quality=quality,
                would_read=would_read,
                note=note,
                labeled_at=datetime.now(UTC),
            )
        )
        save(labels)
        count += 1
    return count


def _verdict(ask: Ask, question: str) -> Verdict:
    while True:
        answer = ask(f"{question} [y/p/n, s, q] ").strip().lower()
        if answer in _ANSWERS:
            return _ANSWERS[answer]
        if answer == "s":
            raise _Skip
        if answer == "q":
            raise _Quit


# --- Agreement ------------------------------------------------------------------------


@dataclass(frozen=True)
class Agreement:
    """Judge verdicts against human labels for one criterion."""

    pairs: Sequence[tuple[Verdict, Verdict]]  # (judge, human)

    @property
    def n(self) -> int:
        return len(self.pairs)

    @property
    def exact(self) -> int:
        return sum(j == h for j, h in self.pairs)

    @property
    def within_one(self) -> int:
        return sum(abs(j.rank - h.rank) <= 1 for j, h in self.pairs)

    @property
    def lenient(self) -> int:
        """The judge graded higher than the human."""
        return sum(j.rank > h.rank for j, h in self.pairs)

    @property
    def strict(self) -> int:
        """The judge graded lower than the human."""
        return sum(j.rank < h.rank for j, h in self.pairs)

    @property
    def matrix(self) -> Counter[tuple[Verdict, Verdict]]:
        return Counter(self.pairs)


def human_verdict(label: Label, criterion: Criterion) -> Verdict | None:
    return label.fits_request if criterion is Criterion.FITS_REQUEST else label.reason_quality


def agreement(
    items: Sequence[SampleItem],
    labels: dict[BookRef, Label],
    judgments: dict[BookRef, BookJudgment],
    criterion: Criterion,
) -> Agreement:
    """Pairs for the items both labeled and judged on this criterion."""
    pairs: list[tuple[Verdict, Verdict]] = []
    for item in items:
        label, judgment = labels.get(item.ref), judgments.get(item.ref)
        if label is None or judgment is None:
            continue
        human, judge = human_verdict(label, criterion), judgment.verdict(criterion)
        if human is not None and judge is not None:
            pairs.append((judge, human))
    return Agreement(pairs)


def render_calibration(
    sample: CalibrationSample,
    split: Split,
    judge: JudgeConfig,
    labels: dict[BookRef, Label],
    judgments: dict[BookRef, BookJudgment],
    held_out_looks: int = 0,
) -> str:
    items = sample.split(split)
    labeled = sum(i.ref in labels for i in items)
    judged = sum(i.ref in judgments and judgments[i.ref].judged for i in items)
    lines = [
        f"# Judge calibration: {sample.version}, {split.replace('_', '-')} half",
        "",
        f"Judge {judge.id} | {len(items)} books, {labeled} labeled, {judged} judged",
    ]
    if split == HELD_OUT:
        lines.append(
            f"Held-out looks so far: {held_out_looks}. Only aggregates are shown for this "
            "half: never tune the prompt on it."
        )
    lines += [
        "",
        "| Criterion | Pairs | Exact | Within one | Judge more lenient | Judge stricter |",
    ]
    lines += ["| --- | --- | --- | --- | --- | --- |"]
    stats = {c: agreement(items, labels, judgments, c) for c in Criterion}
    for criterion, a in stats.items():
        lines.append(
            f"| {criterion.value} | {a.n} | {_share(a.exact, a.n)} | {_share(a.within_one, a.n)} "
            f"| {a.lenient} | {a.strict} |"
        )
    for criterion, a in stats.items():
        lines += ["", f"## {criterion.value}: judge (rows) vs your label (columns)", ""]
        lines += _matrix(a)
    if split == TUNING:
        lines += ["", "## Disagreements", ""]
        lines += _disagreements(items, labels, judgments) or ["None."]
    return "\n".join(lines) + "\n"


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "0/0"


_ORDER = [Verdict.YES, Verdict.PARTIAL, Verdict.NO]


def _matrix(a: Agreement) -> list[str]:
    counts = a.matrix
    rows = ["| judge \\ you | " + " | ".join(v.value for v in _ORDER) + " |"]
    rows.append("| --- " * (len(_ORDER) + 1) + "|")
    for judge in _ORDER:
        cells = [str(counts[(judge, human)]) for human in _ORDER]
        rows.append(f"| {judge.value} | " + " | ".join(cells) + " |")
    return rows


def _disagreements(
    items: Sequence[SampleItem],
    labels: dict[BookRef, Label],
    judgments: dict[BookRef, BookJudgment],
) -> list[str]:
    lines: list[str] = []
    for item in items:
        label, judgment = labels.get(item.ref), judgments.get(item.ref)
        if label is None or judgment is None or not judgment.judged:
            continue
        for criterion in Criterion:
            human, judge = human_verdict(label, criterion), judgment.verdict(criterion)
            if human is None or judge is None or human == judge:
                continue
            reply = (
                judgment.fits_request
                if criterion is Criterion.FITS_REQUEST
                else judgment.reason_quality
            )
            justification = reply.justification if reply else ""
            note = f" Your note: {label.note}" if label.note else ""
            lines.append(
                f"- {judgment.title} ({item.ref.case_id}), {criterion.value}: judge "
                f"{judge.value}, you {human.value}. Judge: {justification!r}.{note}"
            )
    return lines
