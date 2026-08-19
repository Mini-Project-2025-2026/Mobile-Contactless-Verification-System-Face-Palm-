"""Application settings, loaded from environment / .env."""
from __future__ import annotations

import secrets

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _generated() -> str:
    """A value that only exists for this process, when the operator set none.

    Deliberately not a memorable default. A shared fallback password is the same
    password on every deployment, published in this file - and this repository is
    one `gh repo edit --visibility public` away from handing over the console.
    An unset secret should lock the door, not leave a key under it.
    """
    return secrets.token_urlsafe(32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    #: "development" | "production" | "test". Production is louder about missing
    #: secrets and adds HSTS; it never silently substitutes a working default.
    environment: str = "development"
    log_level: str = "INFO"
    #: One JSON object per log line, for a platform that indexes fields. Off in
    #: development, where the reader is a person looking at a terminal.
    log_json: bool = False

    database_url: str = "sqlite:///./attendance.db"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    sql_echo: bool = False

    # Unset in development means "sign with a throwaway": tokens stop working when
    # the process restarts, which is inconvenient. Unset in production with a known
    # default means anyone reading this file can mint one, which is fatal.
    jwt_secret: str = Field(default_factory=_generated)
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

    #: Largest accepted request body. Enrolment posts base64 photographs, so the
    #: ceiling has to clear a handful of them — but not a video file.
    max_request_bytes: int = 12 * 1024 * 1024

    #: Browser origins allowed to call the API, comma separated. "*" keeps the
    #: phase-1 behaviour (any origin), which is only tolerable because every
    #: endpoint authenticates with a bearer token rather than a cookie — nothing
    #: here rides on ambient browser credentials.
    cors_allow_origins: str = "*"

    # When true (set on the hosted demo), seed demo data on startup if absent.
    seed_on_start: bool = False

    # Admin console credentials (override via env in production).
    admin_username: str = "admin"
    admin_password: str = Field(default_factory=_generated)

    # If true, a student may only use ONE device (hard lock at login). Default
    # false: biometric verification prevents proxy attendance, so we relax the
    # login lock and instead bind the ENROLMENT device (re-enrol needs a grant).
    enforce_login_device: bool = False

    # A student who has never enrolled just enrols, on the spot, like the campus
    # app they already use. What is protected is CHANGE: once an ID is tied to a
    # face, re-enrolling it (or adding a modality from another device) needs an
    # admin one-time code. Set true to require a code for the first enrolment too.
    enroll_requires_grant: bool = False

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in ("development", "production", "test"):
            raise ValueError("ENVIRONMENT must be development, production or test")
        return cleaned

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origins(self) -> list[str]:
        """`cors_allow_origins` as a list; "*" stays a single wildcard entry."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()] or ["*"]

    def startup_problems(self, env: dict[str, str]) -> list[str]:
        """Configuration that will not work, named plainly and at boot.

        Checked against the raw environment rather than the parsed values,
        because a generated secret and a configured one are indistinguishable
        once both are just strings on this object. Every one of these fails at
        the worst possible moment otherwise: an unset signing secret means every
        check-in is refused, and nobody finds out until a lecture hall is full.
        """
        problems = []
        if not env.get("JWT_SECRET"):
            problems.append("JWT_SECRET is unset: every student is signed out on each restart.")
        elif len(env["JWT_SECRET"]) < 32:
            problems.append("JWT_SECRET is shorter than 32 characters.")
        if not env.get("ADMIN_PASSWORD"):
            problems.append("ADMIN_PASSWORD is unset: the console password changes on every restart.")
        if not self.biometric_signing_secret:
            problems.append("BIOMETRIC_SIGNING_SECRET is unset: verify verdicts cannot be "
                            "authenticated, so no check-in can ever succeed.")
        if not self.biometric_api_key:
            problems.append("BIOMETRIC_API_KEY is unset: the biometric service will refuse us.")
        if not self.biometric_verify_tls:
            problems.append("BIOMETRIC_VERIFY_TLS is off: biometric traffic is not authenticated.")
        if self.cors_allow_origins.strip() == "*" and self.is_production:
            problems.append("CORS_ALLOW_ORIGINS is '*': any website can call this API from a browser.")
        return problems


settings = Settings()
