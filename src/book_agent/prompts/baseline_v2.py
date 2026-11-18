"""baseline_v2: baseline_v1 plus the minimum page count among the constraints (D-026).

Only the constraint lines change; the system prompt and the rest of the user prompt are
baseline_v1's, byte for byte. The agent's user prompt is this one from agent_v3 on.

Frozen once used by evals: changes go in baseline_v3.
"""

from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters
from book_agent.prompts import baseline_v1

PROMPT_ID = "baseline_v2"

SYSTEM = baseline_v1.SYSTEM  # unchanged


def build_user_prompt(profile: Profile, filters: Filters, preference: str | None = None) -> str:
    return baseline_v1.compose_user_prompt(profile, constraints(filters), preference)


def constraints(filters: Filters) -> list[str]:
    lines: list[str] = []
    if filters.language:
        lines.append(
            "Every book must be available in the language with ISO 639-1 code "
            f"{filters.language!r}."
        )
    if filters.max_pages:
        lines.append(f"Every book must have at most {filters.max_pages} pages.")
    if filters.min_pages:
        lines.append(f"Every book must have at least {filters.min_pages} pages.")
    return lines
