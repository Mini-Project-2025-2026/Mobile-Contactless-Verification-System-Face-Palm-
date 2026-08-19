"""What the verification tenant is configured to do, and what we add on top.

Two systems were making one decision. The biometric service judges a capture
against its own `match_threshold` and returns success or not; this app then
applied `MIN_VERIFY_SCORE` — a second, independently configured floor — to the
same score. Nobody could see both numbers at once, so nobody could say whether
they agreed. The service's own `/v1/config` docstring names this exact problem.

They are not redundant. The service's threshold decides *match*; ours is an
extra margin a campus may want on top ("a match, but a confident one"). What was
missing is the relationship:

  * ours **below** the service's is dead configuration. Every verdict that
    reaches us already cleared the higher bar, so the local floor never rejects
    anything — while looking, in the console, like a working safety setting.
  * ours **above** it is a real additional margin, and worth stating.

This module reads the tenant's configuration, caches it, survives an outage by
keeping the last known answer, and answers three questions the rest of the app
kept guessing at: how confident is confident enough, is palm available at all,
and does this tenant use active liveness.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import biometric
from .config import settings

log = logging.getLogger("attendance.policy")

#: Tenant configuration changes when an administrator changes it, which is rare.
#: Five minutes is short enough that a threshold change takes effect within one
#: class, and long enough that check-in never waits on it.
_TTL_S = 300.0

#: How long to wait before asking again after a failed read. Without this, a
#: service that is down costs every single check-in a full round trip plus its
#: retries — turning "the config is unreadable" into "the app is slow", for a
#: value we already know we will fall back on.
_RETRY_AFTER_FAILURE_S = 30.0

_cached: tuple[float, biometric.TenantConfig] | None = None
_failed_at: float = 0.0
_lock = threading.Lock()


@dataclass(frozen=True)
class Effective:
    """The floor a capture must clear here, and where the number came from."""
    min_score: float
    service_threshold: float
    local_floor: float
    #: True when the local floor is below the service's and therefore rejects nothing.
    local_floor_is_inert: bool
    source: str  # "service" | "local" | "local-only"


def tenant(*, refresh: bool = False) -> biometric.TenantConfig | None:
    """The tenant's configuration, cached. None when it has never been readable.

    An outage returns the last known answer rather than nothing: thresholds that
    were right five minutes ago are a far better basis for a decision than a
    fallback invented on the spot.
    """
    global _cached, _failed_at
    moment = time.monotonic()
    with _lock:
        if _cached is not None and not refresh and moment - _cached[0] < _TTL_S:
            return _cached[1]
        stale = _cached[1] if _cached else None
        if _failed_at and moment - _failed_at < _RETRY_AFTER_FAILURE_S:
            return stale  # asked recently, it was down; do not pay for it again

    try:
        fresh = biometric.tenant_config()
    except biometric.BiometricError as exc:
        with _lock:
            _failed_at = moment
        if stale is None:
            log.warning("tenant config unreadable (%s); falling back to local settings", exc)
        else:
            log.warning("tenant config refresh failed (%s); keeping the last known copy", exc)
        return stale

    with _lock:
        _cached = (moment, fresh)
        _failed_at = 0.0
    return fresh


def reset_cache() -> None:
    """Drop the cached configuration (tests, and after an admin refresh)."""
    global _cached, _failed_at
    with _lock:
        _cached = None
        _failed_at = 0.0


def effective_score_floor() -> Effective:
    """The score a capture must reach, and an honest account of why."""
    local = settings.min_verify_score
    config = tenant()
    if config is None or config.match_threshold <= 0:
        return Effective(min_score=local, service_threshold=0.0, local_floor=local,
                         local_floor_is_inert=False, source="local-only")
    inert = local <= config.match_threshold
    return Effective(
        min_score=max(local, config.match_threshold),
        service_threshold=config.match_threshold,
        local_floor=local,
        local_floor_is_inert=inert,
        source="service" if inert else "local",
    )


def palm_available() -> bool:
    """Does this tenant hold palm templates at all.

    Offering palm enrolment to a tenant with palm switched off sends a student
    through a capture that can only fail, and the failure it produces reads like
    bad lighting. Better not to offer it.
    """
    config = tenant()
    return True if config is None else config.palm_enabled


def liveness_active() -> bool:
    """Does this tenant run the active head-turn challenge."""
    config = tenant()
    return True if config is None else config.active_liveness


def samples_per_user() -> int:
    """How many samples the service keeps per person (0 = unknown)."""
    config = tenant()
    return config.samples_per_user if config else 0


def describe() -> dict:
    """A summary for the readiness probe and the admin console."""
    config = tenant()
    floor = effective_score_floor()
    return {
        "readable": config is not None,
        "min_score": round(floor.min_score, 4),
        "score_source": floor.source,
        "service_match_threshold": round(floor.service_threshold, 4),
        "local_floor": round(floor.local_floor, 4),
        "local_floor_is_inert": floor.local_floor_is_inert,
        "identify_margin": round(config.identify_margin, 4) if config else None,
        "dupe_threshold": round(config.dupe_threshold, 4) if config else None,
        "samples_per_user": config.samples_per_user if config else None,
        "palm_enabled": palm_available(),
        "active_liveness": liveness_active(),
    }
