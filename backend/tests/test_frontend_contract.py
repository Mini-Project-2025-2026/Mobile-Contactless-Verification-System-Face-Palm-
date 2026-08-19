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
    "kiosk": (STATIC / "kiosk.html").read_text(encoding="utf-8"),
}


@pytest.fixture(params=sorted(PAGES))
def page(request) -> tuple[str, str]:
    return request.param, PAGES[request.param]


def test_both_pages_read_the_error_envelope(page):
    """`{"error":{message}}` is what the server sends now."""
    name, html = page
    assert "b.error" in html, f"{name} does not read the error envelope"
    assert "e.message" in html, f"{name} does not use the envelope message"


def test_the_older_pages_still_read_the_old_shape(page):
    """A cached copy of a shipped page must keep working against the new server.

    The kiosk page is new, so it has no cached copies anywhere and no old shape
    to be compatible with.
    """
    name, html = page
    if name == "kiosk":
        pytest.skip("shipped after the envelope changed")
    assert "b.detail" in html, f"{name} dropped the fallback"


def test_the_request_id_is_kept_for_support(page):
    """It is what turns "it says something went wrong" into one grep."""
    name, html = page
    assert "request_id" in html, f"{name} discards the request id"


# --- the student app must actually reach the new surface ---------------------
def test_the_student_app_has_a_consent_screen():
    """Capturing a face with no way to read or withdraw consent is the gap."""
    for path in ("/api/consent/statement", "/api/consent", "/api/consent/withdraw",
                 "/api/consent/my-data"):
        assert path in PAGES["pwa"], f"pwa never calls {path}"


def test_every_screen_the_app_can_show_is_in_the_router():
    """A screen missing from go()'s list is markup nothing can ever display."""
    import re
    html = PAGES["pwa"]
    declared = set(re.findall(r'id="s-([a-z]+)"', html))
    routed = re.search(r'\[((?:"[a-z]+",?)+)\]\.forEach\(x=>\$\("s-"\+x\)', html)
    assert routed, "could not find the screen router"
    assert declared == set(re.findall(r'"([a-z]+)"', routed.group(1)))


def test_palm_is_not_offered_where_the_tenant_has_it_off():
    assert "palm_available" in PAGES["pwa"]


def test_a_consent_refusal_sends_the_student_somewhere_useful():
    """`consent_required` must route to the screen that fixes it."""
    assert "consent_required" in PAGES["pwa"]


# --- the kiosk device ---------------------------------------------------------
def test_the_kiosk_page_is_served():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/kiosk")
    assert response.status_code == 200
    assert "kiosk" in response.text.lower()


def test_the_kiosk_page_drives_the_kiosk_endpoints():
    html = PAGES["kiosk"]
    assert "/api/kiosk/challenge" in html
    assert "/api/kiosk/verify" in html


def test_the_kiosk_stops_when_its_code_expires():
    """A class ends. The device must say so rather than retrying forever."""
    html = PAGES["kiosk"]
    assert "clearInterval" in html
    assert "expired" in html


def test_the_kiosk_sends_its_own_position():
    """The geofence claim comes from the device, which is the point of it."""
    assert "navigator.geolocation" in PAGES["kiosk"]
    assert "gps" in PAGES["kiosk"]


def test_the_kiosk_holds_no_student_session():
    """It is not a login. It must never touch the student token or its routes."""
    html = PAGES["kiosk"]
    assert "/api/auth/login" not in html
    assert "/api/enroll" not in html


# --- the admin console must reach what the backend grew ----------------------
def test_the_console_drives_the_new_admin_surface():
    html = PAGES["admin"]
    for path in ("/api/admin/verification", "/api/admin/templates",
                 "/api/admin/students/erase-biometrics", "/api/admin/audit"):
        assert path in html, f"admin console never calls {path}"


def test_the_console_can_hand_a_class_to_a_kiosk():
    html = PAGES["admin"]
    assert "/kiosk" in html
    assert "openKiosk" in html


def test_every_console_tab_has_a_view_and_a_route():
    """A tab with no view, or a view with no tab, is dead UI."""
    import re
    html = PAGES["admin"]
    tabs = set(re.findall(r"data-t=\"([a-z]+)\"", html))
    views = set(re.findall(r"id=\"v-([a-z]+)\"", html))
    assert tabs == views, f"tabs {tabs ^ views} have no counterpart"


def test_an_inert_score_floor_is_called_out_in_the_console():
    """A floor that rejects nothing must not look like a working control."""
    assert "local_floor_is_inert" in PAGES["admin"]
