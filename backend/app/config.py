"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./attendance.db"
    jwt_secret: str = "dev-insecure-change-me"
    jwt_expire_minutes: int = 43_200  # 30 days
    jwt_algorithm: str = "HS256"

    biometric_base_url: str = "https://127.0.0.1:5000"
    biometric_api_key: str = ""
    biometric_signing_secret: str = ""
    biometric_verify_tls: bool = True

    geofence_default_radius_m: float = 70.0
    min_verify_score: float = 0.40
    # Reject a check-in whose GPS fix is less accurate than this many metres
    # (0 = disabled). Guards against wildly-off fixes sneaking into the geofence.
    max_gps_accuracy_m: float = 0.0

    # When true (set on the hosted demo), seed demo data on startup if absent.
    seed_on_start: bool = False

    # Admin console credentials (override via env in production).
    admin_username: str = "admin"
    admin_password: str = "cLLeB"

    # If true, a student may only use ONE device (hard lock at login). Default
    # false: biometric verification prevents proxy attendance, so we relax the
    # login lock and instead bind the ENROLMENT device (re-enrol needs a grant).
    enforce_login_device: bool = False

    # A student who has never enrolled just enrols, on the spot, like the campus
    # app they already use. What is protected is CHANGE: once an ID is tied to a
    # face, re-enrolling it (or adding a modality from another device) needs an
    # admin one-time code. Set true to require a code for the first enrolment too.
    enroll_requires_grant: bool = False


settings = Settings()
