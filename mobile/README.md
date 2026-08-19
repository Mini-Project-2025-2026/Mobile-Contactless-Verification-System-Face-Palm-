# Attendance-Verify — Mobile (Expo + TypeScript)

Student app, reskinned to match the **KNUST Attendance** visual language (light theme,
crimson-red gradient hero cards, white rounded cards, bottom tab bar) — captured from
the App Store screenshots. The one substitution: where the original marks presence with
an **admin PIN**, this app uses a **live face check** via our backend + Biometric Verify.

## Design system
`src/theme.ts` holds the tokens: crimson gradient `#C41230 → #6E0D1A`, bg `#F5F6F8`,
white cards, status colors. Reusable pieces in `src/components/`
(`GradientCard`, `ui.tsx` → Avatar/StatusPill/ListRow, `TabBar`, `Screen`).

## Screens (bottom tabs: Home · Discover · Devices · Profile)
- **Home** — greeting + red current-course card + "Mark Your Attendance" → **Verify with Face**.
- **Discover** — live map with a translucent red **geofence circle**, course pin, and an
  Inside/Outside Class Area pill (react-native-maps).
- **Devices** — "N Devices Connected" gradient card + registered-device list with Active badge.
- **Profile** — gradient ID card (reference, programme, year/class) + options + logout.
- **My Attendance** (from Profile) — history grouped by semester with status pills.
- **Help Center** (from Profile) — accordion FAQ.
- **Check-in** (overlay) — front-camera head-turn burst → verify → Present/partial/denied.

## Run
```bash
cd mobile
npm install            # needs network (blocked in this sandbox)
npx expo start
```
Config:
- `src/api/client.ts` → set `API_BASE` (Android emulator `http://10.0.2.2:8000`; a real
  phone → your computer's LAN IP, and run uvicorn with `--host 0.0.0.0`).
- `app.json` → replace the two `REPLACE_WITH_*_GOOGLE_MAPS_KEY` placeholders (the Discover
  map uses Google provider). Without a key the map tiles won't render, but every other
  screen works.

## Still to come (phase 3)
Push notifications, streaks/gamification, dark-mode toggle, in-app biometric enrolment,
GPS-spoof hardening, offline QR-credential check-in. See `../docs/design.md`.
