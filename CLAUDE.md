# Book Recommendation Agent

## Project
An agent that recommends three books based on a reader's history (a Goodreads CSV export or a short manual list). Each recommendation includes a link and a reason that cites books the reader has already read. Filters (language, page count, genre) are hard constraints validated in code, not by the LLM.

## Stack
Python 3.13, uv, FastAPI, Pydantic, pydantic-settings, httpx, google-genai, pytest, ruff, pyright (strict).
Planned: Postgres + pgvector (Docker Compose), Langfuse, React + Vite + Tailwind for the UI.

## Architecture (planned)
- Catalog: Goodbooks-10k enriched once with Google Books data (description, pages, language, link), stored in Postgres + pgvector.
- Per request: retrieve ~50 candidates with SQL filters + vector similarity, the LLM reranks and picks 3, code validates the hard rules (3 books, none already read, filters respected, valid links).
- Google Books API is used live only as an agent fallback tool and for the UI book search.
- The LLM sits behind our own LLMClient interface. Provider is selected with LLM_PROVIDER (gemini or claude). No LangChain.

## Architecture rules
- domain/ holds models and pure business rules. It never imports FastAPI, httpx or any LLM SDK.
- External services sit behind Protocols in ports.py. Adapters implement them in adapters/.
- Exception: the LLM contract (LLMClient Protocol, message/tool types, LLM errors) lives in llm/base.py, and provider adapters live next to it (llm/gemini.py, llm/claude.py). base.py never imports a provider SDK.
- The agent loop appends response.message to the history unchanged. Never rebuild, reorder or merge assistant parts, or provider metadata such as thought signatures is lost.
- Dependencies are passed through constructors and wired in one place (FastAPI Depends). No DI framework.
- No mapper layers between identical models. Prefer functions over classes when there is no state.

## Code and testing rules
- Type hints everywhere. pyright (strict) and ruff must pass.
- Every module has tests.
- Prefer fakes that implement the Protocols over unittest.mock.
- Tests that hit real APIs are marked @pytest.mark.live and excluded by default.

## Secrets
- Never commit secrets. API keys live only in .env (gitignored); .env.example documents every setting with placeholders.

## Commands
- Tests: uv run pytest
- Live tests (use real API quota): uv run pytest -m live
- Lint and format: uv run ruff check . && uv run ruff format .
- Types: uv run pyright
- Dev server: uv run fastapi dev src/book_agent/main.py
