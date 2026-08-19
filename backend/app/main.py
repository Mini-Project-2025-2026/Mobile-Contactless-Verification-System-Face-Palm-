"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import errors
from .config import settings
from .db import engine, init_db
from .logging_setup import configure as configure_logging
from .middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import admin, attendance, auth, checkin, courses, devices, enroll, profile

mimetypes.add_type("application/manifest+json", ".webmanifest")
_STATIC = Path(__file__).parent / "static"
_ADMIN_HTML = _STATIC / "admin.html"

log = logging.getLogger("attendance.startup")


def _report_configuration() -> None:
    """Say what is wrong with this deployment's configuration, at boot.

    Every one of these fails later, further from its cause and in front of
    someone who cannot fix it: an unset signing secret refuses every check-in
    with "biometric_unavailable" while a hall waits. In production the report is
    an error; in development it is a warning, because a laptop with no biometric
    service is a normal way to work on the rest of the app.
    """
    problems = settings.startup_problems(dict(os.environ))
    for problem in problems:
        (log.error if settings.is_production else log.warning)("config: %s", problem)
    if not os.environ.get("ADMIN_PASSWORD"):
        # A generated password locks the console rather than leaving a published
        # default open — but an operator who set none would otherwise simply find
        # themselves unable to log in, with nothing explaining why.
        log.warning("config: console password for THIS RUN ONLY: %s", settings.admin_password)
    if not problems:
        log.info("config: %s environment, no problems found", settings.environment)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level, as_json=settings.log_json)
    _report_configuration()
    init_db()
    if settings.seed_on_start:
        from .seed import run as seed_run
        seed_run()
    log.info("attendance-verify ready")
    yield
    log.info("attendance-verify shutting down")


app = FastAPI(
    title="Attendance-Verify API",
    version="0.2.0",
    description="Geofenced course attendance with biometric verification instead of a PIN.",
    lifespan=lifespan,
)

# Order matters: the outermost middleware runs first, so a request gets its id
# before anything else can log, and an oversized body is refused before the
# security headers are composed for a response that will never be sent.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

errors.install(app)

app.include_router(auth.router)
app.include_router(courses.router)
app.include_router(checkin.router)
app.include_router(attendance.router)
app.include_router(devices.router)
app.include_router(profile.router)
app.include_router(enroll.router)
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Is this process alive. Deliberately does no work — a platform health
    check that touches the database restarts the app when the database blinks."""
    return {"ok": True, "service": "attendance-verify", "version": app.version}


@app.get("/health/ready", tags=["meta"])
def readiness() -> dict:
    """Can this process actually serve a check-in right now.

    Names its dependencies separately, because they fail differently and are
    fixed by different people: a database outage is ours, an unreachable
    biometric service is the other team's, and an unset signing secret is a
    config line somebody forgot.
    """
    checks: dict[str, dict] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        log.error("readiness: database unreachable: %s", exc)
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    from . import biometric
    health = biometric.service_health()
    checks["biometric"] = {"ok": health.ok, "url": settings.biometric_base_url,
                           "version": health.version,
                           "active_liveness": health.active_liveness}
    if not health.ok:
        checks["biometric"]["error"] = health.detail

    checks["signing_secret"] = {"ok": bool(settings.biometric_signing_secret)}
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks,
            "environment": settings.environment}


@app.get("/admin", response_class=HTMLResponse, tags=["admin"])
def admin_console() -> str:
    """Serve the admin single-page console."""
    return _ADMIN_HTML.read_text(encoding="utf-8")


# Installable iPhone/Android PWA (login, enrol, geofence check-in) served at /app.
app.mount("/app", StaticFiles(directory=str(_STATIC / "pwa"), html=True), name="pwa")
