"""What every request passes through before it reaches a route.

Four concerns, deliberately separate and deliberately thin:

* **Request id** — accepted from the caller or minted here, stamped on every log
  line for the request and returned in `X-Request-ID`, so a complaint on a phone
  can be found in a log stream.
* **Access log** — method, path, status and duration, at one line per request.
  Without it there is no way to tell a slow biometric service from a slow app.
* **Body size** — enrolment posts base64 photographs, so the ceiling has to be
  generous; without a ceiling at all, one client can post until the dyno dies.
* **Security headers** — this app serves an HTML console and a PWA from the same
  origin as the API, so the browser-facing hardening belongs here.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import settings
from .errors import error_response
from .logging_setup import set_request_id

log = logging.getLogger("attendance.access")

REQUEST_ID_HEADER = "X-Request-ID"

#: Sent on every response. `frame-ancestors 'none'` is the clickjacking guard
#: that matters for an admin console; the CSP is otherwise permissive because
#: both bundled pages are single files with inline styles and scripts.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self), geolocation=(self), microphone=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https://nominatim.openstreetmap.org; "
        "media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'"
    ),
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Give the request an id, log its outcome, and time it."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        set_request_id(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handler below turns this into a response; log the
            # timing here so a failed request is not missing from the access log.
            duration = (time.perf_counter() - started) * 1000
            log.exception("%s %s failed after %.1fms", request.method, request.url.path, duration)
            raise
        duration = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        level = logging.WARNING if response.status_code >= 500 else logging.INFO
        log.log(level, "%s %s -> %d (%.1fms)",
                request.method, request.url.path, response.status_code, duration,
                extra={"method": request.method, "path": request.url.path,
                       "status": response.status_code, "duration_ms": round(duration, 1)})
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse a body larger than the configured ceiling.

    Checks `Content-Length` first, which is what an honest client sends and
    costs nothing. A chunked upload with no length declared is still bounded,
    by counting bytes as they arrive and stopping at the limit.
    """

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return self._too_large(int(declared))
        if not declared and request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()  # cached on the request; routes re-read it
            if len(body) > self.max_bytes:
                return self._too_large(len(body))
        return await call_next(request)

    def _too_large(self, size: int) -> Response:
        log.warning("rejected %d-byte body (limit %d)", size, self.max_bytes)
        return error_response(
            413,
            f"That upload is too large ({size // 1024} KB). "
            f"The limit is {self.max_bytes // 1024} KB — send fewer or smaller images.",
            code="payload_too_large",
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add the browser-facing headers, plus HSTS where the request arrived over TLS."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
        if forwarded == "https" and settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def client_ip(request: Request) -> str:
    """The caller's address, honouring the proxy header Heroku sets.

    Only the first hop is used: the rest of `X-Forwarded-For` is whatever the
    client chose to send, and treating that as identity would let anyone reset
    their own rate limit by inventing a new address per attempt.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
