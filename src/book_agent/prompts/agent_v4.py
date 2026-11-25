"""agent_v4: agent_v3 plus the reader's books most related to the request (D-041).

Changes from agent_v3:
- The user prompt adds a section after the reader's lists: the read books retrieved as
  most related to the request, with rating and a short description. The full lists stay,
  since exclusion needs them.
- The system prompt adds one rule: prefer citing those books when the connection is real.

Without retrieved books (no request text, or retrieval failed), the user prompt is
agent_v3's byte for byte. Tools are agent_v3's. Frozen once used by evals: changes go in
agent_v5.
"""

from collections.abc import Sequence

from book_agent.domain.library import RelatedBook
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters
from book_agent.prompts import agent_v3, baseline_v1, baseline_v2
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.prompts.user_content import one_line, without_tags

PROMPT_ID = "agent_v4"

RELATED_RULE = (
    "- When the reader's message lists books they've read that are most related to the "
    "request, prefer citing those, but only when the connection to the recommended book is "
    "real: cite a book for something it truly shares with the recommendation, never just "
    "because it is listed."
)

SYSTEM = f"{agent_v3.SYSTEM}\n{RELATED_RULE}"
TOOLS = agent_v3.TOOLS  # unchanged

RELATED_HEADING = (
    "Books I've read that are most related to this request (rating out of 5, when I rated "
    "it), with what they are about"
)
RELATED_HEADING_UNRATED = (
    "Books I've read that are most related to this request, with what they are about"
)


def related_section(books: Sequence[RelatedBook], *, show_ratings: bool = True) -> str:
    """The retrieved books, one per line. Descriptions come from catalogs, so they are
    flattened to one line and stripped of tags, like any outside text in a prompt.
    Without `show_ratings` (a manual list's implied ratings, D-043), no number is shown."""
    if not books:
        return ""
    lines = [f"{RELATED_HEADING if show_ratings else RELATED_HEADING_UNRATED}:"]
    for book in books:
        line = f"- {one_line(book.title)} by {one_line(book.author)}"
        if show_ratings and book.rating is not None:
            line += f" ({book.rating:g})"
        if book.description:
            line += f": {one_line(without_tags(book.description, 'request'))}"
        lines.append(line)
    return "\n".join(lines)


def build_related_prompt(
    profile: Profile,
    filters: Filters,
    preference: str | None,
    related: Sequence[RelatedBook],
) -> str:
    return baseline_v1.compose_user_prompt(
        profile,
        baseline_v2.constraints(filters),
        preference,
        [related_section(related, show_ratings=not profile.ratings_implied)],
    )


PROMPT = AgentPrompt(
    prompt_id=PROMPT_ID,
    system=SYSTEM,
    tools=TOOLS,
    build_user_prompt=agent_v3.build_user_prompt,
    build_related_prompt=build_related_prompt,
)
