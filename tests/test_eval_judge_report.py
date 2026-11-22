from book_agent.domain.recommendations import Rule
from book_agent.evals.judge import BookJudgment, BookRef, CriterionJudgment, Verdict
from book_agent.evals.judge_report import render_judge_section
from tests.judge_data import AGENT, checked, stored

ACCEPTED_RUN = stored("a", [checked("One"), checked("Two")])
REJECTED_RUN = stored("b", [checked("Three")], violations=[(Rule.MAX_PAGES, 0)])


def ref(case_id: str, index: int) -> BookRef:
    return BookRef(config_id=AGENT.id, case_id=case_id, run_index=1, item_index=index)


def judged(verdict: Verdict, had_description: bool = True) -> BookJudgment:
    c = CriterionJudgment(justification="j", verdict=verdict)
    return BookJudgment(
        item_index=0, title="T", author="A", has_request=True,
        had_description=had_description, fits_request=c, reason_quality=c,
    )  # fmt: skip


def test_scores_per_view_including_description_split() -> None:
    judgments = {
        ref("a", 0): judged(Verdict.YES),
        ref("a", 1): judged(Verdict.PARTIAL, had_description=False),
        ref("b", 0): judged(Verdict.NO),
        ref("x", 0): judged(Verdict.NO),  # a record outside the report: ignored
    }
    lines = render_judge_section([ACCEPTED_RUN, REJECTED_RUN], judgments, "judge_v1-m-default")
    text = "\n".join(lines)
    assert "## Judge scores (judge_v1-m-default)" in text
    row = f"| {AGENT.id}, "
    assert row + "all judged | 3 | 1 (33%) | 1 (33%) | 1 (33%) |" in text
    assert row + "accepted runs | 2 | 1 (50%) | 1 (50%) | 0 (0%) |" in text
    assert row + "with description | 2 | 1 (50%) | 0 (0%) | 1 (50%) |" in text
    assert row + "without description | 1 | 0 (0%) | 1 (100%) | 0 (0%) |" in text


def test_errors_are_counted_apart() -> None:
    failed = BookJudgment(
        item_index=0, title="T", author="A", has_request=True, had_description=True, error="503"
    )
    text = "\n".join(render_judge_section([ACCEPTED_RUN], {ref("a", 0): failed}, "j"))
    assert f"- {AGENT.id}: 1 books not judged (errors)" in text
    assert "| " + AGENT.id + ", all judged | 0 | - | - | - |" in text


def test_no_judgments_yet() -> None:
    assert render_judge_section([ACCEPTED_RUN], {}, "j")[-1] == "No judgments yet."
