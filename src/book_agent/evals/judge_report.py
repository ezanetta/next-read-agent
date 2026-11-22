"""The judge's scores per configuration, for the eval report (D-033).

For each criterion, the share of yes / partial / no over:
- every judged book,
- books from accepted runs (what a user would actually get),
- books with and without a catalog description, to see whether the judge is more
  lenient when it has to rely on its own knowledge of the book.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from book_agent.domain.acceptance import is_acceptable
from book_agent.evals.judge import BookJudgment, BookRef, Criterion, Verdict
from book_agent.evals.records import EvalRecord
from book_agent.evals.report import Policy, is_accepted


@dataclass(frozen=True)
class _Book:
    config_id: str
    accepted: bool
    judgment: BookJudgment


_VIEWS: list[tuple[str, Callable[[_Book], bool]]] = [
    ("all judged", lambda b: True),
    ("accepted runs", lambda b: b.accepted),
    ("with description", lambda b: b.judgment.had_description),
    ("without description", lambda b: not b.judgment.had_description),
]


def render_judge_section(
    records: Iterable[EvalRecord],
    judgments: dict[BookRef, BookJudgment],
    judge_id: str,
    policy: Policy = is_acceptable,
) -> list[str]:
    books: list[_Book] = []
    errors: Counter[str] = Counter()
    accepted = {(r.config.id, r.case_id, r.run_index): is_accepted(r, policy) for r in records}
    for ref, judgment in judgments.items():
        run = (ref.config_id, ref.case_id, ref.run_index)
        if run not in accepted:
            continue  # a judgment of a record not in this report
        if not judgment.judged:
            errors[ref.config_id] += 1
            continue
        books.append(_Book(ref.config_id, accepted[run], judgment))

    lines = ["", f"## Judge scores ({judge_id})", ""]
    if not books and not errors:
        return [*lines, "No judgments yet."]
    configs = sorted({b.config_id for b in books} | set(errors))
    for config_id in configs:
        if errors[config_id]:
            lines.append(f"- {config_id}: {errors[config_id]} books not judged (errors)")
    for criterion in Criterion:
        lines += ["", f"### {criterion.value}", ""]
        lines += [
            "| Configuration | Books | Yes | Partial | No |",
            "| --- | --- | --- | --- | --- |",
        ]
        for config_id in configs:
            for view, keep in _VIEWS:
                verdicts = [
                    v
                    for b in books
                    if b.config_id == config_id and keep(b)
                    if (v := b.judgment.verdict(criterion)) is not None
                ]
                lines.append(_row(f"{config_id}, {view}", verdicts))
    return lines


def _row(label: str, verdicts: list[Verdict]) -> str:
    counts = Counter(verdicts)
    n = len(verdicts)
    order = (Verdict.YES, Verdict.PARTIAL, Verdict.NO)
    cells = [f"{counts[v]} ({counts[v] / n:.0%})" if n else "-" for v in order]
    return f"| {label} | {n} | " + " | ".join(cells) + " |"
