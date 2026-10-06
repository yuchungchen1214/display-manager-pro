#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
DIST="${DISPLAY_MODE_DIST_DIR:-$ROOT/dist-v$VERSION}"
APP="$DIST/Display Manager Pro.app"
DMG="$DIST/Display Manager Pro $VERSION macOS.dmg"
BACKGROUND="$ROOT/assets/dmg-background.png"
VOLUME_ICON="$ROOT/assets/icon.icns"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/display-manager-pro-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

if [[ ! -d "$APP" ]]; then
  print -u2 "App bundle not found: $APP"
  print -u2 "Build the app first with ./build-ui.sh"
  exit 1
fi
if [[ ! -f "$BACKGROUND" || ! -f "$VOLUME_ICON" ]]; then
  print -u2 "The DMG background or app icon is missing."
  exit 1
fi
if [[ -e "$DMG" ]]; then
  print -u2 "Refusing to overwrite existing disk image: $DMG"
  exit 1
fi
if ! command -v create-dmg >/dev/null 2>&1; then
  print -u2 "create-dmg is required. Install it with Homebrew before packaging."
  exit 1
fi

cp -R "$APP" "$STAGE/"
create-dmg \
  --volname "Display Manager Pro" \
  --volicon "$VOLUME_ICON" \
  --background "$BACKGROUND" \
  --window-pos 200 120 \
  --window-size 800 400 \
  --icon-size 96 \
  --icon "Display Manager Pro.app" 200 150 \
  --app-drop-link 600 150 \
  --hide-extension "Display Manager Pro.app" \
  --no-internet-enable \
  "$DMG" "$STAGE"

printf 'Created installer image: %s\n' "$DMG"
