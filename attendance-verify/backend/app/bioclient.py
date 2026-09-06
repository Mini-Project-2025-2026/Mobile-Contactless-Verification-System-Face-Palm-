"""The HTTP plumbing under every call to the Biometric Verify service.

Split out of `biometric.py` so that module can be about *what* we ask the
service and this one about *how the call is made*. Three things were wrong with
how it was made:

* **A new connection per call.** `httpx.Client(...)` was constructed, used once
  and thrown away for every challenge, verify and enrolment — so every check-in
  paid a fresh TCP handshake and a fresh TLS handshake to the same host. At the
  start of a lecture that is the whole class paying it at once.
* **No retry.** One dropped packet turned into "biometric_unavailable" for a
  student standing in a doorway holding up a phone. A connect failure that never
  reached the service is exactly the case that is safe to try again.
* **No idempotency.** The service supports an `Idempotency-Key` header and
  replays the first response for a repeat within 24 hours. Without it, a retried
  enrolment is a second enrolment, and the retry we just added would be unsafe.

The pooled client is created lazily and closed on shutdown.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

import httpx

from .config import settings

log = logging.getLogger("attendance.biometric")

#: Header the service reads to replay a first response instead of re-running work.
IDEMPOTENCY_HEADER = "Idempotency-Key"

_client: httpx.Client | None = None
_lock = threading.Lock()


def new_idempotency_key(*parts: str) -> str:
    """A key for one logical write, stable across retries of that write."""
    return "-".join(p for p in parts if p) or uuid.uuid4().hex


def get_client() -> httpx.Client:
    """The shared, pooled client. Built on first use, reused thereafter."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                base_url=settings.biometric_base_url.rstrip("/"),
                headers={"X-API-Key": settings.biometric_api_key},
                verify=settings.biometric_verify_tls,
                timeout=httpx.Timeout(settings.biometric_timeout_s, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10,
                                    keepalive_expiry=30.0),
            )
    return _client


def close_client() -> None:
    """Release the pool (application shutdown, and tests that swap the base URL)."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


class RequestFailed(RuntimeError):
    """The call did not produce a usable response, after any retries."""


def request(method: str, path: str, *, json: dict | None = None,
            params: dict | None = None, timeout: float | None = None,
            idempotency_key: str = "", retries: int | None = None) -> httpx.Response:
    """Make one call, retrying only what is safe to retry.

    Retried: transport failures (the request never got a verdict) and 502/503/504
    from a gateway in front of the service. Not retried: any other status — a 409
    duplicate or a 403 scope error means the same thing however many times it is
    asked, and repeating it just delays the answer a student is waiting for.

    A POST is only retried when it carries an idempotency key, so a retry cannot
    become a second enrolment.
    """
    attempts = (settings.biometric_retries if retries is None else retries) + 1
    safe = method.upper() in ("GET", "HEAD") or bool(idempotency_key)
    headers = {IDEMPOTENCY_HEADER: idempotency_key} if idempotency_key else None
    client = get_client()
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            response = client.request(method, path, json=json, params=params,
                                      headers=headers,
                                      timeout=timeout or settings.biometric_timeout_s)
        except httpx.TransportError as exc:
            last = exc
            if not safe or attempt == attempts - 1:
                break
        else:
            if response.status_code < 500 or not safe or attempt == attempts - 1:
                return response
            last = httpx.HTTPStatusError(
                f"{response.status_code} from upstream", request=response.request,
                response=response)
        delay = 0.25 * (2 ** attempt)
        log.warning("biometric %s %s failed (%s); retrying in %.2fs",
                    method, path, type(last).__name__, delay)
        time.sleep(delay)

    raise RequestFailed(f"{method} {path}: {last}") from last
