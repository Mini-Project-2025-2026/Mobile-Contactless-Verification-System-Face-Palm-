"""Bulk enrolment: a department at a time, in the shape the biometric service takes.

Enrolment is the in-person step and the expensive one. Doing it per student does
not scale to a cohort, so the console imports a batch; what matters is that this
database records the outcome, so nobody is asked to enrol again on their phone.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app import biometric, enrolment
from app.biometric import BulkEnrollResult, BulkPersonResult
from app.config import settings
from app.db import engine
from app.main import app
from app.models import Course, Enrollment, Student
from app.security import hash_password


@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Course(code="CS101", title="Intro", semester="2025/2026-1"))
        for sid, name, prog, year in [
            ("20512345", "Ama", "Computer Science", "2023/2024"),
            ("20512399", "Kofi", "Computer Science", "2023/2024"),
            ("20512400", "Yaa", "Computer Science", "2024/2025"),
            ("20512401", "Kwesi", "Mathematics", "2023/2024"),
        ]:
            db.add(Student(student_id=sid, name=name, password_hash=hash_password("pw"),
                           programme=prog, year_group=year))
        db.commit()
    enrolment.reset_cache()
    yield
    enrolment.reset_cache()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    r = client.post("/api/admin/login", json={
        "username": settings.admin_username, "password": settings.admin_password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mock_bulk(monkeypatch, outcomes):
    """outcomes: {user_id: (success, enrolled, modalities, message)}"""
    sent = {}

    def fake(people, *, dedupe=True, timeout=120.0):
        sent["people"] = people
        sent["dedupe"] = dedupe
        results = tuple(
            BulkPersonResult(user_id=uid, success=o[0], enrolled=o[1], modalities=o[2], message=o[3])
            for uid, _ in people for o in [outcomes[uid]]
        )
        return BulkEnrollResult(people=len(people), enrolled=sum(1 for r in results if r.success),
                                results=results, raw={})

    monkeypatch.setattr(biometric, "enroll_users_bulk", fake)
    return sent


def _modality(sid):
    with Session(engine) as db:
        return db.exec(select(Student).where(Student.student_id == sid)).one().enrolled_modality


def test_a_batch_marks_each_student_enrolled_here(client, auth, monkeypatch):
    _mock_bulk(monkeypatch, {
        "20512345": (True, 3, ("face",), ""),
        "20512399": (True, 5, ("face", "palm"), ""),
    })
    r = client.post("/api/admin/enroll/bulk", headers=auth, json={"people": [
        {"student_id": "20512345", "images": ["a", "b", "c"]},
        {"student_id": "20512399", "images": ["a"]},
    ]}).json()

    assert r["enrolled"] == 2
    assert _modality("20512345") == "face"
    assert _modality("20512399") == "face,palm"


def test_a_person_the_service_rejected_is_not_marked_enrolled(client, auth, monkeypatch):
    _mock_bulk(monkeypatch, {
        "20512345": (True, 3, ("face",), ""),
        "20512399": (False, 0, (), "no usable face or palm"),
    })
    r = client.post("/api/admin/enroll/bulk", headers=auth, json={"people": [
        {"student_id": "20512345", "images": ["a"]},
        {"student_id": "20512399", "images": ["blurry"]},
    ]}).json()

    assert r["enrolled"] == 1
    assert _modality("20512345") == "face"
    assert _modality("20512399") == "", "a failed capture must not look enrolled"
    assert any("no usable" in x["message"] for x in r["results"])


def test_an_unknown_id_is_reported_and_never_sent_onward(client, auth, monkeypatch):
    """A typo in a folder name must not create a template nothing can match."""
    sent = _mock_bulk(monkeypatch, {"20512345": (True, 3, ("face",), "")})
    r = client.post("/api/admin/enroll/bulk", headers=auth, json={"people": [
        {"student_id": "20512345", "images": ["a"]},
        {"student_id": "20599999", "images": ["a"]},
    ]}).json()

    assert [uid for uid, _ in sent["people"]] == ["20512345"]
    bad = [x for x in r["results"] if x["student_id"] == "20599999"][0]
    assert bad["success"] is False and "no such student" in bad["message"]


def test_the_batch_asks_the_service_to_dedupe_by_default(client, auth, monkeypatch):
    sent = _mock_bulk(monkeypatch, {"20512345": (True, 3, ("face",), "")})
    client.post("/api/admin/enroll/bulk", headers=auth,
                json={"people": [{"student_id": "20512345", "images": ["a"]}]})
    assert sent["dedupe"] is True, "an import of a whole department must not skip the duplicate check"


def test_a_dead_service_fails_the_batch_without_marking_anyone(client, auth, monkeypatch):
    def boom(people, *, dedupe=True, timeout=120.0):
        raise biometric.BiometricError("unreachable")
    monkeypatch.setattr(biometric, "enroll_users_bulk", boom)

    r = client.post("/api/admin/enroll/bulk", headers=auth,
                    json={"people": [{"student_id": "20512345", "images": ["a"]}]})
    assert r.status_code == 502
    assert _modality("20512345") == ""


def test_bulk_enrol_needs_an_admin(client):
    assert client.post("/api/admin/enroll/bulk",
                       json={"people": [{"student_id": "20512345", "images": ["a"]}]}).status_code in (401, 403)


# ---- putting a cohort on a course ----
def _enrolled_ids(course_id=1):
    with Session(engine) as db:
        return set(db.exec(select(Enrollment.student_id).where(Enrollment.course_id == course_id)).all())


def test_a_whole_programme_goes_on_a_course_at_once(client, auth):
    r = client.post("/api/admin/enroll-course/bulk", headers=auth,
                    json={"course_id": 1, "programme": "computer science"}).json()
    assert r["matched"] == 3 and r["added"] == 3
    assert _enrolled_ids() == {"20512345", "20512399", "20512400"}


def test_a_year_group_can_be_singled_out_and_repeats_are_harmless(client, auth):
    r = client.post("/api/admin/enroll-course/bulk", headers=auth,
                    json={"course_id": 1, "programme": "Computer Science", "year_group": "2023/2024"}).json()
    assert r["added"] == 2 and _enrolled_ids() == {"20512345", "20512399"}

    again = client.post("/api/admin/enroll-course/bulk", headers=auth,
                        json={"course_id": 1, "programme": "Computer Science", "year_group": "2023/2024"}).json()
    assert again["added"] == 0 and again["already_enrolled"] == 2


def test_bulk_course_enrolment_checks_its_inputs(client, auth):
    assert client.post("/api/admin/enroll-course/bulk", headers=auth,
                       json={"course_id": 99, "programme": "Computer Science"}).status_code == 404
    assert client.post("/api/admin/enroll-course/bulk", headers=auth,
                       json={"course_id": 1, "programme": "  "}).status_code == 400
