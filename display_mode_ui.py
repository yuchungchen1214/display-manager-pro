#!/usr/bin/env python3
"""PySide6 display inspector, styled to match ColourSpace Patch Rx."""
from __future__ import annotations

import json
import html
import os
import re
import ctypes
import math
import time
import subprocess
import sys
from urllib.parse import unquote, urlparse
import capture as capture_service
import diagnostics as diagnostics_service
from desktop_modes import format_hz, preferred_mode_for_resolution, resolution_groups
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess, QEvent, QPointF, QRectF, QObject, QSettings
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QCursor, QFont, QFontMetrics, QPainter, QPixmap, QPen, QPalette, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QMenu,
    QScrollArea, QSplitter, QStackedWidget, QStatusBar, QTabWidget, QSizePolicy, QToolTip,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QComboBox,
    QStyledItemDelegate, QStyleOptionViewItem,
)


APP_ORG = "WhARTS"
SETTINGS_NAME = "DisplayModeInspector"  # Keep existing friendly names after the app rename.
APP_TITLE = "Display Manager Pro"
SHORTCUT_HELP = (
    ("Main window", ""),
    ("Refresh displays", "R"),
    ("Open Arrange", "A"),
    ("Export report", "⌘E"),
    ("Switch tabs", "← / →"),
    ("Select display", "↑ / ↓"),
    ("Arrange", ""),
    ("Close Arrange", "A / ⌘W"),
    ("Identify displays (hold)", "I"),
    ("Cycle all Mapping: Off → translucent → opaque", "F"),
    ("Turn off all Mapping", "Esc"),
    ("Show / hide coordinates", "C"),
    ("Toggle snapping", "⌘S"),
    ("Help", ""),
    ("Show Shortcut", "⌘/"),
)


def scaled_high_dpi_icon(source: QPixmap, logical_size: int, dpr: float) -> QPixmap:
    dpr = max(1.0, float(dpr))
    physical_size = round(logical_size * dpr)
    scaled = source.scaled(physical_size, physical_size,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    return scaled


MIRROR_RESOLUTION_UNAVAILABLE = "Unavailable while mirrored."
ROOT = (Path(sys.executable).resolve().parents[4] if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parent)
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
DARK_QSS = """
QMainWindow,
QWidget#root,
QWidget#sidebar,
QWidget#detail,
QTabWidget::pane,
QAbstractScrollArea,
QAbstractScrollArea::viewport,
QStatusBar {
    background-color: #1b1b1b;
}
QWidget#tabContent {
    background-color: #1b1b1b;
}
QListWidget {
    background-color: #111111;
}
QPushButton {
    background-color: #2d2d2d;
    color: #f2f2f2;
    border: 1px solid #383838;
    border-radius: 7px;
    padding: 5px 10px;
}
QPushButton:hover:enabled {
    background-color: #3a3a3a;
}
QPushButton:pressed:enabled {
    background-color: #484848;
}
QPushButton:disabled {
    background-color: #242424;
    color: #777777;
    border-color: #2b2b2b;
}
QPushButton[refreshBusy="true"]:disabled {
    background-color: #2d2d2d;
    color: #f2f2f2;
    border-color: #383838;
}
QTextEdit,
QLineEdit,
QTableWidget {
    background-color: #111111;
    color: #f2f2f2;
}
QLineEdit {
    border: 1px solid #383838;
    selection-background-color: #555555;
}
QTableWidget {
    alternate-background-color: #1f1f1f;
    gridline-color: #333333;
    selection-background-color: #555555;
}
QHeaderView::section {
    background-color: #222222;
    color: #9a9a9a;
    border: 1px solid #333333;
}
QLabel#muted { color: #888; }
QLabel,
QLineEdit,
QTextEdit,
QListWidget,
QTableWidget,
QComboBox,
QHeaderView::section,
QTabBar::tab,
QStatusBar {
    color: #f2f2f2;
}
QTabBar::tab {
    background-color: #2d2d2d;
    color: #f2f2f2;
    border: 0;
    border-right: 1px solid #292929;
    border-radius: 0;
    padding: 5px 12px;
    font-weight: 600;
}
QTabBar::tab:first {
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
}
QTabBar::tab:last {
    border-right: 0;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}
QTabBar::tab:selected,
QTabBar::tab:selected:!active {
    background-color: #555555;
    color: #f2f2f2;
}
QListWidget::item {
    padding: 4px 6px;
}
QListWidget::item:selected,
QListWidget::item:selected:!active {
    background-color: #626262;
    color: #ffffff;
    font-weight: 600;
}
"""

APPEARANCE_DARK = "dark"
APPEARANCE_LIGHT = "light"
APPEARANCE_SYSTEM = "system"
_ACTIVE_APPEARANCE = APPEARANCE_DARK


def appearance_hex(value: str, appearance: str | None = None) -> str:
    """Return a UI color, inverting grayscale exactly for Light appearance."""
    appearance = appearance or _ACTIVE_APPEARANCE
    normalized = value.lower()
    if normalized in ("#d99a3e", "#a85f00"):
        return "#A85F00" if appearance == APPEARANCE_LIGHT else "#d99a3e"
    if appearance != APPEARANCE_LIGHT or not normalized.startswith("#"):
        return value
    digits = normalized[1:]
    if len(digits) == 3:
        channels = [int(digit * 2, 16) for digit in digits]
        compact = True
    elif len(digits) == 6:
        channels = [int(digits[index:index + 2], 16) for index in (0, 2, 4)]
        compact = False
    else:
        return value
    if channels[0] != channels[1] or channels[1] != channels[2]:
        return value
    inverse = 255 - channels[0]
    if compact:
        nibble = f"{inverse >> 4:x}"
        return f"#{nibble * 3}"
    return f"#{inverse:02x}{inverse:02x}{inverse:02x}"


def appearance_stylesheet(appearance: str | None = None) -> str:
    appearance = appearance or _ACTIVE_APPEARANCE
    return re.sub(r"#[0-9a-fA-F]{3,6}",
                  lambda match: appearance_hex(match.group(0), appearance), DARK_QSS)


def appearance_color(value: str) -> QColor:
    return QColor(appearance_hex(value))


class AppearanceController(QObject):
    changed = Signal(str, object, object)

    def __init__(self, app: QApplication, settings: QSettings):
        super().__init__(app)
        self.app = app
        self.settings = settings
        saved = str(settings.value("Appearance/Mode", APPEARANCE_SYSTEM)).lower()
        self.mode = saved if saved in (APPEARANCE_DARK, APPEARANCE_LIGHT,
                                      APPEARANCE_SYSTEM) else APPEARANCE_SYSTEM
        self.set_mode(self.mode, save=False)
        self.app.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)

    def set_mode(self, mode: str, save: bool = True):
        if mode not in (APPEARANCE_DARK, APPEARANCE_LIGHT, APPEARANCE_SYSTEM):
            return
        self.mode = mode
        if save:
            self.settings.setValue("Appearance/Mode", mode)
        requested_scheme = {
            APPEARANCE_DARK: Qt.ColorScheme.Dark,
            APPEARANCE_LIGHT: Qt.ColorScheme.Light,
            APPEARANCE_SYSTEM: Qt.ColorScheme.Unknown,
        }[mode]
        if self.app.styleHints().colorScheme() != requested_scheme or mode != APPEARANCE_SYSTEM:
            self.app.styleHints().setColorScheme(requested_scheme)
        self._apply_current_scheme()

    def _system_scheme_changed(self, _scheme):
        if self.mode == APPEARANCE_SYSTEM:
            self._apply_current_scheme()

    def _apply_current_scheme(self):
        global _ACTIVE_APPEARANCE, CURRENT_COLOR
        scheme = self.app.styleHints().colorScheme()
        if self.mode in (APPEARANCE_DARK, APPEARANCE_LIGHT):
            actual = self.mode
        else:
            actual = APPEARANCE_LIGHT if scheme == Qt.ColorScheme.Light else APPEARANCE_DARK
        previous = _ACTIVE_APPEARANCE
        old_accent = CURRENT_COLOR
        old_unavailable = appearance_color("#888888")
        _ACTIVE_APPEARANCE = actual
        CURRENT_COLOR = appearance_color("#d99a3e")
        new_unavailable = appearance_color("#888888")
        # Always install the app stylesheet, even when the resolved colors
        # match the module default. On startup that default can equal System's
        # current scheme, but the stylesheet has not yet been installed.
        self.app.setStyleSheet(appearance_stylesheet(actual))
        for widget in self.app.allWidgets():
            if isinstance(widget, QTableWidget):
                for row in range(widget.rowCount()):
                    for column in range(widget.columnCount()):
                        item = widget.item(row, column)
                        if item is None:
                            continue
                        color = item.foreground().color()
                        if color == old_accent:
                            item.setForeground(CURRENT_COLOR)
                        elif color == old_unavailable:
                            item.setForeground(new_unavailable)
            if isinstance(widget, QLabel) and widget.textFormat() == Qt.TextFormat.RichText:
                widget.setText(_transform_appearance_markup(
                    widget.text(), previous, actual))
            style = widget.styleSheet()
            if style:
                widget.setStyleSheet(_transform_appearance_markup(
                    style, previous, actual))
            if isinstance(widget, ModeSummaryTable) and widget.rotation_combo is not None:
                widget.rotation_combo.setPalette(QPalette())
                widget._orientation_palette = widget.rotation_combo.palette()
                widget.set_orientation_available(
                    not bool(widget._orientation_unavailable_reason))
        self.changed.emit(actual, old_accent, CURRENT_COLOR)


def _transform_appearance_markup(value: str, source: str, target: str) -> str:
    def convert(match):
        color = match.group(0)
        normalized = color.lower()
        if normalized in ("#d99a3e", "#a85f00"):
            return "#A85F00" if target == APPEARANCE_LIGHT else "#d99a3e"
        if source == target:
            return color
        digits = normalized[1:]
        if len(digits) == 3:
            channels = [int(digit * 2, 16) for digit in digits]
            compact = True
        elif len(digits) == 6:
            channels = [int(digits[index:index + 2], 16) for index in (0, 2, 4)]
            compact = False
        else:
            return color
        if channels[0] != channels[1] or channels[1] != channels[2]:
            return color
        inverse = 255 - channels[0]
        if compact:
            nibble = f"{inverse >> 4:x}"
            return f"#{nibble * 3}"
        return f"#{inverse:02x}{inverse:02x}{inverse:02x}"

    return re.sub(r"#[0-9a-fA-F]{3,6}", convert, value)


class ManualScrollTracker(QObject):
    """Remember when a user takes control of a table's vertical position."""

    _SCROLL_KEYS = {
        Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
        Qt.Key.Key_Home, Qt.Key.Key_End,
    }

    def __init__(self, table: QTableWidget, callback):
        super().__init__(table)
        self._table = table
        self._viewport = table.viewport()
        self._scrollbar = table.verticalScrollBar()
        self._callback = callback
        table.installEventFilter(self)
        self._viewport.installEventFilter(self)
        self._scrollbar.installEventFilter(self)
        self._scrollbar.sliderPressed.connect(callback)
        self._scrollbar.actionTriggered.connect(lambda _action: callback())

    def eventFilter(self, watched, event):
        if watched is self._table or watched is self._viewport:
            if event.type() == QEvent.Type.Wheel:
                self._callback()
            elif (event.type() == QEvent.Type.KeyPress and
                  event.key() in self._SCROLL_KEYS):
                self._callback()
        elif (watched is self._scrollbar and
              event.type() == QEvent.Type.MouseButtonPress):
            self._callback()
        return False


class ApplicationShortcutFilter(QObject):
    """Route app shortcuts without stealing keys from editors and tables."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    @staticmethod
    def _focus_ancestors(widget):
        while widget is not None:
            yield widget
            widget = widget.parentWidget()

    def _main_focus_context(self):
        # Only text-entry controls keep unmodified letters/arrows for editing.
        # Tables, scroll views, and scrollbars must not swallow app navigation.
        if self._is_text_entry(self.owner):
            return "text-entry"
        return "normal"

    @classmethod
    def _is_text_entry(cls, owner):
        if (isinstance(QApplication.activePopupWidget(), QMenu) or
                isinstance(QApplication.activeWindow(), QMenu)):
            return False
        focus = QApplication.focusWidget()
        ancestors = tuple(cls._focus_ancestors(focus))
        return any(isinstance(widget, QLineEdit) or
                   (isinstance(widget, QTextEdit) and not widget.isReadOnly())
                   for widget in ancestors)

    @classmethod
    def _menu_belongs_to(cls, menu, window):
        return (isinstance(menu, QMenu) and
                any(widget is window for widget in cls._focus_ancestors(menu)))

    @classmethod
    def _active_menu_for(cls, window):
        popup = QApplication.activePopupWidget()
        if popup is not None:
            return popup if cls._menu_belongs_to(popup, window) else None
        active = QApplication.activeWindow()
        if cls._menu_belongs_to(active, window):
            return active
        focus = QApplication.focusWidget()
        focus_window = focus.window() if focus is not None else None
        if cls._menu_belongs_to(focus_window, window):
            return focus_window
        return None

    @classmethod
    def _dismiss_active_menu(cls, window):
        menu = cls._active_menu_for(window)
        if menu is None:
            return
        while isinstance(menu.parentWidget(), QMenu):
            menu = menu.parentWidget()
        menu.hide()

    @classmethod
    def _has_focus_in(cls, window):
        popup = QApplication.activePopupWidget()
        if popup is not None:
            return cls._menu_belongs_to(popup, window)
        if cls._active_menu_for(window) is not None:
            return True
        focus = QApplication.focusWidget()
        return (QApplication.activeWindow() is window or
                (focus is not None and focus.window() is window))

    @staticmethod
    def _normalized_modifiers(event):
        relevant = (Qt.KeyboardModifier.ShiftModifier |
                    Qt.KeyboardModifier.ControlModifier |
                    Qt.KeyboardModifier.AltModifier |
                    Qt.KeyboardModifier.MetaModifier)
        return event.modifiers() & relevant

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.EnabledChange and
                watched in (self.owner.refresh_button, self.owner.arrange_button,
                            self.owner.export_button)):
            self.owner._sync_shortcut_action_state()
            return False
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        modifiers = self._normalized_modifiers(event)
        if not self._has_focus_in(self.owner):
            return False
        focus_context = self._main_focus_context()
        if modifiers == Qt.KeyboardModifier.NoModifier:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and focus_context != "text-entry":
                delta = -1 if key == Qt.Key.Key_Left else 1
                next_index = self.owner.tabs.currentIndex() + delta
                if 0 <= next_index < self.owner.tabs.count():
                    self._dismiss_active_menu(self.owner)
                    self.owner.tabs.setCurrentIndex(next_index)
                event.accept()
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and focus_context != "text-entry":
                delta = -1 if key == Qt.Key.Key_Up else 1
                row = self.owner.display_list.currentRow() + delta
                if 0 <= row < self.owner.display_list.count():
                    self._dismiss_active_menu(self.owner)
                    self.owner.display_list.setCurrentRow(row)
                event.accept()
                return True
        return False


class MacLetterShortcutMonitor:
    """Handle local AppKit key events before a Chinese IME consumes them."""

    _LETTERS = {0: "A", 3: "F", 8: "C", 15: "R", 34: "I"}
    _CALLBACK = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_ushort, ctypes.c_bool,
                                 ctypes.c_bool)

    def __init__(self, owner):
        self.owner = owner
        if getattr(sys, "frozen", False):
            library_path = RESOURCE_ROOT / "display-shortcut-monitor.dylib"
        else:
            source_path = ROOT / "tools" / "display-shortcut-monitor.m"
            library_path = ROOT / ".build" / "display-shortcut-monitor.dylib"
            if (not library_path.exists() or
                    source_path.stat().st_mtime > library_path.stat().st_mtime):
                library_path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run([
                    "/usr/bin/clang", "-dynamiclib", "-fobjc-arc",
                    "-framework", "AppKit", str(source_path), "-o", str(library_path),
                ], check=True, capture_output=True, text=True)
        self._library = ctypes.CDLL(str(library_path))
        self._library.DMIInstallShortcutMonitor.argtypes = [self._CALLBACK]
        self._library.DMIInstallShortcutMonitor.restype = ctypes.c_bool
        self._library.DMIRemoveShortcutMonitor.argtypes = []
        self._callback = self._CALLBACK(self._handle_key)
        if not self._library.DMIInstallShortcutMonitor(self._callback):
            raise RuntimeError("Could not install macOS local key monitor")

    def close(self):
        self._library.DMIRemoveShortcutMonitor()

    def _handle_key(self, key_code: int, is_key_down: bool,
                    command_only: bool = False) -> bool:
        dialog = self.owner.arrangement_dialog
        if not is_key_down:
            if key_code == 34 and dialog is not None and dialog._identify_keyboard_held:
                dialog._set_identify_keyboard_held(False)
                return True
            return False
        if command_only:
            if key_code == 44:
                shortcuts = getattr(self.owner, "shortcuts_dialog", None)
                if shortcuts is not None and ApplicationShortcutFilter._has_focus_in(shortcuts):
                    self.owner.show_shortcuts_dialog()
                    return True
                if self._arrange_has_focus(dialog):
                    ApplicationShortcutFilter._dismiss_active_menu(dialog)
                    self.owner.show_shortcuts_dialog()
                    return True
                if ApplicationShortcutFilter._has_focus_in(self.owner):
                    ApplicationShortcutFilter._dismiss_active_menu(self.owner)
                    self.owner.show_shortcuts_dialog()
                    return True
                return False
            if self._arrange_has_focus(dialog):
                if key_code == 1:
                    dialog.toggle_snap()
                    return True
                if key_code == 13:
                    dialog.close_with_shortcut()
                    return True
            if key_code == 14 and ApplicationShortcutFilter._has_focus_in(self.owner):
                button = self.owner.export_button
                if button.isEnabled():
                    ApplicationShortcutFilter._dismiss_active_menu(self.owner)
                    button.click()
                    return True
            return False
        letter = self._LETTERS.get(key_code)
        if letter is None:
            return False
        return self._dispatch(letter)

    @staticmethod
    def _arrange_has_focus(dialog) -> bool:
        return bool(dialog is not None and dialog.isVisible() and
                    ApplicationShortcutFilter._has_focus_in(dialog))

    def _dispatch(self, letter: str) -> bool:
        dialog = self.owner.arrangement_dialog
        if self._arrange_has_focus(dialog):
            if ApplicationShortcutFilter._is_text_entry(dialog):
                return False
            if letter == "A":
                dialog.close_with_shortcut()
                return True
            if letter == "C":
                dialog.toggle_coordinates()
                return True
            if letter == "F":
                dialog.cycle_all_mapping()
                return True
            if letter == "I" and dialog.identify_button.isEnabled():
                dialog._set_identify_keyboard_held(True)
                return True
            return False
        if not ApplicationShortcutFilter._has_focus_in(self.owner):
            return False
        if ApplicationShortcutFilter._is_text_entry(self.owner):
            return False
        button = {"R": self.owner.refresh_button,
                  "A": self.owner.arrange_button}.get(letter)
        if button is None:
            return False
        if not button.isEnabled():
            return False
        ApplicationShortcutFilter._dismiss_active_menu(self.owner)
        button.click()
        return True


def install_manual_scroll_tracking(owner, table: QTableWidget):
    owner._manual_current_scroll = False
    owner._manual_scroll_tracker = ManualScrollTracker(
        table, lambda: setattr(owner, "_manual_current_scroll", True))


def reset_manual_scroll_tracking(owner):
    owner._manual_current_scroll = False


def scroll_item_into_view(table: QTableWidget, item: QTableWidgetItem | None):
    if item is None:
        return
    item_rect = table.visualItemRect(item)
    if not table.viewport().rect().contains(item_rect):
        table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)


CURRENT_COLOR = appearance_color("#d99a3e")
ARRANGEMENT_HIGHLIGHT_COLOR = QColor("#D99A3E")
MIRROR_CARD_OFFSET = 5


class ExplicitForegroundDelegate(QStyledItemDelegate):
    """Keep explicitly colored table text visible when its row is selected."""

    def initStyleOption(self, option: QStyleOptionViewItem, index):
        super().initStyleOption(option, index)
        # Qt/macOS may supply a white HighlightedText brush even when the app
        # is using the inverted Light palette. Keep ordinary selected rows
        # readable, while allowing explicit per-item colors (such as current
        # mode orange) to take precedence below.
        brush = QBrush(appearance_color("#f2f2f2"))
        foreground = index.data(Qt.ItemDataRole.ForegroundRole)
        if foreground is not None:
            explicit_brush = (foreground if isinstance(foreground, QBrush)
                              else QBrush(QColor(foreground)))
            if (explicit_brush.style() != Qt.BrushStyle.NoBrush
                    and explicit_brush.color().isValid()):
                brush = explicit_brush
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            option.palette.setBrush(group, QPalette.ColorRole.HighlightedText, brush)


def install_explicit_foreground_delegate(table: QTableWidget):
    table.setItemDelegate(ExplicitForegroundDelegate(table))


class DoubleClickLabel(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def set_current_appearance(item: QTableWidgetItem, current: bool, marker: bool = False):
    if not current:
        return
    item.setForeground(CURRENT_COLOR)
    if marker:
        font = item.font()
        font.setPointSize(max(8, font.pointSize() - 2))
        item.setFont(font)


def mode_encoding(mode: dict) -> str:
    if mode.get("colorMode") is None:
        return ""
    color = str(mode["colorMode"])
    if "YCbCr444" in color:
        return "YCbCr 4:4:4"
    if "YCbCr422" in color or "422" in color:
        return "YCbCr 4:2:2"
    if "YCbCr420" in color:
        return "YCbCr 4:2:0"
    if color.startswith("RGB") or "_RGB" in color:
        return "RGB 4:4:4"
    return color


def mode_range(mode: dict) -> str:
    if mode.get("colorMode") is None:
        return ""
    color = str(mode["colorMode"])
    if "LimitedRange" in color:
        return "Limited"
    return "Full" if "FullRange" in color else ""


def mode_text(mode: dict) -> str:
    parts = []
    if mode.get('width') is not None and mode.get('height') is not None:
        parts.append(f"{mode['width']} × {mode['height']}")
    if mode.get('refreshRate') is not None:
        parts.append(format_hz(mode['refreshRate']))
    parts.extend(value for value in (mode_encoding(mode), mode_range(mode)) if value)
    if mode.get('bitDepth') is not None:
        parts.append(f"{mode['bitDepth']}-bit")
    if mode.get('hdrMode') is not None:
        parts.append(str(mode['hdrMode']))
    return "  ·  ".join(parts)


def format_refresh(rate) -> str:
    return format_hz(rate)


def natural_sort_key(value):
    """Sort mixed numeric/text table values by their displayed meaning."""
    if value is None:
        return ((1, ""),)
    text = str(value).strip().casefold()
    parts = re.findall(r"\d+(?:\.\d+)?|\D+", text)
    return tuple((0, float(part)) if part[0].isdigit() else (1, part.strip())
                 for part in parts) or ((1, ""),)


def orientation_label(rotation) -> str:
    if rotation is None:
        return ""
    angle = int(round(float(rotation))) % 360
    return f"{angle}°"


def display_label(display: dict) -> str:
    friendly_name = str(display.get("friendlyName") or "").strip()
    if friendly_name:
        return friendly_name
    return display_original_label(display)


def display_original_label(display: dict) -> str:
    builtin = display.get("cgBuiltIn")
    if builtin is None and display.get("isExternal") is not None:
        builtin = not bool(display["isExternal"])
    if bool(builtin):
        return "Built-in Monitor"
    product_name = str(display.get("productName") or "").strip()
    if product_name:
        return product_name
    profiler_name = str(display.get("systemProfilerName") or "").strip()
    if profiler_name and profiler_name.casefold() not in {
            "monitor", "external", "display", "virtual display"}:
        return profiler_name
    core_info = display.get("coreDisplayInfo") or {}
    localized_product = core_info.get("DisplayProductName")
    if isinstance(localized_product, dict):
        localized_product = localized_product.get("en_US") or localized_product.get("en_GB")
    if localized_product and str(localized_product).casefold() not in {
            "monitor", "external", "display", "virtual display"}:
        return str(localized_product)
    return (display.get("productName") or display.get("deviceName") or
            display.get("name") or f"Display {display.get('displayId', '')}")


def display_connection_technology(display: dict) -> str:
    """Return only a technology explicitly identified by system metadata."""
    builtin = display.get("cgBuiltIn")
    if builtin is None and display.get("isExternal") is not None:
        builtin = not bool(display.get("isExternal"))
    if builtin:
        return "Built-in"

    connection_type = str(display.get("systemProfilerConnectionType") or "").casefold()
    normalized = connection_type.removeprefix("spdisplays_").replace("_", " ").strip()
    if normalized in {"airplay", "sidecar"}:
        return "AirPlay / Sidecar"
    if normalized in {"internal", "built in", "built-in"}:
        return "Built-in"
    virtual = str(display.get("systemProfilerVirtualDevice") or "").casefold()
    if virtual in {"spdisplays_yes", "yes", "true", "1"}:
        return "Virtual display"
    return "-"


def display_connection_interface(display: dict) -> str:
    """Normalize explicit interface reports; generic "other" means unknown."""
    if display_connection_technology(display) in {"Built-in", "AirPlay / Sidecar", "Virtual display"}:
        return "-"

    labels = {
        "dp": "DisplayPort (DP)", "displayport": "DisplayPort (DP)",
        "hdmi": "HDMI", "usb-c": "USB-C", "usbc": "USB-C",
        "thunderbolt": "Thunderbolt", "dvi": "DVI", "vga": "VGA",
    }
    unknown_values = {"", "other", "unknown", "none", "unavailable", "not-available",
                      "not-reported", "virtual"}
    for source in (display.get("transportType"), display.get("systemProfilerConnectionType")):
        value = str(source or "").strip().casefold()
        value = value.removeprefix("spdisplays_").replace("_", "-")
        if value in labels:
            return labels[value]
        if value not in unknown_values:
            # Preserve future explicit system values instead of collapsing them
            # into a misleading generic "Other" label.
            return value.replace("-", " ").title()

    connection = display.get("connectionPath") or {}
    port_type = str(connection.get("osPortType") or "").strip().casefold()
    link = connection.get("activeLink") or {}
    link_description = str(link.get("TransportDescription") or "").casefold()
    if port_type == "hdmi":
        return "HDMI"
    if "displayport" in link_description:
        return "DisplayPort (USB-C)" if port_type == "usb-c" else "DisplayPort (DP)"
    return "-"


def display_role_label(display: dict, displays: list[dict]) -> str | None:
    desktop = display.get("desktop") or {}
    if desktop.get("main") is None:
        return None
    if bool(desktop["main"]):
        return "Main Display"
    mirror_id = desktop.get("mirrorsDisplayID")
    try:
        mirror_id = int(mirror_id)
    except (TypeError, ValueError):
        mirror_id = 0
    if mirror_id:
        target = next((candidate for candidate in displays
                       if str(candidate.get("displayId")) == str(mirror_id)), None)
        target_name = display_label(target) if target else f"Display {mirror_id}"
        return f"Mirror for {target_name}"
    return "Extended Display"


def display_role_menu_options(display: dict, displays: list[dict]) -> list[tuple[str, str, str, bool, bool]]:
    """Return (label, role, source ID, checked, enabled) choices for one output."""
    desktop = display.get("desktop") or {}
    display_id = str(display.get("displayId") or "")
    mirror_id = str(display.get("mirrorsDisplayID",
                                desktop.get("mirrorsDisplayID", "0")) or "0")
    is_main = bool(display.get("main", desktop.get("main", False)))
    is_mirrored = mirror_id not in ("", "0", display_id)
    selected_root = mirror_id if is_mirrored else display_id
    has_another_display = any(
        str(candidate.get("displayId") or "") != display_id
        for candidate in displays
    )
    choices = [
        ("Main Display", "main", "", is_main, not is_main),
        ("Extended Display", "extended", "",
         not is_main and not is_mirrored,
         (is_main and has_another_display) or is_mirrored),
    ]
    for candidate in displays:
        candidate_id = str(candidate.get("displayId") or "")
        if not candidate_id or candidate_id == display_id:
            continue
        candidate_desktop = candidate.get("desktop") or {}
        candidate_mirror = str(candidate.get(
            "mirrorsDisplayID", candidate_desktop.get("mirrorsDisplayID", "0")) or "0")
        candidate_root = candidate_mirror if candidate_mirror not in ("", "0") else candidate_id
        if candidate_root == selected_root:
            if is_mirrored and candidate_id == mirror_id:
                choices.append((f"Mirror for {display_label(candidate)}", "mirror",
                                candidate_id, True, False))
            continue
        is_current_target = is_mirrored and mirror_id == candidate_root
        choices.append((f"Mirror for {display_label(candidate)}", "mirror", candidate_id,
                        is_current_target, not is_current_target))
    return choices


def display_mirror_roles(display: dict, displays: list[dict]) -> tuple[bool, bool]:
    """Return (is mirrored by another display, mirrors other displays)."""
    display_id = str(display.get("displayId") or "")
    desktop = display.get("desktop") or {}
    mirror_id = display.get("mirrorsDisplayID")
    if mirror_id is None:
        mirror_id = desktop.get("mirrorsDisplayID")
    try:
        mirror_id = int(mirror_id or 0)
    except (TypeError, ValueError):
        mirror_id = 0
    is_mirror_slave = mirror_id != 0 and str(mirror_id) != display_id

    has_mirror_children = False
    for candidate in displays:
        candidate_id = str(candidate.get("displayId") or "")
        if not candidate_id or candidate_id == display_id:
            continue
        candidate_desktop = candidate.get("desktop") or {}
        candidate_mirror_id = candidate.get("mirrorsDisplayID")
        if candidate_mirror_id is None:
            candidate_mirror_id = candidate_desktop.get("mirrorsDisplayID")
        try:
            has_mirror_children |= int(candidate_mirror_id or 0) == int(display_id)
        except (TypeError, ValueError):
            continue
    return is_mirror_slave, has_mirror_children


def all_mode_availability(mode: dict, current_mode: dict | None,
                         is_mirror_slave: bool) -> tuple[bool, str]:
    """Whether an All modes entry can be applied under the mirror policy."""
    if current_mode and is_current_mode(mode, current_mode.get("modeID")):
        return False, ""
    required = ("modeID", "width", "height", "refreshRate", "colorMode", "bitDepth", "hdrMode")
    if any(mode.get(key) is None for key in required):
        return False, "Mode details unavailable."
    if is_mirror_slave:
        # A mirror receiver may change refresh rate only. The driver mode ID
        # necessarily changes with timing, so compare every other reported
        # image property rather than the opaque identifier.
        fields = ("width", "height", "colorMode", "bitDepth", "hdrMode", "isVRR",
                  "colorGamut", "pixelAspectRatio", "preferredScale")
        if current_mode is None or any(mode.get(key) != current_mode.get(key) for key in fields):
            return False, MIRROR_RESOLUTION_UNAVAILABLE
    return True, ""


def availability_sort_value(current: bool, selectable: bool = True) -> int:
    """Sort key for the first-column marker, without changing its appearance."""
    if current:
        return 1
    return 0 if selectable else -1


def same_desktop_resolution(first: dict, second: dict) -> bool:
    """Compare both desktop dimensions and framebuffer pixels."""
    fields = ("width", "height", "pixelWidth", "pixelHeight")
    return all(first.get(field) == second.get(field) for field in fields)


def resolution_rows_for_display(display: dict, displays: list[dict]) -> list[dict]:
    rows = resolution_groups(display)
    is_mirror_slave, _has_mirror_children = display_mirror_roles(display, displays)
    for row in rows:
        # Keep every enumerated resolution visible; a mirror receiver may only
        # change refresh rate, so its other resolution rows are informational.
        row["selectable"] = not is_mirror_slave or bool(row.get("current"))
        row["refreshRateOnly"] = is_mirror_slave
        if is_mirror_slave and not row.get("current"):
            row["unavailableReason"] = MIRROR_RESOLUTION_UNAVAILABLE
    return rows


def display_identity_key(display: dict) -> str:
    for key in ("uniqueId", "cgUUID"):
        value = str(display.get(key) or "").strip()
        if value:
            return value
    return f"display-{display.get('displayId', '')}"


def icc_profile_display_name(profile: dict | None) -> str:
    profile = profile or {}
    name = str(profile.get("description") or profile.get("name") or "").strip()
    if name:
        return name
    path = unquote(urlparse(str(profile.get("url") or "")).path)
    return Path(path).name if path else ""


def display_tooltip(display: dict) -> str:
    parts = []
    if str(display.get("friendlyName") or "").strip():
        parts.append(display_original_label(display))
    if isinstance(display.get("cgBuiltIn"), bool):
        parts.append("Built-in Monitor" if display["cgBuiltIn"] else "External")
    desktop_mode = display.get("framebufferMode") or {}
    if desktop_mode.get("width") is not None and desktop_mode.get("height") is not None:
        parts.append(f"{desktop_mode['width']} × {desktop_mode['height']}")
    current_mode = display.get("currentMode") or {}
    color_details = [
        value for value in (
            mode_encoding(current_mode),
            mode_range(current_mode),
            f"{current_mode['bitDepth']}-bit" if current_mode.get("bitDepth") is not None else "",
            str(current_mode.get("hdrMode") or ""),
        ) if value
    ]
    if color_details:
        parts.append(" · ".join(color_details))
    return " · ".join(parts)


def arrangement_display_records(displays: list[dict]) -> tuple[list[dict], int]:
    screens = []
    omitted = 0
    for display in displays:
        desktop = display.get("desktop") or {}
        keys = ("x", "y", "width", "height")
        if any(desktop.get(key) is None for key in keys):
            omitted += 1
            continue
        try:
            x, y, width, height = (float(desktop[key]) for key in keys)
        except (TypeError, ValueError):
            omitted += 1
            continue
        if width <= 0 or height <= 0:
            omitted += 1
            continue
        builtin = display.get("cgBuiltIn")
        if builtin is None and display.get("isExternal") is not None:
            builtin = not bool(display["isExternal"])
        name = display_label(display)
        screens.append({
            "name": name, "originalName": display_original_label(display),
            "friendlyName": str(display.get("friendlyName") or "").strip(),
            "x": x, "y": y, "width": width, "height": height,
            "main": bool(desktop.get("main")), "displayId": display.get("displayId"),
            "mirrorsDisplayID": desktop.get("mirrorsDisplayID"),
            "desktopModes": display.get("desktopModes") or [],
            "availableModes": display.get("availableModes") or [],
            "currentMode": display.get("currentMode") or {},
            "framebufferMode": display.get("framebufferMode") or {},
            "rotation": desktop.get("rotation"),
            "colorProfile": display.get("colorProfile") or {},
        })
    return screens, omitted


def identify_overlay_groups(screens: list[dict]) -> list[dict]:
    """Group mirrored outputs so each shared image receives one stable label panel."""
    groups: dict[str, dict] = {}
    for screen in screens:
        display_id = str(screen.get("displayId") or "")
        try:
            mirror_id = int(screen.get("mirrorsDisplayID") or 0)
        except (TypeError, ValueError):
            mirror_id = 0
        group_id = str(mirror_id) if mirror_id and str(mirror_id) != display_id else display_id
        if not display_id or not group_id:
            continue
        group = groups.setdefault(group_id, {"groupID": group_id, "displayIDs": [], "members": []})
        group["displayIDs"].append(display_id)
        original_name = str(screen.get("originalName") or screen.get("name") or "Display")
        friendly_name = str(screen.get("friendlyName") or "").strip()
        group["members"].append({
            "displayID": display_id,
            # Send one final label, not both source fields. A helper already
            # running an older renderer may still draw friendlyName in its
            # former smaller style after receiving a live configure message.
            "name": friendly_name or original_name,
            "friendlyName": "",
        })
    # Keep the mirror master first regardless of capture/enumeration order.
    # The overlay represents the shared output, so its label comes from this
    # one display rather than from a receiver in the mirror group.
    for group in groups.values():
        paired = list(zip(group["displayIDs"], group["members"]))
        paired.sort(key=lambda entry: entry[0] != group["groupID"])
        group["displayIDs"] = [display_id for display_id, _member in paired]
        group["members"] = [member for _display_id, member in paired]
    return list(groups.values())


def identify_overlay_helper_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ROOT)) / "display-identify-overlay"
    return ROOT / ".build" / "display-identify-overlay"


def mirror_group_name_lines(members: list[dict]) -> list[str]:
    names = [str(member.get("name") or "Display") for member in members]
    return names[:3] + ["..."] if len(names) > 4 else names


def arrangement_resolution_text(screen: dict) -> str:
    mode = screen.get("framebufferMode") or {}
    width = mode.get("width") or screen.get("width")
    height = mode.get("height") or screen.get("height")
    if width is None or height is None:
        return "Resolution unavailable"
    text = f"{int(width)} × {int(height)}"
    rotation = screen.get("rotation")
    if rotation is not None and int(round(float(rotation))) % 360:
        text += f", {int(round(float(rotation))) % 360}°"
    return text


def arrangement_layout_signature(records: list[dict]) -> tuple:
    """Geometry signature for the visible arrangement, normalizing mirror sets."""
    by_id = {str(screen.get("displayId")): screen for screen in records}
    signature = []
    for screen in records:
        display_id = str(screen.get("displayId"))
        mirror_id = str(screen.get("mirrorsDisplayID") or "0")
        master = by_id.get(mirror_id) if mirror_id not in ("", "0") else None
        geometry = master if master is not None else screen
        signature.append((display_id, int(geometry.get("x", 0)),
                          int(geometry.get("y", 0)), int(geometry.get("width", 0)),
                          int(geometry.get("height", 0)), bool(screen.get("main")),
                          mirror_id))
    return tuple(sorted(signature))


def arrangement_helper_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ROOT)) / "display-arrangement"
    return ROOT / ".build" / "display-arrangement"


def icc_profiles_helper_binary() -> Path:
    if getattr(sys, "frozen", False):
        return RESOURCE_ROOT / "list-icc-profiles"
    binary = ROOT / ".build" / "list-icc-profiles"
    source = ROOT / "tools" / "list-icc-profiles.m"
    if (not binary.is_file() or
            source.stat().st_mtime_ns > binary.stat().st_mtime_ns):
        binary.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "/usr/bin/clang", "-fobjc-arc", "-framework", "Foundation",
            "-framework", "ColorSync", str(source), "-o", str(binary),
        ], check=True, capture_output=True, text=True, timeout=60)
    return binary


def icc_apply_helper_binary() -> Path:
    if getattr(sys, "frozen", False):
        return RESOURCE_ROOT / "apply-icc-profile"
    binary = ROOT / ".build" / "apply-icc-profile"
    source = ROOT / "tools" / "apply-icc-profile.m"
    if (not binary.is_file() or
            source.stat().st_mtime_ns > binary.stat().st_mtime_ns):
        binary.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "/usr/bin/clang", "-fobjc-arc", "-framework", "Foundation",
            "-framework", "CoreGraphics", "-framework", "ColorSync",
            str(source), "-o", str(binary),
        ], check=True, capture_output=True, text=True, timeout=60)
    return binary


class DisplayReconfigurationWatcher(QObject):
    changed = Signal(int, int)
    _CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    _BEGIN_CONFIGURATION = 1 << 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self._callback = self._CALLBACK(self._display_changed)
        self._user_info = ctypes.c_void_p()
        self._register = self._core_graphics.CGDisplayRegisterReconfigurationCallback
        self._register.argtypes = [self._CALLBACK, ctypes.c_void_p]
        self._register.restype = ctypes.c_int32
        self._remove = self._core_graphics.CGDisplayRemoveReconfigurationCallback
        self._remove.argtypes = [self._CALLBACK, ctypes.c_void_p]
        self._remove.restype = ctypes.c_int32
        result = self._register(self._callback, self._user_info)
        if result != 0:
            raise OSError(f"CoreGraphics callback registration failed ({result})")
        self._registered = True

    def _display_changed(self, display_id, flags, _user_info):
        if flags & self._BEGIN_CONFIGURATION:
            return
        try:
            self.changed.emit(int(display_id), int(flags))
        except BaseException:
            # Exceptions must never escape a C callback boundary.
            return

    def close(self):
        if getattr(self, "_registered", False):
            self._remove(self._callback, self._user_info)
            self._registered = False


class DisplayArrangementCanvas(QWidget):
    moveRequested = Signal(str, int, int, int, int)
    primaryArrangementMoveRequested = Signal(int, int)
    primaryMoveRequested = Signal(str)
    displaySelected = Signal(str)
    displayIdentifyRequested = Signal(str)
    contextMenuRequested = Signal(object, object)
    dragStateChanged = Signal(bool)

    def __init__(self, screens: list[dict], parent=None):
        super().__init__(parent)
        self.screens = screens
        self._drag_screen = None
        self._drag_layout_screen = None
        self._drag_point = None
        self._drag_origin = None
        self._drag_primary_offset = (0, 0)
        self._drag_edge_snap_axes = (False, False)
        self._drag_frame = None
        self._selected_display_id: str | None = None
        self._front_group_id: str | None = None
        self._context_display_id: str | None = None
        self._primary_bar_dragging = False
        self._primary_drop_target = None
        self._primary_bar_point = None
        self._primary_bar_width = 0.0
        self._layout_frame = None
        self._layout_scale = 1.0
        self._preserve_layout_frame = False
        self._arrangement_busy = False
        self._coordinates_visible = False
        self._snap_enabled = True
        self._snap_distance = 40
        self._snap_grid_step = 100
        self._snap_grid_distance = 30
        self._view_origin_offset = (0, 0)
        # paintEvent fills every pixel itself; avoid Qt clearing the backing
        # surface first, which can expose a one-frame blank between updates.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(360, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_arrangement_busy(self, busy: bool):
        self._arrangement_busy = busy
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_coordinates_visible(self, visible: bool):
        self._coordinates_visible = bool(visible)
        self.update()

    def set_snap_enabled(self, enabled: bool):
        self._snap_enabled = bool(enabled)

    def _snap_drag_position(self, screen, x: int, y: int) -> tuple[int, int, bool, bool]:
        if not self._snap_enabled:
            return x, y, False, False
        snap_distance = self._snap_distance
        width = screen["width"]
        height = screen["height"]
        x_edge_candidates = []
        y_edge_candidates = []
        group_ids = self._mirror_group_ids(screen)
        for other in self.screens:
            if other is screen or str(other.get("displayId")) in group_ids:
                continue
            other_x, other_y = other["x"], other["y"]
            other_right = other_x + other["width"]
            other_bottom = other_y + other["height"]
            # Snap left/right edges when the displays' vertical spans meet,
            # and align vertical edges when their horizontal spans meet.
            if (y < other_bottom + snap_distance and
                    y + height > other_y - snap_distance):
                x_edge_candidates.extend((other_x, other_right,
                                          other_x - width, other_right - width))
            if (x < other_right + snap_distance and
                    x + width > other_x - snap_distance):
                y_edge_candidates.extend((other_y, other_bottom,
                                          other_y - height, other_bottom - height))

        def nearest(value, candidates, distance):
            close = [candidate for candidate in candidates
                     if abs(candidate - value) <= distance]
            return min(close, key=lambda candidate: abs(candidate - value)) if close else None

        def snap_axis(value, edge_candidates):
            edge_position = nearest(value, edge_candidates, snap_distance)
            grid_position = round(value / self._snap_grid_step) * self._snap_grid_step
            if abs(grid_position - value) > self._snap_grid_distance:
                grid_position = None
            # Prefer an edge snap on ties so the marker reflects alignment to
            # another display rather than an incidental grid intersection.
            choices = [(abs(position - value), position, is_edge)
                       for position, is_edge in ((edge_position, True),
                                                 (grid_position, False))
                       if position is not None]
            if not choices:
                return round(value), False
            _distance, position, is_edge = min(choices, key=lambda choice: (choice[0], not choice[2]))
            return round(position), is_edge

        snapped_x, edge_x = snap_axis(x, x_edge_candidates)
        snapped_y, edge_y = snap_axis(y, y_edge_candidates)
        return snapped_x, snapped_y, edge_x, edge_y

    def _attach_drop_to_nearest_screen(self, screen, x: int, y: int) -> tuple[int, int]:
        """Attach a released display to the nearest physical display edge."""
        width, height = int(round(screen["width"])), int(round(screen["height"]))
        group_ids = self._mirror_group_ids(screen)
        others = []
        seen = set()
        for candidate in self.screens:
            display_id = str(candidate.get("displayId"))
            if display_id in group_ids or self._is_mirror_slave(candidate):
                continue
            if display_id in seen:
                continue
            seen.add(display_id)
            others.append(candidate)
        if not others:
            return int(x), int(y)

        # Preserve the pointer-selected alignment while requiring a positive
        # edge overlap. Rectangle edges are half-open, so equality at the far
        # endpoint is still valid; the previous +/- 1 bounds introduced a
        # visible one-point gap near the top/left endpoints.
        candidates = []
        for other in others:
            ox, oy = int(round(other["x"])), int(round(other["y"]))
            ow, oh = int(round(other["width"])), int(round(other["height"]))
            min_y, max_y = oy - height, oy + oh
            min_x, max_x = ox - width, ox + ow
            if min_y <= max_y:
                aligned_y = min(max(int(y), min_y), max_y)
                candidates.extend(((ox + ow, aligned_y),
                                   (ox - width, aligned_y)))
            if min_x <= max_x:
                aligned_x = min(max(int(x), min_x), max_x)
                candidates.extend(((aligned_x, oy + oh),
                                   (aligned_x, oy - height)))

        def overlaps_any(candidate_x, candidate_y):
            for other in others:
                ox, oy = int(round(other["x"])), int(round(other["y"]))
                ow, oh = int(round(other["width"])), int(round(other["height"]))
                overlap_x = min(candidate_x + width, ox + ow) - max(candidate_x, ox)
                overlap_y = min(candidate_y + height, oy + oh) - max(candidate_y, oy)
                if overlap_x > 0 and overlap_y > 0:
                    return True
            return False

        valid = [point for point in candidates if not overlaps_any(*point)]
        if not valid:
            # Keep a valid edge placement even in dense topologies; macOS will
            # resolve any remaining multi-display constraint in its transaction.
            valid = candidates
        if not valid:
            return int(x), int(y)
        return min(valid, key=lambda point: (
            (point[0] - x) ** 2 + (point[1] - y) ** 2,
            point[1], point[0],
        ))

    def _mirror_group_ids(self, screen):
        """Return every member of the selected screen's mirror group."""
        display_id = str(screen.get("displayId"))
        try:
            mirror_id = int(screen.get("mirrorsDisplayID") or 0)
        except (TypeError, ValueError):
            mirror_id = 0
        master_id = str(mirror_id) if mirror_id and str(mirror_id) != display_id else display_id
        members = {master_id}
        members.update(
            str(candidate.get("displayId")) for candidate in self.screens
            if str(candidate.get("mirrorsDisplayID") or "0") == master_id
        )
        return members if len(members) > 1 else {display_id}

    def _mirror_group_master(self, screen):
        try:
            mirror_id = int(screen.get("mirrorsDisplayID") or 0)
        except (TypeError, ValueError):
            mirror_id = 0
        if mirror_id and str(mirror_id) != str(screen.get("displayId")):
            return next((candidate for candidate in self.screens
                         if str(candidate.get("displayId")) == str(mirror_id)), screen)
        return screen

    def _primary_target_at(self, point):
        hit = self._hit_test_screen(point)
        if hit is not None:
            return self._mirror_group_master(hit)
        return next((screen for screen in self.screens if screen.get("main")), None)

    def fit_to_view(self):
        self._view_origin_offset = (0, 0)
        self._layout_frame = None
        self._preserve_layout_frame = False
        self.update()

    def _cards_fully_visible(self) -> bool:
        visible = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        rects = self._geometry()
        for screen in self._screen_paint_order():
            rect = rects.get(str(screen.get("displayId")))
            if rect is None:
                return False
            stack_extension = (max(0, len(self._mirror_group_members(screen)) - 1) *
                               MIRROR_CARD_OFFSET)
            if (rect.left() < visible.left() or rect.top() < visible.top() or
                    rect.right() + stack_extension > visible.right() or
                    rect.bottom() + stack_extension > visible.bottom()):
                return False
        return True

    def resizeEvent(self, event):
        self._layout_frame = None
        self._preserve_layout_frame = False
        super().resizeEvent(event)

    def _geometry(self):
        by_id = {str(screen.get("displayId")): screen for screen in self.screens}
        visual_source = {}
        for screen in self.screens:
            display_id = str(screen.get("displayId"))
            try:
                mirror_id = int(screen.get("mirrorsDisplayID") or 0)
            except (TypeError, ValueError):
                mirror_id = 0
            master = by_id.get(str(mirror_id)) if mirror_id and str(mirror_id) != display_id else None
            visual_source[display_id] = master or screen
        frame = self._drag_frame or (self._layout_frame
                                     if self._preserve_layout_frame else None)
        if frame is None:
            view_x, view_y = self._view_origin_offset
            sources = [visual_source[str(screen["displayId"])] for screen in self.screens]
            min_x = min(screen["x"] + view_x for screen in sources)
            min_y = min(screen["y"] + view_y for screen in sources)
            max_x = max(screen["x"] + view_x + screen["width"] for screen in sources)
            max_y = max(screen["y"] + view_y + screen["height"] for screen in sources)
            bounds = self.rect().adjusted(28, 28, -28, -28)
            total_width = max(1, max_x - min_x)
            total_height = max(1, max_y - min_y)
            # Keep the arrangement smaller than the maximum fit so there is
            # open canvas around the displays for long drags in any direction.
            self._layout_scale = min(bounds.width() / total_width,
                                     bounds.height() / total_height) * 0.8
            used_width = total_width * self._layout_scale
            used_height = total_height * self._layout_scale
            offset_x = bounds.left() + (bounds.width() - used_width) / 2
            offset_y = bounds.top() + (bounds.height() - used_height) / 2
            frame = (min_x, min_y, self._layout_scale, offset_x, offset_y)
            self._layout_frame = frame
        min_x, min_y, scale, offset_x, offset_y = frame
        self._layout_scale = scale
        rects = {}
        view_x, view_y = self._view_origin_offset
        for screen in self.screens:
            display_id = str(screen["displayId"])
            source = visual_source[display_id]
            screen_x = source.get("_previewX", source["x"])
            screen_y = source.get("_previewY", source["y"])
            rects[str(screen["displayId"])] = QRectF(
                offset_x + (screen_x + view_x - min_x) * scale,
                offset_y + (screen_y + view_y - min_y) * scale,
                source["width"] * scale,
                source["height"] * scale,
            )
        return rects

    def _screen_paint_order(self):
        # Each mirror group paints its currently selected member on the front
        # card. The active/target group is painted last so its entire tile,
        # including fill and text, stays above overlapping display groups.
        painted = []
        seen_groups = set()
        for screen in self.screens:
            master = self._mirror_group_master(screen)
            master_id = str(master.get("displayId"))
            if master_id in seen_groups:
                continue
            seen_groups.add(master_id)
            members = self._mirror_group_members(master)
            selected = next((member for member in members
                             if str(member.get("displayId")) == self._selected_display_id), None)
            painted.append(selected or master)
        front_group_id = self._front_group_id
        active_screen = (self._drag_screen or
                         (self._primary_drop_target if self._primary_bar_dragging else None))
        if active_screen is None and self._context_display_id is not None:
            active_screen = next((screen for screen in self.screens
                                  if str(screen.get("displayId")) ==
                                  self._context_display_id), None)
        if active_screen is not None:
            front_group_id = str(self._mirror_group_master(active_screen).get("displayId"))
        if front_group_id is not None:
            active = next((screen for screen in painted
                           if str(self._mirror_group_master(screen).get("displayId")) ==
                           front_group_id), None)
            if active is not None:
                painted.remove(active)
                painted.append(active)
        return painted

    def _mirror_group_members(self, screen):
        master = self._mirror_group_master(screen)
        master_id = str(master.get("displayId"))
        members = [candidate for candidate in self.screens
                   if (str(candidate.get("displayId")) == master_id or
                       str(candidate.get("mirrorsDisplayID") or "0") == master_id)]
        if not members:
            return [screen]
        return [master] + sorted(
            [member for member in members if str(member.get("displayId")) != master_id],
            key=lambda member: str(member.get("displayId")))

    def _mirror_stack_members(self, screen):
        """Return members only to determine the decorative stack count."""
        return self._mirror_group_members(screen)

    def _hit_test_screen(self, point):
        """Treat a mirrored stack as one tile with one group-level hit target."""
        rects = self._geometry()
        for representative in reversed(self._screen_paint_order()):
            base = rects.get(str(representative.get("displayId")))
            if base is None:
                continue
            stack_depth = max(0, len(self._mirror_group_members(representative)) - 1)
            group_hit_area = base.adjusted(0, 0,
                                           stack_depth * MIRROR_CARD_OFFSET,
                                           stack_depth * MIRROR_CARD_OFFSET)
            if group_hit_area.contains(point):
                return representative
        return None

    @staticmethod
    def _is_mirror_slave(screen):
        try:
            mirror_id = int(screen.get("mirrorsDisplayID") or 0)
        except (TypeError, ValueError):
            return False
        return mirror_id != 0 and str(mirror_id) != str(screen.get("displayId"))

    def _draw_coordinate_grid(self, painter, rects):
        frame = self._drag_frame or self._layout_frame
        if frame is None:
            return None
        _min_x, _min_y, scale, _offset_x, _offset_y = frame
        if scale <= 0:
            return None
        primary_target = self._primary_drop_target
        origin_screen = (primary_target if self._primary_bar_dragging and primary_target
                         else next((screen for screen in self.screens if screen.get("main")), None))
        if origin_screen is None:
            return None
        origin_rect = rects.get(str(origin_screen["displayId"]))
        if origin_rect is None:
            return None
        origin = origin_rect.topLeft()

        step = 100
        spacing = step * scale
        bounds = self.rect().adjusted(10, 10, -10, -10)
        painter.save()
        painter.setClipRect(bounds)
        pen = QPen(appearance_color("#303030"), 1)
        first_x_index = math.ceil((bounds.left() - origin.x()) / spacing)
        last_x_index = math.floor((bounds.right() - origin.x()) / spacing)
        for index in range(first_x_index, last_x_index + 1):
            x = origin.x() + index * spacing
            pen.setColor(appearance_color("#3b3b3b") if index % 5 == 0
                         else appearance_color("#303030"))
            painter.setPen(pen)
            painter.drawLine(round(x), bounds.top(), round(x), bounds.bottom())
        first_y_index = math.ceil((bounds.top() - origin.y()) / spacing)
        last_y_index = math.floor((bounds.bottom() - origin.y()) / spacing)
        for index in range(first_y_index, last_y_index + 1):
            y = origin.y() + index * spacing
            pen.setColor(appearance_color("#3b3b3b") if index % 5 == 0
                         else appearance_color("#303030"))
            painter.setPen(pen)
            painter.drawLine(bounds.left(), round(y), bounds.right(), round(y))
        painter.restore()
        return origin, step

    def _displayed_screen_coordinates(self, screen) -> tuple[int, int]:
        source = self._mirror_group_master(screen)
        x = source.get("_previewX", source["x"])
        y = source.get("_previewY", source["y"])
        if self._drag_screen is not None and self._drag_screen.get("main"):
            if screen.get("main"):
                return 0, 0
            x -= self._drag_primary_offset[0]
            y -= self._drag_primary_offset[1]
        if (self._primary_bar_dragging and self._primary_drop_target is not None and
                not self._primary_drop_target.get("main")):
            x -= self._primary_drop_target["x"]
            y -= self._primary_drop_target["y"]
        return int(x), int(y)

    @staticmethod
    def _coordinate_label_rect(marker: QPointF, text_width: float,
                               text_height: float) -> QRectF:
        # Qt's canvas y-axis points down, so the upper-right position is a
        # positive x offset and a negative y offset from the origin cross.
        return QRectF(marker.x() + 8, marker.y() - text_height - 8,
                      text_width + 4, text_height + 2)

    def _clear_interaction_feedback(self):
        had_drag = self._drag_screen is not None or self._primary_bar_dragging
        dragging = had_drag
        if self._drag_screen is not None:
            layout_screen = self._drag_layout_screen or self._drag_screen
            if (not layout_screen.get("main") and self._drag_origin is not None):
                layout_screen["x"], layout_screen["y"] = self._drag_origin
            layout_screen.pop("_previewX", None)
            layout_screen.pop("_previewY", None)
        self._drag_screen = None
        self._drag_layout_screen = None
        self._drag_point = None
        self._drag_origin = None
        self._drag_primary_offset = (0, 0)
        self._drag_edge_snap_axes = (False, False)
        self._drag_frame = None
        self._primary_bar_dragging = False
        self._primary_drop_target = None
        self._primary_bar_point = None
        self._primary_bar_width = 0.0
        self._context_display_id = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if had_drag:
            self.fit_to_view()
        else:
            self.update()
        if dragging:
            self.dragStateChanged.emit(False)

    def mousePressEvent(self, event):
        # Recover cleanly if the platform missed a previous mouse-release event.
        self._clear_interaction_feedback()
        if event.button() == Qt.MouseButton.RightButton and self.screens:
            point = event.position()
            screen = self._hit_test_screen(point)
            if screen is not None:
                master = self._mirror_group_master(screen)
                self._front_group_id = str(master["displayId"])
                self._context_display_id = str(screen["displayId"])
                # Paint the promotion before opening the menu or doing any
                # other work triggered by this right-click.
                self.repaint()
                self.contextMenuRequested.emit(screen, event.globalPosition().toPoint())
                event.accept()
                return
            self.contextMenuRequested.emit(None, event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.screens:
            point = event.position()
            rects = self._geometry()
            hit_screen = self._hit_test_screen(point)
            if hit_screen is not None:
                screen = self._mirror_group_master(hit_screen)
                rect = rects.get(str(screen["displayId"]))
                if rect:
                    # Raise the entire display group in the canvas stacking
                    # order immediately; mirror members keep their own order.
                    self._front_group_id = str(screen["displayId"])
                    self.repaint()
                    members = self._mirror_group_members(screen)
                    group_main = next((member for member in members if member.get("main")), screen)
                    if (group_main.get("main") and point.y() <= rect.top() + 24):
                        # The primary menu-bar handle is a separate control,
                        # not a display-card identification target.
                        self.displayIdentifyRequested.emit("")
                        self._primary_bar_dragging = True
                        self._primary_drop_target = self._mirror_group_master(group_main)
                        self._primary_bar_point = point
                        self._primary_bar_width = max(0.0, rect.width() - 2)
                        self._drag_frame = self._layout_frame
                        self.setCursor(Qt.CursorShape.ClosedHandCursor)
                        self.dragStateChanged.emit(True)
                        # Force the brief pressed outline to paint before a
                        # quick click can release and clear the state.
                        self.repaint()
                        event.accept()
                        return
                    self.displayIdentifyRequested.emit(str(hit_screen.get("displayId", "")))
                    self._drag_screen = hit_screen
                    self._drag_layout_screen = self._mirror_group_master(screen)
                    self._drag_point = point
                    self._drag_origin = (self._drag_layout_screen["x"],
                                         self._drag_layout_screen["y"])
                    self._drag_primary_offset = (0, 0)
                    self._drag_frame = self._layout_frame
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    self.dragStateChanged.emit(True)
                    # QWidget.update() may defer painting until after a fast
                    # press/release pair, making the transient gray frame seem
                    # intermittent. Paint it synchronously while still down.
                    self.repaint()
                    event.accept()
                    return
            else:
                self.displayIdentifyRequested.emit("")
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._primary_bar_dragging:
            self._primary_bar_point = event.position()
            self._primary_drop_target = self._primary_target_at(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            event.accept()
            return
        if self._drag_screen is not None:
            self._update_drag_preview(event.position())
            self.update()
            event.accept()
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor if any(
            rect.contains(event.position()) for rect in self._geometry().values())
            else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def _update_drag_preview(self, point):
        """Update the preview; snap settings only affect this drag assistance."""
        if self._drag_screen is None or self._drag_point is None:
            return
        layout_screen = self._drag_layout_screen or self._drag_screen
        delta = point - self._drag_point
        scale = max(0.001, self._layout_scale)
        dx = round(delta.x() / scale)
        dy = round(delta.y() / scale)
        x, y = self._drag_origin[0] + dx, self._drag_origin[1] + dy
        x, y, edge_x, edge_y = self._snap_drag_position(layout_screen, x, y)
        self._drag_edge_snap_axes = (edge_x, edge_y)
        dx, dy = x - self._drag_origin[0], y - self._drag_origin[1]
        if layout_screen.get("main"):
            self._drag_primary_offset = (dx, dy)
            layout_screen["_previewX"] = self._drag_origin[0] + dx
            layout_screen["_previewY"] = self._drag_origin[1] + dy
        else:
            # Keep the optimistic position in the model while dragging and
            # commit the release event's exact position before background work.
            layout_screen["x"] = self._drag_origin[0] + dx
            layout_screen["y"] = self._drag_origin[1] + dy

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._primary_bar_dragging:
            self.displayIdentifyRequested.emit("")
            self._primary_drop_target = self._primary_target_at(event.position())
            target = self._primary_drop_target
            current_primary = next((screen for screen in self.screens
                                    if screen.get("main")), None)
            current_primary_master = (self._mirror_group_master(current_primary)
                                      if current_primary is not None else None)
            self._primary_bar_dragging = False
            self._primary_drop_target = None
            self._primary_bar_point = None
            self._primary_bar_width = 0.0
            self._drag_frame = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if (target is not None and current_primary_master is not None and
                    str(target.get("displayId")) !=
                    str(current_primary_master.get("displayId"))):
                self.primaryMoveRequested.emit(str(target["displayId"]))
            self.update()
            self.dragStateChanged.emit(False)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_screen is not None:
            self.displayIdentifyRequested.emit("")
            # Mouse move events can be coalesced. Always use mouse-up's own
            # coordinates so the dropped card cannot stop one event early.
            self._update_drag_preview(event.position())
            screen = self._drag_screen
            layout_screen = self._drag_layout_screen or screen
            old_origin = self._drag_origin
            layout_screen["x"], layout_screen["y"] = self._attach_drop_to_nearest_screen(
                layout_screen, int(layout_screen["x"]), int(layout_screen["y"]))
            primary_offset = self._drag_primary_offset
            drag_frame = self._drag_frame
            self._drag_screen = None
            self._drag_layout_screen = None
            self._drag_point = None
            self._drag_origin = None
            self._drag_frame = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            painted_drop = False
            if layout_screen.get("main") and primary_offset != (0, 0):
                self._layout_frame = drag_frame
                self.primaryArrangementMoveRequested.emit(*primary_offset)
            elif not layout_screen.get("main") and (
                    (layout_screen["x"], layout_screen["y"]) != old_origin):
                # Commit and fit from the released, legal arrangement now;
                # never wait for the CoreGraphics confirmation to bring cards
                # back into the visible canvas.
                self.fit_to_view()
                target_x, target_y = int(layout_screen["x"]), int(layout_screen["y"])
                self._drag_edge_snap_axes = (False, False)
                self._layout_apply_pending = True
                painted_drop = True
                if len(self._mirror_group_ids(screen)) > 1:
                    self.moveRequested.emit(f"mirror:{layout_screen['displayId']}",
                                            target_x, target_y, *old_origin)
                else:
                    self.moveRequested.emit(str(layout_screen["displayId"]),
                                            target_x, target_y, *old_origin)
            layout_screen.pop("_previewX", None)
            layout_screen.pop("_previewY", None)
            self._drag_primary_offset = (0, 0)
            self._drag_edge_snap_axes = (False, False)
            # A press-and-release without movement still needs to clear the
            # temporary gray outline, but should not synchronously redraw.
            if not painted_drop:
                self.update()
            self.dragStateChanged.emit(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), appearance_color("#1b1b1b"))
        if not self.screens:
            painter.setPen(appearance_color("#999999"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Display arrangement data unavailable")
            return

        rects = self._geometry()
        coordinate_axes = (self._draw_coordinate_grid(painter, rects)
                           if self._coordinates_visible else None)
        coordinate_labels = []
        coordinate_font = painter.font()
        bounds = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        if coordinate_axes is not None:
            origin, _step = coordinate_axes
            painter.setPen(QPen(appearance_color("#777777"), 1.5))
            painter.drawLine(round(origin.x()), self.rect().top() + 10,
                             round(origin.x()), self.rect().bottom() - 10)
            painter.drawLine(self.rect().left() + 10, round(origin.y()),
                             self.rect().right() - 10, round(origin.y()))
        for screen in self._screen_paint_order():
            rect = rects[str(screen["displayId"])]
            group_members = self._mirror_stack_members(screen)
            is_group_tile = len(group_members) > 1
            fill = appearance_color("#292929")
            border = appearance_color("#666666")
            border_width = 1
            if self._drag_screen is screen:
                border = ARRANGEMENT_HIGHLIGHT_COLOR
                border_width = 2
            elif any(member is self._primary_drop_target for member in group_members):
                border = (appearance_color("#888888")
                          if self._primary_drop_target.get("main")
                          else ARRANGEMENT_HIGHLIGHT_COLOR)
                border_width = 2
            # Lower cards are decorative only; the whole stack is one hit target.
            for depth in range(len(group_members) - 1, 0, -1):
                lower_rect = rect.translated(depth * MIRROR_CARD_OFFSET,
                                             depth * MIRROR_CARD_OFFSET)
                painter.setBrush(fill)
                painter.setPen(QPen(appearance_color("#666666"), 1))
                painter.drawRoundedRect(lower_rect, 8, 8)
            painter.setBrush(fill)
            painter.setPen(QPen(border, border_width))
            painter.drawRoundedRect(rect, 8, 8)
            group_main = next((member for member in group_members if member.get("main")), None)
            if (group_main is not None and not self._primary_bar_dragging):
                menu_bar = QRectF(rect.left() + 1, rect.top() + 1,
                                  max(0, rect.width() - 2), 6)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(appearance_color("#888888"))
                painter.drawRoundedRect(menu_bar, 5, 5)
                if menu_bar.height() > 2:
                    painter.drawRect(QRectF(menu_bar.left(), menu_bar.top() + 3,
                                            menu_bar.width(), menu_bar.height() - 3))

            padding = 10
            text_rect = rect.adjusted(padding, padding, -padding, -padding)
            title_font = painter.font()
            title_font.setBold(True)
            title_font.setPointSizeF(11)
            detail_font = QFont(title_font)
            detail_font.setBold(False)
            detail_font.setPointSizeF(9)
            title_metrics = QFontMetrics(title_font)
            detail_metrics = QFontMetrics(detail_font)
            gap = 2
            if is_group_tile:
                master = self._mirror_group_master(screen)
                names = mirror_group_name_lines(group_members)
                dimension = arrangement_resolution_text(master)
                total_height = (len(names) * title_metrics.height() + gap +
                                detail_metrics.height())
                top = text_rect.center().y() - total_height / 2
                painter.setFont(title_font)
                painter.setPen(appearance_color("#f2f2f2"))
                for line_index, name in enumerate(names):
                    line = title_metrics.elidedText(
                        name, Qt.TextElideMode.ElideRight, max(1, int(text_rect.width())))
                    line_rect = QRectF(
                        text_rect.left(), top + line_index * title_metrics.height(),
                        text_rect.width(), title_metrics.height())
                    painter.drawText(
                        line_rect,
                        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, line)
                painter.setFont(detail_font)
                painter.setPen(appearance_color("#bcbcbc"))
                line = detail_metrics.elidedText(
                    dimension, Qt.TextElideMode.ElideRight, max(1, int(text_rect.width())))
                line_rect = QRectF(
                    text_rect.left(), top + len(names) * title_metrics.height() + gap,
                    text_rect.width(), detail_metrics.height())
                painter.drawText(
                    line_rect,
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, line)
            else:
                rotation = screen.get("rotation")
                dimension = f"{int(screen['width'])} × {int(screen['height'])}"
                angle = int(round(float(rotation))) % 360 if rotation is not None else 0
                if angle:
                    dimension += f", {angle}°"
                profile_name = icc_profile_display_name(screen.get("colorProfile"))
                details = [dimension]
                current_mode = screen.get("currentMode") or {}
                color_details = [value for value in (
                    mode_encoding(current_mode), mode_range(current_mode),
                    (f"{current_mode['bitDepth']}-bit"
                     if current_mode.get("bitDepth") is not None else ""),
                    str(current_mode.get("hdrMode") or ""),
                ) if value]
                if color_details:
                    details.append(" · ".join(color_details))
                if profile_name:
                    details.append(profile_name)
                total_height = title_metrics.height() + gap + len(details) * detail_metrics.height()
                top = text_rect.center().y() - total_height / 2
                title_rect = QRectF(text_rect.left(), top, text_rect.width(), title_metrics.height())
                painter.setFont(title_font)
                painter.setPen(appearance_color("#f2f2f2"))
                title = title_metrics.elidedText(
                    screen["name"], Qt.TextElideMode.ElideRight, max(1, int(text_rect.width())))
                painter.drawText(title_rect,
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, title)
                painter.setFont(detail_font)
                painter.setPen(appearance_color("#bcbcbc"))
                for line_index, value in enumerate(details):
                    line = detail_metrics.elidedText(
                        value, Qt.TextElideMode.ElideRight, max(1, int(text_rect.width())))
                    line_rect = QRectF(text_rect.left(),
                                       top + title_metrics.height() + gap + line_index * detail_metrics.height(),
                                       text_rect.width(), detail_metrics.height())
                    painter.drawText(line_rect,
                                     Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, line)
            if self._coordinates_visible and rect.width() >= 72 and rect.height() >= 36:
                coordinate_x, coordinate_y = self._displayed_screen_coordinates(screen)
                coordinate_font = QFont(title_font)
                coordinate_font.setBold(True)
                coordinate_font.setPointSizeF(10)
                painter.setFont(coordinate_font)
                painter.setPen(CURRENT_COLOR)
                coordinate_text = f"({coordinate_x}, {coordinate_y})"
                coordinate_metrics = QFontMetrics(coordinate_font)
                text_width = coordinate_metrics.horizontalAdvance(coordinate_text)
                text_height = coordinate_metrics.height()
                coordinate_rect = self._coordinate_label_rect(
                    rect.topLeft(), text_width, text_height)
                coordinate_labels.append((coordinate_rect, coordinate_text))
                # Draw after the cards so the label stays legible if the
                # origin falls on a neighboring display tile.
                continue
        context_screen = next((screen for screen in self.screens
                               if str(screen.get("displayId")) == self._context_display_id), None)
        if context_screen is not None:
            context_rect = rects.get(str(context_screen.get("displayId")))
            if context_rect is not None:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(appearance_color("#888888"), 2))
                painter.drawRoundedRect(context_rect, 8, 8)
        # Draw origin markers after every display box and menu bar so the
        # markers remain visible at screen corners and overlaps.
        if self._coordinates_visible:
            marker_pen = QPen(CURRENT_COLOR, 2)
            marker_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(marker_pen)
            for screen in self._screen_paint_order():
                rect = rects.get(str(screen["displayId"]))
                if rect is not None:
                    origin = rect.topLeft()
                    edge_x, edge_y = (self._drag_edge_snap_axes
                                      if screen is self._drag_screen else (False, False))
                    # Extend along the display edge being aligned: an X snap
                    # aligns a vertical edge, while a Y snap aligns a
                    # horizontal edge.
                    arm_x = 12 if edge_y else 6
                    arm_y = 12 if edge_x else 6
                    painter.drawLine(round(origin.x() - arm_x), round(origin.y()),
                                     round(origin.x() + arm_x), round(origin.y()))
                    painter.drawLine(round(origin.x()), round(origin.y() - arm_y),
                                     round(origin.x()), round(origin.y() + arm_y))
        if coordinate_axes is not None:
            _origin, step = coordinate_axes
            scale_font = painter.font()
            scale_font.setPointSizeF(8)
            scale_font.setBold(False)
            painter.setFont(scale_font)
            painter.setPen(appearance_color("#aaaaaa"))
            painter.drawText(self.rect().adjusted(12, 0, -12, -8),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                             f"Grid: {int(step)} points")
        if self._primary_bar_dragging and self._primary_bar_point is not None:
            ghost = QRectF(self._primary_bar_point.x() - self._primary_bar_width / 2,
                           self._primary_bar_point.y() - 4, self._primary_bar_width, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(appearance_color("#888888"))
            painter.drawRoundedRect(ghost, 4, 4)
        if coordinate_labels:
            painter.setFont(coordinate_font)
            painter.setPen(CURRENT_COLOR)
            for coordinate_rect, coordinate_text in coordinate_labels:
                painter.drawText(coordinate_rect,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 coordinate_text)


class DisplayArrangementDialog(QDialog):
    dragStateChanged = Signal(bool)
    applyFinished = Signal()
    displaySelected = Signal(str)
    modeSelected = Signal(str, object)
    colorModeSelected = Signal(str, object)
    orientationSelected = Signal(str, int)
    iccProfileSelected = Signal(str, object)
    displayRoleSelected = Signal(str, str, str)
    helperReady = Signal()

    def __init__(self, screens: list[dict], omitted: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Arrangement")
        self.setMinimumSize(520, 400)
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("Current display arrangement")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        title.setFont(font)
        layout.addWidget(title)
        self.canvas = DisplayArrangementCanvas(screens)
        # The canvas has already committed and painted the exact drop point.
        # Start the non-blocking CoreGraphics command in this same event turn;
        # success must not gate or redraw the optimistic canvas position.
        self.canvas.moveRequested.connect(self.apply_display_position)
        self.canvas.displaySelected.connect(self.displaySelected)
        self.canvas.displayIdentifyRequested.connect(self._identify_display)
        self.canvas.contextMenuRequested.connect(self._show_display_menu)
        self.canvas.primaryArrangementMoveRequested.connect(
            self.apply_primary_arrangement_offset)
        self.canvas.primaryMoveRequested.connect(self.apply_primary_display)
        self.canvas.dragStateChanged.connect(self.dragStateChanged)
        self.canvas.setStyleSheet(
            f"border: 1px solid {appearance_hex('#3c3c3c')}; border-radius: 8px;")
        layout.addWidget(self.canvas, 1)
        self._apply_process: QProcess | None = None
        self._helper_build_process: QProcess | None = None
        self._apply_queue: list[tuple[list[str], list[dict] | None]] = []
        self._icc_profiles: list[dict] = []
        self._icc_profiles_loading = False
        self._icc_profiles_error = ""
        self._active_display_menu: QMenu | None = None
        self._active_mapping_menu: QMenu | None = None
        self._display_menu_submenus: list[QMenu] = []
        self._display_menu_app: QApplication | None = None
        self._context_screen: dict | None = None
        self._display_menu_position = None
        self._display_menu_reopen_requested = False
        self._display_menu_reopen_screen: dict | None = None
        self._display_menu_reopen_path: tuple[str, ...] = ()
        self._display_menu_close_pending = False
        self._original_screens: list[dict] | None = None
        self._pending_view_offset: tuple[int, int] | None = None
        # While a local arrangement apply is in flight, retain the exact
        # coordinates already shown by the canvas. Background captures may
        # refresh metadata, but cannot move cards underneath the user.
        self._hold_local_layout = False
        self._layout_apply_pending = False
        self._deferred_confirmed_layout: list[dict] | None = None
        self._ignore_own_layout_events_until = 0.0
        self._identify_process: QProcess | None = None
        self._identify_build_process: QProcess | None = None
        self._identify_pending_command: dict | None = None
        self._identify_stdout_buffer = bytearray()
        self._identify_helper_ready = False
        self._identify_visible = False
        self._identify_target_id: str | None = None
        self._identify_pressed = False
        self._identify_mouse_held = False
        self._identify_keyboard_held = False
        self._identify_shutting_down = False
        # Mapping is a temporary per-display overlay, independent of Coords
        # and Identify. Keep each card's selection while Arrange is open.
        self._mapping_settings: dict[str, dict[str, str]] = {}
        self.omitted_label = QLabel(
            f"Position or size unavailable for {omitted} display{'s' if omitted != 1 else ''}.",
            objectName="muted")
        self.omitted_label.setVisible(bool(omitted))
        layout.addWidget(self.omitted_label)
        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        self.identify_button = QPushButton("Identify")
        self.identify_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.identify_button.setAutoDefault(False)
        self.identify_button.setText("Starting…")
        self.identify_button.setEnabled(False)
        self.identify_button.setToolTip("Hold to identify every display (I)")
        self.identify_button.pressed.connect(self._start_identifying_displays)
        self.identify_button.released.connect(self._stop_identifying_displays)
        self.coordinates_button = QPushButton("Coords: Show")
        self.coordinates_button.setToolTip("Show or hide coordinates (C)")
        self.coordinates_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.coordinates_button.clicked.connect(self.toggle_coordinates)
        self.snap_button = QPushButton("Snap: On")
        self.snap_button.setToolTip("Toggle edge snapping (⌘S)")
        self.snap_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snap_button.clicked.connect(self.toggle_snap)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close Arrange (⌘W)")
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(self.accept)
        self.snap_button.setText("Snap: Off")
        button_width = max(
            button.sizeHint().width()
            for button in (self.identify_button, self.snap_button,
                           self.coordinates_button, close_button)
        )
        self.snap_button.setText("Snap: On")
        self.coordinates_button.setText("Coords: Hide")
        button_width = max(button_width, self.coordinates_button.sizeHint().width()) + 4
        for button in (self.identify_button, self.snap_button,
                       self.coordinates_button, close_button):
            button.setFixedWidth(button_width)
        required_width = 36 + button_width * 4 + button_row.spacing() * 3
        self.setMinimumWidth(max(self.minimumWidth(), required_width))
        self.coordinates_button.setText("Coords: Show")
        button_row.addStretch(1)
        button_row.addWidget(self.identify_button)
        button_row.addWidget(self.snap_button)
        button_row.addWidget(self.coordinates_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self._keyboard_shortcuts = []
        for sequence, callback in (
                ("A", self.close_with_shortcut),
                ("C", self.toggle_coordinates),
                ("F", self.cycle_all_mapping),
                ("Ctrl+S", self.toggle_snap),
                ("Ctrl+W", self.close_with_shortcut)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(
                lambda action=callback: self._run_keyboard_shortcut(action))
            self._keyboard_shortcuts.append(shortcut)
        self.finished.connect(lambda _result: self._release_identify_holds())
        self.finished.connect(lambda _result: self._remove_display_event_filter())
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._identify_application_state_changed)

    def toggle_coordinates(self):
        visible = not self.canvas._coordinates_visible
        self.canvas.set_coordinates_visible(visible)
        self.coordinates_button.setText("Coords: Hide" if visible else "Coords: Show")

    def toggle_snap(self):
        enabled = not self.canvas._snap_enabled
        self.canvas.set_snap_enabled(enabled)
        self.snap_button.setText(f"Snap: {'On' if enabled else 'Off'}")

    def _run_keyboard_shortcut(self, callback):
        if ApplicationShortcutFilter._has_focus_in(self):
            callback()

    def close_with_shortcut(self):
        self._display_menu_reopen_requested = False
        menu = self._active_display_menu
        if menu is not None:
            menu.hide()
        self.accept()

    def _start_identifying_displays(self):
        self._identify_mouse_held = True
        self._update_identify_hold()

    def _stop_identifying_displays(self):
        self._identify_mouse_held = False
        self._update_identify_hold()

    def _set_identify_keyboard_held(self, held: bool):
        self._identify_keyboard_held = held
        self._update_identify_hold()

    def _release_identify_holds(self):
        self._identify_mouse_held = False
        self._identify_keyboard_held = False
        self._update_identify_hold()

    def _identify_application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive and self._identify_keyboard_held:
            self._set_identify_keyboard_held(False)

    def _update_identify_hold(self):
        pressed = self._identify_mouse_held or self._identify_keyboard_held
        if pressed == self._identify_pressed:
            return
        self._identify_pressed = pressed
        if pressed:
            self._identify_target_id = None
        owner = self.parent()
        if owner is not None and hasattr(owner, "_set_identify_visibility"):
            owner._set_identify_visibility(self.canvas.screens, pressed)

    def _identify_display(self, display_id: str):
        owner = self.parent()
        if owner is not None and hasattr(owner, "_set_identify_target"):
            owner._set_identify_target(display_id)

    def _ensure_identify_helper(self):
        if self._identify_shutting_down:
            return False
        if self._identify_process is not None:
            return True
        if self._identify_build_process is not None:
            return False
        binary = identify_overlay_helper_path()
        source = ROOT / "tools" / "display-identify-overlay.m"
        if not getattr(sys, "frozen", False) and (
                not binary.is_file() or source.stat().st_mtime_ns > binary.stat().st_mtime_ns):
            try:
                binary.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QToolTip.showText(QCursor.pos(), f"Display identification unavailable: {exc}",
                                  self.identify_button)
                return False
            process = QProcess(self)
            process.setProgram("/usr/bin/clang")
            process.setArguments([
                "-fobjc-arc", "-framework", "Foundation", "-framework", "AppKit",
                str(source), "-o", str(binary),
            ])
            process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            process.finished.connect(lambda code, status, target=process:
                                     self._identify_build_finished(target, code, status))
            process.errorOccurred.connect(lambda _error, target=process:
                                          self._identify_build_error(target))
            self._identify_build_process = process
            process.start()
            return False
        if not binary.is_file():
            QToolTip.showText(QCursor.pos(), "Display identification helper is missing.",
                              self.identify_button)
            return False
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.started.connect(lambda target=process:
                                self._identify_helper_started(target))
        process.readyReadStandardOutput.connect(
            lambda target=process: self._identify_helper_output(target))
        process.finished.connect(lambda _code, _status, target=process:
                                 self._identify_helper_finished(target))
        process.errorOccurred.connect(lambda _error, target=process:
                                      self._identify_helper_error(target))
        self._identify_process = process
        process.start()
        return True

    def _identify_build_finished(self, process: QProcess, exit_code: int, _status):
        if self._identify_build_process is not process:
            return
        self._identify_build_process = None
        try:
            error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            process.deleteLater()
        except RuntimeError:
            return
        if self._identify_shutting_down:
            return
        if exit_code != 0:
            self._set_identify_button_ready(False, failed=True)
            QToolTip.showText(QCursor.pos(),
                              f"Display identification unavailable: {error[-400:]}",
                              self.identify_button)
            return
        self._ensure_identify_helper()

    def _identify_build_error(self, process: QProcess):
        if self._identify_build_process is not process:
            return
        try:
            process_error = process.error()
        except RuntimeError:
            return
        if process_error != QProcess.ProcessError.FailedToStart:
            return
        self._identify_build_process = None
        self._set_identify_button_ready(False, failed=True)
        QToolTip.showText(QCursor.pos(), "Could not compile display identification helper.",
                          self.identify_button)
        try:
            process.deleteLater()
        except RuntimeError:
            pass

    def _identify_helper_started(self, process: QProcess):
        if self._identify_process is not process:
            return
        self._identify_helper_ready = False
        self._identify_stdout_buffer.clear()
        self.identify_button.setText("Starting…")
        self.identify_button.setEnabled(False)

    def _identify_helper_output(self, process: QProcess):
        if self._identify_process is not process:
            return
        try:
            self._identify_stdout_buffer.extend(bytes(process.readAllStandardOutput()))
        except RuntimeError:
            return
        while b"\n" in self._identify_stdout_buffer:
            line, _, remaining = self._identify_stdout_buffer.partition(b"\n")
            self._identify_stdout_buffer = bytearray(remaining)
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if event.get("event") == "ready":
                self._identify_helper_ready = True
                self._set_identify_button_ready(True)
                self._flush_identify_helper_state()

    def _set_identify_button_ready(self, ready: bool, failed: bool = False):
        self.identify_button.setText("Identify" if ready or failed else "Starting…")
        self.identify_button.setEnabled(ready or failed)

    def _flush_identify_helper_state(self):
        if not self._identify_helper_ready:
            return
        self._identify_send(self._identify_pending_command or {
            "action": "configure", "groups": identify_overlay_groups(self.canvas.screens),
            "identifyColor": ARRANGEMENT_HIGHLIGHT_COLOR.name(),
        })
        if self._identify_pressed:
            state_command = {"action": "show"}
        elif self._identify_visible and self._identify_target_id:
            state_command = {"action": "identify", "displayID": self._identify_target_id}
        else:
            state_command = {"action": "hide"}
        self._identify_send(state_command)
        for display_id, settings in self._mapping_settings.items():
            self._identify_send({"action": "mapping", "displayID": display_id,
                                 **settings})

    def _identify_send(self, command: dict):
        process = self._identify_process
        if (process is None or process.state() != QProcess.ProcessState.Running or
                not self._identify_helper_ready):
            if command.get("action") == "configure":
                self._identify_pending_command = command
            elif command.get("action") in ("show", "hide"):
                self._identify_visible = command["action"] == "show"
            elif command.get("action") == "identify":
                self._identify_target_id = str(command.get("displayID") or "") or None
            return
        payload = (json.dumps(command, ensure_ascii=False, separators=(",", ":"))
                   .encode("utf-8") + b"\n")
        if process.write(payload) < 0:
            self._identify_pending_command = command
        else:
            self._identify_pending_command = None

    @staticmethod
    def _default_mapping_settings():
        return {"enabled": "off", "background": "black", "line": "white"}

    def _set_mapping_option(self, display_id: str | None, category: str, value: str):
        allowed = {
            "enabled": {"off", "opaque", "translucent"},
            "background": {"black", "white"},
            "line": {"black", "white", "red", "blue", "green"},
        }
        if category not in allowed or value not in allowed[category]:
            return
        display_ids = ([str(screen.get("displayId")) for screen in self.canvas.screens]
                       if display_id is None else [str(display_id)])
        updated = []
        for target_id in display_ids:
            settings = self._mapping_settings.setdefault(
                target_id, self._default_mapping_settings())
            settings[category] = value
            updated.append((target_id, settings))
        self._refresh_open_mapping_menu_checks()
        self._sync_mapping_escape_filter()
        if category != "enabled" and all(
                settings["enabled"] == "off" for _, settings in updated):
            return
        if any(settings["enabled"] != "off" for _, settings in updated):
            self._ensure_identify_helper()
        if self._identify_process is not None:
            for target_id, settings in updated:
                self._identify_send({"action": "mapping", "displayID": target_id,
                                     **settings})

    def _refresh_open_mapping_menu_checks(self):
        menu = self._active_display_menu
        if menu is None or not menu.isVisible():
            return
        mapping_menu = self._active_mapping_menu
        if mapping_menu is None:
            return
        display_id = (str(self._context_screen.get("displayId"))
                      if self._context_screen is not None else None)
        settings = self._mapping_settings_for_scope(display_id)
        try:
            mapping_actions = mapping_menu.actions()
        except RuntimeError:
            return
        for action in mapping_actions:
            category = action.property("mappingCategory")
            if category:
                action.setChecked(settings[category] == action.property("mappingValue"))

    def _mapping_is_active(self) -> bool:
        return any(settings.get("enabled") != "off"
                   for settings in self._mapping_settings.values())

    def _sync_mapping_escape_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        if self._mapping_is_active() or self._active_display_menu is not None:
            if self._display_menu_app is not app:
                self._remove_display_event_filter()
                app.installEventFilter(self)
                self._display_menu_app = app
        else:
            self._remove_display_event_filter()

    def _remove_display_event_filter(self):
        if self._display_menu_app is not None:
            self._display_menu_app.removeEventFilter(self)
            self._display_menu_app = None

    def _turn_off_all_mapping(self):
        self._set_mapping_option(None, "enabled", "off")

    def cycle_all_mapping(self):
        current = self._mapping_settings_for_scope(None)["enabled"]
        next_state = {"off": "translucent", "translucent": "opaque",
                      "opaque": "off", None: "off"}[current]
        self._set_mapping_option(None, "enabled", next_state)

    def _mapping_settings_for_scope(self, display_id: str | None) -> dict[str, str | None]:
        if display_id is not None:
            return dict(self._mapping_settings.get(
                str(display_id), self._default_mapping_settings()))
        all_settings = [self._mapping_settings.get(
            str(screen.get("displayId")), self._default_mapping_settings())
            for screen in self.canvas.screens]
        if not all_settings:
            all_settings = [self._default_mapping_settings()]
        return {
            category: (all_settings[0][category]
                       if all(settings[category] == all_settings[0][category]
                              for settings in all_settings) else None)
            for category in ("enabled", "background", "line")
        }

    def _identify_helper_finished(self, process: QProcess):
        if self._identify_process is not process:
            return
        self._identify_process = None
        self._identify_helper_ready = False
        if not self._identify_shutting_down:
            self._set_identify_button_ready(False, failed=True)
        try:
            process.deleteLater()
        except RuntimeError:
            return
        if self._identify_pressed and not self._identify_shutting_down:
            QTimer.singleShot(0, self._ensure_identify_helper)

    def _identify_helper_error(self, process: QProcess):
        if self._identify_process is not process:
            return
        try:
            process_error = process.error()
        except RuntimeError:
            return
        if process_error != QProcess.ProcessError.FailedToStart:
            return
        QToolTip.showText(QCursor.pos(), "Could not start display identification.",
                          self.identify_button)
        self._set_identify_button_ready(False, failed=True)
        self._identify_process = None
        self._identify_helper_ready = False
        try:
            process.deleteLater()
        except RuntimeError:
            pass

    def _show_display_menu(self, screen: dict | None, global_position):
        menu = self._active_display_menu
        was_visible = menu is not None and menu.isVisible()
        self._display_menu_reopen_requested = False
        self._display_menu_reopen_screen = None
        self._display_menu_reopen_path = ()
        self._display_menu_position = global_position
        display_id = str(screen.get("displayId")) if screen is not None else None
        # A right-click can target the exposed portion of a mirrored card
        # underneath another one. Promote that same card visually and make it
        # the active display before drawing the context outline/menu; otherwise
        # the outline floats around a card that is still painted underneath.
        if screen is not None:
            master_id = str(self.canvas._mirror_group_master(screen).get("displayId"))
            self.canvas._front_group_id = master_id
            if self.canvas._selected_display_id != display_id:
                self.canvas._selected_display_id = display_id
                self.canvas.displaySelected.emit(display_id)
            self.canvas._context_display_id = display_id
        else:
            self.canvas._context_display_id = None
        self.canvas.update()
        if menu is None:
            menu = QMenu(self)
            menu.aboutToHide.connect(self._display_menu_hidden)
            self._active_display_menu = menu
            self._sync_mapping_escape_filter()
        self._display_menu_submenus.clear()
        self._active_mapping_menu = None
        menu.clear()
        menu.setToolTipsVisible(True)
        self._context_screen = screen
        if screen is None:
            is_mirror_slave, has_mirror_children = False, False
        else:
            is_mirror_slave, has_mirror_children = display_mirror_roles(
                screen, self.canvas.screens)
        mirror_group = is_mirror_slave or has_mirror_children
        mapping_menu = menu.addMenu("Mapping" if screen is not None else "Mapping all")
        self._active_mapping_menu = mapping_menu
        mapping_menu.setToolTipsVisible(True)
        selected_mapping = self._mapping_settings_for_scope(display_id)
        mapping_sections = (
            ("enabled", (("Off", "off"), ("Semi-transparent On", "translucent"),
                          ("Opaque On", "opaque"))),
            ("background", (("Black background", "black"),
                            ("White background", "white"))),
            ("line", (("Black line", "black"), ("White line", "white"),
                      ("Red line", "red"), ("Blue line", "blue"),
                      ("Green line", "green"))),
        )
        for section_index, (category, choices) in enumerate(mapping_sections):
            if section_index:
                mapping_menu.addSeparator()
            mapping_group = QActionGroup(mapping_menu)
            mapping_group.setExclusive(True)
            for label, value in choices:
                action = mapping_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(selected_mapping[category] == value)
                action.setProperty("mappingCategory", category)
                action.setProperty("mappingValue", value)
                mapping_group.addAction(action)
                action.triggered.connect(
                    lambda _checked=False, did=display_id, key=category, selected=value:
                    self._set_mapping_option(did, key, selected))

        if screen is None:
            self._finish_display_menu_build(menu, global_position, was_visible)
            return

        resolution_data = {
            "desktopModes": screen.get("desktopModes") or [],
            "framebufferMode": screen.get("framebufferMode") or {},
            "displayId": screen.get("displayId"),
            "mirrorsDisplayID": screen.get("mirrorsDisplayID"),
        }
        rows = resolution_rows_for_display(resolution_data, self.canvas.screens)
        current_mode = screen.get("framebufferMode") or {}
        group_members = self.canvas._mirror_group_members(screen) if mirror_group else [screen]
        resolution_menu = menu.addMenu("Resolution")
        resolution_menu.setToolTipsVisible(True)
        size_counts = {}
        for row in rows:
            size_counts[row["values"][0]] = size_counts.get(row["values"][0], 0) + 1
        for row in rows:
            label = row["values"][0]
            if size_counts[label] > 1:
                label += f" ({row['values'][2]} pixels)"
            action = resolution_menu.addAction(label)
            is_current = bool(row.get("current"))
            action.setCheckable(True)
            action.setChecked(is_current)
            action.setEnabled(bool(row.get("selectable")) and not is_current)
            if is_mirror_slave and not is_current:
                action.setToolTip(MIRROR_RESOLUTION_UNAVAILABLE)
            mode = preferred_mode_for_resolution(row, current_mode.get("refreshRate"))
            if mode and not is_mirror_slave:
                action.triggered.connect(
                    lambda _checked=False, did=str(screen["displayId"]), target=mode:
                    self.modeSelected.emit(did, target))
        if not rows:
            empty = resolution_menu.addAction("No resolutions available")
            empty.setEnabled(False)

        orientation_menu = menu.addMenu("Orientation")
        orientation_allowed = not mirror_group
        orientation_menu.setToolTipsVisible(True)
        orientation_menu.setEnabled(orientation_allowed)
        if not orientation_allowed:
            orientation_menu.menuAction().setToolTip(MIRROR_RESOLUTION_UNAVAILABLE)
        current_rotation = screen.get("rotation")
        for angle in (0, 90, 180, 270):
            action = orientation_menu.addAction(orientation_label(angle))
            action.setCheckable(True)
            action.setChecked(current_rotation is not None and int(current_rotation) % 360 == angle)
            action.setEnabled(not action.isChecked())
            action.triggered.connect(
                lambda _checked=False, did=str(screen["displayId"]), value=angle:
                self.orientationSelected.emit(did, value))

        color_menu = menu.addMenu("Color")
        for member in group_members:
            member_menu = (color_menu.addMenu(str(member.get("name") or
                                                   f"Display {member.get('displayId', '')}"))
                           if mirror_group else color_menu)
            current_color_mode = member.get("currentMode") or {}
            color_options = color_mode_groups(
                member.get("availableModes") or [], current_color_mode.get("modeID"),
                current_color_mode)
            if not color_options:
                empty = member_menu.addAction("No color modes available")
                empty.setEnabled(False)
            else:
                for row in color_options:
                    label = " · ".join(value for value in row["values"][:4] if value)
                    action = member_menu.addAction(label or "Unspecified color mode")
                    action.setCheckable(True)
                    action.setChecked(bool(row.get("current")))
                    target = row.get("switchMode")
                    action.setEnabled(bool(target) and not row.get("current"))
                    if target:
                        action.triggered.connect(
                            lambda _checked=False, did=str(member["displayId"]), selected=target:
                            self.colorModeSelected.emit(did, selected))

        icc_menu = menu.addMenu("ICC")
        for member in group_members:
            member_menu = (icc_menu.addMenu(str(member.get("name") or
                                                 f"Display {member.get('displayId', '')}"))
                           if mirror_group else icc_menu)
            current_profile = member.get("colorProfile") or {}
            current_url = ICCProfileTable._path_key(current_profile.get("url"))
            current_name = icc_profile_display_name(current_profile).casefold()
            if self._icc_profiles_loading:
                loading = member_menu.addAction("Loading ICC profiles…")
                loading.setEnabled(False)
            elif self._icc_profiles_error:
                failed = member_menu.addAction("ICC profiles unavailable")
                failed.setEnabled(False)
            elif not self._icc_profiles:
                empty = member_menu.addAction("No ICC profiles found")
                empty.setEnabled(False)
            else:
                for profile in self._icc_profiles:
                    profile_name = icc_profile_display_name(profile) or "Unnamed ICC profile"
                    profile_url = str(profile.get("url") or "")
                    is_current = bool(
                        (current_url and ICCProfileTable._path_key(profile_url) == current_url) or
                        (current_name and profile_name.casefold() == current_name)
                    )
                    action = member_menu.addAction(profile_name)
                    action.setCheckable(True)
                    action.setChecked(is_current)
                    action.setEnabled(bool(profile_url) and not is_current)
                    action.triggered.connect(
                        lambda _checked=False, did=str(member["displayId"]), target=profile:
                        self.iccProfileSelected.emit(did, target))

        role_menu = menu.addMenu("Display role")
        role_menu.setToolTipsVisible(True)
        if mirror_group:
            for member in group_members:
                member_menu = role_menu.addMenu(str(member.get("name") or
                                                      f"Display {member.get('displayId', '')}"))
                self._populate_display_role_menu(member_menu, member)
        else:
            self._populate_display_role_menu(role_menu, screen)
        self._finish_display_menu_build(menu, global_position, was_visible)

    def _populate_display_role_menu(self, menu: QMenu, display: dict):
        display_id = str(display.get("displayId") or "")
        for label, role, source_id, checked, enabled in display_role_menu_options(
                display, self.canvas.screens):
            action = menu.addAction(label)
            action.setProperty("closeDisplayMenuAfterTrigger", True)
            action.setCheckable(True)
            action.setChecked(checked)
            action.setEnabled(enabled)
            if role == "extended" and bool(display.get("main")):
                action.setToolTip(
                    "The nearest available display will become Main Display.")
            action.triggered.connect(
                lambda _checked=False, did=display_id, selected_role=role, target=source_id:
                self.displayRoleSelected.emit(did, selected_role, target))

    def _finish_display_menu_build(self, menu: QMenu, global_position, was_visible: bool):
        # Keep Python wrappers alive for QMenu objects created by addMenu();
        # PySide may otherwise destroy a submenu when its local reference goes
        # out of scope, leaving dead menu actions in the persistent root menu.
        self._display_menu_submenus = []

        def connect_submenus(parent_menu: QMenu, parent_path: tuple[str, ...] = ()):
            for action in parent_menu.actions():
                submenu = action.menu()
                if submenu is None:
                    continue
                self._display_menu_submenus.append(submenu)
                path = parent_path + (submenu.title(),)
                submenu.triggered.connect(
                    lambda selected_action, selected_path=path:
                    self._display_menu_action_triggered(selected_path, selected_action))
                connect_submenus(submenu, path)

        connect_submenus(menu)
        menu.adjustSize()
        menu.move(global_position)
        if was_visible:
            menu.raise_()
        else:
            menu.popup(global_position)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.KeyPress and
                event.key() == Qt.Key.Key_Escape and self._mapping_is_active()):
            self._turn_off_all_mapping()
            menu = self._active_display_menu
            if menu is not None:
                menu.hide()
            event.accept()
            return True
        menu = self._active_display_menu
        if (menu is not None and event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.RightButton):
            global_position = event.globalPosition().toPoint()
            local_position = self.canvas.mapFromGlobal(global_position)
            target = None
            if self.canvas.rect().contains(local_position):
                point = QPointF(local_position)
                target = self.canvas._hit_test_screen(point)
            if target is not None:
                self.canvas._front_group_id = str(
                    self.canvas._mirror_group_master(target).get("displayId"))
                self.canvas.update()
                self._show_display_menu(target, global_position)
                return True
            if self.canvas.rect().contains(local_position):
                self._show_display_menu(None, global_position)
                return True
            menu.hide()
        return super().eventFilter(watched, event)

    def _display_menu_action_triggered(self, path: tuple[str, ...], _action):
        if self._active_display_menu is None:
            return
        if (_action is not None and
                _action.property("closeDisplayMenuAfterTrigger")):
            # A display-role change can replace the mirror topology. Keep the
            # menu closed so the confirmed layout and refreshed menu contents
            # are applied immediately instead of waiting for another click.
            self._display_menu_reopen_requested = False
            self._display_menu_reopen_screen = None
            self._display_menu_reopen_path = ()
            return
        self._display_menu_reopen_requested = True
        self._display_menu_reopen_screen = self._context_screen
        # Qt can forward a leaf trigger through each ancestor QMenu. Keep the
        # deepest path so a mirrored display submenu is restored as well.
        if len(path) >= len(self._display_menu_reopen_path):
            self._display_menu_reopen_path = path

    def _display_menu_hidden(self):
        if self._display_menu_close_pending:
            return
        self._display_menu_close_pending = True
        QTimer.singleShot(0, self._finish_display_menu_hidden)

    def _finish_display_menu_hidden(self):
        self._display_menu_close_pending = False
        if self._display_menu_reopen_requested:
            screen = self._display_menu_reopen_screen
            if screen is not None:
                screen_id = str(screen.get("displayId"))
                screen = next((item for item in self.canvas.screens
                               if str(item.get("displayId")) == screen_id), screen)
            position = self._display_menu_position or QCursor.pos()
            submenu_path = self._display_menu_reopen_path
            self._display_menu_reopen_requested = False
            self._display_menu_reopen_screen = None
            self._display_menu_reopen_path = ()
            self._show_display_menu(screen, position)
            if submenu_path:
                QTimer.singleShot(
                    0, lambda path=submenu_path: self._reopen_display_menu_path(path))
            return

        menu = self._active_display_menu
        self._active_display_menu = None
        self._active_mapping_menu = None
        self._context_screen = None
        if menu is not None:
            menu.deleteLater()
        self._display_menu_submenus.clear()
        self._sync_mapping_escape_filter()
        self.canvas._context_display_id = None
        self.canvas.update()
        self._sync_mapping_escape_filter()
        owner = self.parent()
        if (owner is not None and hasattr(owner, "_arrangement_interaction_active") and
                not owner._arrangement_interaction_active()):
            self.apply_deferred_confirmed_layout()
            owner._apply_deferred_arrangement_records()
            owner._run_deferred_arrangement_refreshes()

    def _reopen_display_menu_path(self, path: tuple[str, ...]):
        parent_menu = self._active_display_menu
        if parent_menu is None or not parent_menu.isVisible():
            return
        for title in path:
            action = next((candidate for candidate in parent_menu.actions()
                           if candidate.menu() is not None and
                           candidate.menu().title() == title), None)
            if action is None:
                return
            submenu = action.menu()
            parent_menu.setActiveAction(action)
            action_rect = parent_menu.actionGeometry(action)
            popup_position = parent_menu.mapToGlobal(action_rect.topRight())
            submenu.popup(popup_position)
            parent_menu = submenu

    def set_icc_profiles(self, profiles: list[dict], loading: bool = False,
                         error: str = ""):
        self._icc_profiles = list(profiles)
        self._icc_profiles_loading = loading
        self._icc_profiles_error = error

    def update_profile_for_display(self, display_id: str, profile: dict):
        for screen in self.canvas.screens:
            if str(screen.get("displayId")) == str(display_id):
                screen["colorProfile"] = profile or {}
                self.canvas.update()
                break

    def update_displays(self, screens: list[dict], omitted: int):
        previous = {str(screen.get("displayId")): screen
                    for screen in self.canvas.screens}
        updated = []
        for incoming in screens:
            screen = dict(incoming)
            old = previous.get(str(screen.get("displayId")), {})
            # Full display captures can briefly omit the mirror relationship
            # while the lightweight arrangement poll still has it. Keep the
            # last known relationship until CoreGraphics reports an explicit
            # non-mirrored value (0); otherwise the two cards swap paint layers
            # during refreshes and the rear card appears to flicker.
            if screen.get("mirrorsDisplayID") is None and old.get("mirrorsDisplayID") is not None:
                screen["mirrorsDisplayID"] = old["mirrorsDisplayID"]
            if self._hold_local_layout:
                # Retain the live canvas coordinates while background reports
                # refresh metadata or confirm our own asynchronous apply.
                if "x" in old and "y" in old:
                    screen["x"], screen["y"] = old["x"], old["y"]
            updated.append(screen)

        geometry_changed = (arrangement_layout_signature(updated) !=
                            arrangement_layout_signature(self.canvas.screens))
        visual_changed = self._visual_signature(updated) != self._visual_signature(
            self.canvas.screens)
        self.canvas.screens = updated
        if (self.canvas._selected_display_id is not None and
                self.canvas._selected_display_id not in {
                    str(screen.get("displayId")) for screen in updated
                }):
            self.canvas._selected_display_id = next(
                (str(screen.get("displayId")) for screen in updated if screen.get("main")),
                None)
        if (self.canvas._front_group_id is not None and
                self.canvas._front_group_id not in {
                    str(self.canvas._mirror_group_master(screen).get("displayId"))
                    for screen in updated
                }):
            self.canvas._front_group_id = None
        if geometry_changed:
            # Local drop coordinates stay authoritative during an apply. Do
            # not refit: even a correct background sample must not cause a
            # visible canvas jump after mouse-up.
            if self._hold_local_layout:
                self.canvas.update()
            elif not self.canvas._preserve_layout_frame:
                self.canvas.fit_to_view()
            else:
                self.canvas.update()
        elif visual_changed:
            # Only repaint when something visible on a card actually changed.
            # The arrangement poll runs frequently; refreshing identical data
            # must not add continuous paint work during or after a drag.
            self.canvas.update()
        self.omitted_label.setText(
            f"Position or size unavailable for {omitted} display{'s' if omitted != 1 else ''}."
        )
        self.omitted_label.setVisible(bool(omitted))
        owner = self.parent()
        if owner is not None and hasattr(owner, "_configure_identify_helper"):
            owner._configure_identify_helper(updated)

    @staticmethod
    def _visual_signature(screens):
        signature = []
        for screen in screens:
            try:
                mirror_id = int(screen.get("mirrorsDisplayID") or 0)
            except (TypeError, ValueError):
                mirror_id = str(screen.get("mirrorsDisplayID") or "0")
            rotation = screen.get("rotation")
            signature.append((
                str(screen.get("displayId")), screen.get("name"),
                screen.get("friendlyName"), int(screen.get("x", 0)),
                int(screen.get("y", 0)), int(screen.get("width", 0)),
                int(screen.get("height", 0)), bool(screen.get("main")),
                mirror_id, None if rotation is None else int(float(rotation)) % 360,
                icc_profile_display_name(screen.get("colorProfile")),
            ))
        return tuple(sorted(signature))

    def apply_display_position(self, display_id: str, x: int, y: int,
                               old_x: int, old_y: int):
        self._layout_apply_pending = False
        display_id = str(display_id)
        original = [{key: value for key, value in screen.items()
                     if key not in ("_previewX", "_previewY")}
                    for screen in self.canvas.screens]
        master_id = (display_id.partition(":")[2]
                     if display_id.startswith("mirror:") else display_id)
        self._hold_local_layout = True
        for screen in original:
            if str(screen.get("displayId")) == master_id:
                screen["x"], screen["y"] = old_x, old_y
                break
        if display_id.startswith("mirror:"):
            mirror_members = sorted({
                str(screen.get("displayId")) for screen in self.canvas.screens
                if str(screen.get("mirrorsDisplayID") or "0") == master_id
                and str(screen.get("displayId")) != master_id
            })
            self._start_arrangement_apply([
                "--mirror-group", master_id, str(x), str(y), *mirror_members],
                original_screens=original)
        else:
            self._start_arrangement_apply([display_id, str(x), str(y)],
                                          original_screens=original)

    def ensure_arrangement_helper(self) -> bool:
        """Return immediately; build the native helper off the UI thread if needed."""
        binary = arrangement_helper_path()
        if getattr(sys, "frozen", False):
            return binary.is_file()
        source = ROOT / "tools" / "display-arrangement.m"
        try:
            if binary.is_file() and source.stat().st_mtime_ns <= binary.stat().st_mtime_ns:
                return True
        except OSError:
            pass
        if self._helper_build_process is not None:
            return False
        try:
            binary.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._arrangement_helper_build_finished)
        process.errorOccurred.connect(self._arrangement_helper_build_error)
        self._helper_build_process = process
        process.start("/usr/bin/clang", [
            "-fobjc-arc", "-framework", "Foundation", "-framework", "CoreGraphics",
            str(source), "-o", str(binary),
        ])
        return False

    def _arrangement_helper_build_finished(self, exit_code: int, _exit_status):
        process = self._helper_build_process
        if process is None:
            return
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        self._helper_build_process = None
        if exit_code != 0 or not arrangement_helper_path().is_file():
            if self._apply_queue:
                self._original_screens = self._apply_queue[0][1]
                self._apply_queue.clear()
                self._restore_drag_preview()
                QMessageBox.warning(
                    self, APP_TITLE,
                    "Unable to prepare display arrangement control:\n\n" +
                    (error or "The display control helper could not be built."))
                self.applyFinished.emit()
            return
        if self._apply_queue:
            self._start_next_queued_apply()
        self.helperReady.emit()

    def _arrangement_helper_build_error(self, error):
        if error != QProcess.ProcessError.FailedToStart or self._helper_build_process is None:
            return
        process = self._helper_build_process
        message = process.errorString()
        process.deleteLater()
        self._helper_build_process = None
        if self._apply_queue:
            self._original_screens = self._apply_queue[0][1]
            self._apply_queue.clear()
            self._restore_drag_preview()
            QMessageBox.warning(
                self, APP_TITLE,
                f"Could not start the display arrangement helper build.\n\n{message}")
            self.applyFinished.emit()

    def apply_primary_arrangement_offset(self, dx: int, dy: int):
        original = [{key: value for key, value in screen.items()
                     if key not in ("_previewX", "_previewY")}
                    for screen in self.canvas.screens]
        for screen in self.canvas.screens:
            if not screen.get("main"):
                screen["x"] -= dx
                screen["y"] -= dy
        self._hold_local_layout = True
        self._pending_view_offset = None
        # Refit the committed local arrangement immediately rather than
        # preserving the old viewport until the asynchronous system result.
        self.canvas.fit_to_view()
        self._start_arrangement_apply(["--primary-offset", str(dx), str(dy)],
                                      original_screens=original)

    def apply_primary_display(self, display_id: str):
        self._start_arrangement_apply(["--primary", str(display_id)])

    def _apply_confirmed_layout(self, actual: list[dict]):
        """Use the actual origins returned by CoreGraphics after commit."""
        actual_by_id = {
            str(screen.get("displayId")): screen for screen in actual
            if screen.get("displayId") is not None
        }
        if not actual_by_id:
            return
        changed = False
        for screen in self.canvas.screens:
            reported = actual_by_id.get(str(screen.get("displayId")))
            if reported is not None:
                for key in ("x", "y", "width", "height", "main", "mirrorsDisplayID"):
                    if key in reported:
                        value = reported[key]
                        if key == "mirrorsDisplayID":
                            try:
                                value = str(int(value or 0))
                            except (TypeError, ValueError):
                                value = str(value)
                        if screen.get(key) != value:
                            screen[key] = value
                            changed = True
        self._deferred_confirmed_layout = None
        if changed:
            # Preserve the optimistic framing when the confirmed correction
            # still fits; refit only if the system placed any card outside the
            # viewport. This avoids a delayed zoom/jump on ordinary drops.
            if self.canvas._cards_fully_visible():
                self.canvas.update()
            else:
                self.canvas.fit_to_view()

    def apply_deferred_confirmed_layout(self):
        if self._deferred_confirmed_layout is None:
            return
        if (self._layout_apply_pending or self._apply_process is not None or
                self.canvas._drag_screen is not None or
                self.canvas._primary_bar_dragging or
                self._active_display_menu is not None):
            return
        actual = self._deferred_confirmed_layout
        self._deferred_confirmed_layout = None
        self._apply_confirmed_layout(actual)

    def _start_arrangement_apply(self, arguments: list[str],
                                  original_screens: list[dict] | None = None):
        if self._apply_process is not None:
            queued_original = (original_screens if original_screens is not None else
                               [dict(screen) for screen in self.canvas.screens])
            # An in-flight CoreGraphics transaction cannot be safely cancelled,
            # but stale pending destinations should never form a long backlog.
            # Only the newest released position remains to be applied next.
            self._apply_queue[:] = [(list(arguments), queued_original)]
            return
        if not self.ensure_arrangement_helper():
            queued_original = (original_screens if original_screens is not None else
                               [dict(screen) for screen in self.canvas.screens])
            self._apply_queue[:] = [(list(arguments), queued_original)]
            return
        self._original_screens = (original_screens if original_screens is not None else
                                  [dict(screen) for screen in self.canvas.screens])
        binary = arrangement_helper_path()
        self.canvas.set_arrangement_busy(True)
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._arrangement_finished)
        process.errorOccurred.connect(self._arrangement_process_error)
        self._apply_process = process
        process.start()

    def _start_next_queued_apply(self):
        if self._apply_process is not None or not self._apply_queue:
            return False
        arguments, original_screens = self._apply_queue.pop(0)
        self._start_arrangement_apply(arguments, original_screens=original_screens)
        return self._apply_process is not None

    def _restore_drag_preview(self):
        if self._pending_view_offset is not None:
            dx, dy = self._pending_view_offset
            view_x, view_y = self.canvas._view_origin_offset
            self.canvas._view_origin_offset = (view_x - dx, view_y - dy)
            self._pending_view_offset = None
        if self._original_screens is not None:
            self.canvas.screens = self._original_screens
        self._hold_local_layout = False
        self.canvas.fit_to_view()
        self._original_screens = None

    def _arrangement_finished(self, exit_code: int, _exit_status):
        process = self._apply_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        self._apply_process = None
        self.canvas.set_arrangement_busy(False)
        has_queued_apply = bool(self._apply_queue)
        try:
            actual = json.loads(output) if exit_code == 0 else None
        except json.JSONDecodeError:
            actual = None
        if isinstance(actual, list) and not has_queued_apply:
            self._pending_view_offset = None
            self._ignore_own_layout_events_until = time.monotonic() + 1.25
            if (self.canvas._drag_screen is not None or
                    self.canvas._primary_bar_dragging or
                    self._layout_apply_pending or
                    self._active_display_menu is not None):
                self._deferred_confirmed_layout = actual
            else:
                self._apply_confirmed_layout(actual)
        elif not has_queued_apply:
            self._restore_drag_preview()
            message = error or output or "CoreGraphics rejected the change."
            if "At least two active displays are required to move the primary display arrangement" not in message:
                QMessageBox.warning(self, APP_TITLE,
                                    "Could not apply the display arrangement.\n\n" + message)
        if has_queued_apply:
            # The canvas already shows the newest optimistic position. Do not
            # paint an intermediate result over it while serializing the next
            # requested arrangement.
            self._original_screens = None
            if self._start_next_queued_apply():
                return
        self._original_screens = None
        self.applyFinished.emit()

    def _arrangement_process_error(self, error):
        if error != QProcess.ProcessError.FailedToStart or self._apply_process is None:
            return
        process = self._apply_process
        message = process.errorString()
        process.deleteLater()
        self._apply_process = None
        self.canvas.set_arrangement_busy(False)
        if self._apply_queue:
            self._original_screens = None
            if self._start_next_queued_apply():
                return
        self._restore_drag_preview()
        QMessageBox.warning(self, APP_TITLE,
                            f"Could not start the display arrangement helper.\n\n{message}")
        self.applyFinished.emit()


def is_current_mode(mode: dict, current_id) -> bool:
    mode_id = mode.get("modeID")
    return mode_id is not None and current_id is not None and str(mode_id) == str(current_id)


def refresh_rate_groups(modes: list[dict], current_id) -> list[dict]:
    groups = {}
    for mode in modes:
        rate = mode.get("refreshRate")
        if rate is None:
            continue
        key = (float(rate), mode.get("isVRR"))
        group = groups.setdefault(key, {"resolutions": set(), "count": 0, "current": False})
        if mode.get("width") is not None and mode.get("height") is not None:
            group["resolutions"].add((int(mode["width"]), int(mode["height"])))
        group["count"] += 1
        group["current"] |= is_current_mode(mode, current_id)
    result = []
    for (rate, vrr), data in sorted(groups.items(), key=lambda item: (item[0][0], str(item[0][1])), reverse=True):
        resolution_text = ", ".join(f"{w} × {h}" for w, h in sorted(data["resolutions"], reverse=True))
        result.append({
            "values": (format_refresh(rate), "Yes" if vrr is True else "No" if vrr is False else "",
                       resolution_text, str(data["count"])),
            "current": data["current"],
            "search": f"{rate} {vrr} {resolution_text}",
        })
    return result


def color_mode_groups(modes: list[dict], current_id, current_mode: dict | None = None) -> list[dict]:
    current_mode = current_mode or {}
    timing = (current_mode.get("width"), current_mode.get("height"), current_mode.get("refreshRate"))
    same_timing = []
    if all(value is not None for value in timing):
        for mode in modes:
            if (mode.get("width") == timing[0] and mode.get("height") == timing[1] and
                    mode.get("refreshRate") is not None and
                    abs(float(mode["refreshRate"]) - float(timing[2])) <= 0.01 and
                    mode.get("modeID") is not None):
                same_timing.append(mode)
    groups = {}
    for mode in same_timing:
        key = (mode.get("colorMode"), mode.get("bitDepth"), mode.get("hdrMode"))
        if all(value is None for value in key):
            continue
        group = groups.setdefault(key, {"count": 0, "current": False})
        group["count"] += 1
        group["current"] |= is_current_mode(mode, current_id)
    result = []
    for (color, depth, hdr), data in sorted(groups.items(), key=lambda item: tuple(str(v or "") for v in item[0])):
        mode = {"colorMode": color}
        values = (mode_encoding(mode), mode_range(mode), f"{depth}-bit" if depth is not None else "",
                  str(hdr) if hdr is not None else "", str(data["count"]))
        target = None if data["current"] else next((entry for entry in same_timing
                       if (entry.get("colorMode"), entry.get("bitDepth"), entry.get("hdrMode")) ==
                       (color, depth, hdr) and not is_current_mode(entry, current_id)), None)
        result.append({
            "values": values,
            "current": data["current"], "search": " ".join(str(value or "") for value in (color, depth, hdr)),
            "switchMode": target,
        })
    return result


def color_switch_target(mode: dict | None) -> dict | None:
    """Accept either a grouped Color row or the exact CADisplay mode it wraps."""
    if not isinstance(mode, dict):
        return None
    target = mode.get("switchMode", mode)
    return target if isinstance(target, dict) and target.get("modeID") is not None else None


class CaptureWorker(QThread):
    completed = Signal(dict, str, bool)
    failed = Signal(str)
    edid_completed = Signal(str, dict)
    edid_failed = Signal(str)
    progress = Signal(str)

    def run(self):
        try:
            if self.edid_display is not None:
                edid = capture_service.read_display_edid(self.edid_display)
                self.edid_completed.emit(str(self.edid_display.get("displayId")), edid)
                return
            if self.export_path:
                report, _summary = capture_service.export_capture(
                    self.export_path, ui_context=self.export_context)
                self.completed.emit(report, str(self.export_path), True)
                return
            report = capture_service.capture_live_state() if self.snapshot_only else capture_service.capture_current_state()
            self.completed.emit(report, "", False)
        except Exception as exc:  # report acquisition failures visibly in the window
            if self.edid_display is not None:
                self.edid_failed.emit(str(exc))
            else:
                self.failed.emit(str(exc))

    def __init__(self, export_path: Path | None = None, parent=None, edid_display: dict | None = None,
                 snapshot_only: bool = False, export_context: dict | None = None):
        super().__init__(parent)
        self.export_path = export_path
        self.edid_display = edid_display
        self.snapshot_only = snapshot_only
        self.export_context = export_context or {}


class ConnectionWorker(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, report: dict, parent=None):
        super().__init__(parent)
        self.devices = report.get("devices", [])

    def run(self):
        try:
            self.completed.emit(diagnostics_service.collect_connection_background(self.devices))
        except Exception as exc:
            self.failed.emit(str(exc))


class ICCProfilesWorker(QThread):
    completed = Signal(list)
    failed = Signal(str)

    def run(self):
        try:
            binary = icc_profiles_helper_binary()
            proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120)
            if proc.returncode:
                self.failed.emit(proc.stderr.strip() or proc.stdout.strip() or
                                 f"Exit status {proc.returncode}")
                return
            profiles = json.loads(proc.stdout)
            if not isinstance(profiles, list):
                raise ValueError("ColorSync returned an invalid profile list")
            self.completed.emit(profiles)
        except Exception as exc:
            self.failed.emit(str(exc))


class ICCApplyWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, display_id: str, profile_url: str, parent=None):
        super().__init__(parent)
        self.display_id = display_id
        self.profile_url = profile_url

    def run(self):
        try:
            binary = icc_apply_helper_binary()
            proc = subprocess.run([str(binary), self.display_id, self.profile_url],
                                  capture_output=True, text=True, timeout=30)
            detail = proc.stderr.strip() or proc.stdout.strip()
            self.completed.emit(proc.returncode == 0, detail)
        except Exception as exc:
            self.completed.emit(False, str(exc))


class ICCProfileTable(QWidget):
    profileSelected = Signal(dict)
    COLUMNS = ("", "ICC profile", "Created", "Location")

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("")
        layout.addWidget(self.search)
        self.table = QTableWidget(0, len(self.COLUMNS))
        install_explicit_foreground_delegate(self.table)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setFont(self.table.font())
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        install_manual_scroll_tracking(self, self.table)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellDoubleClicked.connect(self._apply_row)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(24)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._sort_by_column)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().setSectionsClickable(False)
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip("Double-click to sort descending; double-click again to sort ascending.")
        layout.addWidget(self.table, 1)
        self.search.textChanged.connect(self._filter)
        self._profiles: list[dict] = []
        self._visible_profiles: list[dict] = []
        self._current_url = ""
        self._current_name = ""
        self._sort_column: int | None = None
        self._sort_descending = True
        self._active_profile_menu: QMenu | None = None
        self._context_profile: dict | None = None
        self._profile_menu_app: QApplication | None = None

    def _apply_row(self, row: int, _column: int = 0):
        if 0 <= row < len(self._visible_profiles):
            profile = self._visible_profiles[row]
            if not profile.get("current"):
                self.profileSelected.emit(profile)

    def _context_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid() or index.row() >= len(self._visible_profiles):
            return
        profile = self._visible_profiles[index.row()]
        self._show_profile_menu(profile, self.table.viewport().mapToGlobal(position))

    def _show_profile_menu(self, profile: dict, global_position):
        row = next((row for row, candidate in enumerate(self._visible_profiles)
                    if candidate is profile), None)
        if row is None:
            return
        self.table.setCurrentCell(row, 0)
        self.table.selectRow(row)
        if self._active_profile_menu is None:
            menu = QMenu(self)
            apply_action = menu.addAction("Apply profile")
            apply_action.triggered.connect(self._apply_context_profile)
            finder_action = menu.addAction("Show in Finder")
            finder_action.triggered.connect(self._reveal_context_profile)
            menu.aboutToHide.connect(self._clear_context_menu)
            self._active_profile_menu = menu
            self._context_apply_action = apply_action
            self._context_finder_action = finder_action
            app = QApplication.instance()
            if app is not None:
                self._profile_menu_app = app
                app.installEventFilter(self)
        self._context_profile = profile
        self._context_apply_action.setEnabled(
            not profile.get("current") and bool(profile.get("url")))
        self._context_finder_action.setEnabled(bool(profile.get("url")))
        menu = self._active_profile_menu
        was_visible = menu.isVisible()
        menu.adjustSize()
        menu.move(global_position)
        if was_visible:
            menu.raise_()
        else:
            menu.popup(global_position)

    def eventFilter(self, watched, event):
        menu = self._active_profile_menu
        if (menu is not None and event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.RightButton):
            global_position = event.globalPosition().toPoint()
            table_position = self.table.viewport().mapFromGlobal(global_position)
            if self.table.viewport().rect().contains(table_position):
                index = self.table.indexAt(table_position)
                if index.isValid() and index.row() < len(self._visible_profiles):
                    self._show_profile_menu(self._visible_profiles[index.row()], global_position)
                    return True
            menu.hide()
        return super().eventFilter(watched, event)

    def _apply_context_profile(self):
        if self._context_profile is not None:
            self.profileSelected.emit(self._context_profile)

    def _reveal_context_profile(self):
        if self._context_profile is None:
            return
        path = self._path_key(self._context_profile.get("url"))
        if path and Path(path).exists():
            subprocess.Popen(["/usr/bin/open", "-R", path])

    def _clear_context_menu(self):
        menu = self._active_profile_menu
        self._active_profile_menu = None
        app = self._profile_menu_app
        self._profile_menu_app = None
        if app is not None:
            app.removeEventFilter(self)
        # QAction.triggered may be delivered after QMenu.aboutToHide. Keep the
        # selected profile until the next right-click retargets the menu.
        if menu is not None:
            menu.deleteLater()

    @staticmethod
    def _path_key(value) -> str:
        if not value:
            return ""
        parsed = urlparse(str(value))
        path = unquote(parsed.path) if parsed.scheme == "file" else str(value)
        try:
            return str(Path(path).resolve(strict=False))
        except (OSError, ValueError):
            return path

    @staticmethod
    def _values(profile: dict) -> tuple[str, ...]:
        url = str(profile.get("url") or "")
        path = unquote(urlparse(url).path) if urlparse(url).scheme == "file" else url
        return ("●" if profile.get("current") else "",
                str(profile.get("name") or ""),
                str(profile.get("created") or ""), path)

    def set_current_profile(self, profile: dict | None):
        current_url = self._path_key((profile or {}).get("url"))
        current_name = str((profile or {}).get("description") or "").strip().casefold()
        if current_url != self._current_url or current_name != self._current_name:
            self._current_url = current_url
            self._current_name = current_name

    def set_profiles(self, profiles: list[dict]):
        updated = []
        for source in profiles:
            profile = dict(source)
            profile["current"] = bool(
                (self._current_url and self._path_key(profile.get("url")) == self._current_url) or
                (self._current_name and
                 str(profile.get("name") or "").strip().casefold() == self._current_name))
            updated.append(profile)
        if updated == self._profiles:
            return
        self._profiles = updated
        self._filter(self.search.text())

    def _render(self, profiles: list[dict], resize_columns: bool):
        self._visible_profiles = list(profiles)
        if self._sort_column is not None:
            column = self._sort_column
            self._visible_profiles.sort(
                key=lambda profile: (availability_sort_value(profile.get("current", False)) if column == 0 else
                                     natural_sort_key(self._values(profile)[column])),
                reverse=self._sort_descending)
        self.table.setRowCount(0)
        for profile in self._visible_profiles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = self._values(profile)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(profile.get("url") or value) if column == 3 else value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                set_current_appearance(item, profile.get("current", False), marker=(column == 0))
                self.table.setItem(row, column, item)
        if resize_columns:
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(0, 24)
            self.table.setColumnWidth(1, min(self.table.columnWidth(1), 240))
            self.table.setColumnWidth(3, max(24, self.table.columnWidth(3) // 2))

    def _sort_by_column(self, column: int):
        if self._sort_column == column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = True
        query = self.search.text().strip().casefold()
        visible = [profile for profile in self._profiles
                   if not query or query in " ".join(self._values(profile)).casefold()]
        self._render(visible, resize_columns=False)
        QTimer.singleShot(50, self._ensure_current_visible)

    def _filter(self, query: str):
        query = query.strip().casefold()
        visible = [profile for profile in self._profiles
                   if not query or query in " ".join(self._values(profile)).casefold()]
        self._render(visible, resize_columns=True)

    def scroll_to_current(self):
        if self.search.text().strip() or self._manual_current_scroll:
            return
        for row, profile in enumerate(self._visible_profiles):
            if profile.get("current"):
                scroll_item_into_view(self.table, self.table.item(row, 0))
                return

    def reset_current_scroll(self):
        reset_manual_scroll_tracking(self)

    def _ensure_current_visible(self):
        if self.search.text().strip():
            return
        for row, profile in enumerate(self._visible_profiles):
            item = self.table.item(row, 0)
            if profile.get("current") and item is not None and not self.table.viewport().rect().contains(
                    self.table.visualItemRect(item)):
                self.table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)
                return


class ModeTable(QWidget):
    modeSelected = Signal(str, object)
    COLUMNS = ("", "Connection resolution", "Refresh rate", "Encoding", "Range", "Bit depth", "HDR", "VRR", "Mode ID")

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("")
        layout.addWidget(self.search)
        self.table = QTableWidget(0, len(self.COLUMNS))
        install_explicit_foreground_delegate(self.table)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        # macOS Qt assigns QHeaderView a separate, smaller platform font by default.
        self.table.horizontalHeader().setFont(self.table.font())
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        install_manual_scroll_tracking(self, self.table)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(24)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._sort_by_column)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().setSectionsClickable(False)
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip("Double-click to sort descending; double-click again to sort ascending.")
        layout.addWidget(self.table, 1)
        self.search.textChanged.connect(self._filter)
        self._rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self._mode_availability: dict[int, tuple[bool, str]] = {}
        self.current_mode_id: str | None = None
        self.current_mode: dict | None = None
        self.display_id = ""
        self.is_mirror_slave = False
        self._sort_column: int | None = None
        self._sort_descending = True
        self._active_mode_menu: QMenu | None = None
        self._mode_menu_app: QApplication | None = None
        self._context_mode: dict | None = None
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)

    def set_display_id(self, display_id):
        self.display_id = str(display_id) if display_id is not None else ""

    def set_modes(self, modes: list[dict], current_id: str | None = None,
                  is_mirror_slave: bool = False, current_mode: dict | None = None):
        if (modes == self._rows and current_id == self.current_mode_id and
                is_mirror_slave == self.is_mirror_slave and current_mode == self.current_mode):
            return
        self._rows = modes
        self.current_mode_id = current_id
        self.is_mirror_slave = is_mirror_slave
        self.current_mode = current_mode or next(
            (candidate for candidate in modes if is_current_mode(candidate, current_id)), None)
        self._render(modes)

    def scroll_to_current(self):
        if self.search.text().strip() or self._manual_current_scroll:
            return
        for row_index, mode in enumerate(self._visible_rows):
            if is_current_mode(mode, self.current_mode_id):
                scroll_item_into_view(self.table, self.table.item(row_index, 0))
                return

    def reset_current_scroll(self):
        reset_manual_scroll_tracking(self)

    def _render(self, modes: list[dict]):
        self._render_rows(modes, resize_columns=True)

    def _render_rows(self, modes: list[dict], resize_columns: bool):
        self._visible_rows = list(modes)
        self._mode_availability = {}
        if self._sort_column is not None:
            column = self._sort_column
            self._visible_rows.sort(
                key=lambda mode: (availability_sort_value(
                                      is_current_mode(mode, self.current_mode_id),
                                      all_mode_availability(
                                          mode, self.current_mode,
                                          self.is_mirror_slave)[0])
                                  if column == 0
                                  else natural_sort_key(
                                      self._mode_values(mode, self.current_mode_id)[column])),
                reverse=self._sort_descending)
        self.table.setRowCount(0)
        for mode in self._visible_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            current = is_current_mode(mode, self.current_mode_id)
            selectable, unavailable_reason = all_mode_availability(
                mode, self.current_mode, self.is_mirror_slave)
            self._mode_availability[id(mode)] = (selectable, unavailable_reason)
            values = self._mode_values(mode, self.current_mode_id)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(unavailable_reason or text)
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                set_current_appearance(item, current, marker=(col == 0))
                if unavailable_reason:
                    item.setForeground(appearance_color("#888888"))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, col, item)
        if resize_columns:
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(0, 24)

    @staticmethod
    def _mode_values(mode: dict, current_id=None) -> tuple[str, ...]:
        return (
            "●" if is_current_mode(mode, current_id) else "",
            f"{mode['width']} × {mode['height']}" if mode.get('width') is not None and mode.get('height') is not None else "",
            format_refresh(mode.get('refreshRate')),
            mode_encoding(mode), mode_range(mode),
            f"{mode['bitDepth']}-bit" if mode.get('bitDepth') is not None else "",
            str(mode['hdrMode']) if mode.get('hdrMode') is not None else "",
            "Yes" if mode.get("isVRR") is True else "No" if mode.get("isVRR") is False else "",
            str(mode.get("modeID", "")),
        )

    def _sort_by_column(self, column: int):
        if self._sort_column == column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = True
        self._render_rows(list(self._visible_rows), resize_columns=False)
        QTimer.singleShot(50, self._ensure_current_visible)

    def _ensure_current_visible(self):
        if self.search.text().strip():
            return
        for row_index, mode in enumerate(self._visible_rows):
            if not is_current_mode(mode, self.current_mode_id):
                continue
            item = self.table.item(row_index, 0)
            if item is not None and not self.table.viewport().rect().contains(
                    self.table.visualItemRect(item)):
                self.table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)
            return

    def _context_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid() or index.row() >= len(self._visible_rows):
            return
        mode = self._visible_rows[index.row()]
        unavailable_reason = self._mode_availability.get(id(mode), (False, ""))[1]
        if unavailable_reason:
            self._show_unavailable_tooltip(
                mode, self.table.viewport().mapToGlobal(position))
            return
        self._show_mode_menu(mode, self.table.viewport().mapToGlobal(position))

    def _show_unavailable_tooltip(self, mode: dict, global_position):
        reason = self._mode_availability.get(id(mode), (False, ""))[1]
        if reason:
            menu = self._active_mode_menu
            if menu is not None and menu.isVisible():
                menu.hide()
            QToolTip.hideText()
            QTimer.singleShot(
                0, lambda position=global_position, text=reason:
                QToolTip.showText(position, text, self.table.viewport()))

    def _show_mode_menu(self, mode: dict, global_position):
        row = next((index for index, candidate in enumerate(self._visible_rows)
                    if candidate is mode), None)
        if row is None:
            return
        self.table.setCurrentCell(row, 0)
        self.table.selectRow(row)
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._active_mode_menu is None:
            self._active_mode_menu = QMenu(self)
            self._active_mode_menu.aboutToHide.connect(self._mode_menu_hidden)
            app = QApplication.instance()
            if app is not None:
                self._mode_menu_app = app
                app.installEventFilter(self)
        self._context_mode = mode
        was_visible = self._active_mode_menu.isVisible()
        self._populate_mode_menu()
        self._active_mode_menu.adjustSize()
        self._active_mode_menu.move(global_position)
        if was_visible:
            self._active_mode_menu.raise_()
        else:
            self._active_mode_menu.popup(global_position)

    def _populate_mode_menu(self):
        menu = self._active_mode_menu
        mode = self._context_mode
        if menu is None or mode is None:
            return
        menu.clear()
        action = menu.addAction("Set mode")
        action.setEnabled(bool(self.display_id) and
                          self._mode_availability.get(id(mode), (False, ""))[0])
        action.triggered.connect(lambda _checked=False, target=mode:
                                 self.modeSelected.emit(self.display_id, target))

    def eventFilter(self, watched, event):
        if watched in (self.table, self.table.viewport()):
            if event.type() in (QEvent.Type.ToolTip, QEvent.Type.MouseButtonPress,
                                QEvent.Type.MouseButtonDblClick):
                global_position = (event.globalPos() if event.type() == QEvent.Type.ToolTip
                                   else event.globalPosition().toPoint())
                local_position = self.table.viewport().mapFromGlobal(global_position)
                index = self.table.indexAt(local_position)
                if index.isValid() and index.row() < len(self._visible_rows):
                    mode = self._visible_rows[index.row()]
                    if self._mode_availability.get(id(mode), (False, ""))[1]:
                        self._show_unavailable_tooltip(mode, global_position)
                        return True
        menu = self._active_mode_menu
        if (menu is not None and event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.RightButton):
            global_position = event.globalPosition().toPoint()
            local_position = self.table.viewport().mapFromGlobal(global_position)
            if self.table.viewport().rect().contains(local_position):
                index = self.table.indexAt(local_position)
                if index.isValid() and index.row() < len(self._visible_rows):
                    mode = self._visible_rows[index.row()]
                    if self._mode_availability.get(id(mode), (False, ""))[1]:
                        self._show_unavailable_tooltip(mode, global_position)
                    else:
                        self._show_mode_menu(mode, global_position)
                    return True
            menu.hide()
        return super().eventFilter(watched, event)

    def _mode_menu_hidden(self):
        menu = self._active_mode_menu
        self._active_mode_menu = None
        self._context_mode = None
        app = self._mode_menu_app
        self._mode_menu_app = None
        if app is not None:
            app.removeEventFilter(self)
        if menu is not None:
            menu.deleteLater()

    def _filter(self, query: str):
        query = query.strip().casefold()
        if not query:
            self._render(self._rows)
            return
        self._render_rows([m for m in self._rows if query in " ".join((
            str(m.get("width", "")), str(m.get("height", "")),
            format_refresh(m.get("refreshRate")), mode_encoding(m), mode_range(m),
            str(m.get("bitDepth", "")), str(m.get("hdrMode", "")),
            str(m.get("modeID", "")),
        )).casefold()], resize_columns=False)


class ModeSummaryTable(QWidget):
    modeSelected = Signal(str, object)
    rotationSelected = Signal(str, int)

    def __init__(self, columns: tuple[str, ...], placeholder: str, parent=None,
                 show_rotation_selector: bool = False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("")
        layout.addWidget(self.search)
        self.table = QTableWidget(0, len(columns) + 1)
        install_explicit_foreground_delegate(self.table)
        self.table.setHorizontalHeaderLabels(("", *columns))
        # Match header typography to the table body instead of macOS's small header font.
        self.table.horizontalHeader().setFont(self.table.font())
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        install_manual_scroll_tracking(self, self.table)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(24)
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._sort_by_column)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().setSectionsClickable(False)
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip("Double-click to sort descending; double-click again to sort ascending.")
        layout.addWidget(self.table, 1)
        self.rotation_combo: QComboBox | None = None
        if show_rotation_selector:
            rotation_row = QHBoxLayout()
            rotation_row.addStretch(1)
            rotation_row.addWidget(QLabel("Orientation"))
            self.rotation_combo = QComboBox()
            self.rotation_combo.installEventFilter(self)
            self._orientation_unavailable_reason = ""
            self._orientation_palette = self.rotation_combo.palette()
            for angle in (0, 90, 180, 270):
                self.rotation_combo.addItem(orientation_label(angle), angle)
            self.rotation_combo.activated.connect(self._rotation_activated)
            rotation_row.addWidget(self.rotation_combo)
            layout.addLayout(rotation_row)
        self.search.textChanged.connect(self._filter)
        self._rows: list[dict] = []
        self._visible_rows: list[dict] = []
        self.display_id = ""
        self.rotation_value: int | None = None
        self._active_refresh_menu: QMenu | None = None
        self._refresh_menu_app: QApplication | None = None
        self.double_click_selects_mode = False
        self.double_click_uses_preferred_resolution = False
        self.mirror_slave_refresh_only = False
        self._current_refresh_rate: float | None = None
        self._sort_column: int | None = None
        self._sort_descending = True
        self.table.viewport().installEventFilter(self)

    def set_rows(self, rows: list[dict], hint: str = ""):
        if rows == self._rows:
            return
        self._rows = rows
        self._current_refresh_rate = None
        for row in rows:
            if not row.get("current"):
                continue
            value = str(row.get("currentRefreshRate") or "").removesuffix(" Hz")
            try:
                self._current_refresh_rate = float(value)
            except (TypeError, ValueError):
                pass
            break
        self._render(rows)

    def set_display_id(self, display_id):
        self.display_id = str(display_id) if display_id is not None else ""

    def set_rotation(self, rotation):
        if self.rotation_combo is None or rotation is None:
            return
        angle = int(round(float(rotation))) % 360
        self.rotation_value = angle
        index = self.rotation_combo.findData(angle)
        if index >= 0:
            self.rotation_combo.blockSignals(True)
            self.rotation_combo.setCurrentIndex(index)
            self.rotation_combo.blockSignals(False)

    def _rotation_activated(self, index: int):
        if self.rotation_combo is None:
            return
        angle = self.rotation_combo.itemData(index)
        if angle is not None and self.display_id and int(angle) != self.rotation_value:
            self.rotationSelected.emit(self.display_id, int(angle))

    def set_orientation_available(self, available: bool):
        if self.rotation_combo is None:
            return
        self._orientation_unavailable_reason = (
            "" if available else MIRROR_RESOLUTION_UNAVAILABLE)
        # Keep the control enabled so Qt delivers hover/click events for the
        # explanation; its event filter blocks opening or changing the popup.
        self.rotation_combo.setEnabled(True)
        self.rotation_combo.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus if available else Qt.FocusPolicy.NoFocus)
        palette = QPalette(self._orientation_palette)
        if not available:
            for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
                         QPalette.ColorRole.Text):
                muted = palette.color(QPalette.ColorGroup.Disabled, role)
                palette.setColor(QPalette.ColorGroup.Active, role, muted)
                palette.setColor(QPalette.ColorGroup.Inactive, role, muted)
        self.rotation_combo.setPalette(palette)
        self.rotation_combo.setToolTip(self._orientation_unavailable_reason)

    def _context_menu(self, position):
        index = self.table.indexAt(position)
        if not index.isValid() or index.row() >= len(self._visible_rows):
            return
        row = self._visible_rows[index.row()]
        if row.get("unavailableReason"):
            self._show_unavailable_tooltip(
                row, self.table.viewport().mapToGlobal(position))
            return
        self._focus_row(index.row())
        if self.double_click_selects_mode:
            self._show_color_mode_menu(row, self.table.viewport().mapToGlobal(position))
            return
        self._show_row_menu(row, self.table.viewport().mapToGlobal(position))

    def _show_color_mode_menu(self, row: dict, global_position):
        menu = self._active_refresh_menu
        if menu is None:
            menu = QMenu(self)
            menu.aboutToHide.connect(self._refresh_menu_hidden)
            self._active_refresh_menu = menu
            app = QApplication.instance()
            if app is not None:
                self._refresh_menu_app = app
                app.installEventFilter(self)
        was_visible = menu.isVisible()
        QToolTip.hideText()
        self._populate_color_mode_menu(menu, row)
        menu.adjustSize()
        menu.move(global_position)
        if was_visible:
            menu.raise_()
        else:
            menu.popup(global_position)

    def _populate_color_mode_menu(self, menu: QMenu, row: dict):
        menu.clear()
        action = menu.addAction("Set color mode")
        mode = row.get("switchMode")
        enabled = bool(mode) and not row.get("current") and bool(self.display_id)
        action.setEnabled(enabled)
        if enabled:
            action.triggered.connect(lambda _checked=False, selected=mode:
                                     self.modeSelected.emit(self.display_id, selected))

    @staticmethod
    def _has_menu_items(row: dict) -> bool:
        return bool(row.get("refreshRateItems") or row.get("menuItems"))

    def _show_row_menu(self, row: dict, global_position):
        if not self._has_menu_items(row):
            return
        row_index = next((index for index, visible in enumerate(self._visible_rows)
                          if visible is row), None)
        if row_index is not None:
            self._focus_row(row_index)
        menu = self._active_refresh_menu
        if menu is None:
            menu = QMenu(self)
            menu.aboutToHide.connect(self._refresh_menu_hidden)
            self._active_refresh_menu = menu
            app = QApplication.instance()
            if app is not None:
                self._refresh_menu_app = app
                app.installEventFilter(self)
        was_visible = menu.isVisible()
        QToolTip.hideText()
        self._populate_row_menu(menu, row)
        menu.adjustSize()
        menu.move(global_position)
        if was_visible:
            menu.raise_()
        else:
            menu.popup(global_position)

    def _refresh_menu_hidden(self):
        menu = self._active_refresh_menu
        self._active_refresh_menu = None
        app = self._refresh_menu_app
        self._refresh_menu_app = None
        if app is not None:
            app.removeEventFilter(self)
        if menu is not None:
            menu.deleteLater()

    def _show_unavailable_tooltip(self, row: dict, global_position):
        reason = row.get("unavailableReason")
        if not reason:
            return
        menu = self._active_refresh_menu
        if menu is not None and menu.isVisible():
            menu.hide()
        # Defer until QMenu has finished closing so the tooltip isn't consumed
        # by the menu's own popup/grab handling.
        QTimer.singleShot(
            0, lambda position=global_position, text=reason:
            QToolTip.showText(position, text, self.table.viewport()))

    def _populate_row_menu(self, menu: QMenu, row: dict):
        menu.clear()
        actions = []
        is_color = bool(row.get("menuItems"))
        items = row.get("menuItems", []) if is_color else row.get("refreshRateItems", [])
        modes_by_item = row.get("switchModesByItem", {}) if is_color else row.get("switchModesByRate", {})
        current_item = row.get("menuCurrentItem") if is_color else row.get("currentRefreshRate")
        row_is_current_resolution = bool(row.get("current"))
        for item in items:
            mode = modes_by_item.get(item)
            label = f"{item}  (Current)" if item == current_item else item
            action = menu.addAction(label)
            allowed_refresh_change = not row.get("refreshRateOnly") or row_is_current_resolution
            action.setEnabled(bool(mode) and item != current_item and bool(self.display_id)
                              and allowed_refresh_change)
            if mode:
                action.triggered.connect(lambda checked=False, selected=mode:
                                         self.modeSelected.emit(self.display_id, selected))
            actions.append(action)
        if not actions:
            menu.addAction("No refresh rates available").setEnabled(False)

    def eventFilter(self, watched, event):
        if (watched is self.rotation_combo and
                self._orientation_unavailable_reason):
            if event.type() == QEvent.Type.ToolTip:
                QToolTip.showText(event.globalPos(), self._orientation_unavailable_reason,
                                  self.rotation_combo)
                return True
            if event.type() in (QEvent.Type.MouseButtonPress,
                                QEvent.Type.MouseButtonDblClick):
                QToolTip.showText(event.globalPosition().toPoint(),
                                  self._orientation_unavailable_reason,
                                  self.rotation_combo)
                return True
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.Wheel):
                event.accept()
                return True
        menu = getattr(self, "_active_refresh_menu", None)
        if event.type() == QEvent.Type.ToolTip and menu is not None and menu.isVisible():
            QToolTip.hideText()
            return True
        if (event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.LeftButton):
            global_position = event.globalPosition().toPoint()
            index = self._table_index_at_global(global_position)
            if (index is not None and index.isValid() and
                    index.row() < len(self._visible_rows)):
                row = self._visible_rows[index.row()]
                if row.get("unavailableReason"):
                    self._show_unavailable_tooltip(row, global_position)
                    return True
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
            global_position = event.globalPosition().toPoint()
            index = self._table_index_at_global(global_position)
            if index is not None and index.isValid() and index.row() < len(self._visible_rows):
                row = self._visible_rows[index.row()]
                if row.get("unavailableReason"):
                    self._show_unavailable_tooltip(row, global_position)
                    return True
        if (event.type() == QEvent.Type.MouseButtonDblClick and
                event.button() == Qt.MouseButton.LeftButton):
            global_position = event.globalPosition().toPoint()
            index = self._table_index_at_global(global_position)
            if index is not None and index.isValid() and index.row() < len(self._visible_rows):
                row = self._visible_rows[index.row()]
                if row.get("unavailableReason"):
                    self._show_unavailable_tooltip(row, global_position)
                    return True
                if self.double_click_selects_mode:
                    self._focus_row(index.row())
                    self.modeSelected.emit(self.display_id, row)
                    return True
                if self.double_click_uses_preferred_resolution:
                    self._focus_row(index.row())
                    if self.mirror_slave_refresh_only:
                        # Changing a mirror receiver's refresh rate requires
                        # an explicit rate choice from the row's context menu.
                        return True
                    modes = list(row.get("switchModesByRate", {}).values())
                    if not modes:
                        return True
                    current_rate = self._current_refresh_rate
                    same_rate_modes = ([mode for mode in modes
                                        if current_rate is not None and
                                        abs(float(mode.get("refreshRate") or 0) - current_rate) < 0.01])
                    selected = (same_rate_modes[0] if same_rate_modes else
                                max(modes, key=lambda mode: float(mode.get("refreshRate") or 0)))
                    self.modeSelected.emit(self.display_id, selected)
                    return True
                if self._has_menu_items(row):
                    if menu is not None:
                        self._retarget_refresh_menu(menu, row, global_position)
                    else:
                        self._show_row_menu(row, global_position)
                    return True
        if (menu is not None and event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.RightButton):
            global_position = event.globalPosition().toPoint()
            index = self._table_index_at_global(global_position)
            if index is not None and index.isValid() and index.row() < len(self._visible_rows):
                row = self._visible_rows[index.row()]
                if self.double_click_selects_mode:
                    self._focus_row(index.row(), take_focus=False)
                    self._populate_color_mode_menu(menu, row)
                    menu.adjustSize()
                    menu.move(global_position)
                    menu.raise_()
                    return True
                if self._has_menu_items(row):
                    self._retarget_refresh_menu(menu, row, global_position)
                    return True
            menu.hide()
        return super().eventFilter(watched, event)

    def _table_index_at_global(self, global_position):
        local_position = self.table.viewport().mapFromGlobal(global_position)
        if not self.table.viewport().rect().contains(local_position):
            return None
        return self.table.indexAt(local_position)

    def _retarget_refresh_menu(self, menu: QMenu, row: dict, global_position):
        QToolTip.hideText()
        row_index = next((index for index, visible in enumerate(self._visible_rows)
                          if visible is row), None)
        if row_index is not None:
            self._focus_row(row_index, take_focus=False)
        if self.double_click_selects_mode:
            self._populate_color_mode_menu(menu, row)
        else:
            self._populate_row_menu(menu, row)
        menu.adjustSize()
        menu.move(global_position)
        menu.raise_()

    def _focus_row(self, row_index: int, take_focus: bool = True):
        if row_index < 0 or row_index >= self.table.rowCount():
            return
        self.table.setCurrentCell(row_index, 0)
        self.table.selectRow(row_index)
        item = self.table.item(row_index, 0)
        if item is not None:
            self.table.scrollToItem(item, QTableWidget.ScrollHint.EnsureVisible)
        if take_focus:
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def scroll_to_current(self):
        if self.search.text().strip() or self._manual_current_scroll:
            return
        for row_index, data in enumerate(self._visible_rows):
            if data.get("current"):
                scroll_item_into_view(self.table, self.table.item(row_index, 0))
                return

    def reset_current_scroll(self):
        reset_manual_scroll_tracking(self)

    def _filter(self, query: str):
        query = query.strip().casefold()
        self._render([row for row in self._rows if not query or query in row["search"].casefold()],
                     resize_columns=False)

    def _render(self, rows: list[dict], resize_columns: bool = True):
        self._visible_rows = list(rows)
        if self._sort_column is not None:
            value_index = self._sort_column - 1
            self._visible_rows.sort(
                key=lambda row: availability_sort_value(
                    row["current"], row.get("selectable", True)) if self._sort_column == 0
                else natural_sort_key(row["values"][value_index]),
                reverse=self._sort_descending)
        self.table.setRowCount(0)
        for data in self._visible_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            marker = QTableWidgetItem("●" if data["current"] else "")
            marker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            set_current_appearance(marker, data["current"], marker=True)
            if data.get("selectable") is False:
                marker.setFlags(marker.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, marker)
            for column, value in enumerate(data["values"], start=1):
                if column == 4 and "refreshRateItems" in data and data["refreshRateItems"]:
                    current_rate = data.get("currentRefreshRate")
                    parts = []
                    for rate in data["refreshRateItems"]:
                        escaped = rate.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        if rate == current_rate:
                            parts.append(f'<span style="color:{CURRENT_COLOR.name()}">{escaped}</span>')
                        else:
                            parts.append(escaped)
                    rate_label = QLabel(" / ".join(parts))
                    rate_label.setTextFormat(Qt.TextFormat.RichText)
                    rate_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    rate_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                    rate_label.installEventFilter(self)
                    rate_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                    rate_label.customContextMenuRequested.connect(
                        lambda position, row_data=data, label=rate_label:
                        self._show_row_menu(row_data, label.mapToGlobal(position)))
                    rate_label.setToolTip(data.get("unavailableReason") or value)
                    rate_label.setContentsMargins(4, 0, 4, 0)
                    if data.get("selectable") is False:
                        rate_label.setEnabled(False)
                    else:
                        rate_label.setStyleSheet("background: transparent;")
                    self.table.setCellWidget(row, column, rate_label)
                else:
                    item = QTableWidgetItem(value)
                    item.setToolTip(data.get("unavailableReason") or value)
                    set_current_appearance(item, data["current"])
                    if data.get("selectable") is False:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    self.table.setItem(row, column, item)
        if resize_columns:
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(0, 24)

    def _sort_by_column(self, column: int):
        if self._sort_column == column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = True
        query = self.search.text().strip().casefold()
        visible = [row for row in self._rows if not query or query in row["search"].casefold()]
        self._render(visible, resize_columns=False)
        QTimer.singleShot(50, self._ensure_current_visible)

    def _ensure_current_visible(self):
        if self.search.text().strip():
            return
        for row_index, data in enumerate(self._visible_rows):
            if not data.get("current"):
                continue
            item = self.table.item(row_index, 0)
            if item is not None and not self.table.viewport().rect().contains(
                    self.table.visualItemRect(item)):
                self.table.scrollToItem(item, QTableWidget.ScrollHint.PositionAtCenter)
            return

    def _filter(self, query: str):
        query = query.strip().casefold()
        self._render([row for row in self._rows if not query or query in row["search"].casefold()])


class DisplayInspectorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.report: dict = {}
        self.folder = ""
        self.friendly_names = QSettings(APP_ORG, SETTINGS_NAME)
        app = QApplication.instance()
        self.appearance_controller = getattr(app, "appearance_controller", None)
        if self.appearance_controller is None:
            self.appearance_controller = AppearanceController(app, self.friendly_names)
            app.appearance_controller = self.appearance_controller
        self.worker: CaptureWorker | None = None
        self.connection_worker: ConnectionWorker | None = None
        self.icc_worker: ICCProfilesWorker | None = None
        self.icc_apply_worker: ICCApplyWorker | None = None
        self.icc_profiles: list[dict] = []
        self.icc_profiles_error = ""
        self._friendly_name_menu: QMenu | None = None
        self._friendly_name_menu_item: QListWidgetItem | None = None
        self._friendly_name_menu_app = None
        self._friendly_name_role_menu: QMenu | None = None
        self._role_helper_process: QProcess | None = None
        self._role_helper_build_process: QProcess | None = None
        self._pending_role_arguments: list[str] | None = None
        self._page_render_state: dict[str, tuple[str, str]] = {}
        self._refresh_after_icc_apply = False
        self.connection_scan_error = ""
        self._close_pending = False
        self.mode_switch_process: QProcess | None = None
        self.mode_switch_output = ""
        self.mode_switch_response_sent = False
        self.mode_switch_kind = "desktop"
        self.about_dialog: QDialog | None = None
        self.shortcuts_dialog: QDialog | None = None
        self.arrangement_dialog: DisplayArrangementDialog | None = None
        self._identify_last_configuration = None
        self._arrangement_refresh_timer = QTimer(self)
        self._arrangement_refresh_timer.setSingleShot(True)
        self._arrangement_refresh_timer.timeout.connect(self._query_arrangement_layout)
        self._arrangement_poll_timer = QTimer(self)
        self._arrangement_poll_timer.setInterval(500)
        self._arrangement_poll_timer.timeout.connect(self._query_arrangement_layout)
        self._arrangement_query_process: QProcess | None = None
        self._arrangement_query_error_text = ""
        self._last_arrangement_active_ids: frozenset[str] | None = None
        self._last_arrangement_main_id: str | None = None
        self._arrangement_refresh_pending = False
        self._watched_screens = {}
        self._refresh_after_topology_change = False
        self._display_reconfiguration_watcher = None
        self._external_refresh_pending = False
        self._deferred_arrangement_records = None
        self._external_refresh_timer = QTimer(self)
        self._external_refresh_timer.setSingleShot(True)
        self._external_refresh_timer.setInterval(450)
        self._external_refresh_timer.timeout.connect(self._refresh_after_external_change)
        self._live_state_timer = QTimer(self)
        self._live_state_timer.setInterval(2500)
        self._live_state_timer.timeout.connect(self._poll_live_state)
        self._live_state_timer.start()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(300, 300)
        self.resize(1000, 600)
        self._build_ui()
        self._keyboard_shortcuts: list[tuple[QShortcut, QPushButton]] = []
        self._keyboard_shortcut_filter = ApplicationShortcutFilter(self)
        app.installEventFilter(self._keyboard_shortcut_filter)
        self._mac_letter_shortcut_monitor = None
        shortcut_error = None
        if sys.platform == "darwin" and app.platformName() == "cocoa":
            try:
                self._mac_letter_shortcut_monitor = MacLetterShortcutMonitor(self)
                app.aboutToQuit.connect(self._mac_letter_shortcut_monitor.close)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
                detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
                shortcut_error = f"Native keyboard shortcuts unavailable: {detail}"
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self._screen_removed)
        app.primaryScreenChanged.connect(self._screen_geometry_changed)
        try:
            self._display_reconfiguration_watcher = DisplayReconfigurationWatcher(self)
            self._display_reconfiguration_watcher.changed.connect(
                self._display_reconfiguration_changed)
        except (OSError, AttributeError):
            self._display_reconfiguration_watcher = None
        for screen in app.screens():
            self._watch_screen(screen)
        self.statusBar().showMessage(shortcut_error or "Ready to read displays.")
        self._add_keyboard_shortcut("R", self.refresh_button)
        self._add_keyboard_shortcut("A", self.arrange_button)
        self._add_keyboard_shortcut("Ctrl+E", self.export_button)
        self._sync_shortcut_action_state()
        help_menu = self.menuBar().addMenu("Help")
        self.shortcut_action = QAction("Shortcut", self)
        self.shortcut_action.setMenuRole(QAction.MenuRole.NoRole)
        self.shortcut_action.setShortcut(QKeySequence("Ctrl+/"))
        self.shortcut_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_action.triggered.connect(self.show_shortcuts_dialog)
        help_menu.addAction(self.shortcut_action)
        self.appearance_menu = help_menu.addMenu("Appearance")
        self.appearance_action_group = QActionGroup(self)
        self.appearance_action_group.setExclusive(True)
        self.appearance_actions = {}
        for label, mode in (("Dark", APPEARANCE_DARK),
                            ("Light", APPEARANCE_LIGHT),
                            ("System", APPEARANCE_SYSTEM)):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(mode)
            action.setToolTip("Follow the system appearance" if mode == APPEARANCE_SYSTEM
                              else f"Use {label.lower()} appearance")
            self.appearance_action_group.addAction(action)
            self.appearance_menu.addAction(action)
            action.triggered.connect(
                lambda _checked=False, selected=mode:
                self.appearance_controller.set_mode(selected))
            self.appearance_actions[mode] = action
        self.appearance_actions[self.appearance_controller.mode].setChecked(True)
        self.appearance_controller.changed.connect(self._appearance_changed)
        self.about_action = QAction("About", self)
        self.about_action.setMenuRole(QAction.MenuRole.NoRole)
        self.about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(self.about_action)

    def _appearance_changed(self, _actual_appearance, _old_accent, _new_accent):
        if self.shortcuts_dialog is not None:
            self._apply_shortcuts_dialog_theme()
        if self.arrangement_dialog is not None:
            self.arrangement_dialog.canvas.update()
            if not self.arrangement_dialog._identify_helper_ready:
                pending = self.arrangement_dialog._identify_pending_command
                if pending and pending.get("action") == "configure":
                    pending = dict(pending)
                    pending["identifyColor"] = ARRANGEMENT_HIGHLIGHT_COLOR.name()
                    self.arrangement_dialog._identify_pending_command = pending
            self.arrangement_dialog._identify_send({
                "action": "accent", "color": ARRANGEMENT_HIGHLIGHT_COLOR.name()})
        self.update()

    def _sync_shortcut_action_state(self):
        for shortcut, button in getattr(self, "_keyboard_shortcuts", ()):
            shortcut.setEnabled(button.isEnabled())

    def _add_keyboard_shortcut(self, sequence: str, button: QPushButton):
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.setAutoRepeat(False)
        shortcut.activated.connect(
            lambda target=button: self._run_keyboard_shortcut(target))
        self._keyboard_shortcuts.append((shortcut, button))

    def _run_keyboard_shortcut(self, button: QPushButton):
        if ApplicationShortcutFilter._has_focus_in(self) and button.isEnabled():
            ApplicationShortcutFilter._dismiss_active_menu(self)
            button.click()

    def _apply_shortcuts_dialog_theme(self):
        dialog = self.shortcuts_dialog
        if dialog is None:
            return
        color = dialog.palette().windowText().color()
        def rgba(alpha):
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"
        dark = color.lightness() > 128
        badge_background = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.08)"
        for label in dialog._section_labels:
            label.setStyleSheet(
                f"font-size:12px; font-weight:700; color:{rgba(0.48)};")
        for label in dialog._title_labels:
            label.setStyleSheet(f"font-size:14px; color:{rgba(0.95)};")
        for label in dialog._shortcut_labels:
            label.setStyleSheet(
                f"font-size:12px; color:{rgba(0.75)}; background:{badge_background};"
                "padding:3px 8px; border-radius:6px;")

    def show_shortcuts_dialog(self):
        if self.shortcuts_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Keyboard Shortcuts")
            dialog.setMinimumWidth(520)
            dialog._section_labels = []
            dialog._title_labels = []
            dialog._shortcut_labels = []
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(20, 18, 20, 14)
            layout.setSpacing(10)
            first_section = True
            for title, shortcut in SHORTCUT_HELP:
                if not shortcut:
                    if not first_section:
                        layout.addSpacing(10)
                    first_section = False
                    section_label = QLabel(title)
                    dialog._section_labels.append(section_label)
                    layout.addWidget(section_label)
                    continue
                row = QHBoxLayout()
                row.setContentsMargins(0, 2, 0, 2)
                row.setSpacing(16)
                title_label = QLabel(title)
                title_label.setWordWrap(True)
                shortcut_label = QLabel(shortcut)
                shortcut_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                shortcut_label.setMinimumHeight(32)
                dialog._title_labels.append(title_label)
                dialog._shortcut_labels.append(shortcut_label)
                row.addWidget(title_label, 1)
                row.addWidget(shortcut_label)
                layout.addLayout(row)
            layout.addSpacing(10)
            close_button = QPushButton("Close")
            close_button.setDefault(True)
            close_button.clicked.connect(dialog.hide)
            button_row = QHBoxLayout()
            button_row.addStretch()
            button_row.addWidget(close_button)
            button_row.addStretch()
            layout.addLayout(button_row)
            self.shortcuts_dialog = dialog
            dialog.adjustSize()
        self._apply_shortcuts_dialog_theme()
        if self.shortcuts_dialog.isVisible():
            self.shortcuts_dialog.hide()
        else:
            self.shortcuts_dialog.show()
            self.shortcuts_dialog.raise_()
            self.shortcuts_dialog.activateWindow()

    def show_about_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About")
        dialog.setMinimumWidth(410)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        content = QHBoxLayout()
        content.setSpacing(18)
        icon = QLabel()
        pixmap = QPixmap(str(RESOURCE_ROOT / "assets" / "icon.png"))
        if not pixmap.isNull():
            icon.setPixmap(scaled_high_dpi_icon(
                pixmap, 88, max(2.0, self.devicePixelRatioF())))
            icon.setFixedSize(88, 88)
        content.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(6)
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size:16px; font-weight:600;")
        version_file = RESOURCE_ROOT / "VERSION"
        try:
            version = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            version = QApplication.applicationVersion() or "Unknown"
        version_label = QLabel(f"Ver. {version}")
        copyright_label = QLabel("© 2026 WhARTS Ltd.")
        copyright_label.setStyleSheet(f"color:{appearance_hex('#888')};")
        license_label = QLabel(
            'Licensed under <a href="https://www.gnu.org/licenses/agpl-3.0.txt">'
            'AGPL-3.0-or-later</a>. You may redistribute it under these terms. '
            'Provided without warranty.'
        )
        license_label.setOpenExternalLinks(True)
        license_label.setWordWrap(True)
        text_layout.addWidget(title)
        text_layout.addWidget(version_label)
        text_layout.addWidget(copyright_label)
        text_layout.addWidget(license_label)
        content.addLayout(text_layout, 1)
        layout.addLayout(content)

        ok_button = QPushButton("OK")
        ok_button.setFixedWidth(100)
        ok_button.clicked.connect(dialog.accept)
        layout.addWidget(ok_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        dialog.exec()

    def _build_ui(self):
        root = QWidget(objectName="root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        sidebar = QWidget(objectName="sidebar")
        sidebar.setMinimumWidth(120)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 12, 0)
        side.setSpacing(10)
        self.display_list = QListWidget()
        self.display_list.setSpacing(0)
        self.display_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.display_list.customContextMenuRequested.connect(
            self._show_display_friendly_name_menu)
        self.display_list.currentRowChanged.connect(self.show_display)
        self.display_list.itemDoubleClicked.connect(self._edit_friendly_name)
        side.addWidget(self.display_list, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Refresh display information (R)")
        self.refresh_button.clicked.connect(self.refresh)
        self.arrange_button = QPushButton("Arrange")
        self.arrange_button.setToolTip("Arrange displays (A)")
        self.arrange_button.clicked.connect(self.show_arrangement)
        self.arrange_button.setEnabled(False)
        self.export_button = QPushButton("Export")
        self.export_button.setToolTip("Export display report (⌘E)")
        self.export_button.clicked.connect(self.export_capture)
        side.addWidget(self.refresh_button)
        side.addWidget(self.arrange_button)
        side.addWidget(self.export_button)

        detail = QWidget(objectName="detail")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(16)
        self.heading = DoubleClickLabel("Display information")
        heading_font = self.heading.font()
        heading_font.setBold(True)
        self.heading.setFont(heading_font)
        self.heading.setTextFormat(Qt.TextFormat.RichText)
        self.heading.doubleClicked.connect(self._edit_selected_friendly_name)
        self.heading.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.heading.customContextMenuRequested.connect(
            self._show_heading_friendly_name_menu)
        detail_layout.addWidget(self.heading)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._show_tab_context_menu)
        detail_layout.addWidget(self.tabs, 1)
        self.overview_page = QWidget()
        self.resolution_page = ModeSummaryTable(
            ("Desktop Resolution", "Scale", "Framebuffer pixels", "Refresh rates"),
            "Search resolution, scale or refresh rate", show_rotation_selector=True)
        self.resolution_page.double_click_uses_preferred_resolution = True
        self.resolution_page.modeSelected.connect(self._request_mode_switch)
        self.resolution_page.rotationSelected.connect(self._request_rotation_switch)
        self.color_page = ModeSummaryTable(
            ("Encoding", "Range", "Bit depth", "HDR", "Mode entries"), "Search RGB / YCbCr, bit depth or HDR")
        self.color_page.double_click_selects_mode = True
        self.color_page.modeSelected.connect(self._request_color_mode_switch)
        self.icc_page = ICCProfileTable()
        self.icc_page.profileSelected.connect(self._request_icc_profile)
        self.all_page = ModeTable()
        self.all_page.modeSelected.connect(self._request_all_mode_switch)
        self.hardware_page = QWidget()
        self._add_scrollable_tab(self.overview_page, "Overview")
        self._add_scrollable_tab(self.resolution_page, "Resolution")
        self.tabs.currentChanged.connect(self._tab_changed)
        self._add_scrollable_tab(self.color_page, "Color")
        self._add_scrollable_tab(self.icc_page, "ICC")
        self._add_scrollable_tab(self.all_page, "Catalog")
        self._add_scrollable_tab(self.hardware_page, "Hardware")
        self._build_overview_page()
        self._build_hardware_page()
        edid_page = QWidget()
        self.edid_page = edid_page
        edid_layout = QVBoxLayout(edid_page)
        edid_layout.setContentsMargins(4, 12, 4, 4)
        self.edid_text = QTextEdit()
        self.edid_text.setReadOnly(True)
        self.edid_button = QPushButton("Get EDID")
        self.edid_button.clicked.connect(self.get_selected_edid)
        edid_row = QHBoxLayout()
        edid_row.addStretch(1)
        edid_row.addWidget(self.edid_button)
        edid_layout.addWidget(self.edid_text, 1)
        edid_layout.addLayout(edid_row)
        self._add_scrollable_tab(edid_page, "EDID")
        self.connection_text = self._add_text_tab("Connection")
        self.profile_text = self._add_text_tab("Profile")
        self.diagnostics_page = self.connection_text

        splitter.addWidget(sidebar)
        splitter.addWidget(detail)
        splitter.setSizes([120, 720])
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

    def _create_tab_context_menu(self):
        menu = QMenu(self)
        for index in range(self.tabs.count()):
            action = menu.addAction(self.tabs.tabText(index))
            action.setCheckable(True)
            action.setChecked(index == self.tabs.currentIndex())
            action.triggered.connect(
                lambda _checked=False, tab_index=index: self.tabs.setCurrentIndex(tab_index))
        return menu

    def _show_tab_context_menu(self, position):
        self._create_tab_context_menu().exec(self.tabs.tabBar().mapToGlobal(position))

    def _add_text_tab(self, title):
        text = QTextEdit()
        text.setReadOnly(True)
        self._add_scrollable_tab(text, title)
        return text

    def _add_scrollable_tab(self, content: QWidget, title: str):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content.setObjectName("tabContent")
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return scroll

    def _tab_changed(self, index: int):
        page_container = self.tabs.widget(index)
        page = page_container.widget() if isinstance(page_container, QScrollArea) else page_container
        if not self.display_list.count():
            return
        if page in (self.resolution_page, self.color_page, self.all_page, self.icc_page):
            # Returning to a functional tab is an explicit navigation request:
            # restore the original behavior and reveal the current row again.
            page.reset_current_scroll()
        self.show_display(self.display_list.currentRow())
        if page in (self.resolution_page, self.color_page, self.all_page, self.icc_page):
            page.scroll_to_current()
        if page is self.icc_page:
            self._load_icc_profiles(force=True)
        if ((self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning())):
            return
        # Refresh through the standard capture + deferred scan path. The
        # lighter CADisplay-only snapshot omits desktop modes and can blank
        # Resolution / Color rows and the current ICC marker.
        self.refresh()

    def _request_mode_switch(self, display_id: str, mode: dict):
        display = self._display_record_for_id(display_id)
        if display is not None:
            is_mirror_slave, _has_mirror_children = display_mirror_roles(
                display, self.report.get("devices", []))
            if is_mirror_slave and not same_desktop_resolution(
                    mode, display.get("framebufferMode") or {}):
                self.statusBar().showMessage(
                    "A mirrored display can only change refresh rate, not resolution.", 6000)
                return
        self._launch_mode_switch(display_id, mode, "desktop")

    def _request_color_mode_switch(self, display_id: str, mode: dict):
        target = color_switch_target(mode)
        if target is None:
            if mode.get("current"):
                self.statusBar().showMessage("This color mode is already active.", 5000)
            else:
                self.statusBar().showMessage(
                    "This color mode is not available at the current resolution and refresh rate.",
                    6000)
            return
        self._launch_mode_switch(display_id, target, "color")

    def _request_all_mode_switch(self, display_id: str, mode: dict):
        display = self._display_record_for_id(display_id)
        if display is None:
            self.statusBar().showMessage("Display is no longer available. Refresh and try again.", 6000)
            return
        is_mirror_slave, _has_mirror_children = display_mirror_roles(
            display, self.report.get("devices", []))
        current_mode = display.get("currentMode") or {}
        selectable, reason = all_mode_availability(mode, current_mode, is_mirror_slave)
        if not selectable:
            if reason:
                self.statusBar().showMessage(reason, 6000)
            return
        self._launch_mode_switch(display_id, mode, "all")

    def _request_rotation_switch(self, display_id: str, angle: int):
        display = self._display_record_for_id(display_id)
        if display is not None:
            is_mirror_slave, has_mirror_children = display_mirror_roles(
                display, self.report.get("devices", []))
            if is_mirror_slave or has_mirror_children:
                self.statusBar().showMessage(
                    "Orientation cannot be changed while this display is in a mirror group.", 6000)
                return
        if self.mode_switch_process is not None:
            return
        if getattr(sys, "frozen", False):
            binary = Path(getattr(sys, "_MEIPASS", ROOT)) / "display-rotation-switch"
        else:
            binary = ROOT / ".build" / "display-rotation-switch"
            if not binary.is_file():
                try:
                    binary.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run([
                        "/usr/bin/clang", "-fobjc-arc", "-framework", "Foundation",
                        "-framework", "CoreGraphics",
                        str(ROOT / "tools" / "display-rotation-switch.m"), "-o", str(binary),
                    ], check=True, capture_output=True, text=True, timeout=45)
                except (OSError, subprocess.SubprocessError) as exc:
                    detail = getattr(exc, "stderr", "") or str(exc)
                    QMessageBox.warning(self, APP_TITLE, f"Unable to prepare the rotation control:\n\n{detail[-2500:]}")
                    self.refresh()
                    return
        if not binary.is_file():
            QMessageBox.warning(self, APP_TITLE, "The rotation helper is missing. Restart the UI and try again.")
            self.refresh()
            return
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments([str(display_id), str(angle)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._mode_switch_output_ready)
        process.finished.connect(self._mode_switch_finished)
        self.mode_switch_process = process
        self.mode_switch_output = ""
        self.mode_switch_response_sent = False
        self.mode_switch_kind = "rotation"
        self.refresh_button.setEnabled(False)
        self.arrange_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.resolution_page.rotation_combo.setEnabled(False)
        self.statusBar().showMessage("Preparing the selected display orientation…")
        process.start()

    def _display_record_for_id(self, display_id: str):
        return next((display for display in self.report.get("devices", [])
                     if str(display.get("displayId")) == str(display_id)), None)

    def _launch_mode_switch(self, display_id: str, mode: dict, kind: str):
        if self.mode_switch_process is not None:
            return
        required = (("modeID", "width", "height", "pixelWidth", "pixelHeight", "refreshRate", "ioFlags")
                    if kind == "desktop" else ("modeID", "width", "height", "refreshRate", "colorMode", "bitDepth", "hdrMode"))
        if any(mode.get(key) is None for key in required):
            QMessageBox.warning(self, APP_TITLE, "This exact mode is not available for a safe mode switch.")
            return

        if getattr(sys, "frozen", False):
            helper_name = {"desktop": "display-mode-switch", "color": "display-color-mode-switch",
                           "all": "display-all-mode-switch"}.get(kind, "display-color-mode-switch")
            binary = Path(getattr(sys, "_MEIPASS", ROOT)) / helper_name
        else:
            helper_names = {"desktop": ("display-mode-switch", "display-mode-switch.m"),
                            "color": ("display-color-mode-switch", "display-color-mode-switch.m"),
                            "all": ("display-all-mode-switch", "display-all-mode-switch.m")}
            binary_name, source_name = helper_names.get(kind, helper_names["color"])
            binary = ROOT / ".build" / binary_name
            source = ROOT / "tools" / source_name
            needs_build = (not binary.is_file() or
                           (source.is_file() and
                            source.stat().st_mtime_ns > binary.stat().st_mtime_ns))
            if needs_build:
                try:
                    binary.parent.mkdir(parents=True, exist_ok=True)
                    frameworks = (("-framework", "Foundation", "-framework", "CoreGraphics")
                                  if kind == "desktop" else
                                  ("-framework", "Foundation", "-framework", "QuartzCore",
                                   "-framework", "CoreGraphics"))
                    subprocess.run(["/usr/bin/clang", "-fobjc-arc", *frameworks,
                                    str(source), "-o", str(binary)],
                                   check=True, capture_output=True, text=True, timeout=45)
                except (OSError, subprocess.SubprocessError) as exc:
                    detail = getattr(exc, "stderr", "") or str(exc)
                    QMessageBox.warning(self, APP_TITLE, f"Unable to prepare the display mode switcher:\n\n{detail[-2500:]}")
                    return
        if not binary.is_file():
            QMessageBox.warning(self, APP_TITLE, "The display mode switch helper is missing. Rebuild the app and try again.")
            return

        arguments = ([str(display_id)] + [str(mode[key]) for key in required] if kind == "desktop"
                     else [str(display_id), str(mode["modeID"])])
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._mode_switch_output_ready)
        process.finished.connect(self._mode_switch_finished)
        self.mode_switch_process = process
        self.mode_switch_output = ""
        self.mode_switch_response_sent = False
        self.mode_switch_kind = kind
        self.refresh_button.setEnabled(False)
        self.arrange_button.setEnabled(False)
        self.export_button.setEnabled(False)
        if self.resolution_page.rotation_combo is not None:
            self.resolution_page.rotation_combo.setEnabled(False)
        self.statusBar().showMessage("Preparing the selected display mode…")
        process.start()

    def _mode_switch_output_ready(self):
        process = self.mode_switch_process
        if process is None:
            return
        self.mode_switch_output += bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if "PREVIEW_APPLIED\n" in self.mode_switch_output and not self.mode_switch_response_sent:
            self.mode_switch_response_sent = True
            process.write(b"keep\n")
            status = ("Applying the selected display orientation…" if self.mode_switch_kind == "rotation"
                      else "Applying the color mode for this session…" if self.mode_switch_kind == "color"
                      else "Applying the selected mode for this session…" if self.mode_switch_kind == "all"
                      else "Saving the selected display mode…")
            self.statusBar().showMessage(status)

    def _mode_switch_finished(self, exit_code: int, exit_status):
        process = self.mode_switch_process
        if process is None:
            return
        self.mode_switch_output += bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        output = self.mode_switch_output.strip()
        switch_kind = self.mode_switch_kind
        self.mode_switch_process = None
        self.refresh_button.setEnabled(True)
        self.arrange_button.setEnabled(self.display_list.count() > 0)
        self.export_button.setEnabled(True)
        if self.resolution_page.rotation_combo is not None:
            self.resolution_page.rotation_combo.setEnabled(True)
        if "SAVED" in output and exit_code == 0:
            self.statusBar().showMessage("Display mode changed and saved.", 10000)
            self.refresh()
        elif "KEPT_SESSION_ONLY" in output and exit_code == 0:
            message = ("Display orientation changed." if self.mode_switch_kind == "rotation"
                       else "Display mode changed for the current session." if self.mode_switch_kind == "all"
                       else "Color mode changed for the current session.")
            self.statusBar().showMessage(message, 10000)
            if switch_kind != "rotation":
                self.refresh()
        elif "ERROR:" in output or exit_code != 0:
            detail = next((line.removeprefix("ERROR: ") for line in output.splitlines()
                           if line.startswith("ERROR:")), output or f"Helper exited with status {exit_code}.")
            QMessageBox.warning(self, APP_TITLE, f"Display mode switch did not complete:\n\n{detail}")
            self.statusBar().showMessage("Display mode switch failed.", 10000)
        elif "REVERTED" in output:
            self.statusBar().showMessage("Previous display mode restored.", 10000)
        if switch_kind == "rotation":
            # macOS briefly reports an incomplete desktop-mode list while it
            # rebuilds display modes after a rotation. Read again after it settles.
            QTimer.singleShot(1200, self.refresh)
        process.deleteLater()

    def _build_overview_page(self):
        self.overview_layout = QVBoxLayout(self.overview_page)
        self.overview_layout.setContentsMargins(2, 12, 8, 10)
        self.overview_layout.setSpacing(24)

    def _build_hardware_page(self):
        layout = QVBoxLayout(self.hardware_page)
        layout.setContentsMargins(4, 12, 4, 4)
        self.hardware_form = QFormLayout()
        self.hardware_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.hardware_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.hardware_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.hardware_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.hardware_form.setHorizontalSpacing(18)
        self.hardware_form.setVerticalSpacing(11)
        layout.addLayout(self.hardware_form)
        layout.addWidget(QLabel("Raw display information", objectName="muted"))
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFont(QFont("Menlo", 10))
        layout.addWidget(self.raw_text, 1)
        row = QHBoxLayout()
        self.open_folder_button = QPushButton("Show Export in Finder")
        self.open_folder_button.clicked.connect(self.open_capture_folder)
        self.open_folder_button.setEnabled(False)
        row.addStretch(1)
        row.addWidget(self.open_folder_button)
        layout.addLayout(row)

    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        if self.connection_worker and self.connection_worker.isRunning():
            self.statusBar().showMessage("Connection hardware scan is still running…", 5000)
            return
        self._set_refresh_controls_busy(True)
        self.refresh_button.setEnabled(False)
        self.arrange_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.statusBar().showMessage("Reading displays and modes…")
        self._load_icc_profiles()
        self.worker = CaptureWorker(parent=self)
        self.worker.completed.connect(self._capture_complete)
        self.worker.failed.connect(self._capture_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _set_refresh_controls_busy(self, busy: bool):
        for button in (self.refresh_button, self.arrange_button, self.export_button):
            button.setProperty("refreshBusy", busy)
            style = button.style()
            style.unpolish(button)
            style.polish(button)
            button.update()

    def _load_icc_profiles(self, force: bool = False):
        if self.icc_worker is not None and self.icc_worker.isRunning():
            return
        if self.icc_profiles and not force:
            return
        worker = ICCProfilesWorker(self)
        worker.completed.connect(self._icc_profiles_loaded)
        worker.failed.connect(self._icc_profiles_failed)
        worker.finished.connect(self._icc_worker_finished)
        self.icc_worker = worker
        worker.start()

    def _request_icc_profile(self, profile: dict):
        item = self.display_list.currentItem()
        display = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        display_id = str((display or {}).get("displayId") or "")
        self._apply_icc_profile_to_display(display_id, profile)

    def _request_arrangement_icc_profile(self, display_id: str, profile: dict):
        self._apply_icc_profile_to_display(str(display_id), profile)

    def _apply_icc_profile_to_display(self, display_id: str, profile: dict):
        profile_url = str(profile.get("url") or "")
        if not display_id or not profile_url:
            self.statusBar().showMessage("Select a display and an ICC profile first.", 6000)
            return
        if self.icc_apply_worker is not None and self.icc_apply_worker.isRunning():
            return
        worker = ICCApplyWorker(display_id, profile_url, self)
        worker.completed.connect(self._icc_profile_applied)
        worker.finished.connect(self._icc_apply_worker_finished)
        self.icc_apply_worker = worker
        self.statusBar().showMessage("Applying ICC profile…")
        worker.start()

    def _icc_profile_applied(self, success: bool, detail: str):
        if success:
            self.statusBar().showMessage("ICC profile applied. Updating display information…", 8000)
            self._refresh_after_icc_apply = True
            self._refresh_after_icc_apply_if_idle()
        else:
            message = detail or "ColorSync could not apply this profile to the selected display."
            self.statusBar().showMessage(f"Could not apply ICC profile: {message}", 12000)

    def _icc_apply_worker_finished(self):
        self.icc_apply_worker = None
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def _refresh_after_icc_apply_if_idle(self):
        if not self._refresh_after_icc_apply:
            return
        if ((self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning())):
            return
        self._refresh_after_icc_apply = False
        QTimer.singleShot(0, self.refresh)

    def _icc_profiles_loaded(self, profiles: list):
        self.icc_profiles_error = ""
        available_profiles = list(profiles)
        for row in range(self.display_list.count()):
            item = self.display_list.item(row)
            display = item.data(Qt.ItemDataRole.UserRole) or {}
            profile = dict(display.get("colorProfile") or {})
            key = ICCProfileTable._path_key(profile.get("url"))
            name = icc_profile_display_name(profile).casefold()
            matched = next((candidate for candidate in available_profiles
                            if ((key and ICCProfileTable._path_key(candidate.get("url")) == key) or
                                (name and str(candidate.get("name") or "").strip().casefold() == name))), None)
            if matched is not None:
                profile.update(matched)
                display["colorProfile"] = profile
                item.setData(Qt.ItemDataRole.UserRole, display)
            elif (profile.get("url") and (key or name) and
                  not any(key and ICCProfileTable._path_key(candidate.get("url")) == key
                          for candidate in available_profiles)):
                available_profiles.append({
                    "name": icc_profile_display_name(profile),
                    "created": "", "url": profile.get("url"),
                })
            for current_display in self.report.get("devices", []):
                if str(current_display.get("displayId")) == str(display.get("displayId")):
                    current_display["colorProfile"] = profile
                    break
            if self.arrangement_dialog is not None:
                self.arrangement_dialog.update_profile_for_display(
                    str(display.get("displayId")), profile)
        self.icc_profiles = available_profiles
        if self.arrangement_dialog is not None:
            self.arrangement_dialog.set_icc_profiles(available_profiles)
        if self.display_list.currentRow() >= 0:
            self.show_display(self.display_list.currentRow())

    def _icc_profiles_failed(self, message: str):
        self.icc_profiles_error = message
        if self.arrangement_dialog is not None:
            self.arrangement_dialog.set_icc_profiles([], error=message)
        self.statusBar().showMessage(f"Could not read installed ICC profiles: {message}", 10000)

    def _icc_worker_finished(self):
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def export_capture(self):
        if self.worker and self.worker.isRunning():
            return
        if self.connection_worker and self.connection_worker.isRunning():
            self.statusBar().showMessage("Wait for the background connection scan to finish before exporting.", 5000)
            return
        destination = QFileDialog.getExistingDirectory(self, "Choose Export Location", str(Path.home()))
        if not destination:
            return
        folder_name, accepted = QInputDialog.getText(self, "Export Display Report", "Folder name:")
        if not accepted:
            return
        folder_name = folder_name.strip()
        if not folder_name or folder_name in {".", ".."} or Path(folder_name).name != folder_name:
            QMessageBox.warning(self, APP_TITLE, "Enter a valid folder name.")
            return
        export_path = Path(destination) / folder_name
        if export_path.exists():
            QMessageBox.warning(self, APP_TITLE, "That folder already exists. Choose another name.")
            return
        friendly_names = {
            str(self.display_list.item(row).data(Qt.ItemDataRole.UserRole).get("displayId")):
            str(self.display_list.item(row).data(Qt.ItemDataRole.UserRole).get("friendlyName") or "").strip()
            for row in range(self.display_list.count())
        }
        export_context = {
            "friendlyNamesByDisplayId": friendly_names,
            "connectionDiagnostics": self.report.get("connectionDiagnostics"),
        }
        self.refresh_button.setEnabled(False)
        self.arrange_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.statusBar().showMessage("Capturing and exporting full display diagnostics…")
        self.worker = CaptureWorker(export_path, self, export_context=export_context)
        self.worker.completed.connect(self._capture_complete)
        self.worker.failed.connect(self._capture_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def get_selected_edid(self):
        if self.worker and self.worker.isRunning():
            return
        item = self.display_list.currentItem()
        if item is None:
            return
        display = item.data(Qt.ItemDataRole.UserRole)
        self.edid_button.setEnabled(False)
        self.edid_button.setText("Reading…")
        self.statusBar().showMessage("Reading EDID for selected display…")
        self.worker = CaptureWorker(parent=self, edid_display=display)
        self.worker.edid_completed.connect(self._edid_complete)
        self.worker.edid_failed.connect(self._edid_failed)
        self.worker.finished.connect(self._edid_worker_finished)
        self.worker.start()

    def _edid_complete(self, display_id: str, edid: dict):
        for row in range(self.display_list.count()):
            item = self.display_list.item(row)
            display = item.data(Qt.ItemDataRole.UserRole)
            if str(display.get("displayId")) == display_id:
                display["edid"] = edid
                item.setData(Qt.ItemDataRole.UserRole, display)
                for current in self.report.get("devices", []):
                    if str(current.get("displayId")) == display_id:
                        current["edid"] = edid
                if row == self.display_list.currentRow():
                    self._refresh_display_views(display)
                break
        self.statusBar().showMessage("EDID read complete (not exported).", 10000)

    def _edid_failed(self, message: str):
        self.statusBar().showMessage("EDID read failed.", 10000)
        QMessageBox.warning(self, APP_TITLE, f"Unable to read EDID:\n\n{message}")

    def _edid_worker_finished(self):
        self.edid_button.setEnabled(True)
        self.edid_button.setText("Get EDID")

    def _worker_finished(self):
        if self._close_pending:
            self.close()
        elif self._refresh_after_icc_apply:
            self._refresh_after_icc_apply_if_idle()
        elif self._arrangement_interaction_active():
            # Keep queued captures from replacing arrangement geometry while
            # the user is dragging or macOS is applying a layout.
            return
        elif (self._external_refresh_pending and
              not (self.connection_worker and self.connection_worker.isRunning())):
            self._external_refresh_pending = False
            QTimer.singleShot(0, self.refresh)
        elif (self._refresh_after_topology_change and
              not (self.connection_worker and self.connection_worker.isRunning())):
            self._refresh_after_topology_change = False
            QTimer.singleShot(0, self.refresh)

    def _capture_complete(self, report: dict, folder: str, exported: bool = False):
        # The lightweight capture intentionally omits the slower CoreGraphics
        # desktop-mode enumeration. Keep the last complete result visible until
        # the background scan replaces it, instead of briefly clearing tables.
        previous_displays = {
            str(self.display_list.item(row).data(Qt.ItemDataRole.UserRole).get("displayId")):
            self.display_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.display_list.count())
        }
        for display in report.get("devices", []):
            self._apply_friendly_name(display)
            previous = previous_displays.get(str(display.get("displayId")), {})
            for key in ("desktopModes", "desktopModesStatus", "connectionPath", "edid",
                        "diagnosticsSnapshotStable", "diagnosticsWarning",
                        "currentModeInAvailableModes", "ioDisplayLocation", "identityStatus"):
                if key not in display and key in previous:
                    display[key] = previous[key]
        self.report, self.folder = report, folder
        self.open_folder_button.setEnabled(bool(folder and Path(folder).is_dir()))
        displays = report.get("devices", [])
        selected_item = self.display_list.currentItem()
        previous_id = (selected_item.data(Qt.ItemDataRole.UserRole).get("displayId")
                       if selected_item else None)
        first_load = self.display_list.count() == 0
        self.display_list.blockSignals(True)
        self.display_list.clear()
        selected_row = next((index for index, display in enumerate(displays)
                             if bool((display.get('desktop') or {}).get('main'))), 0)
        for index, display in enumerate(displays):
            title = display_label(display)
            item = QListWidgetItem(title)
            item.setToolTip(display_tooltip(display))
            item.setData(Qt.ItemDataRole.UserRole, display)
            self.display_list.addItem(item)
            if str(display.get("displayId")) == str(previous_id):
                selected_row = index
        self.display_list.blockSignals(False)
        if displays:
            self.tabs.setEnabled(True)
            if first_load:
                self.tabs.setCurrentIndex(0)
            self.display_list.setCurrentRow(selected_row)
        else:
            self._clear_detail()
        self.refresh_button.setEnabled(True)
        self.arrange_button.setEnabled(bool(displays))
        self.refresh_button.setText("Refresh")
        self.export_button.setEnabled(True)
        self.export_button.setText("Export")
        self._set_refresh_controls_busy(False)
        self.statusBar().showMessage(
            (f"Exported to {folder}" if exported else "Display data refreshed (not exported)")
            + f" · {len(displays)} displays · {report.get('capturedAt', '')}", 12000
        )
        if not exported and displays:
            self._start_connection_scan(report)
        if self.arrangement_dialog is not None and self.arrangement_dialog.isVisible():
            screens, omitted = arrangement_display_records(displays)
            if self._arrangement_interaction_active():
                self._deferred_arrangement_records = (screens, omitted)
            else:
                self.arrangement_dialog.update_displays(screens, omitted)

    def _apply_friendly_name(self, display: dict):
        key = display_identity_key(display)
        display["friendlyName"] = str(self.friendly_names.value(f"FriendlyNames/{key}", "") or "").strip()

    def _edit_friendly_name(self, item: QListWidgetItem):
        if item is None:
            return
        display = item.data(Qt.ItemDataRole.UserRole) or {}
        current = str(display.get("friendlyName") or "")
        name, accepted = QInputDialog.getText(
            self, "Friendly Name", "Enter a friendly name for this display:",
            QLineEdit.EchoMode.Normal, current)
        if not accepted:
            return
        self._save_friendly_name(item, name)

    def _save_friendly_name(self, item: QListWidgetItem, name: str):
        if item is None:
            return
        display = item.data(Qt.ItemDataRole.UserRole) or {}
        name = name.strip()
        key = display_identity_key(display)
        settings_key = f"FriendlyNames/{key}"
        if name:
            self.friendly_names.setValue(settings_key, name)
            display["friendlyName"] = name
        else:
            self.friendly_names.remove(settings_key)
            display.pop("friendlyName", None)
        self.friendly_names.sync()
        item.setText(display_label(display))
        item.setToolTip(display_tooltip(display))
        item.setData(Qt.ItemDataRole.UserRole, display)
        for current_display in self.report.get("devices", []):
            if display_identity_key(current_display) == key:
                if name:
                    current_display["friendlyName"] = name
                else:
                    current_display.pop("friendlyName", None)
                break
        if item is self.display_list.currentItem():
            self.show_display(self.display_list.currentRow())
        if self.arrangement_dialog is not None and self.arrangement_dialog.isVisible():
            screens, omitted = arrangement_display_records(self.report.get("devices", []))
            self.arrangement_dialog.update_displays(screens, omitted)

    def _edit_selected_friendly_name(self):
        item = self.display_list.currentItem()
        if item is not None:
            self._edit_friendly_name(item)

    def _show_display_friendly_name_menu(self, position):
        item = self.display_list.itemAt(position)
        if item is None:
            return
        self.display_list.setCurrentItem(item)
        global_position = self.display_list.viewport().mapToGlobal(position)
        self._show_friendly_name_menu(item, global_position)

    def _show_heading_friendly_name_menu(self, position):
        item = self.display_list.currentItem()
        if item is not None:
            self._show_friendly_name_menu(
                item, self.heading.mapToGlobal(position))

    def _show_friendly_name_menu(self, item: QListWidgetItem, global_position):
        if self._friendly_name_menu is not None:
            self._friendly_name_menu_item = item
            if item is self.display_list.itemAt(self.display_list.viewport().mapFromGlobal(
                    global_position)):
                self.display_list.setCurrentItem(item)
            self._populate_friendly_name_menu()
            self._friendly_name_menu.adjustSize()
            self._friendly_name_menu.move(global_position)
            self._friendly_name_menu.raise_()
            return

        menu = QMenu(self)
        self._friendly_name_menu = menu
        self._friendly_name_menu_item = item
        self._friendly_name_set_action = menu.addAction("Set friendly name")
        self._friendly_name_clear_action = menu.addAction("Clear friendly name")
        self._friendly_name_role_menu = menu.addMenu("Display role")
        self._friendly_name_set_action.triggered.connect(self._set_context_friendly_name)
        self._friendly_name_clear_action.triggered.connect(self._clear_context_friendly_name)
        menu.aboutToHide.connect(self._friendly_name_menu_hidden)
        self._populate_friendly_name_menu()
        app = QApplication.instance()
        if app is not None:
            self._friendly_name_menu_app = app
            app.installEventFilter(self)
        menu.popup(global_position)

    def _populate_friendly_name_menu(self):
        item = self._friendly_name_menu_item
        display = item.data(Qt.ItemDataRole.UserRole) if item is not None else {}
        self._friendly_name_clear_action.setEnabled(
            bool(str((display or {}).get("friendlyName") or "").strip()))
        role_menu = self._friendly_name_role_menu
        if role_menu is None:
            return
        role_menu.clear()
        display = display or {}
        display_id = str(display.get("displayId") or "")
        for label, role, source_id, checked, enabled in display_role_menu_options(
                display, self.report.get("devices", [])):
            action = role_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(checked)
            action.setEnabled(bool(display_id) and enabled)
            desktop = display.get("desktop") or {}
            if role == "extended" and bool(display.get("main", desktop.get("main", False))):
                action.setToolTip(
                    "The nearest available display will become Main Display.")
            action.triggered.connect(
                lambda _checked=False, did=display_id, selected_role=role, target=source_id:
                self._request_display_role_change(did, selected_role, target))

    def _request_display_role_change(self, display_id: str, role: str,
                                     mirror_source_id: str = ""):
        arguments = ["--role", str(display_id), str(role)]
        if role == "mirror":
            arguments.append(str(mirror_source_id))
        if self.mode_switch_process is not None:
            self.statusBar().showMessage("Wait for the current display change to finish.", 5000)
            return
        if ((self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning()) or
                (self.icc_apply_worker and self.icc_apply_worker.isRunning())):
            self.statusBar().showMessage("Wait for the current display operation to finish.", 5000)
            return
        dialog = self.arrangement_dialog
        if dialog is not None and dialog.isVisible():
            self._refresh_after_topology_change = True
            dialog._start_arrangement_apply(arguments)
            return
        if self._role_helper_process is not None or self._role_helper_build_process is not None:
            self.statusBar().showMessage("A display role change is already in progress.", 5000)
            return
        binary = arrangement_helper_path()
        source = ROOT / "tools" / "display-arrangement.m"
        try:
            needs_build = (not binary.is_file() or
                           source.stat().st_mtime_ns > binary.stat().st_mtime_ns)
        except OSError:
            needs_build = True
        if getattr(sys, "frozen", False):
            needs_build = not binary.is_file()
        if needs_build:
            if getattr(sys, "frozen", False):
                self.statusBar().showMessage(
                    "Display role control is unavailable. Rebuild the app.", 7000)
                return
            try:
                binary.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.statusBar().showMessage(f"Could not prepare display role control: {exc}", 7000)
                return
            self._pending_role_arguments = arguments
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            process.finished.connect(self._role_helper_build_finished)
            process.errorOccurred.connect(self._role_helper_build_error)
            self._role_helper_build_process = process
            self._set_role_change_busy(True)
            process.start("/usr/bin/clang", [
                "-fobjc-arc", "-framework", "Foundation", "-framework", "CoreGraphics",
                str(source), "-o", str(binary),
            ])
            self.statusBar().showMessage("Preparing display role control…")
            return
        self._run_display_role_helper(arguments)

    def _role_helper_build_finished(self, exit_code: int, _exit_status):
        process = self._role_helper_build_process
        if process is None:
            return
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        self._role_helper_build_process = None
        arguments = self._pending_role_arguments
        self._pending_role_arguments = None
        if exit_code != 0 or not arrangement_helper_path().is_file():
            self._set_role_change_busy(False)
            self.statusBar().showMessage(
                "Could not prepare display role control: " +
                (error or "The helper could not be built."), 10000)
            self._maybe_close_after_role_operation()
            return
        if arguments:
            self._run_display_role_helper(arguments)

    def _role_helper_build_error(self, error):
        if error != QProcess.ProcessError.FailedToStart:
            return
        process = self._role_helper_build_process
        if process is None:
            return
        message = process.errorString()
        process.deleteLater()
        self._role_helper_build_process = None
        self._pending_role_arguments = None
        self._set_role_change_busy(False)
        self.statusBar().showMessage(f"Could not start display role control: {message}", 8000)
        self._maybe_close_after_role_operation()

    def _run_display_role_helper(self, arguments: list[str]):
        process = QProcess(self)
        process.setProgram(str(arrangement_helper_path()))
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._display_role_helper_finished)
        process.errorOccurred.connect(self._display_role_helper_error)
        self._role_helper_process = process
        self._set_role_change_busy(True)
        self.statusBar().showMessage("Applying display role…")
        process.start()

    def _display_role_helper_finished(self, exit_code: int, _exit_status):
        process = self._role_helper_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        self._role_helper_process = None
        self._set_role_change_busy(False)
        if exit_code != 0:
            self.statusBar().showMessage(
                "Could not apply display role: " +
                (error or output or "macOS rejected the change."), 10000)
            self._maybe_close_after_role_operation()
            return
        self.statusBar().showMessage("Display role updated.", 5000)
        self._last_arrangement_active_ids = None
        self._last_arrangement_main_id = None
        self.refresh()
        self._maybe_close_after_role_operation()

    def _display_role_helper_error(self, error):
        if error != QProcess.ProcessError.FailedToStart:
            return
        process = self._role_helper_process
        if process is None:
            return
        message = process.errorString()
        process.deleteLater()
        self._role_helper_process = None
        self._set_role_change_busy(False)
        self.statusBar().showMessage(f"Could not start display role control: {message}", 8000)
        self._maybe_close_after_role_operation()

    def _maybe_close_after_role_operation(self):
        if (self._close_pending and self._role_helper_process is None and
                self._role_helper_build_process is None):
            QTimer.singleShot(0, self.close)

    def _set_role_change_busy(self, busy: bool):
        self.refresh_button.setEnabled(not busy)
        self.arrange_button.setEnabled(not busy and self.display_list.count() > 0)
        self.export_button.setEnabled(not busy)
        self.tabs.setEnabled(not busy)

    def _set_context_friendly_name(self):
        # QAction is triggered while QMenu is still active. Starting the
        # modal text dialog synchronously here can leave the menu consuming
        # the click (or clear its target first on some Qt/platform versions).
        # Capture the item now, then open the dialog after the menu closes.
        item = self._friendly_name_menu_item
        if item is not None:
            QTimer.singleShot(0, lambda target=item: self._edit_friendly_name(target))

    def _clear_context_friendly_name(self):
        if self._friendly_name_menu_item is not None:
            self._save_friendly_name(self._friendly_name_menu_item, "")

    def _friendly_name_menu_hidden(self):
        if self._friendly_name_menu_app is not None:
            self._friendly_name_menu_app.removeEventFilter(self)
        menu = self._friendly_name_menu
        self._friendly_name_menu = None
        self._friendly_name_menu_app = None
        if menu is not None:
            menu.deleteLater()

    def eventFilter(self, watched, event):
        if (self._friendly_name_menu is not None and
                event.type() == QEvent.Type.MouseButtonPress and
                event.button() == Qt.MouseButton.RightButton):
            global_position = event.globalPosition().toPoint()
            target = self._friendly_name_target_at(global_position)
            if target is not None:
                item, is_sidebar = target
                self._friendly_name_menu_item = item
                if is_sidebar:
                    self.display_list.setCurrentItem(item)
                self._populate_friendly_name_menu()
                self._friendly_name_menu.adjustSize()
                self._friendly_name_menu.move(global_position)
                self._friendly_name_menu.raise_()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _friendly_name_target_at(self, global_position):
        if self.heading.rect().contains(self.heading.mapFromGlobal(global_position)):
            item = self.display_list.currentItem()
            return (item, False) if item is not None else None
        widget = QApplication.widgetAt(global_position)
        while widget is not None and widget not in (self.display_list, self.display_list.viewport()):
            widget = widget.parentWidget()
        if widget is not None:
            position = self.display_list.viewport().mapFromGlobal(global_position)
            item = self.display_list.itemAt(position)
            if item is not None:
                return item, True
        return None

    def _watch_screen(self, screen):
        key = id(screen)
        if key in self._watched_screens:
            return
        try:
            screen.geometryChanged.connect(self._schedule_arrangement_refresh)
            screen.geometryChanged.connect(self._screen_geometry_changed)
            self._watched_screens[key] = screen
        except (AttributeError, RuntimeError):
            pass

    def _arrangement_interaction_active(self):
        dialog = self.arrangement_dialog
        if dialog is None or not dialog.isVisible():
            return False
        canvas = dialog.canvas
        return (canvas._drag_screen is not None or canvas._primary_bar_dragging or
                dialog._layout_apply_pending or
                dialog._apply_process is not None or
                dialog._active_display_menu is not None)

    def _screen_geometry_changed(self, *_args):
        dialog = self.arrangement_dialog
        if dialog is not None and dialog._apply_process is not None:
            # Applying a layout generates CoreGraphics geometry events of its
            # own. Ignore them while the helper is running; the local drop
            # coordinates remain on canvas without a corrective redraw.
            return
        if (dialog is not None and
                time.monotonic() < dialog._ignore_own_layout_events_until):
            return
        if dialog is not None:
            dialog._hold_local_layout = False
        if self._arrangement_interaction_active():
            self._external_refresh_pending = True
            self._arrangement_refresh_pending = True
            return
        self._external_refresh_timer.start()

    def _screen_added(self, screen):
        self._watch_screen(screen)
        if self.arrangement_dialog is not None:
            self.arrangement_dialog._hold_local_layout = False
        if self._arrangement_interaction_active():
            self._external_refresh_pending = True
            self._arrangement_refresh_pending = True
            return
        self._schedule_arrangement_refresh()
        self._external_refresh_timer.start()

    def _screen_removed(self, screen):
        self._watched_screens.pop(id(screen), None)
        if self.arrangement_dialog is not None:
            self.arrangement_dialog._hold_local_layout = False
        if self._arrangement_interaction_active():
            self._external_refresh_pending = True
            self._arrangement_refresh_pending = True
            return
        self._schedule_arrangement_refresh()
        self._external_refresh_timer.start()

    def _display_reconfiguration_changed(self, _display_id: int, _flags: int):
        dialog = self.arrangement_dialog
        if dialog is not None and dialog._apply_process is not None:
            return
        if (dialog is not None and
                time.monotonic() < dialog._ignore_own_layout_events_until):
            return
        if dialog is not None:
            dialog._hold_local_layout = False
        if self._arrangement_interaction_active():
            self._arrangement_refresh_pending = True
            self._external_refresh_pending = True
            return
        self._schedule_arrangement_refresh()
        self._external_refresh_timer.start()

    def _schedule_arrangement_refresh(self, *_args):
        dialog = self.arrangement_dialog
        if dialog is None or not dialog.isVisible():
            return
        if dialog._apply_process is not None:
            # The active helper returns the applied layout, so its own display
            # configuration notifications need no follow-up query.
            return
        if (dialog.canvas._drag_screen is not None or dialog.canvas._primary_bar_dragging or
                dialog._active_display_menu is not None):
            self._arrangement_refresh_pending = True
            return
        self._arrangement_refresh_timer.start(250)

    def _query_arrangement_layout(self):
        dialog = self.arrangement_dialog
        if dialog is None or not dialog.isVisible():
            return
        if (dialog.canvas._drag_screen is not None or
                dialog.canvas._primary_bar_dragging or
                dialog._active_display_menu is not None):
            self._arrangement_refresh_pending = True
            return
        if dialog._apply_process is not None:
            return
        if self._arrangement_query_process is not None:
            self._arrangement_refresh_timer.start(300)
            return
        if not dialog.ensure_arrangement_helper():
            return
        binary = arrangement_helper_path()
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setArguments([])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.finished.connect(self._arrangement_layout_queried)
        process.errorOccurred.connect(self._arrangement_query_error)
        self._arrangement_query_process = process
        process.start()

    def _arrangement_layout_queried(self, exit_code: int, _exit_status):
        process = self._arrangement_query_process
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        error = bytes(process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        process.deleteLater()
        self._arrangement_query_process = None
        if exit_code != 0:
            message = error or output or f"Display layout query exited with status {exit_code}."
            if message != self._arrangement_query_error_text:
                self._arrangement_query_error_text = message
                self.statusBar().showMessage(
                    f"Unable to refresh display arrangement: {message}", 10000)
            return
        self._arrangement_query_error_text = ""
        if self.arrangement_dialog is None or not self.arrangement_dialog.isVisible():
            return
        canvas = self.arrangement_dialog.canvas
        if self.arrangement_dialog._apply_process is not None:
            return
        if (canvas._drag_screen is not None or canvas._primary_bar_dragging or
                self.arrangement_dialog._active_display_menu is not None):
            self._arrangement_refresh_pending = True
            return
        try:
            actual = json.loads(output)
        except json.JSONDecodeError:
            return
        if not isinstance(actual, list):
            return
        previous = {str(screen["displayId"]): screen for screen in self.arrangement_dialog.canvas.screens}
        actual_by_id = {}
        for item in actual:
            display_id = str(item.get("displayId", ""))
            if not display_id:
                continue
            actual_by_id[display_id] = item

        current_ids = set(actual_by_id)
        current_main = next((display_id for display_id, item in actual_by_id.items()
                             if item.get("main")), None)
        # Keep the richer capture's display records, including mirror members
        # that CGGetActiveDisplayList may omit. The fast poll is a geometry
        # update source, not an authoritative replacement for the device list.
        screens = []
        for display_id, previous_screen in previous.items():
            screen = dict(previous_screen)
            item = actual_by_id.get(display_id)
            if item is not None:
                screen.update({key: item[key] for key in (
                    "x", "y", "width", "height", "main") if key in item})
            screens.append(screen)
        for display_id, item in actual_by_id.items():
            if display_id in previous:
                continue
            screen = {
                "name": f"Display {display_id}",
                "width": item.get("width", 0),
                "height": item.get("height", 0),
                "displayId": display_id,
            }
            screen.update({key: item[key] for key in ("x", "y", "width", "height", "main")
                           if key in item})
            screens.append(screen)
        if not screens:
            return
        topology_changed_since_last_poll = (
            self._last_arrangement_active_ids is not None and
            current_ids != self._last_arrangement_active_ids
        )
        primary_changed_since_last_poll = (
            self._last_arrangement_main_id is not None and
            current_main != self._last_arrangement_main_id
        )
        self._last_arrangement_active_ids = frozenset(current_ids)
        self._last_arrangement_main_id = current_main
        if (arrangement_layout_signature(screens) !=
                arrangement_layout_signature(self.arrangement_dialog.canvas.screens)):
            self.arrangement_dialog.update_displays(screens, 0)
        # A persistent difference between the fast CoreGraphics list and the
        # richer device capture (notably omitted mirror members) is not itself
        # a topology change. Refresh only when the fast poll changes relative
        # to its own previous result, so repeated polls cannot rebuild/flash the
        # main window or erase a mirrored card.
        if topology_changed_since_last_poll or primary_changed_since_last_poll:
            if ((self.worker and self.worker.isRunning()) or
                    (self.connection_worker and self.connection_worker.isRunning())):
                self._refresh_after_topology_change = True
            else:
                self.refresh()

    def _arrangement_query_error(self, error):
        if error != QProcess.ProcessError.FailedToStart or self._arrangement_query_process is None:
            return
        process = self._arrangement_query_process
        process.deleteLater()
        self._arrangement_query_process = None

    def _start_connection_scan(self, report: dict):
        if not report or (self.connection_worker and self.connection_worker.isRunning()):
            return
        self.connection_scan_error = ""
        self.connection_worker = ConnectionWorker(report, self)
        self.connection_worker.completed.connect(self._connection_scan_complete)
        self.connection_worker.failed.connect(self._connection_scan_failed)
        self.connection_worker.finished.connect(self._worker_finished)
        self.statusBar().showMessage("Display modes loaded; reading resolutions, color profile, and connection hardware in background…")
        self.connection_worker.start()

    def _refresh_after_external_change(self):
        if not self.isVisible() or not self.report.get("devices"):
            return
        dialog = self.arrangement_dialog
        if self._arrangement_interaction_active():
            self._external_refresh_pending = True
            return
        if dialog is not None and dialog._apply_process is not None:
            # Geometry callbacks caused by our own arrange operation are
            # reconciled from its focused layout result, not by a full capture.
            return
        if ((self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning())):
            self._external_refresh_pending = True
            return
        self.refresh()

    def _poll_live_state(self):
        if (not self.isVisible() or not self.report.get("devices") or
                (self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning()) or
                self.mode_switch_process is not None):
            return
        if self._arrangement_interaction_active():
            return
        worker = CaptureWorker(parent=self, snapshot_only=True)
        worker.completed.connect(self._live_state_complete)
        worker.failed.connect(self._live_state_failed)
        worker.finished.connect(self._worker_finished)
        self.worker = worker
        worker.start()

    def _live_state_complete(self, report: dict, _folder: str, _exported: bool = False):
        previous = self.report.get("devices", [])
        previous_by_id = {str(display.get("displayId")): display for display in previous}
        fresh = report.get("devices", [])
        if {str(display.get("displayId")) for display in fresh} != set(previous_by_id):
            self._external_refresh_timer.start(0)
            return
        merged_devices = []
        active_profile_changed = False
        for updated in fresh:
            display_id = str(updated.get("displayId"))
            old = previous_by_id[display_id]
            merged = dict(old)
            merged.update(updated)
            if display_id == str((self.display_list.currentItem().data(Qt.ItemDataRole.UserRole)
                                  if self.display_list.currentItem() else {}).get("displayId")):
                active_profile_changed = (old.get("colorProfile") or {}) != (merged.get("colorProfile") or {})
            for key in ("availableModes", "desktopModes", "desktopModesStatus",
                        "connectionPath", "edid", "diagnosticsSnapshotStable",
                        "diagnosticsWarning", "currentModeInAvailableModes",
                        "ioDisplayLocation", "identityStatus"):
                if updated.get(key) in (None, [], {}) and key in old:
                    merged[key] = old[key]
            merged_devices.append(merged)
        self.report["devices"] = merged_devices
        for row, display in enumerate(merged_devices):
            item = self.display_list.item(row)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, display)
                item.setToolTip(display_tooltip(display))
        row = self.display_list.currentRow()
        if row >= 0:
            self.show_display(row)
            current_page = self.tabs.currentWidget()
            current_page = current_page.widget() if isinstance(current_page, QScrollArea) else current_page
            if active_profile_changed and current_page is self.icc_page:
                self._load_icc_profiles(force=True)

    def _live_state_failed(self, _message: str):
        # A missed polling sample should never disturb the visible UI.
        return

    def _refresh_profile_page(self):
        display = (self.display_list.currentItem().data(Qt.ItemDataRole.UserRole)
                   if self.display_list.currentItem() else {})
        profile_lines = []
        if display.get('colorProfile'):
            profile_lines += ['Current ColorSync profile', json.dumps(display['colorProfile'], indent=2)]
        desktop_data = {k: display[k] for k in ('desktop', 'framebufferMode') if display.get(k) is not None}
        if desktop_data:
            profile_lines += ['', 'Desktop / framebuffer (separate from connection timing)',
                              json.dumps(desktop_data, indent=2)]
        self.profile_text.setPlainText('\n'.join(profile_lines))

    def _connection_scan_complete(self, data: dict):
        self.report["connectionDiagnostics"] = data
        details = data.get("displays", {})
        display_details = data.get("displayDetails", {})
        for row in range(self.display_list.count()):
            item = self.display_list.item(row)
            display = item.data(Qt.ItemDataRole.UserRole)
            display.update(display_details.get(str(display.get("displayId")), {}))
            extra = details.get(str(display.get("displayId")), {})
            display.update(extra)
            item.setData(Qt.ItemDataRole.UserRole, display)
            self.report.get("devices", [])[row].update(display_details.get(
                str(display.get("displayId")), {}))
            self.report.get("devices", [])[row].update(extra)
        if self.display_list.currentRow() >= 0:
            self.show_display(self.display_list.currentRow())
        current_page = self.tabs.currentWidget()
        current_page = current_page.widget() if isinstance(current_page, QScrollArea) else current_page
        if current_page is self.icc_page:
            self._load_icc_profiles(force=True)
        self.statusBar().showMessage("Resolution and connection details loaded in background.", 10000)
    def _connection_scan_failed(self, message: str):
        self.connection_scan_error = message
        if self.display_list.currentRow() >= 0:
            self._show_diagnostics(self.display_list.currentItem().data(Qt.ItemDataRole.UserRole))
        self.statusBar().showMessage("Connection hardware scan failed.", 10000)

    def _capture_failed(self, message: str):
        self._set_refresh_controls_busy(False)
        self.refresh_button.setEnabled(True)
        self.arrange_button.setEnabled(self.display_list.count() > 0)
        self.refresh_button.setText("Refresh")
        self.export_button.setEnabled(True)
        self.export_button.setText("Export")
        self.statusBar().showMessage("Capture failed.", 10000)
        QMessageBox.warning(self, APP_TITLE, f"Unable to read displays:\n\n{message}")

    def show_arrangement(self):
        self._load_icc_profiles()
        displays = [
            self.display_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.display_list.count())
        ]
        screens, omitted = arrangement_display_records(displays)
        if self.arrangement_dialog is None:
            self.arrangement_dialog = DisplayArrangementDialog(screens, omitted, self)
            self.arrangement_dialog.setModal(False)
            self.arrangement_dialog.helperReady.connect(
                lambda: self._arrangement_refresh_timer.start(0))
            self.arrangement_dialog.modeSelected.connect(
                lambda display_id, mode: self._launch_mode_switch(display_id, mode, "desktop"))
            self.arrangement_dialog.colorModeSelected.connect(
                self._request_color_mode_switch)
            self.arrangement_dialog.displaySelected.connect(
                self._select_display_from_arrangement)
            self.arrangement_dialog.orientationSelected.connect(self._request_rotation_switch)
            self.arrangement_dialog.iccProfileSelected.connect(
                self._request_arrangement_icc_profile)
            self.arrangement_dialog.displayRoleSelected.connect(
                self._request_display_role_change)
            self.arrangement_dialog.finished.connect(self._arrangement_poll_timer.stop)
            self.arrangement_dialog.dragStateChanged.connect(
                self._arrangement_drag_state_changed)
            self.arrangement_dialog.applyFinished.connect(
                self._arrangement_apply_finished)
        else:
            self.arrangement_dialog.update_displays(screens, omitted)
        self.arrangement_dialog.set_icc_profiles(
            self.icc_profiles,
            loading=bool(self.icc_worker and self.icc_worker.isRunning()),
            error=self.icc_profiles_error)
        self._configure_identify_helper(self.arrangement_dialog.canvas.screens)
        self.arrangement_dialog._ensure_identify_helper()
        self.arrangement_dialog.show()
        self.arrangement_dialog.raise_()
        self.arrangement_dialog.activateWindow()
        self._arrangement_poll_timer.start()
        self._arrangement_refresh_timer.start(0)

    def _configure_identify_helper(self, screens):
        dialog = self.arrangement_dialog
        if dialog is None:
            return
        command = {"action": "configure", "groups": identify_overlay_groups(screens),
                   "identifyColor": ARRANGEMENT_HIGHLIGHT_COLOR.name()}
        signature = json.dumps(command, ensure_ascii=False, sort_keys=True)
        if signature == self._identify_last_configuration:
            return
        self._identify_last_configuration = signature
        dialog._identify_pending_command = command
        if dialog._identify_process is not None:
            dialog._identify_send(command)

    def _set_identify_visibility(self, screens, visible: bool):
        dialog = self.arrangement_dialog
        if dialog is None:
            return
        dialog._identify_visible = bool(visible)
        dialog._identify_target_id = None
        self._configure_identify_helper(screens)
        dialog._ensure_identify_helper()
        if dialog._identify_process is not None:
            dialog._identify_send({"action": "show" if visible else "hide"})

    def _set_identify_target(self, display_id: str):
        dialog = self.arrangement_dialog
        if dialog is None or dialog._identify_pressed:
            return
        if not display_id:
            dialog._identify_visible = False
            dialog._identify_target_id = None
            dialog._identify_send({"action": "hide"})
            return
        dialog._identify_visible = True
        dialog._identify_target_id = str(display_id)
        dialog._ensure_identify_helper()
        dialog._identify_send({"action": "identify", "displayID": str(display_id)})

    def _stop_identify_helper(self):
        dialog = self.arrangement_dialog
        if dialog is None:
            return
        dialog._identify_shutting_down = True
        dialog._identify_pressed = False
        dialog._identify_mouse_held = False
        dialog._identify_keyboard_held = False
        dialog._identify_visible = False
        dialog._identify_target_id = None
        process = dialog._identify_process
        if process is not None:
            try:
                state = process.state()
                if state == QProcess.ProcessState.Running:
                    process.write(b'{"action":"quit"}\n')
                    process.closeWriteChannel()
                    if not process.waitForFinished(1000):
                        process.kill()
                        process.waitForFinished(2000)
                elif state != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(2000)
            except RuntimeError:
                pass
            dialog._identify_process = None
            try:
                if process.state() == QProcess.ProcessState.NotRunning:
                    process.deleteLater()
            except RuntimeError:
                pass
        build = dialog._identify_build_process
        if build is not None:
            try:
                if build.state() != QProcess.ProcessState.NotRunning:
                    build.kill()
                    build.waitForFinished(2000)
            except RuntimeError:
                pass
            dialog._identify_build_process = None
            try:
                build.deleteLater()
            except RuntimeError:
                pass

    def _select_display_from_arrangement(self, display_id: str):
        for row in range(self.display_list.count()):
            item = self.display_list.item(row)
            display = item.data(Qt.ItemDataRole.UserRole) or {}
            if str(display.get("displayId")) == str(display_id):
                self.display_list.setCurrentRow(row)
                return

    def _arrangement_drag_state_changed(self, dragging: bool):
        if dragging:
            self._arrangement_poll_timer.stop()
            self._arrangement_refresh_timer.stop()
        elif self.arrangement_dialog is not None and self.arrangement_dialog.isVisible():
            # Let the mouse-up's optimistic layout paint first. Draining a
            # queued snapshot or starting another capture inside the release
            # handler made the card appear to pause before settling.
            QTimer.singleShot(0, self._finish_arrangement_interaction)

    def _finish_arrangement_interaction(self):
        if self._arrangement_interaction_active():
            return
        if self.arrangement_dialog is None or not self.arrangement_dialog.isVisible():
            return
        self._resume_arrangement_polling()
        self.arrangement_dialog.apply_deferred_confirmed_layout()
        self._apply_deferred_arrangement_records()
        self._run_deferred_arrangement_refreshes()

    def _arrangement_apply_finished(self):
        # A prior helper may finish while the user is already dragging the
        # next tile or has a menu open. Never let its deferred snapshot paint
        # over that live interaction; the interaction-end callback will drain
        # the pending work when the canvas is free again.
        if self._arrangement_interaction_active():
            return
        self._resume_arrangement_polling()
        if self.arrangement_dialog is not None:
            self.arrangement_dialog.apply_deferred_confirmed_layout()
        self._apply_deferred_arrangement_records()
        self._run_deferred_arrangement_refreshes()

    def _apply_deferred_arrangement_records(self):
        deferred = self._deferred_arrangement_records
        self._deferred_arrangement_records = None
        dialog = self.arrangement_dialog
        if deferred is None or dialog is None or not dialog.isVisible():
            return
        screens, omitted = deferred
        # The dialog owns optimistic coordinates during local applies. Never
        # rewrite a captured snapshot with an older canvas snapshot here;
        # update_displays() is the sole gate that decides whether geometry is
        # allowed to replace the visible layout.
        dialog.update_displays(screens, omitted)

    def _run_deferred_arrangement_refreshes(self):
        if self._arrangement_refresh_pending:
            self._arrangement_refresh_pending = False
            self._arrangement_refresh_timer.start(200)
        if self._external_refresh_pending or self._refresh_after_topology_change:
            self._external_refresh_pending = False
            self._refresh_after_topology_change = False
            self._external_refresh_timer.start(450)

    def _resume_arrangement_polling(self):
        self._arrangement_refresh_timer.stop()
        if self.arrangement_dialog is not None and self.arrangement_dialog.isVisible():
            self._arrangement_poll_timer.start()

    def show_display(self, row: int):
        if row < 0 or row >= self.display_list.count():
            return
        display = self.display_list.item(row).data(Qt.ItemDataRole.UserRole)
        self._refresh_display_views(display)

    def _refresh_display_views(self, display: dict):
        display_key = str(display.get("displayId") or "")
        friendly_name = str(display.get("friendlyName") or "").strip()
        original_name = html.escape(display_original_label(display))
        if friendly_name:
            nickname = html.escape(friendly_name)
            nickname_size = max(8, self.heading.font().pointSize() - 2)
            heading_text = (
                f'<span>{original_name}</span>&nbsp;&nbsp;'
                f'<span style="font-size:{nickname_size}pt; font-weight:700; '
                f'color:{appearance_hex("#888888")};">'
                f'{nickname}</span>'
            )
        else:
            heading_text = original_name
        if self.heading.text() != heading_text:
            self.heading.setText(heading_text)

        container = self.tabs.currentWidget()
        page = container.widget() if isinstance(container, QScrollArea) else container
        if page is None:
            return

        page_key = self._display_page_key(page)
        signature = self._display_page_signature(page_key, display)
        previous_state = self._page_render_state.get(page_key)
        display_changed = (previous_state is None or previous_state[0] != display_key)
        if display_changed and hasattr(page, "reset_current_scroll"):
            page.reset_current_scroll()
        if previous_state == (display_key, signature):
            return

        if page is self.overview_page:
            self._show_overview(display)
        elif page is self.resolution_page:
            is_mirror_slave, has_mirror_children = display_mirror_roles(
                display, self.report.get("devices", []))
            # Keep unavailable mirror-receiver resolutions visible but disabled;
            # the current resolution still exposes supported refresh rates.
            rows = resolution_rows_for_display(display, self.report.get("devices", []))
            self.resolution_page.set_rows(rows, "")
            self.resolution_page.set_display_id(display.get("displayId"))
            self.resolution_page.set_rotation((display.get("desktop") or {}).get("rotation"))
            if self.resolution_page.rotation_combo is not None:
                orientation_allowed = not is_mirror_slave and not has_mirror_children
                self.resolution_page.set_orientation_available(orientation_allowed)
            self.resolution_page.mirror_slave_refresh_only = is_mirror_slave
        elif page is self.color_page:
            self._show_colors(display)
        elif page is self.icc_page:
            self.icc_page.set_current_profile(display.get("colorProfile"))
            self.icc_page.set_profiles(self.icc_profiles)
        elif page is self.all_page:
            is_mirror_slave, _has_mirror_children = display_mirror_roles(
                display, self.report.get("devices", []))
            self.all_page.set_modes(
                display.get("availableModes", []),
                (display.get("currentMode") or {}).get("modeID"),
                is_mirror_slave=is_mirror_slave,
                current_mode=display.get("currentMode"))
            self.all_page.set_display_id(display.get("displayId"))
        elif page is self.hardware_page:
            self._show_hardware(display)
        elif page in (self.edid_page, self.connection_text, self.profile_text):
            self._show_diagnostics(display)
        self._page_render_state[page_key] = (display_key, signature)
        if page in (self.resolution_page, self.color_page, self.all_page, self.icc_page):
            # Follow current changes only until the user manually takes control
            # of this table's scroll position. The page method also avoids
            # moving an already-visible current row.
            QTimer.singleShot(60, page.scroll_to_current)

    def _display_page_key(self, page: QWidget) -> str:
        if page is self.overview_page:
            return "overview"
        if page is self.resolution_page:
            return "resolution"
        if page is self.color_page:
            return "color"
        if page is self.icc_page:
            return "icc"
        if page is self.all_page:
            return "all"
        if page is self.hardware_page:
            return "hardware"
        if page is self.edid_page:
            return "edid"
        if page is self.connection_text:
            return "connection"
        return "profile"

    def _display_page_signature(self, page_key: str, display: dict) -> str:
        mode = display.get("currentMode") or {}
        if page_key == "overview":
            values = {key: display.get(key) for key in (
                "displayId", "cgBuiltIn", "isExternal", "transportType", "connectionPath",
                "systemProfilerName", "systemProfilerConnectionType", "systemProfilerDisplayType",
                "systemProfilerVirtualDevice",
                "currentMode", "desktop", "framebufferMode", "colorProfile",
                "edid", "friendlyName", "mirrorGroup", "mirrorDisplays", "displayRole")}
            values["availableModeCount"] = len(display.get("availableModes") or [])
            values["computedRole"] = display_role_label(display, self.report.get("devices", []))
        elif page_key == "resolution":
            values = {key: display.get(key) for key in ("displayId", "desktopModes", "desktopModesStatus",
                                                          "desktop", "framebufferMode", "currentMode")}
            values["mirrorRoles"] = display_mirror_roles(
                display, self.report.get("devices", []))
        elif page_key == "color":
            values = {"displayId": display.get("displayId"),
                      "availableModes": self._mode_signature(display.get("availableModes")),
                      "currentMode": mode}
        elif page_key == "icc":
            profile = display.get("colorProfile") or {}
            values = {"displayId": display.get("displayId"), "colorProfile": profile,
                      "profileCatalog": [(item.get("url"), item.get("name"), item.get("created"))
                                         for item in self.icc_profiles]}
        elif page_key == "all":
            values = {"displayId": display.get("displayId"),
                      "availableModes": self._mode_signature(display.get("availableModes")),
                      "currentMode": mode}
        elif page_key == "hardware":
            values = display
        elif page_key == "edid":
            values = {key: display.get(key) for key in ("edid", "diagnosticsWarning")}
        elif page_key == "connection":
            values = {key: display.get(key) for key in ("connectionPath", "registryAssociation")}
            values["connectionDiagnostics"] = self.report.get("connectionDiagnostics")
            values["connectionScanError"] = self.connection_scan_error
        else:
            values = {key: display.get(key) for key in ("colorProfile", "desktop", "framebufferMode")}
        return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _mode_signature(modes) -> tuple[tuple, ...]:
        fields = ("modeID", "width", "height", "refreshRate", "colorMode", "bitDepth",
                  "hdrMode", "isVRR", "scale", "rotation")
        return tuple(tuple(mode.get(field) for field in fields)
                     for mode in (modes if isinstance(modes, list) else [])
                     if isinstance(mode, dict))

    def _show_diagnostics(self, display):
        edid = display.get('edid', {})
        lines = []
        if not edid.get('status'):
            lines.append("EDID has not been read. Select Get EDID to read this display.")
        if edid.get('status'):
            lines += [f"EDID: {edid['status']}",
                      "EDID describes advertised capabilities, not the current output mode.", ""]
        if display.get('diagnosticsWarning'):
            lines.insert(0, display['diagnosticsWarning'])
        for source in edid.get('sources', []):
            parsed = source.get('parsed', {})
            lines += [source.get('kind', '')]
            for label, key, obj in (('Source', 'source', source), ('Status', 'status', source),
                                    ('Parsed status', 'status', parsed), ('File', 'file', source)):
                if obj.get(key) is not None:
                    lines.append(f"{label}: {obj[key]}")
            for key in ('byteLength', 'expectedBytes', 'manufacturer', 'productName', 'productID',
                        'serialNumber', 'serialText', 'year', 'week', 'version', 'declaredBitDepth', 'sha256'):
                if key in parsed:
                    lines.append(f"{key}: {parsed[key]}")
            for block in parsed.get('blocks', []):
                lines.append(f"Block {block['index']} checksum: {'OK' if block['checksumValid'] else 'INVALID'}")
            for extension in parsed.get('extensions', []):
                if extension.get('type') == 'CTA-861':
                    lines.append(f"CTA: YCbCr 4:4:4={extension['ycbcr444']}, 4:2:2={extension['ycbcr422']}")
                for block in extension.get('dataBlocks', []):
                    if 'eotfCapabilities' in block:
                        lines.append('Advertised EOTFs: ' + ', '.join(block['eotfCapabilities']))
            lines += parsed.get('warnings', []) + ['']
        self.edid_text.setPlainText('\n'.join(lines))
        route = display.get('connectionPath', {})
        lines = []
        if route.get('route'):
            from topology import route_text
            lines += [route_text(route['route']), '']
        association = display.get('registryAssociation', {}).get('status')
        if association is not None:
            lines.append(f"Display registry association: {association}")
        scalar_fields = (('DCP driver', 'dcpToken'),
                         ('OS port type', 'osPortType'), ('OS port number', 'osPortNumber'),
                         ('Display registry path', 'registryPath'),
                         ('Transport registry path', 'transportRegistryPath'))
        for label, key in scalar_fields:
            if route.get(key) is not None:
                lines.append(f"{label}: {route[key]}")
        for label, key in (('Driver-reported active link', 'activeLink'),
                           ('Driver-reported transport metadata', 'transport')):
            if route.get(key):
                lines += ['', label + ':', json.dumps(route[key], indent=2)]
        connection_data = self.report.get('connectionDiagnostics', {})
        if connection_data:
            if display.get('registryAssociation', {}).get('status'):
                lines += [f"Display registry association: {display['registryAssociation']['status']}", ""]
            if display.get('connectionPath'):
                route = display['connectionPath']
                for label, key in (("Display driver", "registryPath"), ("Driver port token", "dcpToken")):
                    if route.get(key):
                        lines.append(f"{label}: {route[key]}")
                if route.get('transport'):
                    lines.append("Driver-reported transport: " + json.dumps(route['transport'], ensure_ascii=False))
                if route.get('ioavCandidates'):
                    lines += ["", "Matching display-driver service candidates:"]
                    lines += [candidate['path'] for candidate in route['ioavCandidates']]
            transports = connection_data.get('activeDisplayPortTransports', [])
            lines += ["", "Active DisplayPort transports reported by macOS:"]
            for transport in transports:
                props = transport.get('properties', {})
                summary = [props.get('ParentBuiltInPortTypeDescription'),
                           f"Port {props['ParentBuiltInPortNumber']}" if props.get('ParentBuiltInPortNumber') is not None else None,
                           props.get('LinkRateDescription'),
                           f"{props['LaneCount']} lanes" if props.get('LaneCount') is not None else None]
                assigned = transport.get('assignedDisplayID')
                assignment = f"Display ID {assigned}" if assigned is not None else "No unique display match"
                lines += [f"{assignment} · " + " · ".join(str(value) for value in summary if value),
                          transport['path']]
            peripherals = connection_data.get('peripheralInventory', [])
        elif self.connection_scan_error:
            lines.append("Background connection scan failed: " + self.connection_scan_error)
            peripherals = []
        else:
            lines.append("Reading connection hardware in background…")
            peripherals = []
        if not connection_data:
            peripherals = self.report.get('diagnostics', {}).get('peripheralInventory', peripherals)
        if peripherals:
            lines += ['', 'Detected USB / Thunderbolt hardware:']
        for service in peripherals:
            cls = service.get('class', '')
            if any(token in cls for token in ('USBHostDevice', 'Thunderbolt', 'TypeC')):
                lines += [f"{service.get('name', '')} ({cls})", service.get('path', ''), '']
        self.connection_text.setPlainText('\n'.join(lines))
        profile_lines = []
        if display.get('colorProfile'):
            profile_lines += ['Current ColorSync profile', json.dumps(display['colorProfile'], indent=2)]
        desktop_data = {k: display[k] for k in ('desktop', 'framebufferMode') if display.get(k) is not None}
        if desktop_data:
            profile_lines += ['', 'Desktop / framebuffer (separate from connection timing)',
                              json.dumps(desktop_data, indent=2)]
        self.profile_text.setPlainText('\n'.join(profile_lines))

    def _show_overview(self, display: dict):
        while self.overview_layout.count():
            item = self.overview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mode = display.get("currentMode") or {}
        desktop = display.get('desktop') or {}
        framebuffer = display.get('framebufferMode') or {}
        is_builtin = display.get('cgBuiltIn')
        if is_builtin is None and display.get('isExternal') is not None:
            is_builtin = not bool(display['isExternal'])

        identity = []
        identity.append(("Display ID", str(display.get('displayId') or "-")))
        technology = display_connection_technology(display)
        display_type = (technology if technology != "-" else
                        "External" if isinstance(is_builtin, (bool, int)) else "-")
        identity.append(("Display type", display_type))
        role = display_role_label(display, self.report.get("devices", []))
        identity.append(("Display role", role or "-"))
        identity.append(("Connection interface", display_connection_interface(display)))
        route = (display.get('connectionPath') or {}).get('route') or {}
        route_summary = route.get('summary') or " → ".join(
            str(node.get("label")) for node in route.get("nodes", []) if node.get("label"))
        identity.append(("Connection route", route_summary or "-"))

        output = []
        desktop_width = desktop.get('width', framebuffer.get('width'))
        desktop_height = desktop.get('height', framebuffer.get('height'))
        desktop_resolution = (f"{desktop_width} × {desktop_height}"
                              if desktop_width is not None and desktop_height is not None else "-")
        output.append(("Desktop Resolution", desktop_resolution))
        pixel_width = framebuffer.get('pixelWidth')
        pixel_height = framebuffer.get('pixelHeight')
        framebuffer_resolution = (f"{pixel_width} × {pixel_height}"
                                  if pixel_width is not None and pixel_height is not None else "-")
        output.append(("Framebuffer pixels", framebuffer_resolution))
        if desktop_width and desktop_height and pixel_width and pixel_height:
            scale_x = pixel_width / desktop_width
            scale_y = pixel_height / desktop_height
            scale = f"{scale_x:g}×" if scale_x == scale_y else f"{scale_x:g}× / {scale_y:g}×"
        else:
            scale = "-"
        output.append(("Scale", scale))
        timing = (f"{mode['width']} × {mode['height']}"
                  if mode.get('width') is not None and mode.get('height') is not None else "-")
        output.append(("Connection timing", timing))
        output.append(("Refresh rate", format_hz(mode['refreshRate'])
                       if mode.get('refreshRate') is not None else "-"))
        output.append(("Orientation", orientation_label(desktop['rotation'])
                       if desktop.get('rotation') is not None else "-"))

        color = []
        for label, key, formatter in (("Encoding", "colorMode", lambda _: mode_encoding(mode)),
                                       ("Range", "colorMode", lambda _: mode_range(mode)),
                                       ("Bit depth", "bitDepth", lambda x: f"{x}-bit"),
                                       ("HDR", "hdrMode", str)):
            if mode.get(key) is not None:
                value = formatter(mode[key])
            else:
                value = ""
            color.append((label, value or "-"))
        profile = display.get("colorProfile") or {}
        profile_name = profile.get("description") or Path(urlparse(str(profile.get("url") or "")).path).name
        color.append(("ICC profile", profile_name or "-"))

        diagnostics = [("Available modes", str(len(display['availableModes']))
                        if isinstance(display.get('availableModes'), list) else "-")]
        edid = display.get('edid')
        edid_status = str(edid.get('status') or "-").replace('-', ' ').title() if isinstance(edid, dict) else "-"
        diagnostics.append(("EDID read", edid_status))
        if display.get('diagnosticsSnapshotStable') is True:
            capture_status = "Stable during capture"
        elif display.get('diagnosticsWarning'):
            capture_status = display['diagnosticsWarning']
        else:
            capture_status = "-"
        diagnostics.append(("Capture check", capture_status))

        sections = (("Display", identity), ("Current output", output),
                    ("Color output", color), ("Mode and read status", diagnostics))
        labels = [label for _, fields in sections for label, _ in fields]
        label_width = max(
            (QFontMetrics(self.overview_page.font()).horizontalAdvance(label) for label in labels),
            default=0) + 4
        for title, fields in sections:
            if not fields:
                continue
            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(4)
            heading = QLabel(title)
            heading_font = heading.font()
            heading_font.setBold(True)
            heading.setFont(heading_font)
            section_layout.addWidget(heading)
            form = QFormLayout()
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setContentsMargins(0, 0, 0, 0)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setHorizontalSpacing(20)
            form.setVerticalSpacing(8)
            section_layout.addLayout(form)
            for label, value in fields:
                text = QLabel(value)
                text.setWordWrap(label != "Connection route")
                text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                if label == "Connection route":
                    text.setMinimumWidth(text.sizeHint().width())
                label_widget = QLabel(label)
                label_widget.setFixedWidth(label_width)
                form.addRow(label_widget, text)
            self.overview_layout.addWidget(section)
        self.overview_layout.addStretch(1)

    def _show_colors(self, display: dict):
        modes = display.get("availableModes", [])
        current_mode = display.get("currentMode") or {}
        current_id = current_mode.get("modeID")
        self.color_page.set_rows(
            color_mode_groups(modes, current_id, current_mode), "")
        self.color_page.set_display_id(display.get("displayId"))

    def _show_hardware(self, display: dict):
        while self.hardware_form.rowCount():
            self.hardware_form.removeRow(0)
        fields = [
            ("Product name", display.get("productName") or display.get("deviceName") or display.get("name")),
            ("Display ID", display.get("displayId")),
            ("Connection", display.get("transportType")),
            ("Display UUID", display.get("uniqueId")),
            ("CoreDisplay UUID", display.get("cgUUID")),
            ("IORegistry path", display.get("ioDisplayLocation")),
        ]
        if display.get("currentModeInAvailableModes") is not None:
            fields.append(("Current mode in available list", "Yes" if display["currentModeInAvailableModes"] else "No"))
        for label, value in fields:
            if value is None:
                continue
            value_label = QLabel(str(value))
            value_label.setWordWrap(True)
            value_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Minimum)
            value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            self.hardware_form.addRow(QLabel(f"{label}:"), value_label)
        self.raw_text.setPlainText(json.dumps(display, ensure_ascii=False, indent=2))

    def _clear_detail(self):
        self.tabs.setEnabled(False)
        self.arrange_button.setEnabled(False)
        self.folder = ""
        self.open_folder_button.setEnabled(False)
        for text in (self.edid_text, self.connection_text, self.profile_text, self.raw_text):
            text.clear()
        self.heading.setText(html.escape("No active displays found"))
        self.statusBar().showMessage("No display data available.")

    def open_capture_folder(self):
        if self.folder and Path(self.folder).is_dir():
            subprocess.run(["/usr/bin/open", "-R", self.folder], check=False)

    def closeEvent(self, event):
        if self.mode_switch_process is not None:
            self.mode_switch_process.write(b"revert\n")
            self.mode_switch_process.waitForFinished(4000)
        if ((self._role_helper_process is not None and
             self._role_helper_process.state() != QProcess.ProcessState.NotRunning) or
                (self._role_helper_build_process is not None and
                 self._role_helper_build_process.state() != QProcess.ProcessState.NotRunning)):
            self._close_pending = True
            self.statusBar().showMessage("Finishing display role change before closing…")
            event.ignore()
            return
        if ((self.worker and self.worker.isRunning()) or
                (self.connection_worker and self.connection_worker.isRunning()) or
                (self.icc_worker and self.icc_worker.isRunning()) or
                (self.icc_apply_worker and self.icc_apply_worker.isRunning())):
            self._close_pending = True
            self.statusBar().showMessage('Finishing capture before closing…')
            event.ignore()
            return
        if self._display_reconfiguration_watcher is not None:
            self._display_reconfiguration_watcher.close()
            self._display_reconfiguration_watcher = None
        self._stop_identify_helper()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName(APP_ORG)
    app.appearance_controller = AppearanceController(
        app, QSettings(APP_ORG, SETTINGS_NAME))
    window = DisplayInspectorWindow()
    app.aboutToQuit.connect(window._stop_identify_helper)
    window.show()
    window.raise_()
    window.activateWindow()
    # Initial refresh reads the display state into memory only.
    QTimer.singleShot(0, window.refresh)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
