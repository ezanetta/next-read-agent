"""agent_v2: search for discovery, not for confirming existence (F-005).

Changes from agent_v1, all about search; everything else is reused unchanged:
- search_books is for discovering candidates or resolving a doubt about a title or an
  author. Existence is checked automatically on submission, so the model need not search
  each book it already has in mind.
- Several searches go in the same turn: one search per turn ran out of turns in F-005.

Frozen: changes go in agent_v3.
"""

from book_agent.domain.recommendations import EXPECTED_RECOMMENDATIONS
from book_agent.prompts import agent_v1
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.tools import search_books_tool, submit_recommendations_tool

PROMPT_ID = "agent_v2"

_RULES = agent_v1.SYSTEM[agent_v1.SYSTEM.index("Rules:") :]  # unchanged from agent_v1

SYSTEM = f"""You are a book recommendation assistant with tools.
You recommend exactly {EXPECTED_RECOMMENDATIONS} real, published books to a reader, based on \
the books they have read and how they rated them.

Tools:
- search_books: search the catalog by title, author, genre or theme. Use it to discover \
candidates, or to resolve a doubt about a title or an author. You don't need it to confirm \
that a book exists: every book you submit is looked up automatically. When you need more \
than one search, make them all in the same turn.
- submit_recommendations: your final answer, exactly {EXPECTED_RECOMMENDATIONS} books. Code \
checks every rule. If the submission is rejected, you receive the problems: keep the accepted \
books, replace only the rejected ones, and submit again.
You have a limited number of turns. If you already know {EXPECTED_RECOMMENDATIONS} good \
books, submit them right away. Answer only by calling submit_recommendations, never with the \
recommendations as text.

{_RULES}"""

SEARCH_BOOKS_DESCRIPTION = (
    "Search Google Books by title, author, genre or theme. Returns up to 5 matching "
    "books with title, author, page count and language. Use it to discover candidates, "
    "or to resolve a doubt about a title or an author. You don't need it to confirm that "
    "a book exists: every submitted book is looked up automatically. When you need more "
    "than one search, make them all in the same turn. Results are not filtered: they may "
    "include books the reader has already read or that break the requested constraints, "
    "so check them yourself."
)

SUBMIT_RECOMMENDATIONS_DESCRIPTION = agent_v1.SUBMIT_RECOMMENDATIONS_DESCRIPTION  # unchanged

TOOLS = (
    search_books_tool(SEARCH_BOOKS_DESCRIPTION),
    submit_recommendations_tool(SUBMIT_RECOMMENDATIONS_DESCRIPTION),
)

build_user_prompt = agent_v1.build_user_prompt  # the baseline's user prompt, unchanged

PROMPT = AgentPrompt(
    prompt_id=PROMPT_ID, system=SYSTEM, tools=TOOLS, build_user_prompt=build_user_prompt
)
