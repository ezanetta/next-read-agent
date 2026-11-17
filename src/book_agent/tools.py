"""The agent's tools: names, argument models and how each one runs.

Tool descriptions are not here: they are part of each agent prompt version.
"""

import json

from pydantic import BaseModel, Field

from book_agent.domain.models import CatalogBook
from book_agent.domain.recommendations import RecommendationList
from book_agent.llm.base import ToolSpec
from book_agent.ports import BookSearch


class SearchBooksArgs(BaseModel):
    query: str = Field(description="Title, author, genre or theme to search for")
    language: str | None = Field(default=None, description="ISO 639-1 code, for example 'en'")


SEARCH_BOOKS = "search_books"
SUBMIT_RECOMMENDATIONS = "submit_recommendations"


def search_books_tool(description: str) -> ToolSpec:
    """The search_books tool with a prompt version's description.

    Descriptions live in the prompt modules (agent_v1, agent_v2...): what the model reads
    about a tool is part of the prompt version. Name and arguments are fixed here.
    """
    return ToolSpec.from_model(name=SEARCH_BOOKS, description=description, args=SearchBooksArgs)


def submit_recommendations_tool(description: str) -> ToolSpec:
    """The submit_recommendations tool with a prompt version's description."""
    return ToolSpec.from_model(
        name=SUBMIT_RECOMMENDATIONS, description=description, args=RecommendationList
    )


SEARCH_RESULTS = 5


async def find_books(args: SearchBooksArgs, books: BookSearch) -> list[CatalogBook]:
    """The search_books tool's lookup. Raises BookSearchError if the search fails."""
    return await books.search(args.query, language=args.language, max_results=SEARCH_RESULTS)


def format_search_results(args: SearchBooksArgs, results: list[CatalogBook]) -> str:
    """What the model reads: compact JSON, or a hint to broaden the query."""
    if not results:
        return f'No books found for "{args.query}". Try a different or broader query.'
    items = [
        {
            "title": book.title,
            "author": ", ".join(book.authors),
            "pages": book.pages,
            "language": book.language,
        }
        for book in results
    ]
    return json.dumps(items, ensure_ascii=False)


async def run_search_books(args: SearchBooksArgs, books: BookSearch) -> str:
    return format_search_results(args, await find_books(args, books))
