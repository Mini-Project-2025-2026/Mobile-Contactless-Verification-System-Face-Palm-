#!/usr/bin/env bash
set -e
cd /c/Users/kyere/Documents/codes/attendance-verify/mobile
for j in "/c/Program Files/Java/jdk-17"* "/c/Program Files/Eclipse Adoptium/jdk-17"*; do [ -d "$j" ] && export JAVA_HOME="$j" && break; done
echo "[b3] JAVA_HOME=$JAVA_HOME"
echo "[b3] regenerating native project (webview replaces maps)"
rm -rf android
npx --yes expo prebuild -p android --no-install
COLORS=android/app/src/main/res/values/colors.xml
grep -q splashscreen_background "$COLORS" || sed -i 's@</resources>@  <color name="splashscreen_background">#ffffff</color>\n  <color name="iconBackground">#ffffff</color>\n</resources>@' "$COLORS"
echo "[b3] gradle assembleRelease"
cd android
./gradlew assembleRelease --no-daemon -x lint -x lintVitalRelease
APK=$(find app/build/outputs/apk/release -name "*.apk" | head -1)
cp "$APK" /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
echo "[b3] SUCCESS -> attendance-verify.apk"; ls -la /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
