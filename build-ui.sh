#!/bin/zsh
set -euo pipefail
ROOT="/Users/yuchungchen/Documents/PycharmProjects/display_mode_probe"
PYTHON="/Users/yuchungchen/Documents/PycharmProjects/colourspace_patch_rx/.venv/bin/python"
APP_VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
DIST="${DISPLAY_MODE_DIST_DIR:-$ROOT/dist-v$APP_VERSION}"
WORK="${DISPLAY_MODE_BUILD_DIR:-$ROOT/build/pyinstaller-v$APP_VERSION}"
APP="$DIST/Display Manager Pro.app"
BUILD_LOG="/tmp/display-manager-pro-build.log"
if [[ -e "$DIST" ]]; then
  print -u2 "Refusing to overwrite existing build directory: $DIST"
  print -u2 "Choose a new DISPLAY_MODE_DIST_DIR to create another build."
  exit 1
fi
mkdir -p "$ROOT/.build"
clang -fobjc-arc -framework Foundation -framework QuartzCore -framework ApplicationServices \
  -framework CoreGraphics -framework ColorSync "$ROOT/tools/connection-probe.m" \
  -o "$ROOT/.build/connection-probe"
clang -fobjc-arc -framework Foundation -framework IOKit \
  "$ROOT/tools/hardware-probe.m" -o "$ROOT/.build/hardware-probe"
clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  "$ROOT/tools/display-mode-switch.m" -o "$ROOT/.build/display-mode-switch"
clang -fobjc-arc -framework Foundation -framework QuartzCore -framework CoreGraphics \
  "$ROOT/tools/display-color-mode-switch.m" -o "$ROOT/.build/display-color-mode-switch"
clang -fobjc-arc -framework Foundation -framework QuartzCore -framework CoreGraphics \
  "$ROOT/tools/display-all-mode-switch.m" -o "$ROOT/.build/display-all-mode-switch"
clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  "$ROOT/tools/display-rotation-switch.m" -o "$ROOT/.build/display-rotation-switch"
clang -fobjc-arc -framework Foundation -framework CoreGraphics \
  "$ROOT/tools/display-arrangement.m" -o "$ROOT/.build/display-arrangement"
clang -fobjc-arc -framework Foundation -framework AppKit \
  "$ROOT/tools/display-identify-overlay.m" -o "$ROOT/.build/display-identify-overlay"
clang -dynamiclib -fobjc-arc -framework AppKit \
  "$ROOT/tools/display-shortcut-monitor.m" -o "$ROOT/.build/display-shortcut-monitor.dylib"
clang -fobjc-arc -framework Foundation -framework ColorSync \
  "$ROOT/tools/list-icc-profiles.m" -o "$ROOT/.build/list-icc-profiles"
clang -fobjc-arc -framework Foundation -framework CoreGraphics -framework ColorSync \
  "$ROOT/tools/apply-icc-profile.m" -o "$ROOT/.build/apply-icc-profile"
"$PYTHON" -m PyInstaller \
  --windowed \
  --noconfirm \
  --name "Display Manager Pro" \
  --icon "$ROOT/assets/icon.icns" \
  --add-data "$ROOT/assets/icon.png:assets" \
  --add-data "$ROOT/VERSION:." \
  --add-data "$ROOT/LICENSE:." \
  --add-data "$ROOT/THIRD_PARTY_NOTICES.md:." \
  --add-binary "$ROOT/.build/connection-probe:." \
  --add-binary "$ROOT/.build/hardware-probe:." \
  --add-binary "$ROOT/.build/display-mode-switch:." \
  --add-binary "$ROOT/.build/display-color-mode-switch:." \
  --add-binary "$ROOT/.build/display-all-mode-switch:." \
  --add-binary "$ROOT/.build/display-rotation-switch:." \
  --add-binary "$ROOT/.build/display-arrangement:." \
  --add-binary "$ROOT/.build/display-identify-overlay:." \
  --add-binary "$ROOT/.build/display-shortcut-monitor.dylib:." \
  --add-binary "$ROOT/.build/list-icc-profiles:." \
  --add-binary "$ROOT/.build/apply-icc-profile:." \
  --distpath "$DIST" \
  --workpath "$WORK" \
  --specpath "$WORK" \
  --osx-bundle-identifier "com.wharts.displaymodeinspector" \
  "$ROOT/display_mode_ui.py" >"$BUILD_LOG" 2>&1 || {
    tail -60 "$BUILD_LOG"
    exit 1
  }
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$APP/Contents/Info.plist"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
printf 'Build complete: %s\n' "$APP"
