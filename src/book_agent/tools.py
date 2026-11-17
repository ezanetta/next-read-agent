import json

from pydantic import BaseModel, Field

from book_agent.domain.recommendations import RecommendationList
from book_agent.llm.base import ToolSpec
from book_agent.ports import BookSearch


class SearchBooksArgs(BaseModel):
    query: str = Field(description="Title, author, genre or theme to search for")
    language: str | None = Field(default=None, description="ISO 639-1 code, for example 'en'")


SEARCH_BOOKS = ToolSpec.from_model(
    name="search_books",
    description="Search Google Books by title, author, genre or theme. Returns up to 5 matching "
    "books with title, author, page count and language. Use it to find candidates "
    "and to confirm a book exists before recommending it. Results are not filtered: "
    "they may include books the reader has already read or that break the requested "
    "constraints, so check them yourself.",
    args=SearchBooksArgs,
)

SUBMIT_RECOMMENDATIONS = ToolSpec.from_model(
    name="submit_recommendations",
    description=(
        "Submit your final answer: exactly 3 book recommendations for the reader. "
        "Call it only when you have chosen all 3 books. Each book must be new to the "
        "reader: not read, not on their to-read list and not currently being read, in "
        "any language or edition. Each book must respect the requested constraints, "
        "such as language and page count. Each reason must explain the choice and cite "
        "at least one book the reader has read, by title. Your submission will be "
        "validated. If it is rejected, you will receive the problems found: fix only "
        "the rejected books and submit again."
    ),
    args=RecommendationList,
)


async def run_search_books(args: SearchBooksArgs, books: BookSearch) -> str:
    results = await books.search(args.query, language=args.language, max_results=5)
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
