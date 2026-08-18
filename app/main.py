"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import attendance, auth, checkin, courses, devices, enroll, profile, sessions


@asynccontextmanager
async def lifespan(_: FastAPI):
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
app.include_router(sessions.router)
app.include_router(checkin.router)
app.include_router(attendance.router)
app.include_router(devices.router)
app.include_router(profile.router)
app.include_router(enroll.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"ok": True, "service": "attendance-verify"}
