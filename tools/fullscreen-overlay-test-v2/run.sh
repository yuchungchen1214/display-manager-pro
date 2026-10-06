#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
APP="$ROOT/All-Display Overlay Test v2.app"
CONTENTS="$APP/Contents"

mkdir -p "$CONTENTS/MacOS"
/usr/bin/swiftc -parse-as-library -O -framework AppKit \
  "$ROOT/main.swift" -o "$CONTENTS/MacOS/FullscreenOverlayTestV2"
/bin/cp "$ROOT/Info.plist" "$CONTENTS/Info.plist"
/usr/bin/codesign --force --deep --sign - "$APP"
/usr/bin/open -n "$APP"
