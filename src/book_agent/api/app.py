"""The demo API app (D-043): routes, CORS, error bodies, and services from wiring."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from book_agent.api.routes import books, profiles
from book_agent.api.services import ApiServices
from book_agent.demo_profiles import ProfileError

ServicesFactory = Callable[[], AbstractAsyncContextManager[ApiServices]]


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
            yield

    app = FastAPI(
        title="Book Recommendation Agent",
        description="Demo API: temporary profiles, book search, and streamed recommendations.",
        version="1.0.0",
        lifespan=lifespan,
    )
    if services is not None:
        app.state.services = services
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

    return app
