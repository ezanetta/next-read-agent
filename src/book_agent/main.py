"""FastAPI application entry point: `uv run fastapi dev src/book_agent/main.py`.

Services are built from settings when the app starts (D-043), not at import, so importing
this module needs no API key.
"""

from book_agent.api.app import create_app
from book_agent.config import get_settings
from book_agent.wiring import api_services, cors_origins


def _origins() -> list[str]:
    try:
        return cors_origins(get_settings())
    except ValueError:  # no settings (e.g. tests without .env): no CORS, no services yet
        return []


app = create_app(services_factory=lambda: api_services(get_settings()), cors_origins=_origins())
