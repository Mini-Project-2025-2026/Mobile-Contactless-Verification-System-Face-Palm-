"""Geofence math. Pure functions — no I/O, trivially testable."""
from __future__ import annotations

import math

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two WGS84 points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def within_geofence(lat: float, lng: float, center_lat: float, center_lng: float, radius_m: float) -> tuple[bool, float]:
    """Return (inside, distance_m). `inside` is distance <= radius."""
    d = haversine_m(lat, lng, center_lat, center_lng)
    return d <= radius_m, d
