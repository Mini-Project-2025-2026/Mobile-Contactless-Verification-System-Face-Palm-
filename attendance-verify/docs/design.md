# Attendance-Verify — Design

## 1. Goal & scope

Rebuild the KNUST-Attendance user flow closely, substituting **only** the presence-proof
mechanism: biometric face/palm verification (via the Biometric Verify API) instead of an
admin-generated PIN.

In-scope feature parity with the original:
- Sign in with **student ID**; one-device binding.
- Home shows **courses with a currently-live session near me** (geofence-filtered).
- **Check in** when inside the session's geofence (default **70 m**).
- **Double-mark per session** → `present` / `partial` / `absent`.
- **Attendance history** grouped by semester (course, date, time, status).
- **Devices** view (registered devices, last-seen, active state).
- Streaks, dark mode, push — later phases.

The substituted mechanism:
- Original: student types a PIN the lecturer generated for the session.
- Ours: student does a live face (or palm) capture; backend calls `/v1/verify` with
  `user_id = studentID`, confirms the **HMAC-signed** `success` verdict, then records the mark.

## 2. Verification flow (the core)

```
Student taps "Check in" on a live course
      │
      ▼
1. App → POST /api/checkin/challenge {session_id}
        Backend → GET  {BIOMETRIC}/v1/challenge   (X-API-Key)
        Backend ← {active, token, instruction}     ("turn your head slowly")
   App ← {token, instruction, modality:"face"}
      │
      ▼
2. App captures a short burst of frames during the head-turn (expo-camera)
      │
      ▼
3. App → POST /api/checkin/verify {session_id, token, frames[], gps:{lat,lng,acc}}
        Backend:
          a. auth: valid JWT student + active registered device
          b. session live? student enrolled in its course?
          c. geofence: haversine(gps, session) ≤ radius_m   (server-authoritative)
          d. → POST {BIOMETRIC}/v1/verify {user_id: studentID, frames, token}  (X-API-Key)
          e. ← {success, user_id, score, signature{alg,ts,nonce,hmac}}
          f. verify_signature(payload, SIGNING_SECRET)   ← must pass
          g. success && signed && user_id==studentID && score≥min → record a mark
          h. marks_count ≥ marks_required → status=present, else partial
   App ← {status, marks_count, marks_required, distance_m, score}
```

Failure taxonomy surfaced to the student (mapped to friendly text):
`not_in_geofence`, `not_enrolled`, `session_closed`, `device_not_registered`,
`liveness_failed`, `biometric_mismatch`, `bad_signature`, `already_complete`.

## 3. Trust boundary

- Biometric **API key** (verify role) + **signing secret** live only in the backend
  environment. The mobile app holds neither.
- The backend treats a verify response as trustworthy **only** when the HMAC over
  `"{ts}.{nonce}.{body}"` (body = compact-sorted `{success,match,user_id,score,best_score}`)
  matches — identical to the Biometric Verify SDK's `verify_signature`. This prevents a
  tampered/replayed response from writing a false "present".
- Geofence is validated **server-side**; the client value is advisory (anti-GPS-spoof is a
  later hardening phase — see §6).

## 4. Data model

| Entity | Key fields |
|---|---|
| `Student` | `student_id` (unique, == biometric `user_id`), `name`, `password_hash`, `semester` |
| `Device` | `student_id`, `device_uid` (unique), `platform`, `name`, `last_seen`, `active` |
| `Course` | `code`, `title`, `semester`, `lecturer_name` |
| `Enrollment` | `student_id`, `course_id` |
| `Session` | `course_id`, `title`, `lat`, `lng`, `radius_m`, `starts_at`, `ends_at`, `marks_required`, `active` |
| `Attendance` | `session_id`, `student_id`, `status`, `marks_count`, `first_marked_at`, `last_marked_at`, `best_score` |
| `AttendanceMark` | `attendance_id`, `marked_at`, `distance_m`, `score`, `modality`, `sig_nonce` (idempotency/replay guard) |

## 5. Endpoints (attendance backend)

```
POST /api/auth/register-device     student_id + password + device_uid  → JWT (binds device)
POST /api/auth/login               student_id + password + device_uid  → JWT
GET  /api/courses/available        ?lat&lng  → live sessions with distance, in-range flag
GET  /api/courses                  student's enrolled courses
POST /api/sessions                 (lecturer) create a geofenced session
POST /api/checkin/challenge        {session_id} → liveness token + instruction
POST /api/checkin/verify           {session_id, token, frames[], gps} → mark result
GET  /api/attendance/history       grouped by semester
GET  /api/devices                  student's registered devices
```

## 6. Build phases

- **Phase 1 (this pass):** backend core — models, geofence, biometric integration
  (challenge + verify + signature check), check-in flow, auth/device binding, history;
  unit tests for geofence + signature + a mocked check-in. Mobile: API client + login +
  available-courses + check-in capture screen scaffold.
- **Phase 2:** enrollment/session admin UI (lecturer), attendance history & devices
  screens, push notifications, dark mode, streaks.
- **Phase 3 (hardening):** GPS-spoof mitigation (accuracy floor, velocity/teleport checks,
  optional server-issued session beacon), rate-limits, offline QR-credential check-in for
  no-signal halls (reuses `/v1/credentials/verify`), biometric enrollment onboarding.

## 7. Enrollment note

A student can only be verified if their biometric template exists in the Biometric Verify
tenant under `user_id = studentID`. Enrollment (capturing face/palm) is an onboarding step:
either the institution bulk-enrolls from ID photos (`/v1/enroll/bulk`) or the student
self-enrols via an invite link. Phase 1 assumes enrolled students; Phase 3 adds an in-app
enrol screen calling `/v1/enroll`.
