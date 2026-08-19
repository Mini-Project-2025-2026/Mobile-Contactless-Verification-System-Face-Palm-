"""Client for the Biometric Verify API (../contactless-fingerprint-system).

This is the integration core. It does three things:
  1. fetch an active-liveness challenge  (GET  /v1/challenge)
  2. run a 1:1 verify of a claimed student (POST /v1/verify)
  3. validate the HMAC signature on the verdict so a tampered or replayed
     response can never write a false "present".

The signature reconstruction is byte-for-byte identical to the Biometric
Verify SDK's `verify_signature` and the server's `_sign` (face_service/v1.py):

    body   = json.dumps({success,match,user_id,score,best_score},
                        sort_keys=True, separators=(",", ":"))
    msg    = f"{ts}.{nonce}.{body}"
    hmac   = HMAC-SHA256(signing_secret, msg).hexdigest()
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

import httpx

from .config import settings

_SIGNED_KEYS = ("success", "match", "user_id", "score", "best_score")


class BiometricError(RuntimeError):
    """Network / protocol failure talking to the biometric service."""


@dataclass(frozen=True)
class Challenge:
    active: bool
    token: str
    instruction: str


@dataclass(frozen=True)
class EnrollResult:
    enrolled: int
    of: int
    samples: int
    raw: dict
    #: The service refused because this face/palm already belongs to someone else.
    #: One biometric, one identity: this is never a retry-in-better-light failure.
    duplicate: bool = False
    conflict_user_id: str = ""


@dataclass(frozen=True)
class BulkPersonResult:
    user_id: str
    success: bool
    enrolled: int
    modalities: tuple[str, ...]
    message: str
    duplicate: bool = False
    conflict_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BulkEnrollResult:
    people: int
    enrolled: int
    results: tuple[BulkPersonResult, ...]
    raw: dict


@dataclass(frozen=True)
class VerifyResult:
    success: bool
    user_id: str
    score: float
    signature_valid: bool
    nonce: str
    raw: dict


def _client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(
        base_url=settings.biometric_base_url.rstrip("/"),
        headers={"X-API-Key": settings.biometric_api_key},
        verify=settings.biometric_verify_tls,
        timeout=timeout,
    )


def get_challenge() -> Challenge:
    """Ask for a head-turn liveness challenge token."""
    try:
        with _client() as c:
            r = c.get("/v1/challenge")
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:  # network, TLS, non-2xx
        raise BiometricError(f"challenge request failed: {exc}") from exc
    return Challenge(
        active=bool(data.get("active", False)),
        token=str(data.get("token", "")),
        instruction=str(data.get("instruction", "Hold still")),
    )


def enroll_user(user_id: str, images: list[str], *, source: str = "auto") -> EnrollResult:
    """Managed enrolment: register a student's face/palm from one or more images.
    The service auto-detects the modality. Requires an admin-role key."""
    if not images:
        raise BiometricError("enroll_user requires at least one image")
    try:
        with _client() as c:
            r = c.post("/v1/enroll", json={"user_id": user_id, "images": images, "source": source})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise BiometricError(f"enroll request failed: {exc}") from exc

    results = data.get("results", []) or []
    # Highest per-image sample index reflects how many anchors are now stored.
    samples = max((int(res.get("samples") or 0) for res in results), default=0)
    dupe = next((res for res in results if res.get("code") == "duplicate"), None)
    return EnrollResult(
        enrolled=int(data.get("enrolled", 0) or 0),
        of=int(data.get("of", len(images)) or len(images)),
        samples=samples,
        raw=data,
        duplicate=dupe is not None,
        conflict_user_id=str((dupe or {}).get("conflict_user_id") or ""),
    )


def enroll_users_bulk(people: list[tuple[str, list[str]]], *, dedupe: bool = True,
                      timeout: float = 120.0) -> BulkEnrollResult:
    """Enrol many people in one call (POST /v1/enroll/bulk).

    `people` is [(user_id, [base64 image, ...]), ...]. `dedupe` makes the service
    refuse a person whose biometric already belongs to a different name, which is
    exactly the check an import of a whole department should not skip.
    """
    if not people:
        raise BiometricError("enroll_users_bulk requires at least one person")
    body = {
        "people": [{"user_id": uid, "images": images} for uid, images in people],
        "dedupe": dedupe,
    }
    try:
        with _client(timeout) as c:
            r = c.post("/v1/enroll/bulk", json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise BiometricError(f"bulk enroll request failed: {exc}") from exc

    results = tuple(
        BulkPersonResult(
            user_id=str(res.get("user_id") or ""),
            success=bool(res.get("success")),
            enrolled=int(res.get("enrolled") or 0),
            modalities=tuple(res.get("modalities") or ()),
            message=str(res.get("message") or ""),
            duplicate=res.get("code") == "duplicate" or bool(res.get("conflicts")),
            conflict_user_ids=tuple(
                str(c.get("conflict_user_id")) for c in (res.get("conflicts") or [])
                if c.get("conflict_user_id")
            ),
        )
        for res in (data.get("results") or [])
    )
    return BulkEnrollResult(
        people=int(data.get("people", len(people)) or 0),
        enrolled=int(data.get("enrolled", 0) or 0),
        results=results,
        raw=data,
    )


def verify_signature(payload: dict, secret: str | None = None, *, expect_token: str = "") -> bool:
    """Verify the HMAC signature attached to a verify/compare response.

    With `expect_token` (the liveness token we asked for), also require that this
    verdict answered OUR challenge. A signature alone says the verdict is genuine,
    not that it is ours and current, so a captured response would otherwise stay
    valid for any later check-in.
    """
    secret = secret if secret is not None else settings.biometric_signing_secret
    sig = payload.get("signature")
    if not sig or not secret:
        return False
    body = json.dumps({k: payload.get(k) for k in _SIGNED_KEYS}, sort_keys=True, separators=(",", ":"))
    msg = f"{sig.get('ts')}.{sig.get('nonce')}.{body}".encode()
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(sig.get("hmac", ""))):
        return False
    if not expect_token:
        return True

    # A service that does not bind cannot be trusted to have answered this
    # challenge, and we asked for one, so the absence of a binding is a failure.
    bound = sig.get("bound") or {}
    if bound.get("token") != expect_token:
        return False
    bound_body = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    bind_msg = f"{sig.get('ts')}.{sig.get('nonce')}.{expected}.{bound_body}".encode()
    bind_expected = hmac.new(secret.encode(), bind_msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(bind_expected, str(sig.get("binding", "")))


def verify_student(student_id: str, *, frames: list[str] | None = None, token: str = "", image: str | None = None) -> VerifyResult:
    """1:1 verify a claimed student.

    Prefer `frames` + `token` (active liveness). Falls back to a single `image`
    (weaker — no liveness) when the caller cannot capture a head-turn burst.
    """
    body: dict = {"user_id": student_id}
    if frames:
        body["frames"] = frames
        if token:
            body["token"] = token
    elif image:
        body["image"] = image
    else:
        raise BiometricError("verify_student requires frames or image")

    try:
        with _client() as c:
            r = c.post("/v1/verify", json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise BiometricError(f"verify request failed: {exc}") from exc

    sig = data.get("signature") or {}
    return VerifyResult(
        success=bool(data.get("success", False)),
        user_id=str(data.get("user_id", "")),
        score=float(data.get("score", 0.0) or 0.0),
        # Face check-ins send a liveness token, so we require the verdict to be
        # bound to it. A palm check-in has no token and falls back to the plain
        # signature plus the replay-nonce the caller records.
        signature_valid=verify_signature(data, expect_token=token if frames else ""),
        nonce=str(sig.get("nonce", "")),
        raw=data,
    )


@dataclass(frozen=True)
class UserStatus:
    enrolled: bool
    modalities: tuple[str, ...]
    samples: dict


def user_status(user_id: str) -> UserStatus | None:
    """What the service holds for one person, or None if it cannot say.

    None means "ask another way" (an older service without this endpoint), never
    "not enrolled" - the two must not be confused, or a student with a template
    gets sent back through enrolment.
    """
    try:
        with _client() as c:
            r = c.get(f"/v1/users/{user_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        raise BiometricError(f"user status request failed: {exc}") from exc
    if not data.get("success"):
        return None
    return UserStatus(
        enrolled=bool(data.get("enrolled")),
        modalities=tuple(data.get("modalities") or ()),
        samples=dict(data.get("samples") or {}),
    )


def list_enrolled_user_ids(*, page: int = 500) -> set[str]:
    """Every user_id the tenant currently holds a template for.

    The service exposes no per-user lookup, so we page the roster. Callers are
    expected to cache (see `app.enrolment`), never to call this per request.
    """
    ids: set[str] = set()
    offset = 0
    try:
        with _client() as c:
            while True:
                r = c.get("/v1/users", params={"limit": page, "offset": offset})
                r.raise_for_status()
                data = r.json()
                batch = [str(u) for u in (data.get("users") or [])]
                ids.update(batch)
                offset += len(batch)
                if not batch or offset >= int(data.get("total", 0) or 0):
                    return ids
    except httpx.HTTPError as exc:
        raise BiometricError(f"user list request failed: {exc}") from exc
