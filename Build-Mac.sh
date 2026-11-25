#!/bin/bash
# AI Voice Studio for Mac: builds the app and a DMG for the chip of THIS Mac (Apple Silicon M1/M2/M3/M4 -> arm64, Intel i5/i7/i9 -> x86_64).
# Run it by double-clicking BUILD_MAC.command. It needs nothing but Python 3.10 or newer (for the build only).
set -e
cd "$(dirname "$0")"
say() { printf '\n== %s\n' "$1"; }
fail() { printf '\nSTOPPED: %s\n' "$1"; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "This script builds the Mac version, so it must run on a Mac."
ARCH="$(uname -m)"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' studio/core.py | head -1)"
[ -n "$VERSION" ] || fail "Could not read the version from studio/core.py."
say "Building AI Voice Studio $VERSION for this Mac ($ARCH)"
if [ "$ARCH" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  fail "This Terminal is running in Intel mode (Rosetta) on an M-series Mac. Open Terminal normally (not in Rosetta) and run this again, so the app is built for Apple Silicon."
fi

# 1. Python for the build (3.10 or newer; 3.11 or 3.12 is best)
PY=""
for candidate in python3.12 python3.11 python3.13 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "No Python 3.10 or newer was found. Install Python 3.11 from https://www.python.org/downloads/macos/ (the .pkg file), then double-click BUILD_MAC.command again."
say "Using $($PY --version) at $(command -v $PY)"

# 2. A private build environment
[ -d .build-venv ] || "$PY" -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip >/dev/null
say "Installing the build tools (this can take a few minutes the first time)"
python -m pip install --disable-pip-version-check -r requirements-gui.txt

# 3. The icon (a Mac needs an .icns file; it is made from the logo)
if [ ! -f resources/avs.icns ]; then
  say "Making the app icon"
  rm -rf .iconset && mkdir .iconset
  for size in 16 32 64 128 256 512; do
    sips -z $size $size resources/logo.png --out ".iconset/icon_${size}x${size}.png" >/dev/null
  done
  cp .iconset/icon_32x32.png .iconset/icon_16x16@2x.png
  cp .iconset/icon_64x64.png .iconset/icon_32x32@2x.png
  cp .iconset/icon_256x256.png .iconset/icon_128x128@2x.png
  cp .iconset/icon_512x512.png .iconset/icon_256x256@2x.png
  sips -z 1024 1024 resources/logo.png --out .iconset/icon_512x512@2x.png >/dev/null
  rm -f .iconset/icon_64x64.png
  iconutil -c icns .iconset -o resources/avs.icns
  rm -rf .iconset
fi

# 4. The app
say "Building the app"
rm -rf build dist
python -m PyInstaller --noconfirm --clean AI_Voice_Studio_mac.spec
APP="dist/AI Voice Studio.app"
[ -d "$APP" ] || fail "The app was not created. Read the messages above."

# 5. A local signature (required on Apple Silicon; it is not an Apple Developer ID)
say "Signing the app on this Mac"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" && echo "Signature is valid."

# 6. A quick self-test of the finished app
say "Testing the finished app"
TESTHOME="$(mktemp -d)"
AI_VOICE_STUDIO_HOME="$TESTHOME" "$APP/Contents/MacOS/AI_Voice_Studio" --self-check || true
REPORT="$TESTHOME/packaged-self-check.json"
if [ -f "$REPORT" ] && grep -q '"result": "PASS"' "$REPORT" && grep -q "\"version\": \"$VERSION\"" "$REPORT"; then echo "Self-test passed."; else
  [ -f "$REPORT" ] && cat "$REPORT"
  fail "The self-test of the finished app did not pass."
fi
rm -rf "$TESTHOME"

# 7. The DMG
say "Making the DMG"
mkdir -p BuildOut
rm -rf .dmgstage && mkdir .dmgstage
cp -R "$APP" .dmgstage/
ln -s /Applications .dmgstage/Applications
cat > .dmgstage/READ\ ME\ FIRST.txt <<'TXT'
1. Drag "AI Voice Studio" onto the Applications folder.
2. Open it from Applications.
   If macOS says it cannot be opened because it is from an unidentified developer: right-click (or Control-click) the app, choose Open, then Open.
   Or open Terminal and run:  xattr -dr com.apple.quarantine "/Applications/AI Voice Studio.app"
3. The first time, the app asks to install Python 3.11 and the voice models (Models page). macOS may ask for your password.
TXT
DMG="BuildOut/AI_Voice_Studio_${VERSION}_${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "AI Voice Studio" -srcfolder .dmgstage -ov -format UDZO "$DMG" >/dev/null
rm -rf .dmgstage
shasum -a 256 "$DMG" | tee "$DMG.sha256.txt"

say "Done"
echo "App:  $(pwd)/$APP"
echo "DMG:  $(pwd)/$DMG"
echo "To use it: open the DMG and drag AI Voice Studio to Applications (an app you built yourself opens without any warning on this Mac)."
