#!/bin/bash
# Build the native installer and bundle the service source it installs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
APP="$OUT/CodexMap.app"
BUILD="$(mktemp -d -t codexmap-build)"
trap 'rm -rf "$BUILD"' EXIT
VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
mkdir -p "$OUT"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/payload"
for ARCH in arm64 x86_64; do
    xcrun swiftc -swift-version 5 -O -parse-as-library -target "$ARCH-apple-macos13.0" \
        "$ROOT/macos/CodexMap.swift" -o "$BUILD/CodexMap-$ARCH"
done
xcrun lipo -create "$BUILD/CodexMap-arm64" "$BUILD/CodexMap-x86_64" -output "$APP/Contents/MacOS/CodexMap"
cp -R "$ROOT/app" "$APP/Contents/Resources/payload/app"
for FILE in install.py update.py uninstall.py VERSION; do
    cp "$ROOT/$FILE" "$APP/Contents/Resources/payload/$FILE"
done
# Keep bytecode and local state out of the distribution.
find "$APP/Contents/Resources/payload" -name '__pycache__' -type d -exec rm -rf {} +
find "$APP/Contents/Resources/payload" -name '*.pyc' -delete
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>CodexMap</string>
<key>CFBundleDisplayName</key><string>CodexMap</string>
<key>CFBundleIdentifier</key><string>com.codexmap.installer</string>
<key>CFBundleExecutable</key><string>CodexMap</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundleVersion</key><string>$VERSION</string>
<key>CFBundleIconFile</key><string>CodexMap.icns</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
xcrun swift "$ROOT/macos/make-icon.swift" "$BUILD/CodexMap.iconset"
/usr/bin/iconutil -c icns "$BUILD/CodexMap.iconset" -o "$APP/Contents/Resources/CodexMap.icns"
/usr/bin/codesign --force --deep --sign "${CODE_SIGN_IDENTITY:--}" "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
rm -f "$OUT/CodexMap-macOS.zip"
/usr/bin/ditto -c -k --norsrc --noextattr --noqtn --keepParent "$APP" "$OUT/CodexMap-macOS.zip"
printf 'Built %s\n' "$OUT/CodexMap-macOS.zip"
