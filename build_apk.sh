#!/usr/bin/env bash
set -e
cd /c/Users/kyere/Documents/codes/attendance-verify/mobile

# Prefer a JDK 17 if one is installed (Android Gradle Plugin is happiest on 17).
for j in "/c/Program Files/Java/jdk-17"* "/c/Program Files/Eclipse Adoptium/jdk-17"* "/c/Program Files/Microsoft/jdk-17"*; do
  [ -d "$j" ] && export JAVA_HOME="$j" && break
done
echo "[build] JAVA_HOME=$JAVA_HOME"
java -version 2>&1 | head -1

echo "[build] === expo prebuild (generate native android project) ==="
npx --yes expo prebuild -p android --no-install

echo "[build] === gradle assembleRelease ==="
cd android
./gradlew assembleRelease --no-daemon -x lint -x lintVitalRelease

APK=$(find app/build/outputs/apk/release -name "*.apk" | head -1)
if [ -n "$APK" ]; then
  cp "$APK" /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
  echo "[build] SUCCESS apk -> C:/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk"
  ls -la /c/Users/kyere/Documents/codes/attendance-verify/attendance-verify.apk
else
  echo "[build] FAILED: no apk produced"; exit 1
fi
