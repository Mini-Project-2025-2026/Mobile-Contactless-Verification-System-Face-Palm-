"""Guessing a shared password should be slow, and a wrong guess should say nothing.

The two credentials that guard sign-in here are weak on purpose: a programme
password is known to a whole cohort, and the console has one account. Neither can
be made strong, so what has to hold is that an attacker cannot simply try.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import guard
from app.config import settings
from app.db import engine
from app.main import app
from app.models import Student
from app.ratelimit import SlidingWindow
from app.security import hash_password

SID = "20512345"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Student(student_id=SID, name="Ama", password_hash=hash_password("passw0rd")))
        db.commit()
    guard.reset_all()
    yield
    guard.reset_all()


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, password, student_id=SID):
    return client.post("/api/auth/login", json={
        "student_id": student_id, "password": password, "device_uid": "dev-1"})


def test_a_correct_password_still_works(client):
    assert _login(client, "passw0rd").status_code == 200


def test_repeated_guessing_is_locked_out(client, monkeypatch):
    monkeypatch.setattr(guard, "student_logins", SlidingWindow(3, 300, 900))

    for _ in range(3):
        assert _login(client, "wrong").status_code == 401
    blocked = _login(client, "wrong")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["Retry-After"]) > 0


def test_the_lockout_holds_even_once_the_password_is_right(client, monkeypatch):
    """Otherwise the lockout is only a delay before the guess that succeeds."""
    monkeypatch.setattr(guard, "student_logins", SlidingWindow(2, 300, 900))
    for _ in range(2):
        _login(client, "wrong")
    assert _login(client, "passw0rd").status_code == 429


def test_a_success_clears_the_history(client, monkeypatch):
    """A student who mistypes twice and then gets it right starts fresh."""
    monkeypatch.setattr(guard, "student_logins", SlidingWindow(3, 300, 900))
    _login(client, "wrong")
    _login(client, "wrong")
    assert _login(client, "passw0rd").status_code == 200
    _login(client, "wrong")
    _login(client, "wrong")
    assert _login(client, "passw0rd").status_code == 200


def test_one_student_being_guessed_at_does_not_lock_out_another(client, monkeypatch):
    """The key is (identity, address), so an attacker cannot lock out a cohort."""
    monkeypatch.setattr(guard, "student_logins", SlidingWindow(2, 300, 900))
    with Session(engine) as db:
        db.add(Student(student_id="20599999", name="Kofi",
                       password_hash=hash_password("other-pass")))
        db.commit()

    for _ in range(3):
        _login(client, "wrong", student_id=SID)
    assert _login(client, "wrong", student_id=SID).status_code == 429
    assert _login(client, "other-pass", student_id="20599999").status_code == 200


def test_an_unknown_student_and_a_wrong_password_are_indistinguishable(client):
    absent = _login(client, "anything", student_id="00000000")
    wrong = _login(client, "wrong")
    assert absent.status_code == wrong.status_code == 401
    assert absent.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_a_withdrawn_student_cannot_sign_in(client):
    """The record stays for the semester report; the account stops working."""
    with Session(engine) as db:
        row = db.exec(select(Student).where(Student.student_id == SID)).one()
        row.active = False
        db.add(row)
        db.commit()
    assert _login(client, "passw0rd").status_code == 401


def test_admin_login_is_locked_out_too(client, monkeypatch):
    monkeypatch.setattr(guard, "admin_logins", SlidingWindow(2, 300, 1800))
    for _ in range(2):
        r = client.post("/api/admin/login", json={"username": "admin", "password": "no"})
        assert r.status_code == 401
    assert client.post("/api/admin/login",
                       json={"username": "admin", "password": "no"}).status_code == 429


def test_admin_credentials_are_compared_in_constant_time():
    """`==` on strings returns at the first differing character, which measures
    how much of the password was right."""
    assert guard.check_credentials("admin", "secret", "admin", "secret") is True
    assert guard.check_credentials("admin", "secre", "admin", "secret") is False
    assert guard.check_credentials("root", "secret", "admin", "secret") is False


def test_admin_login_still_works_with_the_right_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password", "correct-horse")
    r = client.post("/api/admin/login", json={"username": "admin", "password": "correct-horse"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_idle_windows_are_swept_away():
    """The dictionary must not keep one entry per student id ever seen."""
    window = SlidingWindow(5, 1.0, 0.0)
    window.record("a|1.2.3.4", now=0.0)
    assert window.sweep(now=0.5) == 0     # still inside its window
    assert window.sweep(now=10.0) == 1    # long past it
