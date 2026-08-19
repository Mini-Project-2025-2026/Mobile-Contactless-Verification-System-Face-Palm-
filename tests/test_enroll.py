"""Enrolment policy: face-compulsory, first-enrol binds device, re-enrol/new
device needs an admin one-time grant. Biometric service is mocked."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric, enrolment
from app.biometric import EnrollResult
from app.db import engine
from app.main import app
from app.models import EnrollGrant, Student
from app.security import hash_password

SID = "20599999"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Test", password_hash=hash_password("pw")))
        db.commit()
    yield


@pytest.fixture(autouse=True)
def mock_enroll(monkeypatch):
    monkeypatch.setattr(
        biometric, "enroll_user",
        lambda uid, images, source="auto": EnrollResult(enrolled=len(images), of=len(images), samples=2, raw={}),
    )


@pytest.fixture(autouse=True)
def service_roster(monkeypatch):
    """Templates the biometric service holds. Tests mutate the set in place."""
    roster: set[str] = set()
    monkeypatch.setattr(biometric, "list_enrolled_user_ids", lambda page=500: set(roster))
    enrolment.reset_cache()
    yield roster
    enrolment.reset_cache()


def _forget_enrolment_cache() -> None:
    """Blank the DB's copy of the enrolment, as a rebuilt/migrated row would be."""
    with Session(engine) as db:
        st = db.exec(select(Student).where(Student.student_id == SID)).one()
        st.enrolled_modality = ""
        st.enroll_device_uid = ""
        db.add(st)
        db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, device):
    r = client.post("/api/auth/login", json={
        "student_id": SID, "password": "pw", "device_uid": device, "platform": "t", "device_name": "t"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _enroll(client, H, modality, grant=""):
    return client.post("/api/enroll", headers=H,
                       json={"modality": modality, "images": ["a", "b", "c"], "grant_token": grant}).json()


def test_face_first_then_optional_palm_free(client):
    A = _login(client, "devA")
    assert _enroll(client, A, "face")["ok"] is True
    assert client.get("/api/enroll/status", headers=A).json()["can_mark"] is True
    # adding a not-yet-held modality from the same device is free
    r = _enroll(client, A, "palm")
    assert r["ok"] is True
    st = client.get("/api/enroll/status", headers=A).json()
    assert st["face_enrolled"] and st["palm_enrolled"]


def test_reenroll_existing_modality_needs_grant(client):
    A = _login(client, "devA")
    _enroll(client, A, "face")
    r = _enroll(client, A, "face")  # redo face
    assert r["ok"] is False and r["code"] == "grant_required"


def test_new_device_needs_grant(client):
    _enroll(client, _login(client, "devA"), "face")
    r = _enroll(client, _login(client, "devB"), "face")
    assert r["ok"] is False and r["code"] == "grant_required"


def test_grant_unlocks_and_is_single_use(client):
    _enroll(client, _login(client, "devA"), "face")
    with Session(engine) as db:
        db.add(EnrollGrant(token="CODE1234", student_id=SID,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
    B = _login(client, "devB")
    assert _enroll(client, B, "face", grant="CODE1234")["ok"] is True   # consumed, re-binds devB
    assert _enroll(client, B, "face", grant="CODE1234")["code"] == "grant_required"  # single-use


def test_expired_grant_rejected(client):
    _enroll(client, _login(client, "devA"), "face")
    with Session(engine) as db:
        db.add(EnrollGrant(token="OLD12345", student_id=SID,
                           expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    r = _enroll(client, _login(client, "devB"), "face", grant="OLD12345")
    assert r["code"] == "grant_required"


def test_status_readopts_enrolment_the_db_forgot(client, service_roster):
    """The template still exists service-side: the student is enrolled, full stop."""
    A = _login(client, "devA")
    _enroll(client, A, "face")
    service_roster.add(SID)
    _forget_enrolment_cache()
    enrolment.reset_cache()

    st = client.get("/api/enroll/status", headers=A).json()
    assert st["can_mark"] is True and st["face_enrolled"] is True

    with Session(engine) as db:  # and the cache was healed, not just the answer
        assert db.exec(select(Student).where(Student.student_id == SID)).one().enrolled_modality == "face"


def test_forgotten_cache_still_needs_a_grant_to_re_enrol(client, service_roster):
    """Self-heal must not become a free re-enrolment from any new device."""
    _enroll(client, _login(client, "devA"), "face")
    service_roster.add(SID)
    _forget_enrolment_cache()
    enrolment.reset_cache()

    r = _enroll(client, _login(client, "devB"), "face")
    assert r["ok"] is False and r["code"] == "grant_required"


def test_service_outage_leaves_status_untouched(client, monkeypatch, service_roster):
    """An unreachable service must not crash status, nor wipe what we know."""
    A = _login(client, "devA")
    _enroll(client, A, "face")

    def boom(page=500):
        raise biometric.BiometricError("unreachable")

    monkeypatch.setattr(biometric, "list_enrolled_user_ids", boom)
    enrolment.reset_cache()
    assert client.get("/api/enroll/status", headers=A).json()["can_mark"] is True

    _forget_enrolment_cache()
    enrolment.reset_cache()
    assert client.get("/api/enroll/status", headers=A).json()["can_mark"] is False


def test_roster_is_cached_between_requests(client, monkeypatch, service_roster):
    calls = []
    monkeypatch.setattr(biometric, "list_enrolled_user_ids", lambda page=500: (calls.append(1), set())[1])
    enrolment.reset_cache()
    A = _login(client, "devA")
    for _ in range(3):
        client.get("/api/enroll/status", headers=A)
    assert len(calls) == 1
