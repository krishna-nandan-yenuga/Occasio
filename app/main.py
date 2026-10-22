"""FastAPI app factory: routes, middleware, rate limiter, ideas cache, static files, health.

No CORS middleware is installed, so browsers keep the API same-origin only.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import chat, plans
from app.api.ratelimit import RateLimiter
from app.config import Settings, get_settings
from app.llm.provider import LLMProvider, get_provider
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.services.ideas_cache import IdeasCache
from app.storage.repo import PlanRepository

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    """Build the app; `provider` overrides the one chosen by settings (used in tests)."""
    settings = settings or get_settings()
    production = settings.app_env == "prod"
    repo = PlanRepository(settings.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo.init_schema()
        yield

    app = FastAPI(
        title="Occasio",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
    )
    app.state.repo = repo
    app.state.provider = provider or get_provider(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.ideas_cache = IdeasCache()
    # Added last = outermost, so even 413 responses carry the security headers.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, production=production)
    app.include_router(plans.router)
    app.include_router(chat.router)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
