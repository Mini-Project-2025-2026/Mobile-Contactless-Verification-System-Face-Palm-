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
| Same person marks several friends | Server binds mark to the authenticated student + one registered device |

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

## Repo layout

```
attendance-verify/
├── backend/            FastAPI service (runnable, tested)
│   └── app/
│       ├── biometric.py   ← integration core: challenge + verify + HMAC check
│       ├── geo.py         ← haversine geofence
│       ├── routers/checkin.py  ← the novel flow (replaces the PIN)
│       └── ...
├── mobile/             Expo (React Native + TypeScript) client scaffold
└── docs/design.md      Full design + phase plan
```

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

## Tests

```sh
cd backend && ./venv/Scripts/python.exe -m pytest tests -q     # 63 tests
```
