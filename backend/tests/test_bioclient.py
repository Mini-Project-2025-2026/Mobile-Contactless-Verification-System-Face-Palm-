"""How calls to the biometric service are made: pooling, retries, idempotency.

The service is a network hop away from a student holding up a phone in a
doorway. What matters here is that a dropped packet does not become "enrol
again", and that recovering from one can never enrol anybody twice.
"""
from __future__ import annotations

import httpx
import pytest

from app import bioclient, biometric


@pytest.fixture(autouse=True)
def clean_client():
    bioclient.close_client()
    yield
    bioclient.close_client()


def _mount(handler, *, retries: int | None = None):
    """Point the shared client at an in-process transport."""
    bioclient.close_client()
    bioclient._client = httpx.Client(
        base_url="https://biometric.test",
        transport=httpx.MockTransport(handler),
    )
    return bioclient._client


def test_the_connection_is_pooled_not_rebuilt_per_call():
    """One client, reused. A fresh TLS handshake per check-in is the whole class
    paying for one at the start of a lecture."""
    first = bioclient.get_client()
    assert bioclient.get_client() is first


def test_a_transport_failure_is_retried(monkeypatch):
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) < 3:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, json={"success": True, "status": "ok"})

    _mount(handler)
    response = bioclient.request("GET", "/v1/health", retries=2)
    assert response.status_code == 200
    assert len(attempts) == 3


def test_retries_give_up_and_say_so(monkeypatch):
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _mount(handler)
    with pytest.raises(bioclient.RequestFailed):
        bioclient.request("GET", "/v1/health", retries=1)


def test_a_write_without_an_idempotency_key_is_never_retried(monkeypatch):
    """A retried enrolment with no key is a second enrolment. Better to fail."""
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("down", request=request)

    _mount(handler)
    with pytest.raises(bioclient.RequestFailed):
        bioclient.request("POST", "/v1/enroll", json={"user_id": "S1"}, retries=3)
    assert len(attempts) == 1


def test_a_write_with_an_idempotency_key_is_retried_and_carries_it(monkeypatch):
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)
    seen_keys = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers.get(bioclient.IDEMPOTENCY_HEADER))
        if len(seen_keys) < 2:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"success": True})

    _mount(handler)
    bioclient.request("POST", "/v1/enroll", json={"user_id": "S1"},
                      idempotency_key="enroll-S1", retries=2)
    # The same key on the retry is what lets the service replay its first answer
    # instead of enrolling the student a second time.
    assert seen_keys == ["enroll-S1", "enroll-S1"]


def test_a_refusal_is_not_retried(monkeypatch):
    """A 409 means the same thing however many times it is asked."""
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(409, json={"success": False, "code": "duplicate"})

    _mount(handler)
    response = bioclient.request("POST", "/v1/enroll", json={},
                                 idempotency_key="k", retries=3)
    assert response.status_code == 409
    assert len(attempts) == 1


def test_a_gateway_error_is_retried(monkeypatch):
    monkeypatch.setattr(bioclient.time, "sleep", lambda _: None)
    codes = [503, 503, 200]
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        code = codes[len(attempts)]
        attempts.append(code)
        return httpx.Response(code, json={"success": code == 200})

    _mount(handler)
    assert bioclient.request("GET", "/v1/health", retries=2).status_code == 200
    assert attempts == [503, 503, 200]


def test_enrol_sends_an_idempotency_key_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get(bioclient.IDEMPOTENCY_HEADER)
        return httpx.Response(200, json={"success": True, "enrolled": 1, "of": 1,
                                         "results": [{"samples": 3}]})

    _mount(handler)
    result = biometric.enroll_user("20512345", ["img"])
    assert result.enrolled == 1
    assert seen["key"] and "20512345" in seen["key"]


def test_the_roster_brings_back_modalities_in_one_pass():
    """`/v1/users` already returns them; asking again per student was a wasted hop."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("offset"))
        return httpx.Response(200, json={
            "success": True, "users": ["S1", "S2"],
            "modalities": {"S1": ["face"], "S2": ["face", "palm"]},
            "total": 2, "offset": 0, "limit": 500})

    _mount(handler)
    roster = biometric.list_roster()
    assert roster == {"S1": ("face",), "S2": ("face", "palm")}
    assert len(calls) == 1


def test_tenant_config_reads_the_thresholds_the_service_judges_by():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/config"
        return httpx.Response(200, json={
            "success": True, "match_threshold": 0.62, "identify_margin": 0.08,
            "dupe_threshold": 0.75, "samples_per_user": 5,
            "active_liveness": True, "palm_enabled": True})

    _mount(handler)
    cfg = biometric.tenant_config()
    assert cfg.match_threshold == 0.62
    assert cfg.identify_margin == 0.08
    assert cfg.palm_enabled is True


def test_identify_asks_who_this_is_with_no_claimed_identity():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        sent.update(_json.loads(request.content))
        return httpx.Response(200, json={"success": True, "user_id": "20512345",
                                         "score": 0.81, "signature": {"nonce": "n1"}})

    _mount(handler)
    result = biometric.identify_person(frames=["a", "b"], token="t1")
    assert "user_id" not in sent            # that is the whole point of 1:N
    assert sent["token"] == "t1"
    assert result.user_id == "20512345"


def test_a_queued_bulk_import_comes_back_as_a_job():
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content)
        assert body["async"] is True
        return httpx.Response(202, json={"success": True, "queued": True,
                                         "job_id": "job-7", "people": 40})

    _mount(handler)
    out = biometric.enroll_users_bulk([("S1", ["i"])] * 40, queue=True)
    assert out.queued is True
    assert out.job_id == "job-7"
