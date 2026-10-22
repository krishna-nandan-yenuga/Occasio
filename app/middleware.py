"""ASGI middleware: security headers on every response, and a request body size limit.

Both are plain ASGI classes rather than BaseHTTPMiddleware, so responses are not buffered.
"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


# Every legitimate request body is a small JSON document; the largest (a full event context)
# is a few kilobytes.
MAX_BODY_BYTES = 64 * 1024
TOO_LARGE = "Request body too large."


class BodySizeLimitMiddleware:
    """Rejects request bodies over `max_bytes` with 413 before they are parsed.

    A declared Content-Length is checked up front; a body without one (chunked) is counted as
    it streams and the request fails as soon as it passes the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI re-raises HTTPException from body parsing, so this becomes a 413.
                    raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, receive if declared else counting_receive, send)
