"""The demo API app (D-043): routes, CORS, error bodies, and services from wiring."""

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from book_agent.api.body_limit import BodySizeLimit
from book_agent.api.routes import books, profiles, recommendations
from book_agent.api.services import ApiServices
from book_agent.api.shutdown import drain_on_signals
from book_agent.demo_profiles import CSV_MAX_BYTES, ProfileError
from book_agent.recommendation_stream import RecommendationEvent

ServicesFactory = Callable[[], AbstractAsyncContextManager[ApiServices]]
# The upload's limit is the CSV's plus room for the multipart framing; every other body
# gets BodySizeLimit's default, the 64 KB JSON limit (R-005, D-043).
MULTIPART_OVERHEAD = 16 * 1024
BODY_LIMITS = {"/profiles/goodreads": CSV_MAX_BYTES + MULTIPART_OVERHEAD}


def create_app(
    services: ApiServices | None = None,
    *,
    services_factory: ServicesFactory | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """The app. Tests pass `services` built with fakes; production passes a factory that
    wiring builds from settings, entered when the app starts and closed when it stops."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if services is not None or services_factory is None:
            yield
            return
        async with services_factory() as built:
            app.state.services = built
            restore = drain_on_signals(built.draining)
            try:
                yield
            finally:
                restore()

    app = FastAPI(
        title="Book Recommendation Agent",
        description="Demo API: temporary profiles, book search, and streamed recommendations.",
        version="1.0.0",
        lifespan=lifespan,
    )
    if services is not None:
        app.state.services = services
    # Before CORS, so CORS stays outermost and a 413 still carries its headers.
    app.add_middleware(BodySizeLimit, limits=BODY_LIMITS)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )
    app.include_router(books.router)
    app.include_router(profiles.router)
    app.include_router(recommendations.router)

    @app.get("/health", operation_id="health", tags=["health"])
    async def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        """Liveness probe."""
        return {"status": "ok"}

    @app.exception_handler(ProfileError)
    async def profile_error(_: Request, exc: ProfileError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"error": "invalid_profile", "message": str(exc)}, status_code=400)

    @app.exception_handler(HTTPException)
    async def error_body(_: Request, exc: HTTPException) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        # Routes raise with an ErrorBody dict as detail: return it as the whole body.
        body = (
            exc.detail if isinstance(exc.detail, dict) else {"error": "http", "message": exc.detail}
        )
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = with_readable_names(
                with_event_schema(
                    get_openapi(
                        title=app.title,
                        version=app.version,
                        description=app.description,
                        routes=app.routes,
                    )
                )
            )
        return app.openapi_schema

    app.openapi = openapi  # pyright: ignore[reportAttributeAccessIssue]
    return app


# FastAPI names multipart bodies after the operation; the UI's types read better renamed.
RENAMED_SCHEMAS = {"Body_createGoodreadsProfile": "GoodreadsUpload"}


def with_readable_names(schema: dict[str, Any]) -> dict[str, Any]:
    """Rename generated component names, and every reference to them."""
    text = json.dumps(schema)
    for old, new in RENAMED_SCHEMAS.items():
        text = text.replace(f'"#/components/schemas/{old}"', f'"#/components/schemas/{new}"')
    renamed: dict[str, Any] = json.loads(text)
    components: dict[str, Any] = renamed.get("components", {}).get("schemas", {})
    for old, new in RENAMED_SCHEMAS.items():
        if old in components:
            components[new] = {**components.pop(old), "title": new}
    return renamed


def with_event_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the stream's event union to the components, so the UI's types include it: a
    streamed body isn't a response model FastAPI would publish by itself."""
    event = TypeAdapter(RecommendationEvent).json_schema(
        ref_template="#/components/schemas/{model}"
    )
    components: dict[str, Any] = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(event.pop("$defs", {}))
    components[recommendations.EVENT_SCHEMA] = event
    return schema
