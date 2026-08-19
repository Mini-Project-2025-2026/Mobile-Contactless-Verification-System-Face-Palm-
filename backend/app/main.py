"""FastAPI application entrypoint."""
from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import admin, attendance, auth, checkin, courses, devices, enroll, profile

mimetypes.add_type("application/manifest+json", ".webmanifest")
_STATIC = Path(__file__).parent / "static"
_ADMIN_HTML = _STATIC / "admin.html"


def _warn_about_generated_secrets() -> None:
    """Say so, loudly, when a secret was generated rather than configured.

    A generated admin password locks the console instead of leaving a published
    default open, but an operator who never set one would otherwise just find
    themselves unable to log in, with nothing explaining why.
    """
    import os

    if not os.environ.get("ADMIN_PASSWORD"):
        print(f"[config] ADMIN_PASSWORD not set. Console password for THIS RUN ONLY: "
              f"{settings.admin_password}", flush=True)
    if not os.environ.get("JWT_SECRET"):
        print("[config] JWT_SECRET not set. Signing with a throwaway key: every "
              "student is signed out when this process restarts.", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _warn_about_generated_secrets()
    init_db()
    if settings.seed_on_start:
        from .seed import run as seed_run
        seed_run()
    yield


app = FastAPI(
    title="Attendance-Verify API",
    version="0.1.0",
    description="Geofenced course attendance with biometric verification instead of a PIN.",
    lifespan=lifespan,
)

# Mobile clients call from arbitrary origins; tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return {"ok": True, "service": "attendance-verify"}


@app.get("/admin", response_class=HTMLResponse, tags=["admin"])
def admin_console() -> str:
    """Serve the admin single-page console."""
    return _ADMIN_HTML.read_text(encoding="utf-8")


# Installable iPhone/Android PWA (login, enrol, geofence check-in) served at /app.
app.mount("/app", StaticFiles(directory=str(_STATIC / "pwa"), html=True), name="pwa")
