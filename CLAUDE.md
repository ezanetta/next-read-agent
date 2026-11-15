# Book Recommendation Agent

## Project
An agent that recommends 3 books based on the books I've read (Goodreads CSV export or a manual list), each with a link and a reason that cites my own books. Supports filters (language, page count, genre) as hard constraints validated in code, not by the LLM.

## Plan
For the full plan, scope, features and open questions, read docs/PLAN.md before starting any non-trivial task.

## Stack
Python 3.13, uv, FastAPI, Pydantic, pydantic-settings, httpx, pytest, ruff, pyright (strict).
Later: Postgres + pgvector (Docker Compose), Langfuse, React + Vite + Tailwind for the UI.

## Architecture (planned)
- Catalog: Goodbooks-10k enriched once with Google Books data (description, pages, language, link), stored in Postgres + pgvector.
- Per request: retrieve ~50 candidates with SQL filters + vector similarity, the LLM reranks and picks 3, code validates the hard rules (3 books, none already read, filters respected, valid links).
- Google Books API is used live only as an agent fallback tool and for the UI book search.
- The LLM sits behind our own LLMClient interface. Provider is selected with LLM_PROVIDER (gemini or claude). No LangChain.
- 
## Architecture rules
- domain/ holds models and pure business rules. It never imports FastAPI, httpx or any LLM SDK.
- External services sit behind Protocols in ports.py. Adapters implement them in adapters/.
- Exception: the LLM contract (LLMClient Protocol, message/tool types, LLM errors) lives in llm/base.py, and provider adapters live next to it (llm/gemini.py, llm/claude.py). base.py never imports a provider SDK.
- The agent loop appends response.message to the history unchanged. Never rebuild, reorder or merge assistant parts, or provider metadata such as thought signatures is lost.
- Dependencies are passed through constructors and wired in one place (FastAPI Depends). No DI framework.
- Prefer fakes that implement the Protocols over unittest.mock.
- No mapper layers between identical models. Prefer functions over classes when there is no state.
- Tests that hit real APIs are marked @pytest.mark.live and excluded by default.

## How to work with me
- I come from Kotlin. Explain new Python concepts by comparing them to Kotlin.
- Small steps: one module at a time. Wait for my OK before moving on.
- Type hints everywhere. pyright strict and ruff must pass. Tests for every module.
- Never commit secrets. API keys only live in .env.

## Ownership rules
- I write the agent loop (agent.py) and the eval harness (evals/) myself. In those areas, review, explain and suggest, but do not write the implementation unless I explicitly ask.

## Learning notes
At the end of every task, once tests pass, write a numbered explanation in docs/learning/ (local only, gitignored): what we built, why, Kotlin comparisons, and Mermaid diagrams where they help.
In every learning note, add a short "Patterns used" section: which design patterns the step applies, where in the code, and what problem each one solves.

## Commands
- Tests: uv run pytest
- Lint and format: uv run ruff check . && uv run ruff format .
- Types: uv run pyright
- Dev server: uv run fastapi dev src/book_agent/main.py
