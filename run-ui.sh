#!/bin/zsh
set -eu
PROJECT_DIR="${0:A:h}"
UI_PYTHON="$PROJECT_DIR/.venv/bin/python"
ARRANGEMENT_SOURCE="$PROJECT_DIR/tools/display-arrangement.m"
ARRANGEMENT_HELPER="$PROJECT_DIR/.build/display-arrangement"
IDENTIFY_SOURCE="$PROJECT_DIR/tools/display-identify-overlay.m"
IDENTIFY_HELPER="$PROJECT_DIR/.build/display-identify-overlay"
FRAME_SOURCE="$PROJECT_DIR/tools/display-frame-output.swift"
FRAME_HELPER="$PROJECT_DIR/.build/display-frame-output"
if [[ ! -x "$UI_PYTHON" ]]; then
  print -u2 "UI Python environment not found: $UI_PYTHON"
  exit 1
fi
if [[ ! -x "$ARRANGEMENT_HELPER" || "$ARRANGEMENT_SOURCE" -nt "$ARRANGEMENT_HELPER" ]]; then
  mkdir -p "$PROJECT_DIR/.build"
  /usr/bin/clang -fobjc-arc -framework Foundation -framework CoreGraphics \
    "$ARRANGEMENT_SOURCE" -o "$ARRANGEMENT_HELPER"
fi
if [[ ! -x "$IDENTIFY_HELPER" || "$IDENTIFY_SOURCE" -nt "$IDENTIFY_HELPER" ]]; then
  mkdir -p "$PROJECT_DIR/.build"
  /usr/bin/clang -fobjc-arc -framework Foundation -framework AppKit \
    "$IDENTIFY_SOURCE" -o "$IDENTIFY_HELPER"
fi
if [[ ! -x "$FRAME_HELPER" || "$FRAME_SOURCE" -nt "$FRAME_HELPER" ]]; then
  mkdir -p "$PROJECT_DIR/.build"
  /usr/bin/swiftc -parse-as-library -O \
    -module-cache-path "$PROJECT_DIR/.build/swift-module-cache" \
    -framework AppKit -framework ScreenCaptureKit -framework CoreGraphics \
    -framework CoreImage -framework CoreVideo -framework CoreMedia \
    "$FRAME_SOURCE" -o "$FRAME_HELPER"
fi
cd "$PROJECT_DIR"
exec "$UI_PYTHON" -u "$PROJECT_DIR/display_mode_ui.py"
