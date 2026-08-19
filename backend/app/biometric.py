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

The transport (pooling, retries, idempotency) lives in `bioclient`; this module
is about what we ask and how the answer is read.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from .bioclient import RequestFailed, new_idempotency_key, request
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
    #: Set when the batch was queued instead of run inline (see `enroll_users_bulk`).
    job_id: str = ""
    queued: bool = False


@dataclass(frozen=True)
class VerifyResult:
    success: bool
    user_id: str
    score: float
    signature_valid: bool
    nonce: str
    raw: dict


@dataclass(frozen=True)
class ServiceHealth:
    ok: bool
    version: str = ""
    active_liveness: bool = False
    detail: str = ""


@dataclass(frozen=True)
class TenantConfig:
    """The thresholds this tenant is actually judged against (GET /v1/config).

    The service decides the verdict; we were separately deciding whether to
    accept it, using a locally configured floor. Two systems making one decision
    with no way to tell whether they agree — the service's own documentation
    calls that out. Reading the real numbers is what lets us say so.
    """
    match_threshold: float
    identify_margin: float
    dupe_threshold: float
    samples_per_user: int
    active_liveness: bool
    palm_enabled: bool
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RosterEntry:
    user_id: str
    modalities: tuple[str, ...]


def _json(method: str, path: str, **kw) -> dict:
    """One call, returning the decoded body, with failures named consistently."""
    try:
        response = request(method, path, **kw)
        response.raise_for_status()
        return response.json()
    except RequestFailed as exc:
        raise BiometricError(str(exc)) from exc
    except Exception as exc:  # non-2xx, undecodable body
        raise BiometricError(f"{method} {path} failed: {exc}") from exc


# --- service state --------------------------------------------------------
def service_health() -> ServiceHealth:
    """Is the biometric service up (GET /v1/health).

    Used by our own readiness probe. Deliberately not `get_challenge`: asking for
    a liveness challenge mints a single-use token and bills a call, and a probe
    that runs every thirty seconds should cost the tenant nothing.
    """
    try:
        data = _json("GET", "/v1/health", timeout=5.0)
    except BiometricError as exc:
        return ServiceHealth(ok=False, detail=str(exc)[:200])
    return ServiceHealth(
        ok=bool(data.get("success")),
        version=str(data.get("version", "")),
        active_liveness=bool(data.get("active_liveness", False)),
    )


def tenant_config() -> TenantConfig:
    """The thresholds and capabilities configured for our tenant (GET /v1/config)."""
    data = _json("GET", "/v1/config", timeout=10.0)
    return TenantConfig(
        match_threshold=float(data.get("match_threshold") or 0.0),
        identify_margin=float(data.get("identify_margin") or 0.0),
        dupe_threshold=float(data.get("dupe_threshold") or 0.0),
        samples_per_user=int(data.get("samples_per_user") or 0),
        active_liveness=bool(data.get("active_liveness")),
        palm_enabled=bool(data.get("palm_enabled")),
        raw=data,
    )


def get_challenge() -> Challenge:
    """Ask for a head-turn liveness challenge token."""
    data = _json("GET", "/v1/challenge")
    return Challenge(
        active=bool(data.get("active", False)),
        token=str(data.get("token", "")),
        instruction=str(data.get("instruction", "Hold still")),
    )


# --- enrolment ------------------------------------------------------------
def enroll_user(user_id: str, images: list[str], *, source: str = "auto",
                idempotency_key: str = "") -> EnrollResult:
    """Managed enrolment: register a student's face/palm from one or more images.
    The service auto-detects the modality. Requires an admin-role key."""
    if not images:
        raise BiometricError("enroll_user requires at least one image")
    data = _json(
        "POST", "/v1/enroll",
        json={"user_id": user_id, "images": images, "source": source},
        idempotency_key=idempotency_key or new_idempotency_key("enroll", user_id),
    )

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


def _bulk_results(data: dict) -> tuple[BulkPersonResult, ...]:
    return tuple(
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


def enroll_users_bulk(people: list[tuple[str, list[str]]], *, dedupe: bool = True,
                      timeout: float | None = None,
                      queue: bool = False, idempotency_key: str = "") -> BulkEnrollResult:
    """Enrol many people in one call (POST /v1/enroll/bulk).

    `people` is [(user_id, [base64 image, ...]), ...]. `dedupe` makes the service
    refuse a person whose biometric already belongs to a different name, which is
    exactly the check an import of a whole department should not skip.

    With `queue=True` the service accepts the batch and answers 202 with a job
    id to poll (`job_status`), instead of holding the socket open while it works.
    A cohort import should not be shaped by how long a gateway waits.
    """
    if not people:
        raise BiometricError("enroll_users_bulk requires at least one person")
    body = {
        "people": [{"user_id": uid, "images": images} for uid, images in people],
        "dedupe": dedupe,
    }
    if queue:
        body["async"] = True
    data = _json(
        "POST", "/v1/enroll/bulk", json=body,
        timeout=timeout or settings.biometric_bulk_timeout_s,
        idempotency_key=idempotency_key or new_idempotency_key(
            "bulk", *(uid for uid, _ in people[:3]), str(len(people))),
    )
    return BulkEnrollResult(
        people=int(data.get("people", len(people)) or 0),
        enrolled=int(data.get("enrolled", 0) or 0),
        results=_bulk_results(data),
        raw=data,
        job_id=str(data.get("job_id") or ""),
        queued=bool(data.get("queued")),
    )


def job_status(job_id: str) -> dict:
    """Progress and results of a queued batch (GET /v1/jobs/{id})."""
    return _json("GET", f"/v1/jobs/{job_id}", timeout=15.0)


# --- verification ---------------------------------------------------------
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


def _capture_body(frames: list[str] | None, token: str, image: str | None) -> dict:
    body: dict = {}
    if frames:
        body["frames"] = frames
        if token:
            body["token"] = token
    elif image:
        body["image"] = image
    else:
        raise BiometricError("a capture requires frames or an image")
    return body


def _read_verdict(data: dict, *, expect_token: str) -> VerifyResult:
    sig = data.get("signature") or {}
    return VerifyResult(
        success=bool(data.get("success", False)),
        user_id=str(data.get("user_id", "")),
        score=float(data.get("score", 0.0) or 0.0),
        signature_valid=verify_signature(data, expect_token=expect_token),
        nonce=str(sig.get("nonce", "")),
        raw=data,
    )


def verify_student(student_id: str, *, frames: list[str] | None = None, token: str = "",
                   image: str | None = None) -> VerifyResult:
    """1:1 verify a claimed student.

    Prefer `frames` + `token` (active liveness). Falls back to a single `image`
    (weaker — no liveness) when the caller cannot capture a head-turn burst.
    """
    body = {"user_id": student_id, **_capture_body(frames, token, image)}
    data = _json("POST", "/v1/verify", json=body)
    # Face check-ins send a liveness token, so we require the verdict to be
    # bound to it. A palm check-in has no token and falls back to the plain
    # signature plus the replay-nonce the caller records.
    return _read_verdict(data, expect_token=token if frames else "")


def identify_person(*, frames: list[str] | None = None, token: str = "",
                    image: str | None = None) -> VerifyResult:
    """1:N — ask the service WHO this is, with no claimed identity (POST /v1/identify).

    The verdict is signed exactly as a 1:1 verify is, and carries the winning
    `user_id`. The service applies its own `identify_margin` (the winner must beat
    the runner-up by it) before answering, which is the guard that stops a lookalike
    being returned as a confident match.
    """
    data = _json("POST", "/v1/identify", json=_capture_body(frames, token, image))
    return _read_verdict(data, expect_token=token if frames else "")


# --- roster ---------------------------------------------------------------
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
        response = request("GET", f"/v1/users/{user_id}")
    except RequestFailed as exc:
        raise BiometricError(str(exc)) from exc
    if response.status_code == 404:
        return None
    try:
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise BiometricError(f"user status request failed: {exc}") from exc
    if not data.get("success"):
        return None
    return UserStatus(
        enrolled=bool(data.get("enrolled")),
        modalities=tuple(data.get("modalities") or ()),
        samples=dict(data.get("samples") or {}),
    )


def list_roster(*, page: int = 500) -> dict[str, tuple[str, ...]]:
    """Every enrolled user_id mapped to the modalities held for them.

    `/v1/users` returns a `modalities` map alongside the page of ids. We used to
    discard it and then ask per user, which is one extra round trip per student
    for something already on the wire.
    """
    roster: dict[str, tuple[str, ...]] = {}
    offset = 0
    while True:
        data = _json("GET", "/v1/users", params={"limit": page, "offset": offset})
        batch = [str(u) for u in (data.get("users") or [])]
        mods = data.get("modalities") or {}
        for user_id in batch:
            roster[user_id] = tuple(mods.get(user_id) or ())
        offset += len(batch)
        if not batch or offset >= int(data.get("total", 0) or 0):
            return roster


def list_enrolled_user_ids(*, page: int = 500) -> set[str]:
    """Every user_id the tenant currently holds a template for."""
    return set(list_roster(page=page))
