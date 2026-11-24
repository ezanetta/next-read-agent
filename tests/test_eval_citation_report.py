from book_agent.evals.citation_report import render_citation_section
from book_agent.evals.records import EvalRecord
from tests.judge_data import AGENT, BASELINE, checked, stored

V4 = AGENT.model_copy(update={"prompt_id": "agent_v4"})


def with_retrieved(record: EvalRecord, *titles: str) -> EvalRecord:
    return record.model_copy(update={"retrieved": list(titles)})


def test_diversity_and_reuse_per_configuration() -> None:
    records = [
        stored(f"case-{i}", [checked("A", cited=["Dune", "Dune"]), checked("B", cited=["Emma"])])
        for i in range(3)
    ] + [stored("case-0", [checked("C", cited=["Ficciones"])], config=BASELINE)]
    lines = render_citation_section(records)
    assert "| agent-agent_v3-m-default-h2 | 6 | 6 | 2 | 2 | n/a |" in lines  # a repeat counts once
    assert "| baseline-baseline_v2-m-default-h2 | 1 | 1 | 1 | 0 | n/a |" in lines
    assert "- agent-agent_v3-m-default-h2, cited in 3+ cases: Dune (3), Emma (3)" in lines
    assert not any(line.startswith("### Retrieved") for line in lines)


def test_retrieved_and_cited_per_case() -> None:
    hit = with_retrieved(
        stored("sea", [checked("A", cited=["Moby Dick"]), checked("B", cited=["Dune"])],
               config=V4),
        "Moby Dick", "Emma",
    )  # fmt: skip
    again = with_retrieved(
        stored("sea", [checked("C", cited=["Moby Dick"])], config=V4, run_index=2),
        "Moby Dick",
        "Emma",
    )
    no_text = stored("library-only", [checked("D", cited=["Dune"])], config=V4, request=None)
    lines = render_citation_section([hit, again, no_text])
    assert "| agent-agent_v4-m-default-h2 | 4 | 4 | 2 | 0 | 2/3 (67%) |" in lines
    sea = lines.index("**sea**")
    assert lines[sea + 1] == "- Retrieved: Moby Dick; Emma"
    assert lines[sea + 2] == "- Cited: Moby Dick ✓ (2); Dune (1)"
    library_only = lines.index("**library-only**")
    assert lines[library_only + 1] == "- Retrieved: none (no request text, or retrieval failed)"


def test_runs_without_a_validated_submission_are_skipped() -> None:
    record = stored("x", [checked("A")]).model_copy(update={"final_validation": None})
    assert render_citation_section([record])[-1] == "No validated submissions yet."
