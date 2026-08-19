"""The two bundled pages must agree with the API they call.

They are single HTML files with inline script, so nothing type-checks them
against the backend. When the error envelope changed shape, both silently fell
back to `r.statusText` — a student outside the geofence saw "Bad Request"
instead of the sentence telling them to move closer. These read the shipped
pages as text and assert the handful of contracts that would fail silently.
"""
from __future__ import annotations

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
PAGES = {
    "pwa": (STATIC / "pwa" / "index.html").read_text(encoding="utf-8"),
    "admin": (STATIC / "admin.html").read_text(encoding="utf-8"),
}


@pytest.fixture(params=sorted(PAGES))
def page(request) -> tuple[str, str]:
    return request.param, PAGES[request.param]


def test_both_pages_read_the_error_envelope(page):
    """`{"error":{message}}` is what the server sends now."""
    name, html = page
    assert "b.error" in html, f"{name} does not read the error envelope"
    assert "e.message" in html, f"{name} does not use the envelope message"


def test_both_pages_still_read_the_old_shape(page):
    """A cached copy of the page must keep working against the new server."""
    name, html = page
    assert "b.detail" in html, f"{name} dropped the fallback"


def test_the_request_id_is_kept_for_support(page):
    """It is what turns "it says something went wrong" into one grep."""
    name, html = page
    assert "request_id" in html, f"{name} discards the request id"
