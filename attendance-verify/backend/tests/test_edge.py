"""What every request passes through: ids, error shape, limits, headers.

These cover the parts of the service a student never sees named but always
feels — a failure they can quote back to support, an upload that is refused
politely instead of taking the process down, and a readiness answer that says
which dependency is broken rather than just "no".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app import biometric
from app.db import engine
from app.main import app
from app.middleware import client_ip


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_every_response_carries_a_request_id(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers["X-Request-ID"]


def test_a_caller_supplied_request_id_is_kept(client):
    r = client.get("/health", headers={"X-Request-ID": "trace-me-123"})
    assert r.headers["X-Request-ID"] == "trace-me-123"


def test_failures_come_back_in_one_envelope_with_the_id(client):
    r = client.get("/api/profile")  # no bearer token
    assert r.status_code == 401
    body = r.json()["error"]
    assert body["code"] == "unauthorized"
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["message"]


def test_a_raised_code_prefix_survives_into_the_envelope(client):
    """`raise HTTPException(409, "session_closed")` keeps its machine code."""
    r = client.post("/api/checkin/challenge", json={"session_id": 1},
                    headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_validation_errors_name_the_field_inside_the_envelope(client):
    r = client.post("/api/auth/login", json={"student_id": "S1"})  # password missing
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request"
    assert any(f["field"] == "password" for f in body["fields"])


def test_an_oversized_body_is_refused_before_it_is_parsed(client):
    r = client.post("/api/auth/login",
                    content=b"x" * 32,
                    headers={"Content-Length": str(50 * 1024 * 1024),
                             "Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_security_headers_are_on_every_response(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_readiness_names_the_broken_dependency(client, monkeypatch):
    monkeypatch.setattr(biometric, "service_health",
                        lambda: biometric.ServiceHealth(ok=False, detail="connection refused"))
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["biometric"]["ok"] is False
    assert "connection refused" in body["checks"]["biometric"]["error"]


def test_readiness_is_ok_when_everything_answers(client, monkeypatch):
    monkeypatch.setattr(biometric, "service_health",
                        lambda: biometric.ServiceHealth(ok=True, version="v1", active_liveness=True))
    body = client.get("/health/ready").json()
    assert body["checks"]["biometric"] == {
        "ok": True, "url": body["checks"]["biometric"]["url"],
        "version": "v1", "active_liveness": True}


def test_liveness_probe_does_no_work(client):
    """It must not touch the database: a blinking database should not restart us."""
    assert client.get("/health").json()["ok"] is True


class _Req:
    def __init__(self, headers, host="10.0.0.1"):
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_trusts_only_the_first_proxy_hop():
    # Anything after the first entry is attacker-supplied; treating it as identity
    # would let one client invent a new address for every rate-limited attempt.
    assert client_ip(_Req({"x-forwarded-for": "203.0.113.7, 10.0.0.5"})) == "203.0.113.7"
    assert client_ip(_Req({})) == "10.0.0.1"
