"""Which library books the reasons cite, per configuration, for the eval report (D-041).

Counted over every run with a validated submission (final_validation), accepted or not,
from each recommended book's `cited_titles` (a reason citing a book twice counts once):
- distinct library books cited, and the books cited in 3 or more cases (the same
  favorites reused across unrelated requests);
- for configurations with library retrieval, the share of citations that name one of
  the books retrieved for that run;
- per case, the retrieved books and the books actually cited, for a manual check that
  diversity didn't come from random citations.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from book_agent.evals.records import EvalRecord

REUSE_CASES = 3  # a book cited in this many cases or more counts as reused (D-041)


@dataclass
class _Config:
    books: int = 0
    citations: int = 0
    cited: Counter[str] = field(default_factory=Counter[str])
    cases_by_book: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    with_retrieval: int = 0  # citations in runs that showed retrieved books
    from_retrieved: int = 0
    retrieved_by_case: dict[str, list[list[str]]] = field(
        default_factory=dict[str, list[list[str]]]
    )
    cited_by_case: defaultdict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter[str])
    )

    @property
    def reused(self) -> list[tuple[str, int]]:
        pairs = [(t, len(c)) for t, c in self.cases_by_book.items() if len(c) >= REUSE_CASES]
        return sorted(pairs, key=lambda pair: (-pair[1], pair[0]))


def render_citation_section(records: Iterable[EvalRecord]) -> list[str]:
    configs: defaultdict[str, _Config] = defaultdict(_Config)
    for record in records:
        if record.final_validation is None:
            continue
        stats = configs[record.config.id]
        retrieved = set(record.retrieved)
        if record.retrieved:
            stats.retrieved_by_case.setdefault(record.case_id, [])
            if record.retrieved not in stats.retrieved_by_case[record.case_id]:
                stats.retrieved_by_case[record.case_id].append(record.retrieved)
        for item in record.final_validation.items:
            stats.books += 1
            for title in dict.fromkeys(item.cited_titles):  # once per reason, in order
                stats.citations += 1
                stats.cited[title] += 1
                stats.cases_by_book[title].add(record.case_id)
                stats.cited_by_case[record.case_id][title] += 1
                if record.retrieved:
                    stats.with_retrieval += 1
                    stats.from_retrieved += title in retrieved

    lines = ["", "## Library citations", ""]
    if not configs:
        return [*lines, "No validated submissions yet."]
    lines += [
        "| Configuration | Recommended books | Citations | Distinct books cited "
        f"| Cited in {REUSE_CASES}+ cases | Citations from the retrieved books |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for config_id, s in sorted(configs.items()):
        share = _share(s.from_retrieved, s.with_retrieval) if s.with_retrieval else "n/a"
        lines.append(
            f"| {config_id} | {s.books} | {s.citations} | {len(s.cited)} | {len(s.reused)} "
            f"| {share} |"
        )
    for config_id, s in sorted(configs.items()):
        if s.reused:
            reused = ", ".join(f"{title} ({cases})" for title, cases in s.reused)
            lines += ["", f"- {config_id}, cited in {REUSE_CASES}+ cases: {reused}"]
    for config_id, s in sorted(configs.items()):
        if s.retrieved_by_case:
            lines += _per_case(config_id, s)
    return lines


def _per_case(config_id: str, stats: _Config) -> list[str]:
    lines = ["", f"### Retrieved and cited per case ({config_id})", ""]
    lines.append("Cited books marked ✓ were among the retrieved ones; (N) counts reasons.")
    for case_id in sorted(stats.cited_by_case.keys() | stats.retrieved_by_case.keys()):
        lines += ["", f"**{case_id}**"]
        lists = stats.retrieved_by_case.get(case_id, [])
        retrieved_any = {t for r in lists for t in r}
        if not lists:
            lines.append("- Retrieved: none (no request text, or retrieval failed)")
        for number, titles in enumerate(lists, start=1):
            label = "Retrieved" if len(lists) == 1 else f"Retrieved ({number} of {len(lists)})"
            lines.append(f"- {label}: " + "; ".join(titles))
        cited = stats.cited_by_case.get(case_id, Counter[str]())
        if cited:
            items = [
                f"{title}{' ✓' if title in retrieved_any else ''} ({count})"
                for title, count in cited.most_common()
            ]
            lines.append("- Cited: " + "; ".join(items))
        else:
            lines.append("- Cited: none")
    return lines


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})"
