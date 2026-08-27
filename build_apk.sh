#!/usr/bin/env bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MOBILE_DIR="$SCRIPT_DIR/mobile"
APK_DEST="$SCRIPT_DIR/attendance-verify.apk"

cd "$MOBILE_DIR"

if [ ! -d "android" ]; then
  echo "[build] === No android/ dir found — running expo prebuild ==="
  npx --yes expo prebuild -p android --no-install

  COLORS="android/app/src/main/res/values/colors.xml"
  if [ -f "$COLORS" ] && ! grep -q "splashscreen_background" "$COLORS"; then
    echo "[build] Patching colors.xml..."
    sed -i 's@</resources>@  <color name="splashscreen_background">#ffffff</color>\n  <color name="iconBackground">#ffffff</color>\n</resources>@' "$COLORS"
  fi
else
  echo "[build] === android/ already exists — skipping prebuild, going straight to Gradle ==="
fi

echo "[build] === Assembling Release APK ==="
cd android
./gradlew assembleRelease --no-daemon -x lint -x lintVitalRelease

APK=$(find app/build/outputs/apk/release -name "*.apk" | head -1)
if [ -n "$APK" ]; then
  cp "$APK" "$APK_DEST"
  echo "[build] SUCCESS: APK -> $APK_DEST"
  ls -lh "$APK_DEST"
else
  echo "[build] FAILED: No APK produced"
  exit 1
fi
