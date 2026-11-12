# Book Recommendation Agent

## Project
An agent that recommends 3 books based on the books I've read (Goodreads CSV export or a manual list), each with a link and a reason that cites my own books. Supports filters (language, page count, genre) as hard constraints validated in code, not by the LLM.

## Stack
Python 3.13, uv, FastAPI, Pydantic, pydantic-settings, httpx, pytest, ruff, pyright (strict).
Later: Postgres + pgvector (Docker Compose), Langfuse, React + Vite + Tailwind for the UI.

## Architecture (planned)
- Catalog: Goodbooks-10k enriched once with Google Books data (description, pages, language, link), stored in Postgres + pgvector.
- Per request: retrieve ~50 candidates with SQL filters + vector similarity, the LLM reranks and picks 3, code validates the hard rules (3 books, none already read, filters respected, valid links).
- Google Books API is used live only as an agent fallback tool and for the UI book search.
- The LLM sits behind our own LLMClient interface. Provider is selected with LLM_PROVIDER (gemini or claude). No LangChain.

## How to work with me
- I come from Kotlin. Explain new Python concepts by comparing them to Kotlin.
- Small steps: one module at a time. Wait for my OK before moving on.
- Type hints everywhere. pyright strict and ruff must pass. Tests for every module.
- Never commit secrets. API keys only live in .env.

## Ownership rules
- I write the agent loop (agent.py) and the eval harness (evals/) myself. In those areas, review, explain and suggest, but do not write the implementation unless I explicitly ask.

## Commands
- Tests: uv run pytest
- Lint and format: uv run ruff check . && uv run ruff format .
- Types: uv run pyright
- Dev server: uv run fastapi dev src/book_agent/main.py
