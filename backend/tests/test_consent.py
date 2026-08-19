"""The lawful basis for holding a student's face.

The verification service has carried consent since before this app integrated
with it — a versioned statement, a receipt pinned to the text agreed, and
immediate enforcement of a withdrawal. What these cover is that the student can
now see it, give it themselves, read it back, and take it away.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric
from app.db import engine
from app.main import app
from app.models import Student
from app.security import hash_password

SID = "20512345"


class FakeConsent:
    """Stands in for the service's consent registry."""

    def __init__(self, *, require: bool = False):
        self.policy = biometric.ConsentPolicy(
            text="I agree that my facial biometric features may be stored as an "
                 "encrypted template, solely to verify my identity.",
            version=3, text_sha256="abc123",
            enforce_withdrawal=True, require_consent=require)
        self.receipts: dict[str, biometric.ConsentReceipt] = {}

    def record(self, user_id: str, *, method: str = "self"):
        receipt = biometric.ConsentReceipt(
            user_id=user_id, status="granted", granted_at=1_700_000_000,
            method=method, version=self.policy.version,
            text_sha256=self.policy.text_sha256)
        self.receipts[user_id] = receipt
        return receipt

    def withdraw(self, user_id: str) -> bool:
        held = self.receipts.get(user_id)
        if held is None:
            return False
        self.receipts[user_id] = biometric.ConsentReceipt(
            user_id=user_id, status="withdrawn", granted_at=held.granted_at,
            method=held.method, version=held.version,
            text_sha256=held.text_sha256, withdrawn_at=1_700_000_900)
        return True


@pytest.fixture
def registry(monkeypatch):
    fake = FakeConsent()
    monkeypatch.setattr(biometric, "consent_policy", lambda: fake.policy)
    monkeypatch.setattr(biometric, "consent_receipt", lambda uid: fake.receipts.get(uid))
    monkeypatch.setattr(biometric, "record_consent", fake.record)
    monkeypatch.setattr(biometric, "withdraw_consent", fake.withdraw)
    monkeypatch.setattr(biometric, "export_user_record",
                        lambda uid: {"enrolled": True, "modalities": {"face": {"anchors": 5}}})
    # Nothing here should reach the roster; say so explicitly rather than relying
    # on an unreachable base URL to produce the same answer by accident.
    monkeypatch.setattr(biometric, "list_roster", lambda page=500: {})
    monkeypatch.setattr(biometric, "user_status", lambda uid: None)
    return fake


@pytest.fixture(autouse=True)
def fresh():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("pw"),
                       programme="Computer Science", enrolled_modality="face"))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _never_enrolled() -> None:
    """A student with no template yet — the state a first enrolment starts from."""
    with Session(engine) as db:
        row = db.exec(select(Student).where(Student.student_id == SID)).one()
        row.enrolled_modality = ""
        db.add(row)
        db.commit()


@pytest.fixture
def headers(client):
    token = client.post("/api/auth/login", json={
        "student_id": SID, "password": "pw", "device_uid": "dev-1"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_a_student_can_read_what_they_are_being_asked_to_agree_to(client, headers, registry):
    body = client.get("/api/consent/statement", headers=headers).json()
    assert "encrypted template" in body["text"]
    assert body["version"] == 3
    assert body["withdrawal_stops_verification"] is True


def test_nothing_is_on_record_until_the_student_agrees(client, headers, registry):
    assert client.get("/api/consent", headers=headers).json() == {
        "recorded": False, "status": "none", "granted_at": 0, "method": "",
        "version": 0, "text_sha256": "", "withdrawn_at": None}


def test_agreeing_in_the_app_is_recorded_as_the_student_s_own_act(client, headers, registry):
    """`self` is the strongest form — not an administrator ticking a box."""
    created = client.post("/api/consent", headers=headers)
    assert created.status_code == 201
    assert created.json()["method"] == "self"

    read_back = client.get("/api/consent", headers=headers).json()
    assert read_back["status"] == "granted"
    assert read_back["version"] == 3
    assert read_back["text_sha256"] == "abc123"


def test_withdrawal_is_honoured_and_takes_the_enrolment_with_it(client, headers, registry):
    client.post("/api/consent", headers=headers)
    response = client.post("/api/consent/withdraw", headers=headers)
    assert response.status_code == 200
    assert registry.receipts[SID].status == "withdrawn"

    # Our cached "this student is enrolled" must go too, or the face-required
    # gate keeps waving them through to a verify the service now refuses.
    with Session(engine) as db:
        assert db.exec(select(Student).where(
            Student.student_id == SID)).one().enrolled_modality == ""


def test_withdrawing_nothing_is_a_404_not_a_silent_success(client, headers, registry):
    assert client.post("/api/consent/withdraw", headers=headers).status_code == 404


def test_a_student_can_read_everything_held_about_them(client, headers, registry):
    body = client.get("/api/consent/my-data", headers=headers).json()
    assert body["student"]["student_id"] == SID
    assert body["student"]["enrolled_modalities"] == ["face"]
    # metadata only: the template itself never leaves the service
    assert body["biometric"]["modalities"]["face"]["anchors"] == 5


def test_enrolment_is_refused_where_the_campus_requires_consent_first(
        client, headers, monkeypatch, registry):
    registry.policy = biometric.ConsentPolicy(
        text=registry.policy.text, version=3, text_sha256="abc123",
        enforce_withdrawal=True, require_consent=True)
    monkeypatch.setattr(biometric, "consent_policy", lambda: registry.policy)

    response = client.post("/api/enroll", headers=headers,
                           json={"modality": "face", "images": ["img"]}).json()
    assert response["code"] == "consent_required"
    assert "withdraw it at any time" in response["message"]


def test_consent_given_clears_the_way_to_enrol(client, headers, monkeypatch, registry):
    registry.policy = biometric.ConsentPolicy(
        text=registry.policy.text, version=3, text_sha256="abc123",
        enforce_withdrawal=True, require_consent=True)
    monkeypatch.setattr(biometric, "consent_policy", lambda: registry.policy)
    monkeypatch.setattr(biometric, "enroll_user",
                        lambda uid, images, **kw: biometric.EnrollResult(
                            enrolled=1, of=1, samples=3, raw={}))
    monkeypatch.setattr(biometric, "list_roster", lambda page=500: {})

    _never_enrolled()
    client.post("/api/consent", headers=headers)
    response = client.post("/api/enroll", headers=headers,
                           json={"modality": "face", "images": ["img"]}).json()
    assert response["code"] == "ok"


def test_an_unreachable_consent_registry_does_not_block_enrolment(
        client, headers, monkeypatch):
    """An outage in the consent lookup must not stop a student enrolling on the
    day their class starts; the service records consent on the enrol path anyway."""
    def boom():
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "consent_policy", boom)
    monkeypatch.setattr(biometric, "enroll_user",
                        lambda uid, images, **kw: biometric.EnrollResult(
                            enrolled=1, of=1, samples=3, raw={}))
    monkeypatch.setattr(biometric, "list_roster", lambda page=500: {})
    monkeypatch.setattr(biometric, "user_status", lambda uid: None)

    _never_enrolled()
    response = client.post("/api/enroll", headers=headers,
                           json={"modality": "face", "images": ["img"]}).json()
    assert response["code"] == "ok"
