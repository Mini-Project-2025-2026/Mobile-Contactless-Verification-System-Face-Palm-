"""Rate limiting and sign-in lockout.

Two of this system's credentials are weak on purpose. A programme password is
shared by a whole cohort and read out in a lecture hall; the admin console has
one username. Neither survives an attacker who is allowed to guess as fast as
the network permits, and no amount of biometric verification downstream helps if
someone can simply sign in as a student and then wait for a grant.

So guessing is made slow: a sliding window of failures per (identity, client),
then a lockout. Counting failures rather than requests means an honest student
who mistypes twice is unaffected, while a script that walks the keyspace stops
after a handful of tries.

State is per process and in memory. That is the honest fit for a single web
dyno; with several, each holds its own window and the effective limit multiplies
by the worker count. Documented rather than hidden — the alternative is a Redis
dependency this deployment does not have.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Decision:
    """The verdict for one attempt."""
    allowed: bool
    retry_after_s: int = 0
    remaining: int = 0


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class SlidingWindow:
    """Fixed number of events per rolling window, with an optional lockout.

    `check` is read-only — it reports whether a key is currently locked out.
    `record` adds an event and returns the decision for the *next* attempt. A
    login route calls `check` before doing work and `record` only on failure, so
    a correct password never counts against anyone.
    """

    def __init__(self, limit: int, window_s: float, lockout_s: float = 0.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> Decision:
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return Decision(True, remaining=self.limit)
            if bucket.locked_until > now:
                return Decision(False, retry_after_s=int(bucket.locked_until - now) + 1)
            self._trim(bucket, now)
            if len(bucket.hits) >= self.limit:
                return Decision(False, retry_after_s=self._retry_after(bucket, now))
            return Decision(True, remaining=self.limit - len(bucket.hits))

    def record(self, key: str, *, now: float | None = None) -> Decision:
        """Count one event against `key` and report what happens next."""
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            self._trim(bucket, now)
            bucket.hits.append(now)
            if len(bucket.hits) >= self.limit and self.lockout_s:
                bucket.locked_until = now + self.lockout_s
                return Decision(False, retry_after_s=int(self.lockout_s) + 1)
            if len(bucket.hits) >= self.limit:
                return Decision(False, retry_after_s=self._retry_after(bucket, now))
            return Decision(True, remaining=self.limit - len(bucket.hits))

    def clear(self, key: str) -> None:
        """Forget a key's history — what a successful sign-in earns."""
        with self._lock:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        """Drop all state (tests, and an operator unlocking everyone)."""
        with self._lock:
            self._buckets.clear()

    def sweep(self, *, now: float | None = None) -> int:
        """Discard buckets nothing is using, so an idle process does not grow.

        Without this the dictionary keeps one entry per student id ever seen —
        small, but unbounded, and unbounded is what eventually pages someone.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            stale = [
                key for key, bucket in self._buckets.items()
                if bucket.locked_until <= now
                and not [h for h in bucket.hits if now - h < self.window_s]
            ]
            for key in stale:
                del self._buckets[key]
            return len(stale)

    def _trim(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_s
        bucket.hits = [h for h in bucket.hits if h > cutoff]

    def _retry_after(self, bucket: _Bucket, now: float) -> int:
        oldest = min(bucket.hits, default=now)
        return max(1, int(self.window_s - (now - oldest)) + 1)


def client_key(*parts: str) -> str:
    """A limiter key from its parts, with empty parts kept distinguishable."""
    return "|".join(p or "-" for p in parts)
