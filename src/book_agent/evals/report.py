"""Scoring recorded runs: pure functions over EvalRecords, no LLM calls.

The acceptance policy is applied here, at report time (D-021), so a policy change
rescores old runs for free. Caveat: for the agent, the run's path (rejections,
resubmissions) followed the policy in force when it ran; rescoring judges its final
validation only.
"""

import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from statistics import mean

from book_agent.domain.acceptance import blocking_problems, is_acceptable
from book_agent.domain.recommendations import Lookup, ValidationResult
from book_agent.evals.records import EvalRecord
from book_agent.tools import SEARCH_BOOKS

Policy = Callable[[ValidationResult], bool]

ACCEPTED, REJECTED = "accepted", "rejected"


def is_accepted(record: EvalRecord, policy: Policy = is_acceptable) -> bool:
    return record.final_validation is not None and policy(record.final_validation)


def outcome(record: EvalRecord, policy: Policy = is_acceptable) -> str:
    """ "accepted", the error kind, or "rejected" (validated, but the policy says no)."""
    if is_accepted(record, policy):
        return ACCEPTED
    return record.error_kind or REJECTED


def used_fallback(record: EvalRecord) -> bool:
    validations = [
        *record.rejections,
        *([record.final_validation] if record.final_validation else []),
    ]
    return any(item.found_by is Lookup.FALLBACK for v in validations for item in v.items)


def search_counts(record: EvalRecord) -> list[int]:
    """Searches per turn, for the turns that searched."""
    counts = [sum(1 for t in turn.tools if t.name == SEARCH_BOOKS) for turn in record.turns]
    return [c for c in counts if c > 0]


def searched_one_per_turn(record: EvalRecord) -> bool:
    """At least 2 turns with searches, and every one of them made exactly one (F-007)."""
    counts = search_counts(record)
    return len(counts) >= 2 and all(c == 1 for c in counts)


def p95(values: Sequence[float]) -> float:
    """Nearest-rank 95th percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


@dataclass
class ConfigStats:
    config_id: str
    system: str
    runs: int
    accepted: int
    outcomes: Counter[str]
    violations: Counter[str]  # by rule, on final validations
    unknowns: Counter[str]  # by rule, on final validations
    rejection_reasons: Counter[str]  # by rule, blocking problems of rejected submissions
    mean_iterations: float
    max_iterations: int
    mean_submissions: float
    one_search_per_turn: int
    searches_per_search_turn: float  # mean over all turns that searched
    mean_requests: float
    mean_input_tokens: float
    mean_output_tokens: float
    total_tokens: int
    mean_latency_s: float
    p95_latency_s: float
    repair_turn_used: int
    fallback_used: int
    warnings: list[str] = field(default_factory=list[str])

    @property
    def accept_rate(self) -> float:
        return self.accepted / self.runs if self.runs else 0.0


@dataclass
class Report:
    case_set: str
    configs: list[ConfigStats]
    # case id -> config id -> (accepted, runs)
    per_case: dict[str, dict[str, tuple[int, int]]]


def build_report(
    case_set: str, records: Iterable[EvalRecord], policy: Policy = is_acceptable
) -> Report:
    by_config: dict[str, list[EvalRecord]] = {}
    for record in records:
        by_config.setdefault(record.config.id, []).append(record)

    per_case: dict[str, dict[str, tuple[int, int]]] = {}
    for config_id, group in by_config.items():
        for record in group:
            accepted, runs = per_case.setdefault(record.case_id, {}).get(config_id, (0, 0))
            per_case[record.case_id][config_id] = (
                accepted + is_accepted(record, policy),
                runs + 1,
            )
    configs = [config_stats(cid, group, policy) for cid, group in sorted(by_config.items())]
    return Report(case_set=case_set, configs=configs, per_case=dict(sorted(per_case.items())))


def config_stats(config_id: str, records: list[EvalRecord], policy: Policy) -> ConfigStats:
    finals = [r.final_validation for r in records if r.final_validation is not None]
    turns_searches = [c for r in records for c in search_counts(r)]
    tokens_in = [sum(c.usage.input_tokens for c in r.calls if c.usage) for r in records]
    tokens_out = [sum(c.usage.output_tokens for c in r.calls if c.usage) for r in records]
    durations = [r.duration_s for r in records]
    return ConfigStats(
        config_id=config_id,
        system=records[0].config.system,
        runs=len(records),
        accepted=sum(is_accepted(r, policy) for r in records),
        outcomes=Counter(outcome(r, policy) for r in records),
        violations=Counter(v.rule.value for f in finals for v in f.violations),
        unknowns=Counter(u.rule.value for f in finals for u in f.unknowns),
        rejection_reasons=Counter(
            p.rule.value for r in records for v in r.rejections for p in blocking_problems(v)
        ),
        mean_iterations=mean(r.iterations for r in records),
        max_iterations=max(r.iterations for r in records),
        mean_submissions=mean(r.submissions for r in records),
        one_search_per_turn=sum(searched_one_per_turn(r) for r in records),
        searches_per_search_turn=mean(turns_searches) if turns_searches else 0.0,
        mean_requests=mean(len(r.calls) for r in records),
        mean_input_tokens=mean(tokens_in),
        mean_output_tokens=mean(tokens_out),
        total_tokens=sum(tokens_in) + sum(tokens_out),
        mean_latency_s=mean(durations),
        p95_latency_s=p95(durations),
        repair_turn_used=sum(r.repair_turn_used for r in records),
        fallback_used=sum(used_fallback(r) for r in records),
        warnings=_warnings(records),
    )


def _warnings(records: list[EvalRecord]) -> list[str]:
    warnings: list[str] = []
    commits = {r.git_commit for r in records}
    if len(commits) > 1:
        warnings.append(f"runs come from {len(commits)} git commits")
    dirty = sum(r.git_dirty for r in records)
    if dirty:
        warnings.append(f"{dirty} runs had uncommitted changes")
    if len({r.profile_sha256 for r in records}) > 1:
        warnings.append("runs used different libraries")
    if len({r.cases_sha256 for r in records}) > 1:
        warnings.append("runs used different versions of the case file")
    return warnings


# --- Markdown ------------------------------------------------------------------------


def render_markdown(report: Report) -> str:
    lines = [f"# Eval report: {report.case_set}", ""]
    if not report.configs:
        return "\n".join([*lines, "No records yet."])
    lines += ["## Per configuration", ""]
    lines += _config_table(report.configs)
    for stats in report.configs:
        lines += ["", f"### {stats.config_id}", ""]
        lines += [f"- Outcomes: {_counts(stats.outcomes)}"]
        lines += [f"- Violations by rule (final): {_counts(stats.violations)}"]
        lines += [f"- Unknowns by rule (final): {_counts(stats.unknowns)}"]
        if stats.system == "agent":
            lines += [f"- Rejection reasons: {_counts(stats.rejection_reasons)}"]
        lines += [f"- Warning: {w}" for w in stats.warnings]
    lines += ["", "## Per case (accepted / runs)", ""]
    lines += _case_table(report)
    return "\n".join(lines) + "\n"


NOT_APPLICABLE = "n/a"  # agent-only metrics in a baseline row

_CONFIG_COLUMNS = [
    "Configuration",
    "Runs",
    "Accepted",
    "Iterations (mean / max)",
    "Submissions",
    "One search per turn",
    "Searches per search turn",
    "Requests",
    "Tokens in / out",
    "Latency (mean / p95)",
    "Repair turn",
    "Fallback",
]


def _config_table(configs: list[ConfigStats]) -> list[str]:
    rows = [_row(_CONFIG_COLUMNS), _row(["---"] * len(_CONFIG_COLUMNS))]
    for s in configs:
        agent = s.system == "agent"

        def agent_only(text: str, agent: bool = agent) -> str:
            return text if agent else NOT_APPLICABLE

        rows.append(
            _row(
                [
                    s.config_id,
                    str(s.runs),
                    f"{s.accepted} ({s.accept_rate:.0%})",
                    agent_only(f"{s.mean_iterations:.1f} / {s.max_iterations}"),
                    f"{s.mean_submissions:.1f}",
                    agent_only(f"{s.one_search_per_turn} / {s.runs}"),
                    agent_only(f"{s.searches_per_search_turn:.1f}"),
                    f"{s.mean_requests:.1f}",
                    f"{s.mean_input_tokens:,.0f} / {s.mean_output_tokens:,.0f}",
                    f"{s.mean_latency_s:.1f}s / {s.p95_latency_s:.1f}s",
                    agent_only(str(s.repair_turn_used)),
                    str(s.fallback_used),
                ]
            )
        )
    return rows


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _case_table(report: Report) -> list[str]:
    config_ids = [s.config_id for s in report.configs]
    rows = [_row(["Case", *config_ids]), _row(["---"] * (len(config_ids) + 1))]
    for case_id, cells in report.per_case.items():
        values = [
            f"{cells[c][0]}/{cells[c][1]}" if c in cells else NOT_APPLICABLE for c in config_ids
        ]
        rows.append(_row([case_id, *values]))
    return rows


def _counts(counter: Counter[str]) -> str:
    return ", ".join(f"{key} {count}" for key, count in counter.most_common()) or "none"
