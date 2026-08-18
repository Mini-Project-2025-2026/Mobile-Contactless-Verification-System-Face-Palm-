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

    # When true (set on the hosted demo), seed demo data on startup if absent.
    seed_on_start: bool = False

    # Admin console credentials (override via env in production).
    admin_username: str = "admin"
    admin_password: str = "cLLeB"


settings = Settings()
