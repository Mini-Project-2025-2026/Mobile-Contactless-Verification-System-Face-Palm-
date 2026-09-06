"""One shape for every failure the API returns.

Three different things used to come back when a request failed: FastAPI's
`{"detail": "..."}` for a raised HTTPException, a validation error whose body is
a nested list of dicts, and — for anything unhandled — a bare 500 with a
traceback in the log and nothing in the response tying the two together.

A phone app has to branch on all three, and a student reporting "it says
something went wrong" gives support nothing to search for. So: one envelope,
always the same keys, always carrying the request id that is also on every log
line for that request.

    {"error": {"code": "not_in_geofence", "message": "...", "request_id": "..."}}

Domain codes the mobile client already branches on (`not_in_geofence`,
`grant_required`, …) are unchanged — they live in the 200-response bodies, not
here. This covers the failures, where nothing was agreed before.
"""
from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_setup import get_request_id

log = logging.getLogger("attendance.error")

#: HTTP status -> stable machine code, for statuses raised without one.
_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_unavailable",
    503: "unavailable",
}


def envelope(code: str, message: str, **extra) -> dict:
    body = {"code": code, "message": message, "request_id": get_request_id()}
    body.update(extra)
    return {"error": body}


def error_response(status_code: int, message: str, *, code: str = "",
                   headers: dict[str, str] | None = None, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=envelope(code or _CODES.get(status_code, "error"), message, **extra),
        headers=headers,
    )


#: A detail that is nothing but a snake_case token IS the code — the shape used
#: by `raise HTTPException(409, "session_closed")` all over this codebase.
_BARE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def _code_from_detail(detail: object, status_code: int) -> tuple[str, str]:
    """Recover the machine code from the detail, in either shape used here.

    `raise HTTPException(409, "device_conflict: another device is registered")`
    and `raise HTTPException(409, "session_closed")` were both written to give
    the client something stable to branch on. Honour both rather than making
    every call site change shape — and rather than flattening them all to
    "conflict", which is what a status-code lookup alone would do.
    """
    text = detail if isinstance(detail, str) else str(detail)
    head, sep, tail = text.partition(":")
    if sep and " " not in head.strip() and head.strip():
        return head.strip(), tail.strip() or head.strip()
    if _BARE_CODE.match(text.strip()):
        return text.strip(), text.strip()
    return _CODES.get(status_code, "error"), text


def install(app: FastAPI) -> None:
    """Attach the handlers. Called once, from the app factory."""

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _code_from_detail(exc.detail, exc.status_code)
        return error_response(exc.status_code, message, code=code,
                              headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep the field detail: it is what tells a client developer which key
        # was wrong. Just put it inside the same envelope as everything else.
        fields = [
            {"field": ".".join(str(p) for p in err.get("loc", ())[1:]),
             "problem": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        return error_response(status.HTTP_422_UNPROCESSABLE_ENTITY,
                              "Some fields were not accepted.",
                              code="invalid_request", fields=fields)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The detail goes to the log with the request id; the client gets the id
        # and nothing else. A stack trace in a response is a map of the server.
        log.exception("unhandled error on %s %s: %s", request.method, request.url.path, exc)
        return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR,
                              "Something went wrong on our side. Quote the request id if you report this.",
                              code="internal_error")
