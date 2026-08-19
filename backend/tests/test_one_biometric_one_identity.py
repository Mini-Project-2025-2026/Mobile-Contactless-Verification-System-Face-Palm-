"""A face or palm belongs to exactly one student ID. Nothing overrides that.

The biometric service enforces it (`code: "duplicate"`, with the conflicting
user_id), but this backend used to read only the enrolled count, so a duplicate
arrived as enrolled=0 and was reported to the student as bad lighting. The
refusal now travels intact, and an admin grant cannot buy past it: a grant
authorises re-enrolling YOUR OWN biometric, never taking on someone else's.
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric, enrolment
from app.biometric import BulkEnrollResult, BulkPersonResult, EnrollResult
from app.config import settings
from app.db import engine
from app.main import app
from app.models import EnrollGrant, Student
from app.security import hash_password

AMA, KOFI = "20512345", "20512399"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=AMA, name="Ama", password_hash=hash_password("pw")))
        db.add(Student(student_id=KOFI, name="Kofi", password_hash=hash_password("pw")))
        db.commit()
    enrolment.reset_cache()
    yield
    enrolment.reset_cache()


@pytest.fixture(autouse=True)
def quiet_roster(monkeypatch):
    monkeypatch.setattr(biometric, "list_roster", lambda page=500: {})


@pytest.fixture
def client():
    return TestClient(app)


def _auth(client, sid):
    r = client.post("/api/auth/login", json={
        "student_id": sid, "password": "pw", "device_uid": f"dev-{sid}",
        "platform": "t", "device_name": "t"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _service_says_duplicate(monkeypatch, conflict_with):
    """What the service returns when the capture belongs to someone else."""
    def fake(uid, images, source="auto"):
        return EnrollResult(enrolled=0, of=len(images), samples=0, raw={},
                            duplicate=True, conflict_user_id=conflict_with)
    monkeypatch.setattr(biometric, "enroll_user", fake)


def _enroll(client, auth, modality="face", grant=""):
    return client.post("/api/enroll", headers=auth,
                       json={"modality": modality, "images": ["a", "b", "c"], "grant_token": grant}).json()


def _modality_of(sid):
    with Session(engine) as db:
        return db.exec(select(Student).where(Student.student_id == sid)).one().enrolled_modality


def test_a_face_already_on_another_id_is_refused(client, monkeypatch):
    _service_says_duplicate(monkeypatch, conflict_with=AMA)
    r = _enroll(client, _auth(client, KOFI))

    assert r["ok"] is False and r["code"] == "duplicate_biometric"
    assert _modality_of(KOFI) == ""


def test_the_refusal_does_not_read_as_a_lighting_problem(client, monkeypatch):
    """The two failures call for opposite responses: retry vs go to the office."""
    _service_says_duplicate(monkeypatch, conflict_with=AMA)
    dupe = _enroll(client, _auth(client, KOFI))

    monkeypatch.setattr(biometric, "enroll_user",
                        lambda uid, images, source="auto": EnrollResult(enrolled=0, of=3, samples=0, raw={}))
    unusable = _enroll(client, _auth(client, KOFI))

    assert dupe["code"] != unusable["code"]
    assert "already registered" in dupe["message"]
    assert "lighting" in unusable["message"]


def test_it_never_names_the_other_student_to_a_student(client, monkeypatch):
    _service_says_duplicate(monkeypatch, conflict_with=AMA)
    r = _enroll(client, _auth(client, KOFI))
    assert AMA not in r["message"], "whose ID it collides with is the admin's business"


def test_an_admin_grant_cannot_buy_past_it(client, monkeypatch):
    """A grant authorises re-enrolling your own biometric, not claiming another."""
    with Session(engine) as db:
        db.add(EnrollGrant(token="APPROVED1", student_id=KOFI,
                           expires_at=datetime.now(UTC) + timedelta(hours=1)))
        db.commit()
    _service_says_duplicate(monkeypatch, conflict_with=AMA)

    r = _enroll(client, _auth(client, KOFI), grant="APPROVED1")
    assert r["ok"] is False and r["code"] == "duplicate_biometric"
    assert _modality_of(KOFI) == ""

    with Session(engine) as db:  # and the code is not burned by a refusal
        grant = db.exec(select(EnrollGrant).where(EnrollGrant.token == "APPROVED1")).one()
    assert grant.used_at is None


def test_a_palm_on_another_id_is_refused_the_same_way(client, monkeypatch):
    _service_says_duplicate(monkeypatch, conflict_with=AMA)
    r = _enroll(client, _auth(client, KOFI), modality="palm")
    assert r["code"] == "duplicate_biometric" and "palm" in r["message"]


def test_a_bulk_import_names_the_collision_for_the_operator(client, monkeypatch):
    def fake_bulk(people, **kw):
        return BulkEnrollResult(people=1, enrolled=0, raw={}, results=(
            BulkPersonResult(user_id=KOFI, success=False, enrolled=0, modalities=(),
                             message="biometric already enrolled under a different name",
                             duplicate=True, conflict_user_ids=(AMA,)),
        ))
    monkeypatch.setattr(biometric, "enroll_users_bulk", fake_bulk)

    a = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password}).json()["access_token"]
    r = client.post("/api/admin/enroll/bulk", headers={"Authorization": f"Bearer {a}"},
                    json={"people": [{"student_id": KOFI, "images": ["a"]}]}).json()

    (row,) = r["results"]
    assert row["success"] is False and row["duplicate"] is True
    assert AMA in row["message"] and row["conflicts"] == [AMA]
    assert _modality_of(KOFI) == ""
