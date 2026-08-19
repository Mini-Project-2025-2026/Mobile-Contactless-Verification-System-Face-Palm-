#!/usr/bin/env bash
set -e
cd /c/Users/kyere/Documents/codes/attendance-verify/mobile/android
echo "[build2] gradle assembleRelease (incremental, no prebuild)"
./gradlew assembleRelease --no-daemon -x lint -x lintVitalRelease
APK=$(find app/build/outputs/apk/release -name "*.apk" | head -1)
if [ -n "$APK" ]; then
  cp "$APK" /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
  echo "[build2] SUCCESS -> attendance-verify.apk"
  ls -la /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
else
  echo "[build2] FAILED: no apk"; exit 1
fi
