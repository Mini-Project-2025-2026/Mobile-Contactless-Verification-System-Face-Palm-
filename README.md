# Attendance-Verify

A geofenced course attendance app — a close functional clone of the "KNUST Attendance"
flow (sign in with your **student ID**, see the **courses whose session is live near
you**, check in when you're inside the **70 m** geofence) — with **one substitution**:

> Where the original marks you present via an **admin-generated PIN**, this app proves
> presence with **contactless face/palm biometric verification + active liveness**,
> using the **Biometric Verify** API (`../contactless-fingerprint-system`).

No PIN to share, screenshot, or relay to an absent friend. The student's own live face
(or palm) is the attendance token, and the backend only writes a record after it
**verifies the HMAC-signed verdict** returned by the biometric service.

## Why this is stronger than a PIN

| Attack on a PIN | What biometric verify does |
|---|---|
| Lecturer's PIN leaks in a group chat → everyone marks present | The token is the student's live face; can't be forwarded |
| Friend enters the PIN for an absent student | 1:1 verify against *that student's* enrolled template fails |
| Photo of an absent student held to the camera | Active-liveness head-turn challenge (`frames` + `token`) rejects it |
| Same person marks several friends | Each mark is a 1:1 verify against that student's own template |
| Marking once and leaving | Present needs a START **and** an END check-in, both inside the geofence |
| Enrolling your own face against a classmate's ID | One biometric belongs to one student ID; the service refuses a second |

## Architecture

```
┌─────────────────────────┐        ┌───────────────────────────┐        ┌────────────────────────┐
│  Mobile app (Expo/RN)   │        │  Attendance backend        │        │  Biometric Verify API  │
│  - student ID login     │  HTTPS │  (FastAPI)                 │  HTTPS │  (your existing system)│
│  - GPS geofence         │ ─────► │  - courses / sessions      │ ─────► │  /v1/challenge         │
│  - camera capture       │        │  - enrollment, geofence    │  X-API │  /v1/verify (signed)   │
│  - live head-turn       │        │  - orchestrates verify     │  -Key  │                        │
└─────────────────────────┘        │  - verifies HMAC signature │        └────────────────────────┘
                                   │  - writes attendance       │
                                   │  SQLite / Postgres         │
                                   └───────────────────────────┘
```

**Key security choice:** the biometric API key never ships in the mobile app. The app
sends camera frames to *our* backend; the backend calls Biometric Verify server-to-server
and validates the returned signature before trusting the result.

## Quick start (backend)

```bash
cd backend
python -m venv venv && venv/Scripts/pip install -r requirements.txt
cp .env.example .env          # fill in BIOMETRIC_* values
venv/Scripts/python -m app.seed          # demo student/course/session
venv/Scripts/uvicorn app.main:app --reload
# open http://127.0.0.1:8000/docs
venv/Scripts/pytest            # run tests
```

See [`docs/design.md`](docs/design.md) for the full design and the build phases.

## Repository layout

```
backend/   FastAPI service: API, admin console (/admin), installable PWA (/app), tests
mobile/    React Native (Expo) Android client
docs/      design notes
supabase/  database notes
```

## Deploying the backend

Heroku's app root is `backend/`, so it is deployed as a subtree of this repo:

```sh
git push origin main
git push --force heroku "$(git subtree split --prefix backend main)":refs/heads/main
```

The force is expected: `subtree split` builds a fresh backend-only history each
time, so its commit ids never fast-forward. Heroku is a deploy target, not a
source of truth — this repo is.

## How people use it

**Students** sign in with their student ID and the password shared by their programme,
then enrol their face once (palm optional). A phone can be handed around a class, so a
student without one still marks from a classmate's handset: the login claims the ID, and
the face proves it. Attendance runs in two windows — a START and an END check-in, both
inside the geofence — so leaving after the first minute does not count as present.

Everything students use runs as an installable PWA at `/app` on the backend, and as an
Android build in [Releases](../../releases).

**Lecturers and admins** work in `/admin`:

| Tab | What it is for |
|---|---|
| Sessions | Open a class at a pin, open the END window, extend a class that is running out of time |
| Courses | Course list with class sizes — a course with no students is flagged |
| Students | Create students, set the programme sign-in password, issue one-time enrolment codes |
| Students → bulk | Import a whole department's biometrics from a folder, one sub-folder per student ID |
| Attendance | Live view of one session |
| End of semester | The whole course record, on screen and as a CSV download |

The end-of-semester record comes in two shapes: the **register** (one row per student, one
column per class, totals) and the **full detail** (one row per student per class, with mark
times, scores, distances and modality). Both list who never enrolled, because a student
absent for that reason is a different problem from one who skipped.

## Configuration

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Postgres in production; SQLite locally |
| `JWT_SECRET`, `ADMIN_PASSWORD` | **Set both.** Unset means a per-process value is generated: the console is locked rather than opened by a published default, and the process prints what it generated |
| `BIOMETRIC_BASE_URL`, `BIOMETRIC_API_KEY`, `BIOMETRIC_SIGNING_SECRET` | The verification service and the secret its verdicts are signed with |
| `MIN_VERIFY_SCORE`, `MAX_GPS_ACCURACY_M` | Score floor and the GPS accuracy beyond which a fix is not trusted |
| `ENROLL_REQUIRES_GRANT` | `true` puts an admin one-time code in front of a student's *first* enrolment too. Re-enrolment always needs one |

## Tests

```sh
cd backend && ./venv/Scripts/python.exe -m pytest tests -q     # 71 tests
```
