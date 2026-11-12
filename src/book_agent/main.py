"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="Book Recommendation Agent")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
