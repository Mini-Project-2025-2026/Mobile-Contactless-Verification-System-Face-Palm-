"""Sign-in = unique student ID + the password shared by their programme.

Computer Science students all use one password, Mathematics students another.
The ID is the part that identifies you; the shared password only keeps
strangers out, which is why it can never be what protects enrolment.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.config import settings
from app.db import engine
from app.main import app
from app.models import ProgrammeCredential, Student
from app.security import hash_password

CS_PW = "knust-cs-2026!"
MATH_PW = "knust-math-2026!"


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        # no individual password: these students exist only under their programme
        db.add(Student(student_id="20512345", name="Ama", password_hash="", programme="Computer Science"))
        db.add(Student(student_id="20512399", name="Kofi", password_hash="", programme="Mathematics"))
        # a legacy account that still carries a private password
        db.add(Student(student_id="20500001", name="Yaa", password_hash=hash_password("her-own-pw"),
                       programme="Computer Science"))
        db.add(ProgrammeCredential(programme="computer science", password_hash=hash_password(CS_PW)))
        db.add(ProgrammeCredential(programme="mathematics", password_hash=hash_password(MATH_PW)))
        db.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, sid, pw):
    return client.post("/api/auth/login", json={
        "student_id": sid, "password": pw, "device_uid": f"dev-{sid}", "platform": "t", "device_name": "t"})


def test_a_student_signs_in_with_their_programme_password(client):
    r = _login(client, "20512345", CS_PW)
    assert r.status_code == 200 and r.json()["name"] == "Ama"


def test_another_programme_password_does_not_work(client):
    assert _login(client, "20512345", MATH_PW).status_code == 401


def test_the_student_id_must_still_be_real(client):
    assert _login(client, "20599999", CS_PW).status_code == 401


def test_a_private_password_still_works_alongside_the_shared_one(client):
    assert _login(client, "20500001", "her-own-pw").status_code == 200
    assert _login(client, "20500001", CS_PW).status_code == 200


def test_a_student_with_no_password_at_all_cannot_be_signed_into(client):
    """An empty hash must never verify — not with "", not with anything."""
    with Session(engine) as db:
        db.add(Student(student_id="20500002", name="Nobody", password_hash="", programme=""))
        db.commit()
    assert _login(client, "20500002", "").status_code == 401
    assert _login(client, "20500002", "anything").status_code == 401


def test_admin_sets_and_rotates_a_programme_password(client):
    a = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password}).json()["access_token"]
    H = {"Authorization": f"Bearer {a}"}

    rows = {r["programme"]: r for r in client.get("/api/admin/programmes", headers=H).json()}
    assert rows["Computer Science"]["students"] == 2 and rows["Computer Science"]["password_set"] is True

    # case and spacing are how a human typed it, not a different programme
    r = client.post("/api/admin/programmes/password", headers=H,
                    json={"programme": "  computer   SCIENCE ", "password": "rotated-cs-2026!"})
    assert r.status_code == 200
    assert _login(client, "20512345", "rotated-cs-2026!").status_code == 200
    assert _login(client, "20512345", CS_PW).status_code == 401


def test_a_programme_password_has_to_be_long_enough(client):
    a = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password}).json()["access_token"]
    # A cohort password is short on purpose (it is read out in a lecture hall), but
    # not so short that a stranger reaches a student's record by guessing twice.
    assert client.post("/api/admin/programmes/password", headers={"Authorization": f"Bearer {a}"},
                       json={"programme": "Computer Science", "password": "cs24"}).status_code == 422
    assert client.post("/api/admin/programmes/password", headers={"Authorization": f"Bearer {a}"},
                       json={"programme": "Computer Science", "password": "CS@2024"}).status_code == 200


def test_setting_a_programme_password_needs_an_admin(client):
    assert client.post("/api/admin/programmes/password",
                       json={"programme": "Computer Science", "password": "no-admin-here!"}).status_code in (401, 403)


def test_no_published_default_opens_the_console(monkeypatch):
    """An unset ADMIN_PASSWORD must lock the door, not leave a key under it.

    The old default sat in config.py: one repository visibility change away from
    handing anyone the console of every deployment that never overrode it.
    """
    import importlib
    import os

    from app import config

    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(config.Settings, "model_config",
                        {**config.Settings.model_config, "env_file": None})
    first = config.Settings()
    second = config.Settings()

    assert first.admin_password not in ("cLLeB", "admin", "password", "")
    assert len(first.admin_password) >= 32
    assert first.admin_password != second.admin_password, "not a fixed fallback"
    assert first.jwt_secret != "dev-insecure-change-me"
