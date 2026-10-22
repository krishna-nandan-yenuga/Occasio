"""FastAPI app factory: routes, security headers, rate limiter, static files, health check.

No CORS middleware is installed, so browsers keep the API same-origin only.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import chat, plans
from app.api.ratelimit import RateLimiter
from app.config import Settings, get_settings
from app.llm.provider import LLMProvider, get_provider
from app.storage.repo import PlanRepository

STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
}
HSTS = "max-age=31536000; includeSubDomains"
# Swagger UI loads its assets from a CDN, which the strict CSP above would block.
DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Adds SECURITY_HEADERS (and HSTS in production) to every HTTP response."""

    def __init__(self, app: ASGIApp, production: bool) -> None:
        self.app = app
        self.headers = [(k.encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
        if production:
            self.headers.append((b"strict-transport-security", HSTS.encode()))
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        exempt = not self.production and scope["path"].startswith(DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = self.headers
                if exempt:
                    headers = [h for h in headers if h[0] != b"content-security-policy"]
                message["headers"] = [*message.get("headers", []), *headers]
            await send(message)

        await self.app(scope, receive, send_with_headers)


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
