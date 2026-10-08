"""Frame editor UI and native helper process integration."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

from PySide6.QtCore import (QEvent, Qt, Signal, QPointF, QRectF, QProcess, QTimer,
                            QSize, QRect)
from PySide6.QtGui import (QAction, QColor, QNativeGestureEvent, QPainter,
                           QPen, QShortcut, QKeySequence, QPalette)
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLayout, QMenu,
    QMessageBox, QPushButton, QSizePolicy, QStyle, QVBoxLayout, QWidget,
)


class FlowLayout(QLayout):
    """A compact, height-for-width layout that wraps source buttons naturally."""

    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _do_layout(self, rect, test_only=False):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        spacing = self.spacing()
        rows = []
        row, row_width, row_height = [], 0, 0
        for item in self._items:
            hint = item.sizeHint()
            next_width = row_width + (spacing if row else 0) + hint.width()
            if row and next_width > area.width():
                rows.append((row, row_height))
                row, row_width, row_height = [], 0, 0
                next_width = hint.width()
            row.append((item, hint, row_width + (spacing if row else 0)))
            row_width = next_width
            row_height = max(row_height, hint.height())
        if row:
            rows.append((row, row_height))

        y = area.y()
        for row, row_height in rows:
            for item, hint, x_offset in row:
                if not test_only:
                    item.setGeometry(QRect(
                        area.x() + x_offset,
                        y + (row_height - hint.height()) // 2,
                        hint.width(), hint.height()))
            y += row_height + spacing
        return y - spacing - rect.y() + bottom if rows else top + bottom


class PersistentToggleMenu(QMenu):
    """A native menu whose designated check action toggles without closing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._persistent_toggle_action = None

    def set_persistent_toggle_action(self, action):
        self._persistent_toggle_action = action

    def _toggle_persistent_action(self):
        action = self._persistent_toggle_action
        if action is None or not action.isEnabled():
            return False
        action.setChecked(not action.isChecked())
        return True

    def mouseReleaseEvent(self, event):
        if self.actionAt(event.position().toPoint()) is self._persistent_toggle_action:
            self._toggle_persistent_action()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if (self.activeAction() is self._persistent_toggle_action and
                event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                Qt.Key.Key_Enter)):
            self._toggle_persistent_action()
            event.accept()
            return
        super().keyPressEvent(event)


def _helper_path() -> Path:
    root = Path(sys.executable).resolve().parents[4] if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", root)) / "display-frame-output"
    return root / ".build" / "display-frame-output"


def _pixel_layout_displays(displays):
    """Project Arrange's mixed-scale desktop rectangles into a pixel canvas.

    Every display keeps its real framebuffer width and height. Relative
    positions are accumulated from the main display; when displays touch, the
    seam is exact, and any gap/overlap is converted using the neighboring
    display's local pixel density.
    """
    records = []
    for display in displays:
        desktop = display.get("desktop") or {}
        try:
            x, y, width, height = (float(desktop[key]) for key in
                                   ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        pixel_width, pixel_height = FrameWindow._pixels(display)
        records.append({
            "displayID": str(display.get("displayId")),
            "name": display.get("frameLabel") or display.get("name") or "Display",
            "desktopX": x, "desktopY": y,
            "desktopWidth": width, "desktopHeight": height,
            "pixelWidth": pixel_width, "pixelHeight": pixel_height,
            "mirrorsDisplayID": desktop.get("mirrorsDisplayID"),
            "main": bool(desktop.get("main", display.get("main", False))),
        })

    if not records:
        return []

    by_id = {record["displayID"]: record for record in records}
    independent = [record for record in records
                   if not _mirror_master_id(record) or
                   _mirror_master_id(record) == record["displayID"] or
                   _mirror_master_id(record) not in by_id]
    if not independent:
        independent = records[:1]
    root = next((record for record in independent if record["main"]), independent[0])
    mapped = {root["displayID"]: (0.0, 0.0)}

    def overlap(start_a, size_a, start_b, size_b):
        return max(0.0, min(start_a + size_a, start_b + size_b) - max(start_a, start_b))

    def axis_position(parent, child, axis, parent_pixel_origin):
        if axis == "x":
            p_start, p_size = parent["desktopX"], parent["desktopWidth"]
            c_start, c_size = child["desktopX"], child["desktopWidth"]
            p_pixel_size = parent["pixelWidth"]
        else:
            p_start, p_size = parent["desktopY"], parent["desktopHeight"]
            c_start, c_size = child["desktopY"], child["desktopHeight"]
            p_pixel_size = parent["pixelHeight"]
        p_scale = p_pixel_size / p_size
        c_pixel_size = child["pixelWidth"] if axis == "x" else child["pixelHeight"]
        p_end, c_end = p_start + p_size, c_start + c_size
        if c_start >= p_end:
            return parent_pixel_origin + p_pixel_size + (c_start - p_end) * p_scale
        if c_end <= p_start:
            c_scale = c_pixel_size / c_size
            return parent_pixel_origin - c_pixel_size - (p_start - c_end) * c_scale
        return parent_pixel_origin + (c_start - p_start) * p_scale

    unresolved = [record for record in independent if record["displayID"] not in mapped]
    while unresolved:
        placed = [record for record in independent if record["displayID"] in mapped]

        def nearest_distance(child):
            return min(
                max(0.0, parent["desktopX"] - (child["desktopX"] + child["desktopWidth"]),
                    child["desktopX"] - (parent["desktopX"] + parent["desktopWidth"]))
                + max(0.0, parent["desktopY"] - (child["desktopY"] + child["desktopHeight"]),
                      child["desktopY"] - (parent["desktopY"] + parent["desktopHeight"]))
                for parent in placed)

        child = min(unresolved, key=nearest_distance)

        def best_parent(axis):
            if axis == "x":
                orth = lambda item: (item["desktopY"], item["desktopHeight"])
                pos_key, size_key = "desktopX", "desktopWidth"
            else:
                orth = lambda item: (item["desktopX"], item["desktopWidth"])
                pos_key, size_key = "desktopY", "desktopHeight"
            child_orth, child_orth_size = orth(child)

            def score(parent):
                parent_orth, parent_orth_size = orth(parent)
                shared = overlap(child_orth, child_orth_size, parent_orth, parent_orth_size)
                orth_gap = max(0.0, parent_orth - (child_orth + child_orth_size),
                               child_orth - (parent_orth + parent_orth_size))
                gap = max(0.0, parent[pos_key] - (child[pos_key] + child[size_key]),
                          child[pos_key] - (parent[pos_key] + parent[size_key]))
                return (shared, -orth_gap, -gap)

            return max(placed, key=score)

        x_parent = best_parent("x")
        y_parent = best_parent("y")
        _, child_y = mapped[y_parent["displayID"]]
        child_x, _ = mapped[x_parent["displayID"]]
        mapped[child["displayID"]] = (
            axis_position(x_parent, child, "x", child_x),
            axis_position(y_parent, child, "y", child_y),
        )
        unresolved.remove(child)

    for record in records:
        mirror_id = _mirror_master_id(record)
        if mirror_id and mirror_id != record["displayID"] and mirror_id in by_id:
            master = by_id[mirror_id]
            mx, my = mapped.get(mirror_id, (0.0, 0.0))
            record.update(x=mx, y=my,
                          width=master["pixelWidth"], height=master["pixelHeight"])
        else:
            px, py = mapped.get(record["displayID"], (0.0, 0.0))
            record.update(x=px, y=py,
                          width=record["pixelWidth"], height=record["pixelHeight"])
    return records


def _mirror_master_id(record):
    mirror_id = str(record.get("mirrorsDisplayID") or "0")
    return "" if mirror_id == "0" else mirror_id


class FrameCanvas(QWidget):
    sceneChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.output_pixels = (1920.0, 1080.0)
        self.layout_bounds = QRectF(0.0, 0.0, 1920.0, 1080.0)
        self.workspace_bounds = QRectF(-2000.0, -2000.0, 5920.0, 5080.0)
        self.zoom_factor = 1.0
        self.layout_displays: list[dict] = []
        self._visible_layout_displays: list[dict] = []
        self.sources: list[dict] = []
        self.selected = -1
        self.selected_sources: set[int] = set()
        self._center_snap_latches = {}
        self._corner_snap_latch = None
        self._corner_edge_snap_latch = None
        self._source_edge_snap_latch = None
        self._hovered = -1
        self._rotate_hover = None
        self._drag = ""
        self._rotation_state = None
        self._dimension_inside_cache = {}
        self._rotation_angle = None
        self._rotation_label_pos = None
        self._last = QPointF()
        self._handle = 12.0

    def set_output_size(self, width, height):
        self._center_snap_latches.clear()
        self._corner_snap_latch = None
        self._corner_edge_snap_latch = None
        self._source_edge_snap_latch = None
        self.output_pixels = (max(1.0, float(width)), max(1.0, float(height)))
        self.layout_bounds = QRectF(0.0, 0.0, *self.output_pixels)
        self.workspace_bounds = self.layout_bounds.adjusted(-2000, -2000, 2000, 2000)
        self.layout_displays = []
        self._visible_layout_displays = []
        self.update()

    def set_layout_displays(self, displays):
        self._center_snap_latches.clear()
        self._corner_snap_latch = None
        self._corner_edge_snap_latch = None
        self._source_edge_snap_latch = None
        self.layout_displays = [dict(display) for display in displays
                                if float(display.get("width") or 0) > 0
                                and float(display.get("height") or 0) > 0]
        if not self.layout_displays:
            return
        left = min(float(display["x"]) for display in self.layout_displays)
        top = min(float(display["y"]) for display in self.layout_displays)
        right = max(float(display["x"]) + float(display["width"])
                    for display in self.layout_displays)
        bottom = max(float(display["y"]) + float(display["height"])
                     for display in self.layout_displays)
        self.layout_bounds = QRectF(left, top, right - left, bottom - top)
        self.workspace_bounds = self.layout_bounds.adjusted(-2000, -2000, 2000, 2000)
        self.output_pixels = (right - left, bottom - top)
        self._visible_layout_displays = [
            display for display in self.layout_displays
            if not _mirror_master_id(display)
            or _mirror_master_id(display) == str(display.get("displayID"))
        ] or self.layout_displays[:1]
        self.zoom_factor = self._clamp_zoom(self.zoom_factor)
        self.update()

    def add_source(self, display_id, name, width, height):
        source_w, source_h = max(1.0, float(width)), max(1.0, float(height))
        source_id = str(display_id)
        output = next((display for display in self.layout_displays
                       if str(display.get("displayID")) == source_id), None)
        if output:
            x = float(output["x"]) + (float(output["width"]) - source_w) / 2
            y = float(output["y"]) + (float(output["height"]) - source_h) / 2
        else:
            bounds = self.layout_bounds
            x = bounds.left() + (bounds.width() - source_w) / 2
            y = bounds.top() + (bounds.height() - source_h) / 2
        view_scale = (self._canvas_rect().width()
                      / max(1.0, self.layout_bounds.width()))
        offset = 16.0 / max(view_scale, 1e-9)
        while any(
                math.isclose(float(source["x"]), x, abs_tol=1e-6)
                and math.isclose(float(source["y"]), y, abs_tol=1e-6)
                and math.isclose(float(source["width"]), source_w, abs_tol=1e-6)
                and math.isclose(float(source["height"]), source_h, abs_tol=1e-6)
                and math.isclose(float(source.get("rotation", 0)) % 360, 0.0,
                                 abs_tol=1e-6)
                for source in self.sources):
            x += offset
            y += offset
        self.sources.append({"displayID": str(display_id), "name": name,
                             "x": x, "y": y,
                             "width": source_w, "height": source_h,
                             "showCursor": True, "rotation": 0})
        self.selected = len(self.sources) - 1
        self.selected_sources = {self.selected}
        self._center_snap_latches.clear()
        self.update()
        self.sceneChanged.emit()

    def remove_selected(self):
        selected = self._selection_indices()
        if selected:
            for index in reversed(selected):
                self.sources.pop(index)
            self.selected = min(selected[0], len(self.sources) - 1)
            self.selected_sources = ({self.selected} if self.selected >= 0 else set())
            self._center_snap_latches.clear()
            self.update()
            self.sceneChanged.emit()

    def copy_selected(self):
        selected = self._selection_indices()
        if not selected:
            return
        bounds = self._selection_bounds(selected)
        left, top = bounds["x"], bounds["y"]
        self._source_clipboard = [
            {**self.sources[index],
             "x": float(self.sources[index]["x"]) - left,
             "y": float(self.sources[index]["y"]) - top}
            for index in selected
        ]

    def paste_sources(self):
        clipboard = getattr(self, "_source_clipboard", None)
        if not clipboard:
            return
        selected = self._selection_indices()
        if selected:
            bounds = self._selection_bounds(selected)
            left, top = bounds["x"], bounds["y"]
            view_scale = (self._canvas_rect().width()
                          / max(1.0, self.layout_bounds.width()))
            offset_points = 16.0 / max(view_scale, 1e-9)
            offset_x = left + offset_points
            offset_y = top + offset_points
        else:
            bounds = self.layout_bounds
            offset_x = bounds.left() + (bounds.width() - max(
                item["x"] + item["width"] for item in clipboard)) / 2
            offset_y = bounds.top() + (bounds.height() - max(
                item["y"] + item["height"] for item in clipboard)) / 2
        first = len(self.sources)
        for item in clipboard:
            source = dict(item)
            source["x"] += offset_x
            source["y"] += offset_y
            self.sources.append(source)
        pasted = set(range(first, len(self.sources)))
        self._set_selection(pasted, max(pasted))
        self._center_snap_latches.clear()
        self.update()
        self.sceneChanged.emit()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_sources()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def _selection_indices(self):
        indices = {index for index in self.selected_sources
                   if 0 <= index < len(self.sources)}
        if not indices and 0 <= self.selected < len(self.sources):
            indices = {self.selected}
        return sorted(indices)

    def _set_selection(self, indices, primary=None):
        valid = {index for index in indices if 0 <= index < len(self.sources)}
        if valid != set(self._selection_indices()):
            self._center_snap_latches.clear()
            self._corner_snap_latch = None
            self._corner_edge_snap_latch = None
            self._source_edge_snap_latch = None
        self.selected_sources = valid
        if primary in valid:
            self.selected = primary
        elif valid:
            self.selected = max(valid)
        else:
            self.selected = -1

    def select_all(self):
        indices = set(range(len(self.sources)))
        self._set_selection(indices, max(indices) if indices else None)
        self.update()

    def source_scene(self):
        keys = ("displayID", "x", "y", "width", "height")
        return [{**{key: item[key] for key in keys},
                 "showCursor": bool(item.get("showCursor", True)),
                 "rotation": int(round(float(item.get("rotation", 0))))}
                for item in self.sources]

    def _stage_rect(self):
        """The black work surface surrounding the arranged display group."""
        margin = 8
        return QRectF(margin, margin, max(1, self.width() - 2 * margin),
                      max(1, self.height() - 2 * margin))

    def _canvas_rect(self):
        """The Arrange group, centered in the viewport at the current zoom."""
        bounds = self.layout_bounds
        w, h = bounds.width(), bounds.height()
        stage = self._stage_rect()
        base_scale = min(stage.width() * 0.60 / w, stage.height() * 0.60 / h)
        scale = base_scale * self.zoom_factor
        dw, dh = w * scale, h * scale
        x = stage.center().x() - dw / 2
        y = stage.center().y() - dh / 2
        return QRectF(x, y, dw, dh)

    def _clamp_zoom(self, zoom):
        stage = self._stage_rect()
        fit_scale = min(stage.width() * 0.84 / self.workspace_bounds.width(),
                        stage.height() * 0.84 / self.workspace_bounds.height())
        layout_scale = min(stage.width() * 0.60 / self.layout_bounds.width(),
                           stage.height() * 0.60 / self.layout_bounds.height())
        minimum = fit_scale / layout_scale
        edge_margin = min(24.0, max(12.0, min(stage.width(), stage.height()) * 0.025))
        available_width = max(1.0, stage.width() - 2 * edge_margin)
        available_height = max(1.0, stage.height() - 2 * edge_margin)
        maximum = min(
            available_width / self.layout_bounds.width(),
            available_height / self.layout_bounds.height(),
        ) / layout_scale
        maximum = max(minimum, maximum)
        return min(maximum, max(minimum, float(zoom)))

    def resizeEvent(self, event):
        self.zoom_factor = self._clamp_zoom(self.zoom_factor)
        super().resizeEvent(event)

    def _zoom_by(self, factor):
        if factor <= 0:
            return
        self.zoom_factor = self._clamp_zoom(self.zoom_factor * factor)
        self.update()

    def wheelEvent(self, event):
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        if pixel_delta:
            factor = 1.01 ** (pixel_delta / 2.0)
        elif angle_delta:
            factor = 1.2 ** (angle_delta / 120.0)
        else:
            event.ignore()
            return
        self._zoom_by(factor)
        event.accept()

    def event(self, event):
        if event.type() == QEvent.Type.NativeGesture:
            gesture = event
            if (isinstance(gesture, QNativeGestureEvent)
                    and gesture.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
                self._zoom_by(1.0 + gesture.value())
                event.accept()
                return True
        return super().event(event)

    def _to_canvas_rect(self, source):
        canvas = self._canvas_rect()
        bounds = self.layout_bounds
        w, h = bounds.width(), bounds.height()
        return QRectF(canvas.x() + (source["x"] - bounds.left()) * canvas.width() / w,
                      canvas.y() + (source["y"] - bounds.top()) * canvas.height() / h,
                      source["width"] * canvas.width() / w,
                      source["height"] * canvas.height() / h)

    def _from_canvas_point(self, point):
        canvas = self._canvas_rect()
        bounds = self.layout_bounds
        return QPointF(bounds.left() + (point.x() - canvas.left()) *
                       bounds.width() / canvas.width(),
                       bounds.top() + (point.y() - canvas.top()) *
                       bounds.height() / canvas.height())

    @staticmethod
    def _rotated_source_corners(source):
        center_x = float(source["x"]) + float(source["width"]) / 2
        center_y = float(source["y"]) + float(source["height"]) / 2
        angle = math.radians(float(source.get("rotation", 0)))
        cosine, sine = math.cos(angle), math.sin(angle)
        corners = ((source["x"], source["y"]),
                   (source["x"] + source["width"], source["y"]),
                   (source["x"], source["y"] + source["height"]),
                   (source["x"] + source["width"], source["y"] + source["height"]))
        return [QPointF(center_x + (x - center_x) * cosine - (y - center_y) * sine,
                        center_y + (x - center_x) * sine + (y - center_y) * cosine)
                for x, y in corners]

    def _source_canvas_corners(self, source):
        canvas = self._canvas_rect()
        bounds = self.layout_bounds
        return [QPointF(canvas.left() + (point.x() - bounds.left()) *
                        canvas.width() / bounds.width(),
                        canvas.top() + (point.y() - bounds.top()) *
                        canvas.height() / bounds.height())
                for point in self._rotated_source_corners(source)]

    def _source_canvas_bounds(self, source):
        corners = self._source_canvas_corners(source)
        xs, ys = [point.x() for point in corners], [point.y() for point in corners]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def _appearance_color(self, value):
        """Use dark-theme color literals and invert grayscale for Light."""
        color = QColor(value)
        if (self.palette().window().color().lightness() >= 128
                and color.red() == color.green() == color.blue()):
            channel = 255 - color.red()
            color.setRgb(channel, channel, channel, color.alpha())
        return color

    def _source_card_color(self):
        # Keep the dark-theme base neutral; derive Light by exact grayscale inversion.
        color = self._appearance_color("#303030")
        color.setAlpha(235)
        return color

    def _paint_order(self):
        order = list(range(len(self.sources)))
        if self._drag:
            selected = self._selection_indices()
            order = [index for index in order if index not in selected] + selected
        return order

    def _source_dimensions_visible(self, index):
        return index in self._selection_indices() or index == self._hovered

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().window())
        stage = self._stage_rect()
        painter.fillRect(stage, self._appearance_color("#000000"))
        # Zoom changes the view scale, not the work surface's bounds. Clip the
        # Arrange tiles, source cards, labels, and handles to the black stage.
        painter.save()
        painter.setClipRect(stage)
        if self._visible_layout_displays:
            for display in self._visible_layout_displays:
                x = float(display["x"])
                y = float(display["y"])
                width = float(display["width"])
                height = float(display["height"])
                rect = self._to_canvas_rect({"x": x, "y": y,
                                            "width": width, "height": height})
                painter.fillRect(rect, self._appearance_color("#000000"))
                output_guide_color = self._appearance_color("#454545")
                painter.setPen(QPen(output_guide_color, 1))
                painter.drawRect(rect)
                pixel_width = display.get("pixelWidth") or round(width)
                pixel_height = display.get("pixelHeight") or round(height)
                text = (f"{display['name']}\n"
                        f"{int(round(float(pixel_width)))} × "
                        f"{int(round(float(pixel_height)))}")
                painter.setPen(output_guide_color)
                painter.drawText(rect.adjusted(6, 6, -6, -6),
                                 Qt.AlignmentFlag.AlignCenter, text)
        for index in self._paint_order():
            source = self.sources[index]
            rect = self._to_canvas_rect(source)
            painter.save()
            painter.translate(rect.center())
            painter.rotate(float(source.get("rotation", 0)))
            local_rect = QRectF(-rect.width() / 2, -rect.height() / 2,
                                rect.width(), rect.height())
            painter.fillRect(local_rect, self._source_card_color())
            selected = index in self._selection_indices()
            painter.setPen(QPen(self._appearance_color(
                                    "#D99A3E" if selected else "#B0B0B0"),
                                2 if selected else 1))
            # Handle drawing below uses an orange brush. Reset it before each
            # outline so drawRect cannot fill later cards with that stale brush.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(local_rect)
            painter.setPen(self.palette().windowText().color())
            painter.drawText(local_rect, Qt.AlignmentFlag.AlignCenter, source["name"])
            painter.restore()
            visual_rect = self._source_canvas_bounds(source)

            if not self._source_dimensions_visible(index):
                continue

            # Keep the source dimensions attached to the top and right edges.
            # Move both labels outside together if either would crowd the name.
            metrics = painter.fontMetrics()
            dimension_pen = (self._appearance_color("#D99A3E") if selected else
                             self.palette().windowText().color())
            painter.setPen(dimension_pen)
            width_text = f"{round(source['width'])} px"
            width_text_width = metrics.horizontalAdvance(width_text)
            height_text = f"{round(source['height'])} px"
            height_text_width = metrics.horizontalAdvance(height_text)
            label_height = metrics.height()
            name_bounds = painter.boundingRect(
                visual_rect, Qt.AlignmentFlag.AlignCenter, source["name"])
            labels_inside = self._dimension_labels_fit_inside(
                visual_rect, name_bounds, width_text_width, height_text_width, label_height)
            rotation_active = (self._rotation_state is not None
                               and index in self._rotation_state["indices"])

            if float(source.get("rotation", 0)) % 360 or rotation_active:
                inside_override = (self._rotation_state["dimension_inside"].get(index)
                                   if rotation_active else None)
                width_label, height_label, labels_inside = self._rotated_dimension_label_rects(
                    source, width_text_width, height_text_width, label_height,
                    name_bounds, inside_override=inside_override)
                if not rotation_active:
                    self._dimension_inside_cache[index] = labels_inside
                painter.drawText(width_label, Qt.AlignmentFlag.AlignCenter, width_text)
                painter.drawText(height_label, Qt.AlignmentFlag.AlignCenter, height_text)
                continue

            self._dimension_inside_cache[index] = labels_inside

            label_x = visual_rect.center().x() - width_text_width / 2
            label_x = min(max(2.0, label_x), max(2.0, self.width() - width_text_width - 2))
            width_baseline = (visual_rect.top() + 3 + metrics.ascent() if labels_inside else
                              visual_rect.top() - 5 - metrics.descent())
            width_baseline = min(max(metrics.ascent() + 2, width_baseline),
                                 self.height() - metrics.descent() - 2)
            painter.drawText(QPointF(label_x, width_baseline), width_text)

            if labels_inside:
                height_x = visual_rect.right() - height_text_width - 6
            else:
                height_x = visual_rect.right() + 8
                if height_x + height_text_width > self.width() - 2:
                    height_x = visual_rect.right() - height_text_width - 8
            height_x = min(max(2.0, height_x), max(2.0, self.width() - height_text_width - 2))
            height_y = visual_rect.center().y() + metrics.ascent() / 2
            height_y = min(max(metrics.ascent() + 2, height_y), self.height() - metrics.descent() - 2)
            painter.drawText(QPointF(height_x, height_y), height_text)
        selection = self._selection_indices()
        if selection:
            group_rect = self._selection_canvas_rect(selection)
            if len(selection) > 1:
                painter.setPen(QPen(self._appearance_color("#D99A3E"), 1,
                                    Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(group_rect)
            painter.setBrush(self._appearance_color("#D99A3E"))
            painter.setPen(Qt.PenStyle.NoPen)
            if len(selection) > 1:
                handle_points = [point for _, point in self._handle_points(group_rect)]
            else:
                corners = self._source_canvas_corners(self.sources[selection[0]])
                handle_points = [*corners,
                                 self._midpoint(corners[0], corners[1]),
                                 self._midpoint(corners[2], corners[3]),
                                 self._midpoint(corners[0], corners[2]),
                                 self._midpoint(corners[1], corners[3])]
            for point in handle_points:
                painter.drawRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))
        if self._rotation_angle is not None:
            label = f"{self._rotation_angle}°"
            metrics = painter.fontMetrics()
            label_rect = self._rotation_label_rect(
                self._rotation_label_pos,
                metrics.horizontalAdvance(label) + 14, metrics.height() + 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._appearance_color(QColor(24, 24, 24, 220)))
            painter.drawRoundedRect(label_rect, 5, 5)
            painter.setPen(self.palette().windowText().color())
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
        self._paint_measurement_guides(painter)
        painter.restore()
        painter.end()

    @staticmethod
    def _ray_polygon_intersections(points, origin, direction):
        intersections = []
        for first, second in zip(points, points[1:] + points[:1]):
            edge = second - first
            offset = first - origin
            denominator = direction.x() * edge.y() - direction.y() * edge.x()
            if abs(denominator) < 1e-9:
                continue
            distance = (offset.x() * edge.y() - offset.y() * edge.x()) / denominator
            fraction = (offset.x() * direction.y() - offset.y() * direction.x()) / denominator
            if distance > 1e-6 and -1e-9 <= fraction <= 1 + 1e-9:
                intersections.append(distance)
        return intersections

    def _measurement_guide_specs(self, source_polygons, excluded_indices=None):
        """Measure each active card edge to the nearest external screen edge."""
        excluded_indices = set(excluded_indices or ())
        polygons = []
        for display in self._visible_layout_displays:
            x, y = float(display["x"]), float(display["y"])
            right = x + float(display["width"])
            bottom = y + float(display["height"])
            polygons.append([QPointF(x, y), QPointF(right, y),
                             QPointF(right, bottom), QPointF(x, bottom)])
        if not polygons:
            bounds = self.layout_bounds
            polygons.append([bounds.topLeft(), bounds.topRight(),
                             bounds.bottomRight(), bounds.bottomLeft()])
        for index, source in enumerate(self.sources):
            if index in excluded_indices:
                continue
            corners = self._rotated_source_corners(source)
            polygons.append([corners[0], corners[1], corners[3], corners[2]])

        specs = []
        for source_polygon in source_polygons:
            for first, second in zip(source_polygon,
                                     source_polygon[1:] + source_polygon[:1]):
                edge = second - first
                length = math.hypot(edge.x(), edge.y())
                if length < 1e-9:
                    continue
                # Polygon order is top-left, top-right, bottom-right,
                # bottom-left, so this normal points outward.
                outward = QPointF(edge.y() / length, -edge.x() / length)
                edge_mid = self._midpoint(first, second)
                candidates = [distance for polygon in polygons
                              for distance in self._ray_polygon_intersections(
                                  polygon, edge_mid, outward)]
                if not candidates:
                    continue
                distance = min(candidates)
                start_scene = edge_mid
                end_scene = edge_mid + outward * distance
                start = self._scene_to_canvas_point(start_scene)
                end = self._scene_to_canvas_point(end_scene)
                center = QPointF((start.x() + end.x()) / 2,
                                 (start.y() + end.y()) / 2)
                specs.append((start, end, f"{round(distance)} px", center))
        return specs

    def _scene_to_canvas_point(self, point):
        canvas, bounds = self._canvas_rect(), self.layout_bounds
        return QPointF(
            canvas.left() + (point.x() - bounds.left()) * canvas.width() / bounds.width(),
            canvas.top() + (point.y() - bounds.top()) * canvas.height() / bounds.height(),
        )

    def _measurement_anchor(self):
        selection = self._selection_indices()
        if not selection:
            return [], set()
        if len(selection) > 1:
            rect = self._selection_bounds(selection)
            if not rect:
                return [], set()
            polygon = [QPointF(rect["x"], rect["y"]),
                       QPointF(rect["x"] + rect["width"], rect["y"]),
                       QPointF(rect["x"] + rect["width"], rect["y"] + rect["height"]),
                       QPointF(rect["x"], rect["y"] + rect["height"])]
            return [polygon], set(selection)
        source = self.sources[selection[0]]
        corners = self._rotated_source_corners(source)
        return [[corners[0], corners[1], corners[3], corners[2]]], set(selection)

    def _paint_measurement_guides(self, painter):
        if not self._drag or self._drag == "rotate":
            return
        source_polygons, excluded = self._measurement_anchor()
        if not source_polygons:
            return
        metrics = painter.fontMetrics()
        line_color = self._appearance_color("#303030")
        text_color = self._appearance_color("#4E4E4E")
        painter.setPen(QPen(line_color, 1))
        for start, end, label, center in self._measurement_guide_specs(source_polygons, excluded):
            painter.drawLine(start, end)
            text_width = metrics.horizontalAdvance(label)
            painter.setPen(text_color)
            painter.drawText(QPointF(center.x() - text_width / 2,
                                     center.y() + (metrics.ascent() - metrics.descent()) / 2), label)
            painter.setPen(QPen(line_color, 1))

    @staticmethod
    def _dimension_labels_fit_inside(rect, name_bounds, width_text_width,
                                     height_text_width, label_height):
        if (rect.width() < width_text_width + 12
                or rect.height() < label_height + 6
                or rect.width() < height_text_width + 10):
            return False
        width_label = QRectF(
            rect.center().x() - width_text_width / 2, rect.top() + 3,
            width_text_width, label_height)
        height_label = QRectF(
            rect.right() - height_text_width - 6,
            rect.center().y() - label_height / 2,
            height_text_width, label_height)
        return not width_label.intersects(name_bounds) and not height_label.intersects(name_bounds)

    def _rotated_dimension_label_rects(self, source, width_text_width,
                                       height_text_width, label_height,
                                       name_bounds, inside_override=None):
        """Anchor horizontal dimension labels to the card's rotated top/right edges."""
        local_rect = self._to_canvas_rect(source)
        corners = self._source_canvas_corners(source)
        center = local_rect.center()
        top_midpoint = self._midpoint(corners[0], corners[1])
        right_midpoint = self._midpoint(corners[1], corners[3])

        def outward_normal(point):
            dx, dy = point.x() - center.x(), point.y() - center.y()
            length = math.hypot(dx, dy) or 1.0
            return dx / length, dy / length

        top_normal = outward_normal(top_midpoint)
        right_normal = outward_normal(right_midpoint)

        def place(midpoint, normal, text_width, inside):
            nx, ny = normal
            projected_half_extent = (abs(nx) * text_width
                                     + abs(ny) * label_height) / 2
            offset = projected_half_extent + 4
            direction = -1 if inside else 1
            text_center = QPointF(midpoint.x() + direction * nx * offset,
                                  midpoint.y() + direction * ny * offset)
            return QRectF(text_center.x() - text_width / 2,
                          text_center.y() - label_height / 2,
                          text_width, label_height)

        angle = math.radians(float(source.get("rotation", 0)))
        cosine, sine = math.cos(angle), math.sin(angle)

        def fits_inside(label_rect):
            for point in (label_rect.topLeft(), label_rect.topRight(),
                          label_rect.bottomLeft(), label_rect.bottomRight()):
                dx, dy = point.x() - center.x(), point.y() - center.y()
                local_x = cosine * dx + sine * dy
                local_y = -sine * dx + cosine * dy
                if (abs(local_x) > local_rect.width() / 2
                        or abs(local_y) > local_rect.height() / 2):
                    return False
            return not label_rect.intersects(name_bounds)

        inside_labels = (
            place(top_midpoint, top_normal, width_text_width, True),
            place(right_midpoint, right_normal, height_text_width, True),
        )
        labels_inside = (bool(inside_override) if inside_override is not None
                         else all(fits_inside(label) for label in inside_labels))
        if labels_inside:
            return (*inside_labels, True)
        outside_labels = (
            place(top_midpoint, top_normal, width_text_width, False),
            place(right_midpoint, right_normal, height_text_width, False),
        )
        return (*outside_labels, False)

    def _hit_test(self, pos):
        if not self._stage_rect().contains(pos):
            return ""
        rotation_handle = self._rotation_handle_at(pos)
        if rotation_handle is not None:
            if rotation_handle["kind"] == "source":
                self._set_selection({rotation_handle["index"]}, rotation_handle["index"])
            self._begin_rotation(pos)
            return "rotate"
        selection = self._selection_indices()
        if len(selection) > 1:
            group_rect = self._selection_canvas_rect(selection)
            for handle, point in self._handle_points(group_rect):
                if (pos - point).manhattanLength() <= self._handle:
                    return "group_resize_" + handle
        for index in range(len(self.sources) - 1, -1, -1):
            source = self.sources[index]
            rect = self._to_canvas_rect(source)
            corners = self._source_canvas_corners(source)
            visual_rect = self._source_canvas_bounds(source)
            if not visual_rect.adjusted(-self._handle, -self._handle,
                                self._handle, self._handle).contains(pos):
                continue
            if index not in selection:
                self._set_selection({index}, index)
            else:
                self.selected = index
            if len(self._selection_indices()) > 1:
                return "move" if visual_rect.contains(pos) else ""
            for handle, point in zip(("tl", "tr", "bl", "br"), corners):
                if (pos - point).manhattanLength() <= self._handle:
                    return "resize_" + handle
            edges = (("tm", self._midpoint(corners[0], corners[1])),
                     ("bm", self._midpoint(corners[2], corners[3])),
                     ("ml", self._midpoint(corners[0], corners[2])),
                     ("mr", self._midpoint(corners[1], corners[3])))
            for handle, point in edges:
                if (pos - point).manhattanLength() <= self._handle:
                    return "resize_" + handle
            return "move" if self._source_index_at(pos) == index else ""
        if self._stage_rect().contains(pos):
            self._set_selection(set())
        return ""

    def _rotation_handle_points(self, rect, corners=None):
        center = rect.center()
        corners = corners or (rect.topLeft(), rect.topRight(),
                              rect.bottomLeft(), rect.bottomRight())
        result = []
        for point in corners:
            dx, dy = point.x() - center.x(), point.y() - center.y()
            length = math.hypot(dx, dy) or 1.0
            # Keep the rotation target close enough to discover without
            # colliding with the corner resize handle itself.
            result.append(QPointF(point.x() + dx / length * 20.0,
                                  point.y() + dy / length * 20.0))
        return result

    @staticmethod
    def _near_resize_corner(pos, corners):
        # Preserve the exact corner for resizing; surrounding points belong
        # to the larger, easier-to-hit rotation target.
        return any(math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= 6
                   for point in corners)

    def _rotation_handle_at(self, pos):
        if not self._stage_rect().contains(pos):
            return None
        selection = self._selection_indices()
        if len(selection) > 1:
            group_rect = self._selection_canvas_rect(selection)
            corners = (group_rect.topLeft(), group_rect.topRight(),
                       group_rect.bottomLeft(), group_rect.bottomRight())
            if self._near_resize_corner(pos, corners):
                return None
            for corner, point in zip(("tl", "tr", "bl", "br"),
                                     self._rotation_handle_points(group_rect)):
                if math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= 16:
                    return {"kind": "group", "corner": corner, "point": point}
            return None
        for index in range(len(self.sources) - 1, -1, -1):
            source = self.sources[index]
            visual_rect = self._source_canvas_bounds(source)
            corners = self._source_canvas_corners(source)
            if self._near_resize_corner(pos, corners):
                continue
            for corner, point in zip(("tl", "tr", "bl", "br"),
                                     self._rotation_handle_points(visual_rect, corners)):
                if math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= 16:
                    return {"kind": "source", "index": index,
                            "corner": corner, "point": point}
        return None

    def _rotation_hover_angle(self, rotation_handle):
        if rotation_handle is None:
            return None
        if rotation_handle["kind"] == "source":
            rotation = self.sources[rotation_handle["index"]].get("rotation", 0)
            return int(round(float(rotation))) % 360
        return 0

    def _rotation_label_rect(self, cursor_pos, width, height):
        """Place the angle badge beside the pointer, flipping/clamping at edges."""
        gap = 8
        left = cursor_pos.x() + gap
        top = cursor_pos.y() - height - gap
        if left + width > self.width() - 4:
            left = cursor_pos.x() - width - gap
        if top < 4:
            top = cursor_pos.y() + gap
        left = min(max(4.0, left), max(4.0, self.width() - width - 4))
        top = min(max(4.0, top), max(4.0, self.height() - height - 4))
        return QRectF(left, top, width, height)

    def _set_interaction_cursor(self, resize_cursor=None, rotating=False):
        if rotating:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._rotate_hover is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif resize_cursor is not None:
            self.setCursor(resize_cursor)
        else:
            self.unsetCursor()

    @staticmethod
    def _resize_cursor_shape(rect, point):
        dx, dy = point.x() - rect.center().x(), point.y() - rect.center().y()
        if abs(dx) < abs(dy) * 0.45:
            return Qt.CursorShape.SizeVerCursor
        if abs(dy) < abs(dx) * 0.45:
            return Qt.CursorShape.SizeHorCursor
        return (Qt.CursorShape.SizeFDiagCursor if dx * dy >= 0
                else Qt.CursorShape.SizeBDiagCursor)

    def _resize_cursor_at(self, pos):
        selection = self._selection_indices()
        if len(selection) > 1:
            rect = self._selection_canvas_rect(selection)
            for _, point in self._handle_points(rect):
                if (pos - point).manhattanLength() <= self._handle:
                    return self._resize_cursor_shape(rect, point)
            return None
        if len(selection) == 1:
            source = self.sources[selection[0]]
            rect = self._source_canvas_bounds(source)
            corners = self._source_canvas_corners(source)
            points = [*corners,
                      self._midpoint(corners[0], corners[1]),
                      self._midpoint(corners[2], corners[3]),
                      self._midpoint(corners[0], corners[2]),
                      self._midpoint(corners[1], corners[3])]
            for point in points:
                if (pos - point).manhattanLength() <= self._handle:
                    return self._resize_cursor_shape(rect, point)
        return None

    def _begin_rotation(self, pos):
        indices = self._selection_indices()
        bounds = self._selection_bounds(indices)
        center = QPointF(bounds["x"] + bounds["width"] / 2,
                         bounds["y"] + bounds["height"] / 2)
        point = self._from_canvas_point(pos)
        angle = math.degrees(math.atan2(point.y() - center.y(),
                                        point.x() - center.x()))
        self._rotation_state = {
            "center": center,
            "last_angle": angle,
            "delta": 0.0,
            "indices": indices,
            "originals": [dict(self.sources[index]) for index in indices],
            "dimension_inside": {
                index: self._dimension_inside_cache.get(index, True)
                for index in indices
            },
        }
        self._rotation_angle = 0
        self._rotation_label_pos = QPointF(pos)

    @staticmethod
    def _rotation_delta_for_display(delta, snap_offset=0.0):
        nearest_quarter = round((delta + snap_offset) / 90.0) * 90 - snap_offset
        if abs(delta - nearest_quarter) <= 4.0:
            delta = float(nearest_quarter)
        return int(round(delta))

    def _rotate_selection(self, pos):
        state = self._rotation_state
        if not state:
            return
        self._rotation_label_pos = QPointF(pos)
        point = self._from_canvas_point(pos)
        center = state["center"]
        angle = math.degrees(math.atan2(point.y() - center.y(),
                                        point.x() - center.x()))
        change = (angle - state["last_angle"] + 180.0) % 360.0 - 180.0
        state["delta"] += change
        state["last_angle"] = angle
        snap_offset = (float(state["originals"][0].get("rotation", 0))
                       if len(state["indices"]) == 1 else 0.0)
        delta = self._rotation_delta_for_display(state["delta"], snap_offset)
        radians = math.radians(delta)
        cosine, sine = math.cos(radians), math.sin(radians)
        cx, cy = center.x(), center.y()
        for index, original in zip(state["indices"], state["originals"]):
            source_cx = original["x"] + original["width"] / 2
            source_cy = original["y"] + original["height"] / 2
            offset_x, offset_y = source_cx - cx, source_cy - cy
            rotated_x = cx + offset_x * cosine - offset_y * sine
            rotated_y = cy + offset_x * sine + offset_y * cosine
            source = self.sources[index]
            source["x"] = rotated_x - original["width"] / 2
            source["y"] = rotated_y - original["height"] / 2
            source["rotation"] = (float(original.get("rotation", 0)) + delta) % 360
        self._rotation_angle = (int(round(float(state["originals"][0].get("rotation", 0)) + delta)) % 360
                                if len(state["indices"]) == 1 else delta)

    @staticmethod
    def _midpoint(first, second):
        return QPointF((first.x() + second.x()) / 2,
                       (first.y() + second.y()) / 2)

    @staticmethod
    def _handle_points(rect):
        return (
            ("tl", rect.topLeft()), ("tr", rect.topRight()),
            ("bl", rect.bottomLeft()), ("br", rect.bottomRight()),
            ("tm", QPointF(rect.center().x(), rect.top())),
            ("bm", QPointF(rect.center().x(), rect.bottom())),
            ("ml", QPointF(rect.left(), rect.center().y())),
            ("mr", QPointF(rect.right(), rect.center().y())),
        )

    def _selection_canvas_rect(self, indices=None):
        indices = self._selection_indices() if indices is None else indices
        rects = [self._source_canvas_bounds(self.sources[index]) for index in indices]
        if not rects:
            return QRectF()
        result = QRectF(rects[0])
        for rect in rects[1:]:
            result = result.united(rect)
        return result

    def _selection_bounds(self, indices=None):
        indices = self._selection_indices() if indices is None else indices
        items = [self.sources[index] for index in indices]
        if not items:
            return None
        corners = [point for item in items for point in self._rotated_source_corners(item)]
        left = min(point.x() for point in corners)
        top = min(point.y() for point in corners)
        right = max(point.x() for point in corners)
        bottom = max(point.y() for point in corners)
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def _source_index_at(self, pos):
        if not self._stage_rect().contains(pos):
            return -1
        # Sources are stored and painted bottom-to-top, so hit the topmost first.
        point = self._from_canvas_point(pos)
        for index in range(len(self.sources) - 1, -1, -1):
            source = self.sources[index]
            center_x = source["x"] + source["width"] / 2
            center_y = source["y"] + source["height"] / 2
            angle = math.radians(-float(source.get("rotation", 0)))
            dx, dy = point.x() - center_x, point.y() - center_y
            local_x = center_x + dx * math.cos(angle) - dy * math.sin(angle)
            local_y = center_y + dx * math.sin(angle) + dy * math.cos(angle)
            if (source["x"] <= local_x <= source["x"] + source["width"]
                    and source["y"] <= local_y <= source["y"] + source["height"]):
                return index
        return -1

    def _move_selected_layer(self, direction):
        if not 0 <= self.selected < len(self.sources):
            return False
        count = len(self.sources)
        index = self.selected
        targets = {
            "forward": min(index + 1, count - 1),
            "backward": max(index - 1, 0),
            "front": count - 1,
            "back": 0,
        }
        target = targets.get(direction, index)
        if target == index:
            return False
        source = self.sources.pop(index)
        self.sources.insert(target, source)
        self._set_selection({target}, target)
        self.update()
        self.sceneChanged.emit()
        return True

    def _set_source_cursor_visible(self, index, visible):
        self._set_sources_cursor_visible((index,), visible)

    def _set_sources_cursor_visible(self, indices, visible):
        valid = {index for index in indices if 0 <= index < len(self.sources)}
        if not valid:
            return
        for index in valid:
            self.sources[index]["showCursor"] = bool(visible)
        self.update()
        self.sceneChanged.emit()

    def _select_context_menu_targets(self, index):
        """Preserve a multi-selection when its member opens the context menu."""
        if not 0 <= index < len(self.sources):
            return []
        selection = set(self._selection_indices())
        if index not in selection:
            selection = {index}
        self._set_selection(selection, index)
        self.update()
        return self._selection_indices()

    def _exec_context_menu(self, menu, position):
        return menu.exec(position)

    def _resize_selected_group(self, dx, dy, handle, constrain=True, centered=False):
        indices = self._selection_indices()
        if len(indices) < 2:
            return
        items = [self.sources[index] for index in indices]
        group_bounds = self._selection_bounds(indices)
        left, top = group_bounds["x"], group_bounds["y"]
        right, bottom = left + group_bounds["width"], top + group_bounds["height"]
        width, height = right - left, bottom - top
        left_edge, right_edge = "l" in handle, "r" in handle
        top_edge, bottom_edge = "t" in handle, "b" in handle
        horizontal, vertical = left_edge or right_edge, top_edge or bottom_edge
        if not horizontal and not vertical:
            return

        multiplier = 2.0 if centered else 1.0
        scale_x = (max(0.0, (width + multiplier * (-dx if left_edge else dx)) / width)
                   if horizontal else 1.0)
        scale_y = (max(0.0, (height + multiplier * (-dy if top_edge else dy)) / height)
                   if vertical else 1.0)
        if constrain:
            scale = (scale_x if horizontal and not vertical else
                     scale_y if vertical and not horizontal else
                     scale_x if abs(dx) / width >= abs(dy) / height else scale_y)
            scale_x = scale_y = scale

        min_x = max(16.0 / item["width"] for item in items)
        min_y = max(16.0 / item["height"] for item in items)
        scale_x = max(min_x, scale_x)
        scale_y = max(min_y, scale_y)
        anchor_x = ((left + right) / 2 if centered else
                    right if left_edge else left if right_edge else (left + right) / 2)
        anchor_y = ((top + bottom) / 2 if centered else
                    bottom if top_edge else top if bottom_edge else (top + bottom) / 2)
        for item in items:
            item["x"] = anchor_x + (item["x"] - anchor_x) * scale_x
            item["y"] = anchor_y + (item["y"] - anchor_y) * scale_y
            item["width"] *= scale_x
            item["height"] *= scale_y

    def contextMenuEvent(self, event):
        index = self._source_index_at(event.pos())
        if index < 0:
            event.ignore()
            return
        targets = self._select_context_menu_targets(index)
        menu = PersistentToggleMenu(self)
        actions = {
            "forward": menu.addAction("Bring Forward"),
            "backward": menu.addAction("Send Backward"),
            "front": menu.addAction("Bring to Front"),
            "back": menu.addAction("Send to Back"),
        }
        single_selection = len(targets) == 1
        actions["forward"].setEnabled(
            single_selection and index < len(self.sources) - 1)
        actions["backward"].setEnabled(single_selection and index > 0)
        actions["front"].setEnabled(
            single_selection and index < len(self.sources) - 1)
        actions["back"].setEnabled(single_selection and index > 0)
        menu.addSeparator()
        cursor_values = [
            bool(self.sources[target].get("showCursor", True))
            for target in targets
        ]
        cursor_label = ("Show Stream Cursor" if len(targets) == 1 else
                        "Show Stream Cursor for Selection")
        cursor_action = QAction(cursor_label, menu)
        cursor_action.setCheckable(True)
        cursor_action.setChecked(all(cursor_values))
        menu.set_persistent_toggle_action(cursor_action)
        mixed_cursor_state = any(cursor_values) and not all(cursor_values)
        menu.addAction(cursor_action)
        mixed_mark = None
        if mixed_cursor_state:
            # Keep QAction's native label layout; overlay the mixed-state dash
            # in the menu gutter so it cannot reserve extra icon/text spacing.
            mixed_mark = QLabel("−", menu)
            mixed_mark.setObjectName("mixedCursorStateMark")
            mixed_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mixed_mark.setFont(menu.font())
            mixed_mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            mixed_mark.setForegroundRole(QPalette.ColorRole.Text)

            def position_mixed_mark():
                action_rect = menu.actionGeometry(cursor_action)
                inset = menu.style().pixelMetric(
                    QStyle.PixelMetric.PM_MenuPanelWidth, None, menu) + 8
                mixed_mark.setGeometry(
                    action_rect.left() + inset, action_rect.top(), 12,
                    action_rect.height())
                mixed_mark.raise_()
                mixed_mark.show()

            def update_mixed_mark_color(action):
                role = (QPalette.ColorRole.HighlightedText
                        if action is cursor_action else QPalette.ColorRole.Text)
                palette = mixed_mark.palette()
                palette.setColor(QPalette.ColorRole.WindowText,
                                 menu.palette().color(role))
                mixed_mark.setPalette(palette)

            menu.aboutToShow.connect(lambda: QTimer.singleShot(0, position_mixed_mark))
            menu.hovered.connect(update_mixed_mark_color)

        def apply_cursor_state(visible):
            self._set_sources_cursor_visible(targets, visible)
            if mixed_mark is not None:
                mixed_mark.hide()

        cursor_action.toggled.connect(apply_cursor_state)
        chosen = self._exec_context_menu(menu, event.globalPos())
        direction = next((key for key, action in actions.items() if action is chosen), None)
        if direction:
            self._move_selected_layer(direction)
        event.accept()

    def _resize_selected(self, dx, dy, handle, constrain=False, centered=False):
        if not 0 <= self.selected < len(self.sources):
            return
        item = self.sources[self.selected]
        bounds = self.workspace_bounds
        minimum = 16.0
        right = item["x"] + item["width"]
        bottom = item["y"] + item["height"]

        if centered:
            x, y = float(item["x"]), float(item["y"])
            width, height = float(item["width"]), float(item["height"])
            center_x, center_y = x + width / 2, y + height / 2
            left_edge, right_edge = "l" in handle, "r" in handle
            top_edge, bottom_edge = "t" in handle, "b" in handle
            horizontal = left_edge or right_edge
            vertical = top_edge or bottom_edge
            if constrain:
                if horizontal and vertical:
                    delta_w = 2 * (-dx if left_edge else dx)
                    delta_h = 2 * (-dy if top_edge else dy)
                    scale_w = max(0.0, (width + delta_w) / width)
                    scale_h = max(0.0, (height + delta_h) / height)
                    scale = (scale_w if abs(delta_w) / width >= abs(delta_h) / height
                             else scale_h)
                elif horizontal:
                    scale = (width + 2 * (-dx if left_edge else dx)) / width
                elif vertical:
                    scale = (height + 2 * (-dy if top_edge else dy)) / height
                else:
                    return
                min_scale = max(minimum / width, minimum / height)
                # Center-anchored resizing must not stop at the workspace edge:
                # the workspace is only an initial editing canvas, not a hard
                # limit on source dimensions.
                scale = max(min_scale, scale)
                new_width, new_height = width * scale, height * scale
            else:
                new_width = width + (2 * (-dx if left_edge else dx) if horizontal else 0)
                new_height = height + (2 * (-dy if top_edge else dy) if vertical else 0)
                if horizontal:
                    new_width = max(minimum, new_width)
                if vertical:
                    new_height = max(minimum, new_height)
            item["x"], item["y"] = center_x - new_width / 2, center_y - new_height / 2
            item["width"], item["height"] = new_width, new_height
            return

        if constrain:
            x, y = item["x"], item["y"]
            width, height = item["width"], item["height"]
            ratio = width / height
            left_edge, right_edge = "l" in handle, "r" in handle
            top_edge, bottom_edge = "t" in handle, "b" in handle
            horizontal_edge = left_edge or right_edge
            vertical_edge = top_edge or bottom_edge

            if horizontal_edge and vertical_edge:
                # Keep the opposite corner fixed; let the dominant pointer axis
                # determine the uniform scale factor.
                delta_w = -dx if left_edge else dx
                delta_h = -dy if top_edge else dy
                scale_w = max(0.0, (width + delta_w) / width)
                scale_h = max(0.0, (height + delta_h) / height)
                scale = scale_w if abs(delta_w) / width >= abs(delta_h) / height else scale_h
                anchor_x = right if left_edge else x
                anchor_y = bottom if top_edge else y
                max_scale = min(
                    (anchor_x - bounds.left() if left_edge else bounds.right() - anchor_x) / width,
                    (anchor_y - bounds.top() if top_edge else bounds.bottom() - anchor_y) / height,
                )
            elif horizontal_edge:
                delta_w = -dx if left_edge else dx
                scale = (width + delta_w) / width
                anchor_x = right if left_edge else x
                center_y = y + height / 2
                max_scale = min(
                    (anchor_x - bounds.left() if left_edge else bounds.right() - anchor_x) / width,
                    2 * (center_y - bounds.top()) / height,
                    2 * (bounds.bottom() - center_y) / height,
                )
            elif vertical_edge:
                delta_h = -dy if top_edge else dy
                scale = (height + delta_h) / height
                anchor_y = bottom if top_edge else y
                center_x = x + width / 2
                max_scale = min(
                    (anchor_y - bounds.top() if top_edge else bounds.bottom() - anchor_y) / height,
                    2 * (center_x - bounds.left()) / width,
                    2 * (bounds.right() - center_x) / width,
                )
            else:
                return

            min_scale = max(minimum / width, minimum / height)
            scale = min(max(min_scale, scale), max(min_scale, max_scale))
            new_width, new_height = width * scale, height * scale
            if left_edge:
                item["x"] = right - new_width
            elif not horizontal_edge:
                item["x"] = x + (width - new_width) / 2
            if top_edge:
                item["y"] = bottom - new_height
            elif not vertical_edge:
                item["y"] = y + (height - new_height) / 2
            item["width"], item["height"] = new_width, new_height
            return

        if "l" in handle:
            candidate = item["x"] + dx
            minimum_x = max(bounds.left(), right - bounds.width())
            maximum = right - minimum
            item["x"] = min(max(minimum_x, candidate), maximum)
            item["width"] = right - item["x"]
        elif "r" in handle:
            item["width"] = min(max(minimum, item["width"] + dx),
                                 max(minimum, bounds.right() - item["x"]))

        if "t" in handle:
            candidate = item["y"] + dy
            minimum_y = max(bounds.top(), bottom - bounds.height())
            maximum = bottom - minimum
            item["y"] = min(max(minimum_y, candidate), maximum)
            item["height"] = bottom - item["y"]
        elif "b" in handle:
            item["height"] = min(max(minimum, item["height"] + dy),
                                 max(minimum, bounds.bottom() - item["y"]))

    def _move_selected_by(self, dx, dy):
        indices = self._selection_indices()
        if not indices:
            return
        bounds = self.workspace_bounds
        items = [self.sources[index] for index in indices]
        left = min(item["x"] for item in items)
        top = min(item["y"] for item in items)
        right = max(item["x"] + item["width"] for item in items)
        bottom = max(item["y"] + item["height"] for item in items)
        dx = min(max(bounds.left() - left, dx), bounds.right() - right)
        dy = min(max(bounds.top() - top, dy), bounds.bottom() - bottom)
        for item in items:
            item["x"] += dx
            item["y"] += dy

    def _output_snap_delta(self, axis, side, edge, previous, threshold):
        """Return a snap delta only when an edge moves outward from inside an output."""
        outputs = self.layout_displays or [{
            "x": self.layout_bounds.left(), "y": self.layout_bounds.top(),
            "width": self.layout_bounds.width(), "height": self.layout_bounds.height(),
        }]
        candidates = []
        if axis == "x":
            start, size = "x", "width"
            previous_edge = float(previous["x"] if side == "l" else
                                  previous["x"] + previous["width"])
            orthogonal_start = float(previous["y"])
            orthogonal_end = orthogonal_start + float(previous["height"])
        else:
            start, size = "y", "height"
            previous_edge = float(previous["y"] if side == "t" else
                                  previous["y"] + previous["height"])
            orthogonal_start = float(previous["x"])
            orthogonal_end = orthogonal_start + float(previous["width"])

        for output in outputs:
            output_start = float(output[start])
            output_end = output_start + float(output[size])
            if axis == "x":
                output_orth_start = float(output["y"])
                output_orth_end = output_orth_start + float(output["height"])
            else:
                output_orth_start = float(output["x"])
                output_orth_end = output_orth_start + float(output["width"])
            # Use the finite projected span of the visible rotated bounds,
            # not only the card centerline, to decide whether edges meet.
            if (orthogonal_end < output_orth_start
                    or orthogonal_start > output_orth_end):
                continue

            target = output_start if side in ("l", "t") else output_end
            approached_from_inside = (
                previous_edge >= target and edge < previous_edge
                if side in ("l", "t") else
                previous_edge <= target and edge > previous_edge
            )
            delta = target - edge
            if approached_from_inside and abs(delta) <= threshold:
                candidates.append(delta)
        return min(candidates, key=abs) if candidates else None

    def _source_snap_delta(self, axis, side, edge, previous, threshold):
        """Snap only when separate source cards approach edge-to-edge."""
        selected_indices = self._selection_indices()
        if (len(selected_indices) == 1
                and abs(float(self.sources[selected_indices[0]].get("rotation", 0)))
                % 180 > 1e-6):
            # A rotated card's visible edge is oblique; its AABB is not a real
            # edge. Oblique source edges are handled by the vector snapper.
            return None
        if axis == "x":
            previous_edge = float(previous["x"] if side == "l" else
                                  previous["x"] + previous["width"])
        else:
            previous_edge = float(previous["y"] if side == "t" else
                                  previous["y"] + previous["height"])

        candidates = []
        selected = set(self._selection_indices())
        item = self._selection_bounds()
        if item is None:
            return None
        if axis == "x":
            orth_start = float(item["y"])
            orth_end = orth_start + float(item["height"])
        else:
            orth_start = float(item["x"])
            orth_end = orth_start + float(item["width"])
        for index, other in enumerate(self.sources):
            if index in selected:
                continue
            if (len(selected_indices) == 1
                    and abs(float(other.get("rotation", 0))) % 180 > 1e-6):
                continue
            other_corners = self._rotated_source_corners(other)
            other_left = min(point.x() for point in other_corners)
            other_top = min(point.y() for point in other_corners)
            other_right = max(point.x() for point in other_corners)
            other_bottom = max(point.y() for point in other_corners)
            if axis == "x":
                other_orth_start, other_orth_end = other_top, other_bottom
                target = other_left if side == "r" else other_right
            else:
                other_orth_start, other_orth_end = other_left, other_right
                target = other_top if side == "b" else other_bottom
            # Only snap when the finite edge segments actually overlap; an
            # infinitely extended alignment axis must not pull distant cards.
            if min(orth_end, other_orth_end) <= max(orth_start, other_orth_start):
                continue

            if axis == "x":
                gap_before = (target - previous_edge if side == "r" else
                              previous_edge - target)
            else:
                gap_before = (target - previous_edge if side == "b" else
                              previous_edge - target)
            if gap_before < 0:
                continue

            approached_from_outside = (
                edge < previous_edge
                if side in ("l", "t") else
                edge > previous_edge
            )
            delta = target - edge
            if approached_from_outside and abs(delta) <= threshold:
                candidates.append(delta)
        return min(candidates, key=abs) if candidates else None

    def _snap_source_edges_by_vector(self, movement):
        """Align parallel visible source edges with a normal-direction correction."""
        indices = self._selection_indices()
        if len(indices) != 1:
            self._source_edge_snap_latch = None
            return set()
        selected_index = indices[0]
        selected_source = self.sources[selected_index]
        edge_pairs = ((0, 1), (1, 3), (3, 2), (2, 0))
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        movement_x, movement_y = movement
        angular_tolerance_cos = math.cos(math.radians(1.0))
        candidates = []

        def edge_geometry(source, edge_index):
            points = self._rotated_source_corners(source)
            start_index, end_index = edge_pairs[edge_index]
            start, end = points[start_index], points[end_index]
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = math.hypot(dx, dy)
            if length <= 1e-6:
                return None
            tangent = (dx / length, dy / length)
            normal = (-tangent[1], tangent[0])
            center = (float(source["x"]) + float(source["width"]) / 2,
                      float(source["y"]) + float(source["height"]) / 2)
            midpoint = ((start.x() + end.x()) / 2,
                        (start.y() + end.y()) / 2)
            if ((midpoint[0] - center[0]) * normal[0]
                    + (midpoint[1] - center[1]) * normal[1] < 0):
                normal = (-normal[0], -normal[1])
            return start, end, midpoint, tangent, normal

        def edge_pair_data(target_index, selected_edge_index, target_edge_index):
            target = self.sources[target_index]
            selected_edge = edge_geometry(selected_source, selected_edge_index)
            target_edge = edge_geometry(target, target_edge_index)
            if selected_edge is None or target_edge is None:
                return None
            start, end, midpoint, tangent, normal = selected_edge
            target_start, target_end, target_midpoint, target_tangent, target_normal = target_edge
            parallel_cross = tangent[0] * target_tangent[1] - tangent[1] * target_tangent[0]
            if abs(parallel_cross) > math.sin(math.radians(1.0)):
                return None
            if (normal[0] * target_normal[0] + normal[1] * target_normal[1]
                    > -angular_tolerance_cos):
                return None
            projection = lambda point: point.x() * tangent[0] + point.y() * tangent[1]
            selected_projection = sorted((projection(start), projection(end)))
            target_projection = sorted((projection(target_start), projection(target_end)))
            overlap = min(selected_projection[1], target_projection[1]) - max(
                selected_projection[0], target_projection[0])
            if overlap <= 1e-6:
                return None
            gap = ((target_midpoint[0] - midpoint[0]) * normal[0]
                   + (target_midpoint[1] - midpoint[1]) * normal[1])
            return gap, normal

        latch = self._source_edge_snap_latch
        if latch is not None:
            target_index = latch["target"]
            if target_index not in range(len(self.sources)) or target_index in indices:
                self._source_edge_snap_latch = None
            else:
                pair = edge_pair_data(target_index, latch["source_edge"],
                                      latch["target_edge"])
                if pair is not None and abs(pair[0]) <= threshold * 2.5:
                    gap, normal = pair
                    selected_source["x"] += normal[0] * gap
                    selected_source["y"] += normal[1] * gap
                    return {"x", "y"}
                self._source_edge_snap_latch = None

        for other_index, other in enumerate(self.sources):
            if other_index == selected_index:
                continue
            for selected_edge_index in range(len(edge_pairs)):
                for target_edge_index in range(len(edge_pairs)):
                    pair = edge_pair_data(other_index, selected_edge_index,
                                          target_edge_index)
                    if pair is None:
                        continue
                    gap, normal = pair
                    if not 1e-6 < gap <= threshold:
                        continue
                    previous_gap = (gap + movement_x * normal[0]
                                    + movement_y * normal[1])
                    if previous_gap <= gap + 1e-6:
                        continue
                    candidates.append((gap, other_index, selected_edge_index,
                                       target_edge_index, normal))

        if not candidates:
            return set()
        gap, target_index, selected_edge_index, target_edge_index, normal = min(
            candidates, key=lambda item: item[0])
        correction_x, correction_y = normal[0] * gap, normal[1] * gap
        selected_source["x"] += correction_x
        selected_source["y"] += correction_y
        self._source_edge_snap_latch = {
            "target": target_index, "source_edge": selected_edge_index,
            "target_edge": target_edge_index,
        }
        return {"x", "y"}

    def _snap_delta(self, axis, side, edge, previous, threshold,
                    source_only=False, output_only=False):
        # Source-to-source alignment has precedence over output boundaries;
        # distance only chooses among candidates of the same kind.
        if output_only:
            return self._output_snap_delta(axis, side, edge, previous, threshold)
        source_delta = self._source_snap_delta(axis, side, edge, previous, threshold)
        if source_delta is not None:
            return source_delta
        if source_only:
            return None
        return self._output_snap_delta(axis, side, edge, previous, threshold)

    def _snap_selected_to_edges(self, handle, previous, movement=(0.0, 0.0),
                                constrain=False, skip_axes=(), centered=False,
                                source_only=False, output_only=False):
        """Snap source edges to output boundaries, only while moving outward."""
        indices = self._selection_indices()
        if len(indices) != 1:
            return
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        item = self.sources[self.selected]
        left, top = float(item["x"]), float(item["y"])
        right, bottom = left + float(item["width"]), top + float(item["height"])

        if handle == "move":
            dx, dy = movement
            visual_bounds = self._selection_bounds(indices)
            left, top = visual_bounds["x"], visual_bounds["y"]
            right = left + visual_bounds["width"]
            bottom = top + visual_bounds["height"]
            snapped_axes = set()
            if "x" in skip_axes:
                pass
            elif dx < 0:
                delta = self._snap_delta("x", "l", left, previous, threshold,
                                         source_only=source_only,
                                         output_only=output_only)
                if delta is not None:
                    item["x"] += delta
                    snapped_axes.add("x")
            elif dx > 0:
                delta = self._snap_delta("x", "r", right, previous, threshold,
                                         source_only=source_only,
                                         output_only=output_only)
                if delta is not None:
                    item["x"] += delta
                    snapped_axes.add("x")
            if "y" in skip_axes:
                pass
            elif dy < 0:
                delta = self._snap_delta("y", "t", top, previous, threshold,
                                         source_only=source_only,
                                         output_only=output_only)
                if delta is not None:
                    item["y"] += delta
                    snapped_axes.add("y")
            elif dy > 0:
                delta = self._snap_delta("y", "b", bottom, previous, threshold,
                                         source_only=source_only,
                                         output_only=output_only)
                if delta is not None:
                    item["y"] += delta
                    snapped_axes.add("y")
            return snapped_axes

        active_x = "l" if "l" in handle else "r" if "r" in handle else ""
        active_y = "t" if "t" in handle else "b" if "b" in handle else ""
        if constrain:
            candidates = []
            for axis, side, edge, extent in (
                    ("x", active_x, left if active_x == "l" else right,
                     float(item["width"])),
                    ("y", active_y, top if active_y == "t" else bottom,
                     float(item["height"]))):
                if not side:
                    continue
                delta = self._snap_delta(axis, side, edge, previous, threshold,
                                         source_only=source_only,
                                         output_only=output_only)
                if delta is not None:
                    factor = 1.0 + (-delta if side in ("l", "t") else delta) / (
                        extent / 2 if centered else extent)
                    if factor > 0:
                        candidates.append((abs(delta), axis, side, factor))
            if not candidates:
                return
            _, _axis, _side, factor = min(candidates, key=lambda candidate: candidate[0])
            width, height = float(item["width"]), float(item["height"])
            new_width, new_height = width * factor, height * factor
            if new_width < 16.0 or new_height < 16.0:
                return
            if centered:
                center_x, center_y = (left + right) / 2, (top + bottom) / 2
                item["x"] = center_x - new_width / 2
                item["y"] = center_y - new_height / 2
            elif active_x == "l":
                item["x"] = right - new_width
            elif active_x == "r":
                item["x"] = left
            else:
                item["x"] = (left + right - new_width) / 2
            if not centered and active_y == "t":
                item["y"] = bottom - new_height
            elif not centered and active_y == "b":
                item["y"] = top
            elif not centered:
                item["y"] = (top + bottom - new_height) / 2
            item["width"], item["height"] = new_width, new_height
            return

        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        if active_x:
            edge = left if active_x == "l" else right
            delta = self._snap_delta("x", active_x, edge, previous, threshold,
                                     source_only=source_only,
                                     output_only=output_only)
            if delta is not None and active_x == "l":
                new_width = float(item["width"]) - delta
                if new_width >= 16.0:
                    item["width"] = new_width
                    item["x"] = center_x - new_width / 2 if centered else left + delta
            elif delta is not None:
                new_width = float(item["width"]) + delta
                if new_width >= 16.0:
                    item["width"] = new_width
                    if centered:
                        item["x"] = center_x - new_width / 2
        if active_y:
            edge = top if active_y == "t" else bottom
            delta = self._snap_delta("y", active_y, edge, previous, threshold,
                                     source_only=source_only,
                                     output_only=output_only)
            if delta is not None and active_y == "t":
                new_height = float(item["height"]) - delta
                if new_height >= 16.0:
                    item["height"] = new_height
                    item["y"] = center_y - new_height / 2 if centered else top + delta
            elif delta is not None:
                new_height = float(item["height"]) + delta
                if new_height >= 16.0:
                    item["height"] = new_height
                    if centered:
                        item["y"] = center_y - new_height / 2

    def _snap_rotated_resize_to_output_edge(self, handle, previous,
                                            constrain, centered=False):
        """Snap only the grabbed rotated corner, keeping the resize anchor fixed."""
        if len(self._selection_indices()) != 1 or len(handle) != 2:
            return set()
        source = self.sources[self.selected]
        corners_by_handle = {"tl": 0, "tr": 1, "bl": 2, "br": 3}
        active_index = corners_by_handle.get(handle)
        if active_index is None:
            return set()
        opposite_index = {0: 3, 1: 2, 2: 1, 3: 0}[active_index]
        current_corners = self._rotated_source_corners(source)
        previous_corners = self._rotated_source_corners(previous)
        outputs = self.layout_displays or [{
            "x": self.layout_bounds.left(), "y": self.layout_bounds.top(),
            "width": self.layout_bounds.width(),
            "height": self.layout_bounds.height(),
        }]
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        candidates = []
        for corner_index in (active_index,):
            active = current_corners[corner_index]
            before = previous_corners[corner_index]
            for output in outputs:
                left, top = float(output["x"]), float(output["y"])
                right = left + float(output["width"])
                bottom = top + float(output["height"])
                if not (left - 1e-6 <= before.x() <= right + 1e-6
                        and top - 1e-6 <= before.y() <= bottom + 1e-6):
                    continue
                for axis, edge, along, low, high, moving_outward in (
                    ("x", left, active.y(), top, bottom, active.x() < before.x()),
                    ("x", right, active.y(), top, bottom, active.x() > before.x()),
                    ("y", top, active.x(), left, right, active.y() < before.y()),
                    ("y", bottom, active.x(), left, right, active.y() > before.y()),
                ):
                    coordinate = active.x() if axis == "x" else active.y()
                    distance = abs(edge - coordinate)
                    if (moving_outward and low - 1e-6 <= along <= high + 1e-6
                            and distance <= threshold):
                        candidates.append((distance, axis, edge, corner_index, low, high))
        if not candidates:
            return set()

        size_width, size_height = float(source["width"]), float(source["height"])
        angle = math.radians(float(source.get("rotation", 0)))
        cosine, sine = math.cos(angle), math.sin(angle)
        if centered:
            anchor = QPointF(source["x"] + size_width / 2,
                             source["y"] + size_height / 2)
        else:
            anchor = current_corners[opposite_index]

        # Only the grabbed corner may trigger a contact. Other corners can
        # pass an output boundary without affecting this resize gesture.
        for _distance, axis, edge, corner_index, low, high in sorted(candidates):
            active = current_corners[corner_index]
            active_coordinate = active.x() if axis == "x" else active.y()
            anchor_coordinate = anchor.x() if axis == "x" else anchor.y()
            if constrain:
                denominator = active_coordinate - anchor_coordinate
                if abs(denominator) < 1e-6:
                    continue
                factor = (edge - anchor_coordinate) / denominator
                if factor <= 0:
                    continue
                new_width, new_height = size_width * factor, size_height * factor
                contact = anchor + (active - anchor) * factor
            else:
                corner_x, corner_y = corner_index % 2, corner_index // 2
                anchor_x = 0.5 if centered else opposite_index % 2
                anchor_y = 0.5 if centered else opposite_index // 2
                vx = QPointF(cosine * (corner_x - anchor_x),
                             sine * (corner_x - anchor_x))
                vy = QPointF(-sine * (corner_y - anchor_y),
                             cosine * (corner_y - anchor_y))
                vectors = (vx, vy)
                coefficients = [v.x() if axis == "x" else v.y() for v in vectors]
                dimension = max(range(2), key=lambda i: abs(coefficients[i]))
                if abs(coefficients[dimension]) < 1e-6:
                    continue
                delta_size = (edge - active_coordinate) / coefficients[dimension]
                new_width = size_width + (delta_size if dimension == 0 else 0)
                new_height = size_height + (delta_size if dimension == 1 else 0)
                contact = active + vectors[dimension] * delta_size
            along = contact.y() if axis == "x" else contact.x()
            if new_width >= 16.0 and new_height >= 16.0 and low <= along <= high:
                break
        else:
            return set()

        source["width"], source["height"] = new_width, new_height
        if centered:
            source["x"] = anchor.x() - new_width / 2
            source["y"] = anchor.y() - new_height / 2
        else:
            offset_x = new_width if "l" in handle else 0.0
            offset_y = new_height if "t" in handle else 0.0
            origin_offset_x = (new_width * (1 - cosine) / 2
                               + new_height * sine / 2)
            origin_offset_y = (new_height * (1 - cosine) / 2
                               - new_width * sine / 2)
            source["x"] = (anchor.x() - origin_offset_x
                           - cosine * offset_x + sine * offset_y)
            source["y"] = (anchor.y() - origin_offset_y
                           - sine * offset_x - cosine * offset_y)
        return {axis}

    def _snap_group_move_to_edges(self, previous, movement, skip_axes=(),
                                  source_only=False, output_only=False):
        indices = self._selection_indices()
        if len(indices) < 2:
            return set()
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        current = self._selection_bounds(indices)
        dx, dy = movement
        snap_x = None
        snap_y = None
        if "x" in skip_axes:
            pass
        elif dx < 0:
            snap_x = self._snap_delta("x", "l", current["x"], previous,
                                      threshold, source_only=source_only,
                                      output_only=output_only)
        elif dx > 0:
            edge = current["x"] + current["width"]
            snap_x = self._snap_delta("x", "r", edge, previous, threshold,
                                      source_only=source_only,
                                      output_only=output_only)
        if "y" in skip_axes:
            pass
        elif dy < 0:
            snap_y = self._snap_delta("y", "t", current["y"], previous,
                                      threshold, source_only=source_only,
                                      output_only=output_only)
        elif dy > 0:
            edge = current["y"] + current["height"]
            snap_y = self._snap_delta("y", "b", edge, previous, threshold,
                                      source_only=source_only,
                                      output_only=output_only)
        for index in indices:
            if snap_x is not None:
                self.sources[index]["x"] += snap_x
            if snap_y is not None:
                self.sources[index]["y"] += snap_y
        return ({"x"} if snap_x is not None else set()) | (
            {"y"} if snap_y is not None else set())

    def _snap_selection_corners(self, previous, movement, skip_axes=()):
        """Snap moving selection corners before lower-priority edge targets."""
        indices = self._selection_indices()
        if not indices or previous is None:
            self._corner_snap_latch = None
            return set()
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        release_threshold = threshold * 2.5
        current = self._selection_bounds(indices)
        selected = set(indices)
        targets = []
        for index, source in enumerate(self.sources):
            if index in selected:
                continue
            targets.extend((float(point.x()), float(point.y()))
                           for point in self._rotated_source_corners(source))
        if not targets:
            self._corner_snap_latch = None
            return set()

        if len(indices) == 1:
            visual_corners = self._rotated_source_corners(self.sources[indices[0]])
            shift_x = float(current["x"] - previous["x"])
            shift_y = float(current["y"] - previous["y"])
            corners = tuple(
                (name, float(point.x()), float(point.y()),
                 float(point.x()) - shift_x, float(point.y()) - shift_y)
                for name, point in zip(("tl", "tr", "bl", "br"), visual_corners)
            )
        else:
            corners = (
                ("tl", float(current["x"]), float(current["y"]),
                 float(previous["x"]), float(previous["y"])),
                ("tr", float(current["x"] + current["width"]),
                 float(current["y"]), float(previous["x"] + previous["width"]),
                 float(previous["y"])),
                ("bl", float(current["x"]),
                 float(current["y"] + current["height"]), float(previous["x"]),
                 float(previous["y"] + previous["height"])),
                ("br", float(current["x"] + current["width"]),
                 float(current["y"] + current["height"]),
                 float(previous["x"] + previous["width"]),
                 float(previous["y"] + previous["height"])),
            )

        def apply_corner_latch(latch):
            corner = next((entry for entry in corners if entry[0] == latch["corner"]), None)
            if corner is None or latch["target"] not in targets:
                return None
            _, x, y, _before_x, _before_y = corner
            target_x, target_y = latch["target"]
            gap_x, gap_y = x - target_x, y - target_y
            if math.hypot(gap_x, gap_y) > release_threshold:
                return None
            for index in indices:
                self.sources[index]["x"] -= gap_x
                self.sources[index]["y"] -= gap_y
            return set(latch["axes"])

        if self._corner_snap_latch is not None:
            latched_axes = apply_corner_latch(self._corner_snap_latch)
            if latched_axes is not None:
                return latched_axes
            self._corner_snap_latch = None

        moved_x, moved_y = movement
        moved = {"x": moved_x, "y": moved_y}
        candidates = []
        for corner_name, x, y, before_x, before_y in corners:
            for target_x, target_y in targets:
                gap_x, gap_y = x - target_x, y - target_y
                if max(abs(gap_x), abs(gap_y)) > threshold:
                    continue

                before_gap_x = before_x - target_x
                before_gap_y = before_y - target_y
                before_distance = math.hypot(
                    0.0 if "x" in skip_axes else before_gap_x,
                    0.0 if "y" in skip_axes else before_gap_y)
                current_distance = math.hypot(
                    0.0 if "x" in skip_axes else gap_x,
                    0.0 if "y" in skip_axes else gap_y)
                # Use radial approach, not per-axis sign checks, so pointer
                # events that step across a corner still catch the magnet.
                if (not any(axis not in skip_axes and abs(moved[axis]) > 1e-6
                            for axis in ("x", "y"))
                        or current_distance >= before_distance - 1e-6):
                    continue
                snapped_axes = {"x", "y"} - set(skip_axes)
                if snapped_axes:
                    candidates.append((current_distance, corner_name,
                                       (target_x, target_y), snapped_axes))

        if not candidates:
            return set()
        _distance, corner_name, target, snapped_axes = min(
            candidates, key=lambda item: item[0])
        self._corner_snap_latch = {
            "corner": corner_name, "target": target,
            "axes": tuple(sorted(snapped_axes)),
        }
        corner = next(entry for entry in corners if entry[0] == corner_name)
        gap_x, gap_y = corner[1] - target[0], corner[2] - target[1]
        for index in indices:
            if "x" in snapped_axes:
                self.sources[index]["x"] -= gap_x
            if "y" in snapped_axes:
                self.sources[index]["y"] -= gap_y
        return snapped_axes

    def _snap_selection_corners_to_source_edges(self, previous, movement):
        """Snap an approaching selection corner onto a finite source edge."""
        indices = self._selection_indices()
        if not indices or previous is None:
            self._corner_edge_snap_latch = None
            return set()
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        release_threshold = threshold * 2.5
        selected = set(indices)
        targets = []
        for target_index, source in enumerate(self.sources):
            if target_index in selected:
                continue
            corners = self._rotated_source_corners(source)
            polygon = (corners[0], corners[1], corners[3], corners[2])
            targets.extend((target_index, edge_index, first, second)
                           for edge_index, (first, second) in enumerate(
                               zip(polygon, polygon[1:] + polygon[:1])))
        if not targets:
            self._corner_edge_snap_latch = None
            return set()

        current = self._selection_bounds(indices)
        if len(indices) == 1:
            shift_x = float(current["x"] - previous["x"])
            shift_y = float(current["y"] - previous["y"])
            corners = tuple(
                (name, float(point.x()), float(point.y()),
                 float(point.x()) - shift_x, float(point.y()) - shift_y)
                for name, point in zip(
                    ("tl", "tr", "bl", "br"),
                    self._rotated_source_corners(self.sources[indices[0]])))
        else:
            corners = (
                ("tl", current["x"], current["y"], previous["x"], previous["y"]),
                ("tr", current["x"] + current["width"], current["y"],
                 previous["x"] + previous["width"], previous["y"]),
                ("bl", current["x"], current["y"] + current["height"],
                 previous["x"], previous["y"] + previous["height"]),
                ("br", current["x"] + current["width"],
                 current["y"] + current["height"],
                 previous["x"] + previous["width"],
                 previous["y"] + previous["height"]),
            )

        def project(x, y, first, second):
            dx, dy = second.x() - first.x(), second.y() - first.y()
            length_squared = dx * dx + dy * dy
            if length_squared <= 1e-12:
                return None
            fraction = ((x - first.x()) * dx + (y - first.y()) * dy) / length_squared
            # Endpoints are handled by the higher-priority corner-to-corner
            # snap, so this rule only applies to the finite edge interior.
            if not 1e-6 < fraction < 1 - 1e-6:
                return None
            return (first.x() + fraction * dx, first.y() + fraction * dy)

        def resolve(target_index, edge_index, x, y):
            target = next((entry for entry in targets
                           if entry[0] == target_index and entry[1] == edge_index), None)
            if target is None:
                return None
            point = project(x, y, target[2], target[3])
            if point is None:
                return None
            gap_x, gap_y = x - point[0], y - point[1]
            return math.hypot(gap_x, gap_y), gap_x, gap_y

        latch = self._corner_edge_snap_latch
        if latch is not None:
            corner = next((entry for entry in corners if entry[0] == latch["corner"]), None)
            resolved = (resolve(latch["target"], latch["edge"],
                                corner[1], corner[2]) if corner else None)
            if resolved is not None and resolved[0] <= release_threshold:
                _, gap_x, gap_y = resolved
                for index in indices:
                    self.sources[index]["x"] -= gap_x
                    self.sources[index]["y"] -= gap_y
                return {"x", "y"}
            self._corner_edge_snap_latch = None

        movement_x, movement_y = movement
        candidates = []
        for corner_name, x, y, before_x, before_y in corners:
            for target_index, edge_index, first, second in targets:
                current_point = project(x, y, first, second)
                before_point = project(before_x, before_y, first, second)
                if current_point is None or before_point is None:
                    continue
                gap_x, gap_y = x - current_point[0], y - current_point[1]
                distance = math.hypot(gap_x, gap_y)
                if distance > threshold:
                    continue
                before_distance = math.hypot(before_x - before_point[0],
                                             before_y - before_point[1])
                if (distance >= before_distance - 1e-6
                        or movement_x * gap_x + movement_y * gap_y >= -1e-6):
                    continue
                candidates.append((distance, corner_name, target_index,
                                   edge_index, gap_x, gap_y))
        if not candidates:
            return set()
        _distance, corner_name, target_index, edge_index, gap_x, gap_y = min(
            candidates, key=lambda item: item[0])
        self._corner_edge_snap_latch = {
            "corner": corner_name, "target": target_index, "edge": edge_index,
        }
        for index in indices:
            self.sources[index]["x"] -= gap_x
            self.sources[index]["y"] -= gap_y
        return {"x", "y"}

    def _snap_selection_corners_to_output_edges(self, previous, movement,
                                               skip_axes=()):
        """Snap corners approaching an output edge from inside its bounds."""
        indices = self._selection_indices()
        if not indices or previous is None:
            return set()
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        current = self._selection_bounds(indices)
        if len(indices) == 1:
            shift_x = current["x"] - previous["x"]
            shift_y = current["y"] - previous["y"]
            corners = [(point.x(), point.y(), point.x() - shift_x,
                        point.y() - shift_y)
                       for point in self._rotated_source_corners(
                           self.sources[indices[0]])]
        else:
            corners = [
                (current["x"], current["y"], previous["x"], previous["y"]),
                (current["x"] + current["width"], current["y"],
                 previous["x"] + previous["width"], previous["y"]),
                (current["x"] + current["width"],
                 current["y"] + current["height"],
                 previous["x"] + previous["width"],
                 previous["y"] + previous["height"]),
                (current["x"], current["y"] + current["height"],
                 previous["x"], previous["y"] + previous["height"]),
            ]
        outputs = self.layout_displays or [{
            "x": self.layout_bounds.left(), "y": self.layout_bounds.top(),
            "width": self.layout_bounds.width(),
            "height": self.layout_bounds.height(),
        }]
        movement_x, movement_y = movement
        candidates = {"x": [], "y": []}
        for x, y, before_x, before_y in corners:
            for output in outputs:
                left, top = float(output["x"]), float(output["y"])
                right = left + float(output["width"])
                bottom = top + float(output["height"])
                if not (left - 1e-6 <= before_x <= right + 1e-6
                        and top - 1e-6 <= before_y <= bottom + 1e-6):
                    continue
                for side, axis, edge, coordinate, low, high, delta, motion in (
                    ("left", "x", left, y, top, bottom, left - x, movement_x),
                    ("right", "x", right, y, top, bottom, right - x, movement_x),
                    ("top", "y", top, x, left, right, top - y, movement_y),
                    ("bottom", "y", bottom, x, left, right, bottom - y, movement_y),
                ):
                    if axis in skip_axes or not low - 1e-6 <= coordinate <= high + 1e-6:
                        continue
                    moving_outward = (motion < -1e-6 if side in ("left", "top")
                                      else motion > 1e-6)
                    if moving_outward and abs(delta) <= threshold:
                        candidates[axis].append((abs(delta), delta))
        snapped_axes = set()
        deltas = {}
        for axis in ("x", "y"):
            if candidates[axis]:
                _distance, deltas[axis] = min(candidates[axis], key=lambda item: item[0])
                snapped_axes.add(axis)
        for index in indices:
            if "x" in deltas:
                self.sources[index]["x"] += deltas["x"]
            if "y" in deltas:
                self.sources[index]["y"] += deltas["y"]
        return snapped_axes

    def _snap_selection_center_to_output(self, previous, movement, skip_axes=()):
        """Latch each moving center axis to finite output-center alignment lines."""
        indices = self._selection_indices()
        if not indices or previous is None:
            return set()
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        release_threshold = threshold * 2.5
        current = self._selection_bounds(indices)
        previous_x = previous["x"] + previous["width"] / 2
        previous_y = previous["y"] + previous["height"] / 2
        current_x = current["x"] + current["width"] / 2
        current_y = current["y"] + current["height"] / 2
        movement_x, movement_y = movement
        outputs = self.layout_displays or [{
            "x": self.layout_bounds.left(), "y": self.layout_bounds.top(),
            "width": self.layout_bounds.width(), "height": self.layout_bounds.height(),
        }]
        snapped_axes = set()

        def apply_axis(axis, delta):
            for index in indices:
                self.sources[index][axis] += delta

        for axis, previous_center, current_center, movement_delta in (
                ("x", previous_x, current_x, movement_x),
                ("y", previous_y, current_y, movement_y)):
            if axis in skip_axes:
                # A higher-priority edge snap wins this axis and supersedes
                # any center latch carried over from an earlier pointer event.
                self._center_snap_latches.pop(axis, None)
                continue
            latch = self._center_snap_latches.get(axis)
            if latch is not None:
                accumulated = latch["drift"] + movement_delta
                if abs(accumulated) <= release_threshold:
                    latch["drift"] = accumulated
                    center = self._selection_bounds(indices)
                    center = center["x"] + center["width"] / 2 if axis == "x" else \
                        center["y"] + center["height"] / 2
                    apply_axis(axis, latch["target"] - center)
                    snapped_axes.add(axis)
                else:
                    # The current event's movement is already applied. Restore
                    # only the distance accumulated while the axis was latched.
                    apply_axis(axis, latch["drift"])
                    del self._center_snap_latches[axis]
                continue

            if movement_delta == 0:
                continue
            candidates = []
            live_bounds = self._selection_bounds(indices)
            live_x = live_bounds["x"] + live_bounds["width"] / 2
            live_y = live_bounds["y"] + live_bounds["height"] / 2
            for output in outputs:
                output_x, output_y = float(output["x"]), float(output["y"])
                output_width = float(output["width"])
                output_height = float(output["height"])
                if axis == "x":
                    target = output_x + output_width / 2
                    orthogonal = live_y
                    orth_start, orth_end = output_y, output_y + output_height
                else:
                    target = output_y + output_height / 2
                    orthogonal = live_x
                    orth_start, orth_end = output_x, output_x + output_width
                # Unlike infinite alignment guides, only snap while the
                # selection center lies along this output's finite centerline.
                if not orth_start <= orthogonal <= orth_end:
                    continue
                crossed = min(previous_center, current_center) <= target <= max(
                    previous_center, current_center)
                approaching = abs(current_center - target) < abs(previous_center - target)
                if not crossed and (not approaching or abs(current_center - target) > threshold):
                    continue
                candidates.append((abs(current_center - target), target))
            if candidates:
                _, target = min(candidates)
                center = live_x if axis == "x" else live_y
                apply_axis(axis, target - center)
                self._center_snap_latches[axis] = {"target": target, "drift": 0.0}
                snapped_axes.add(axis)
        return snapped_axes

    def _snap_group_resize_to_edges(self, handle, previous, constrain, centered=False):
        indices = self._selection_indices()
        if len(indices) < 2:
            return
        canvas = self._canvas_rect()
        threshold = 8.0 * self.layout_bounds.width() / max(1.0, canvas.width())
        current = self._selection_bounds(indices)
        left, top = current["x"], current["y"]
        right, bottom = left + current["width"], top + current["height"]
        active_x = "l" if "l" in handle else "r" if "r" in handle else ""
        active_y = "t" if "t" in handle else "b" if "b" in handle else ""
        candidates = []
        for axis, side, edge in (
                ("x", active_x, left if active_x == "l" else right),
                ("y", active_y, top if active_y == "t" else bottom)):
            if not side:
                continue
            delta = self._snap_delta(axis, side, edge, previous, threshold)
            if delta is not None:
                candidates.append((abs(delta), axis, delta))
        if not candidates:
            return
        if constrain:
            _, axis, delta = min(candidates, key=lambda candidate: candidate[0])
            correction = delta / 2 if centered else delta
            dx = correction if axis == "x" else 0.0
            dy = correction if axis == "y" else 0.0
        else:
            dx = next((delta / 2 if centered else delta
                       for _, axis, delta in candidates if axis == "x"), 0.0)
            dy = next((delta / 2 if centered else delta
                       for _, axis, delta in candidates if axis == "y"), 0.0)
        self._resize_selected_group(dx, dy, handle, constrain=constrain,
                                    centered=centered)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit_index = self._source_index_at(event.position())
            multi_select_modifiers = (Qt.KeyboardModifier.ControlModifier
                                      | Qt.KeyboardModifier.MetaModifier
                                      | Qt.KeyboardModifier.ShiftModifier)
            if hit_index >= 0 and event.modifiers() & multi_select_modifiers:
                selection = set(self._selection_indices())
                if hit_index in selection:
                    selection.remove(hit_index)
                else:
                    selection.add(hit_index)
                self._set_selection(selection, self.selected if self.selected in selection else hit_index)
                self._drag = ""
                self._last = event.position()
                self.update()
                event.accept()
                return
            self._drag = self._hit_test(event.position())
            if self._drag == "rotate":
                self._set_interaction_cursor(rotating=True)
            self._last = event.position()
            self.update()

    def mouseMoveEvent(self, event):
        if not self._drag:
            previous_rotation_hover = self._rotate_hover
            previous_rotation_angle = self._rotation_angle
            previous_label_pos = self._rotation_label_pos
            self._rotate_hover = self._rotation_handle_at(event.position())
            self._rotation_angle = self._rotation_hover_angle(self._rotate_hover)
            self._rotation_label_pos = (QPointF(event.position())
                                        if self._rotate_hover is not None else None)
            resize_cursor = (None if self._rotate_hover is not None else
                             self._resize_cursor_at(event.position()))
            self._set_interaction_cursor(resize_cursor)
            hovered = self._source_index_at(event.position())
            if hovered != self._hovered:
                self._hovered = hovered
                self.update()
            elif (previous_rotation_hover != self._rotate_hover
                  or previous_rotation_angle != self._rotation_angle
                  or previous_label_pos != self._rotation_label_pos):
                self.update()
            return
        if not self._selection_indices():
            return
        canvas = self._canvas_rect()
        bounds = self.layout_bounds
        out_w, out_h = bounds.width(), bounds.height()
        dx = (event.position().x() - self._last.x()) * out_w / canvas.width()
        dy = (event.position().y() - self._last.y()) * out_h / canvas.height()
        if self._drag == "move":
            previous = self._selection_bounds()
            self._move_selected_by(dx, dy)
            corner_snapped_axes = self._snap_selection_corners(
                previous, (dx, dy))
            if corner_snapped_axes:
                self._corner_edge_snap_latch = None
                self._source_edge_snap_latch = None
            corner_edge_snapped_axes = (
                self._snap_selection_corners_to_source_edges(previous, (dx, dy))
                if not corner_snapped_axes else set())
            if corner_edge_snapped_axes:
                self._source_edge_snap_latch = None
            vector_snapped_axes = (
                self._snap_source_edges_by_vector((dx, dy))
                if not corner_snapped_axes and not corner_edge_snapped_axes else set())
            source_snapped_axes = (corner_snapped_axes | corner_edge_snapped_axes
                                   | vector_snapped_axes)
            if vector_snapped_axes:
                axis_source_snapped_axes = set()
            elif len(self._selection_indices()) > 1:
                axis_source_snapped_axes = self._snap_group_move_to_edges(
                    previous, (dx, dy), skip_axes=source_snapped_axes,
                    source_only=True)
            elif previous is not None:
                axis_source_snapped_axes = self._snap_selected_to_edges(
                    "move", previous, (dx, dy), skip_axes=source_snapped_axes,
                    source_only=True)
            else:
                axis_source_snapped_axes = set()
            source_snapped_axes |= axis_source_snapped_axes
            output_corner_snapped_axes = self._snap_selection_corners_to_output_edges(
                previous, (dx, dy), skip_axes=source_snapped_axes)
            output_snapped_axes = source_snapped_axes | output_corner_snapped_axes
            if len(self._selection_indices()) > 1:
                output_edge_snapped_axes = self._snap_group_move_to_edges(
                    previous, (dx, dy), skip_axes=output_snapped_axes,
                    output_only=True)
            elif previous is not None:
                output_edge_snapped_axes = self._snap_selected_to_edges(
                    "move", previous, (dx, dy), skip_axes=output_snapped_axes,
                    output_only=True)
            else:
                output_edge_snapped_axes = set()
            self._snap_selection_center_to_output(
                previous, (dx, dy),
                skip_axes=(output_snapped_axes | output_edge_snapped_axes))
        elif self._drag == "rotate":
            self._rotate_selection(event.position())
        elif self._drag.startswith("group_resize_"):
            handle = self._drag.removeprefix("group_resize_")
            previous = self._selection_bounds()
            centered = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            constrain = not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._resize_selected_group(
                dx, dy, handle, constrain=constrain, centered=centered)
            self._snap_group_resize_to_edges(handle, previous, constrain,
                                             centered=centered)
        else:
            keep_proportions = not bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            centered = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            handle = self._drag[-2:]
            source = self.sources[self.selected]
            previous = dict(source)
            angle = math.radians(float(source.get("rotation", 0)))
            local_dx = math.cos(angle) * dx + math.sin(angle) * dy
            local_dy = -math.sin(angle) * dx + math.cos(angle) * dy
            if source.get("rotation", 0):
                # Resize in the card's local axes, then rotate the moved center
                # back into desktop coordinates to keep its orientation stable.
                self._resize_selected(local_dx, local_dy, handle,
                                      constrain=keep_proportions, centered=centered)
                new_center_local_x = source["x"] + source["width"] / 2
                new_center_local_y = source["y"] + source["height"] / 2
                old_center_x = previous["x"] + previous["width"] / 2
                old_center_y = previous["y"] + previous["height"] / 2
                move_x, move_y = new_center_local_x - old_center_x, new_center_local_y - old_center_y
                source["x"] = old_center_x + math.cos(angle) * move_x - math.sin(angle) * move_y - source["width"] / 2
                source["y"] = old_center_y + math.sin(angle) * move_x + math.cos(angle) * move_y - source["height"] / 2
            else:
                self._resize_selected(dx, dy, handle, constrain=keep_proportions,
                                      centered=centered)
            if source.get("rotation", 0):
                self._snap_rotated_resize_to_output_edge(
                    handle, previous, keep_proportions, centered=centered)
            else:
                self._snap_selected_to_edges(handle, previous,
                                             constrain=keep_proportions,
                                             centered=centered)
        self._last = event.position()
        self.update()
        self.sceneChanged.emit()

    def mouseReleaseEvent(self, event):
        self._drag = ""
        self._rotation_state = None
        self._center_snap_latches.clear()
        self._corner_snap_latch = None
        self._corner_edge_snap_latch = None
        self._source_edge_snap_latch = None
        self._rotate_hover = self._rotation_handle_at(event.position())
        self._rotation_angle = self._rotation_hover_angle(self._rotate_hover)
        self._rotation_label_pos = (QPointF(event.position())
                                    if self._rotate_hover is not None else None)
        if self._rotate_hover is None:
            resize_cursor = self._resize_cursor_at(event.position())
        else:
            resize_cursor = None
        self._set_interaction_cursor(resize_cursor)
        self._hovered = self._source_index_at(event.position())
        self.update()

    def leaveEvent(self, event):
        had_rotation_hint = self._rotate_hover is not None or self._rotation_angle is not None
        self._rotate_hover = None
        self._rotation_angle = None
        self._rotation_label_pos = None
        self.unsetCursor()
        if self._hovered != -1 or had_rotation_hint:
            self._hovered = -1
            self.update()
        super().leaveEvent(event)


class FrameWindow(QDialog):
    def __init__(self, displays: list[dict], parent=None):
        super().__init__(parent)
        self.displays = displays
        self._process: QProcess | None = None
        self._helper_ready = False
        self._output_active = False
        self._space_preview_active = False
        self._escape_press_count = 0
        self._latest_helper_message = ""
        self._scene_timer = QTimer(self)
        self._scene_timer.setSingleShot(True)
        self._scene_timer.setInterval(60)
        self._scene_timer.timeout.connect(self._write_scene)
        self._escape_reset_timer = QTimer(self)
        self._escape_reset_timer.setSingleShot(True)
        self._escape_reset_timer.setInterval(1000)
        self._escape_reset_timer.timeout.connect(self._reset_escape_sequence)
        self.setWindowTitle("Frame")
        self.resize(720, 500)
        self.setMinimumSize(520, 400)

        root = QVBoxLayout(self)
        self.canvas = FrameCanvas(self)
        self.source_buttons_layout = FlowLayout(spacing=8)
        self.source_buttons_layout.addWidget(QLabel("Add source"))
        self._rebuild_source_buttons()
        root.addLayout(self.source_buttons_layout)
        root.addWidget(self.canvas, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.output_button = QPushButton("Start Output")
        self.output_button.clicked.connect(self.toggle_output)
        bottom.addWidget(self.output_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        root.addLayout(bottom)
        self.canvas.sceneChanged.connect(self._send_scene_if_active)
        self.update_displays(displays)
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(
            self._application_state_changed)

    def eventFilter(self, watched, event):
        if (not isinstance(watched, QWidget) or
                not self._widget_belongs_to_frame(watched)):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.KeyRelease:
            if event.key() != Qt.Key.Key_Escape and self._escape_press_count:
                self._reset_escape_sequence()
            if (event.key() == Qt.Key.Key_Space and self._space_preview_active
                    and not event.isAutoRepeat()):
                self._space_preview_active = False
                if self._output_active:
                    self._stop_output()
                event.accept()
                return True
            return super().eventFilter(watched, event)
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        if event.key() != Qt.Key.Key_Escape and self._escape_press_count:
            self._reset_escape_sequence()
        modifiers = event.modifiers()
        command_or_control = bool(modifiers & (
            Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.ControlModifier))
        if event.key() == Qt.Key.Key_Space:
            self._dismiss_context_menu()
            if not event.isAutoRepeat() and not self._output_active and self.canvas.sources:
                self._start_output()
                self._space_preview_active = self._output_active
            event.accept()
            return True
        if (command_or_control and modifiers & Qt.KeyboardModifier.AltModifier
                and event.key() == Qt.Key.Key_F and not event.isAutoRepeat()):
            self._dismiss_context_menu()
            self.toggle_output()
            event.accept()
            return True
        if (not modifiers and event.key() == Qt.Key.Key_F
                and not event.isAutoRepeat()):
            self._dismiss_context_menu()
            self.close()
            event.accept()
            return True
        if (command_or_control and event.key() == Qt.Key.Key_W
                and not event.isAutoRepeat()):
            self._dismiss_context_menu()
            self.close()
            event.accept()
            return True
        if event.key() == Qt.Key.Key_Escape:
            if not event.isAutoRepeat():
                self._escape_press_count += 1
                if self._escape_press_count >= 3:
                    self._reset_escape_sequence()
                    self._dismiss_context_menu()
                    self._stop_output()
                else:
                    self._escape_reset_timer.start()
            event.accept()
            return True
        if command_or_control and event.key() == Qt.Key.Key_C:
            self._dismiss_context_menu()
            self._copy_sources()
            event.accept()
            return True
        if command_or_control and event.key() == Qt.Key.Key_V:
            self._dismiss_context_menu()
            self._paste_sources()
            event.accept()
            return True
        if command_or_control and event.key() == Qt.Key.Key_A:
            self._dismiss_context_menu()
            self.canvas.select_all()
            event.accept()
            return True
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._dismiss_context_menu()
            self.canvas.remove_selected()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _widget_belongs_to_frame(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _dismiss_context_menu(self):
        popup = QApplication.activePopupWidget()
        if not isinstance(popup, QMenu) or not self._widget_belongs_to_frame(popup):
            return
        while isinstance(popup.parentWidget(), QMenu):
            popup = popup.parentWidget()
        popup.close()

    def _reset_escape_sequence(self):
        self._escape_press_count = 0
        self._escape_reset_timer.stop()

    def _application_state_changed(self, state):
        if (state != Qt.ApplicationState.ApplicationActive
                and self._space_preview_active):
            self._space_preview_active = False
            if self._output_active:
                self._stop_output()

    def _copy_sources(self):
        self.canvas.copy_selected()

    def _paste_sources(self):
        self.canvas.paste_sources()

    def toggle_output(self):
        if self._output_active and self._space_preview_active:
            # Promote the held preview to persistent output.
            self._space_preview_active = False
        elif self._output_active:
            self._stop_output()
        else:
            self._start_output()

    def update_displays(self, displays):
        self.displays = [dict(display) for display in displays]
        self._rebuild_source_buttons()
        self.canvas.set_layout_displays(_pixel_layout_displays(self.displays))
        self._send_scene_if_active()

    def _rebuild_source_buttons(self):
        # Keep the leading label while replacing buttons after display refresh.
        while self.source_buttons_layout.count() > 1:
            item = self.source_buttons_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.source_buttons = []
        for display in self.displays:
            label = display.get("frameLabel") or display["name"]
            button = QPushButton(label, self)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda _checked=False, display_id=str(display["displayId"]):
                self._add_source(display_id))
            self.source_buttons_layout.addWidget(button)
            self.source_buttons.append(button)

    @staticmethod
    def _pixels(display):
        # Frame geometry is sent to the native renderer in framebuffer pixels.
        # desktop.width/height are logical points and can be half-size on Retina.
        framebuffer = display.get("framebufferMode") or {}
        mode = display.get("currentMode") or {}
        desktop = display.get("desktop") or {}
        width = (framebuffer.get("pixelWidth") or mode.get("pixelWidth") or
                 mode.get("framebufferWidth") or desktop.get("width") or 1920)
        height = (framebuffer.get("pixelHeight") or mode.get("pixelHeight") or
                  mode.get("framebufferHeight") or desktop.get("height") or 1080)
        return float(width), float(height)

    def _add_source(self, display_id=None):
        if display_id is None and self.displays:
            display_id = self.displays[0].get("displayId")
        display = next((item for item in self.displays
                        if str(item.get("displayId")) == str(display_id)), None)
        if not display:
            return
        desktop = display.get("desktop") or {}
        pixel_width, pixel_height = self._pixels(display)
        self.canvas.add_source(
            str(display["displayId"]), display.get("frameLabel") or display["name"],
            pixel_width or desktop.get("width"),
            pixel_height or desktop.get("height"))

    def _ensure_helper(self):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            return True
        binary = _helper_path()
        if not binary.is_file():
            QMessageBox.warning(
                self, "Frame", "The Frame output helper is missing. Restart the app with run-ui.sh or rebuild the app.")
            return False
        process = QProcess(self)
        process.setProgram(str(binary))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_helper_output)
        process.readyReadStandardError.connect(self._read_helper_error)
        process.errorOccurred.connect(self._helper_error)
        process.finished.connect(self._helper_finished)
        self._process = process
        process.start()
        return True

    def _start_output(self):
        if not self.canvas.sources:
            return
        if not self._ensure_helper():
            return
        self._output_active = True
        self._update_output_button()
        if self._helper_ready:
            self._write_scene()

    def _send_scene_if_active(self):
        if not self._output_active or self._process is None:
            return
        self._scene_timer.start()

    def _write_scene(self):
        if not self._output_active or self._process is None:
            return
        command = {"action": "scene", "enabled": True,
                   "outputs": self.canvas.layout_displays,
                   "sources": self.canvas.source_scene()}
        self._process.write((json.dumps(command) + "\n").encode())

    def _stop_output(self):
        self._output_active = False
        self._space_preview_active = False
        if self._process is not None and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(b'{"action":"stop"}\n')
        self._update_output_button()

    def _update_output_button(self):
        self.output_button.setText("Stop Output" if self._output_active
                                   else "Start Output")

    def _read_helper_output(self):
        if self._process is None:
            return
        for raw in bytes(self._process.readAllStandardOutput()).decode(errors="replace").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            if kind == "ready":
                self._helper_ready = True
                if self._output_active:
                    self._write_scene()
            elif kind == "active":
                self._output_active = True
                self._update_output_button()
            elif kind == "stopped":
                self._output_active = False
                self._update_output_button()
            elif kind == "topologyChanged":
                self._output_active = False
                self._update_output_button()
            elif kind == "error":
                self._latest_helper_message = str(event.get("message") or "Frame output failed.")
                self._output_active = False
                if "Screen Recording permission" in self._latest_helper_message:
                    QMessageBox.warning(self, "Screen Recording Permission", self._latest_helper_message)
                self._update_output_button()

    def _read_helper_error(self):
        if self._process is not None:
            message = bytes(self._process.readAllStandardError()).decode(errors="replace").strip()
            if message:
                self._latest_helper_message = message[-2000:]

    def _helper_error(self, error):
        if self._process is not None and error == QProcess.ProcessError.FailedToStart:
            self._latest_helper_message = self._process.errorString()
            self._output_active = False
            self._update_output_button()

    def _helper_finished(self, _code, _status):
        self._process = None
        self._helper_ready = False
        self._output_active = False
        self._update_output_button()

    def showEvent(self, event):
        super().showEvent(event)

    def shutdown(self):
        self._stop_output()
        process = self._process
        if process is not None:
            try:
                if process.state() == QProcess.ProcessState.Running:
                    process.write(b'{"action":"quit"}\n')
                    process.closeWriteChannel()
                    if not process.waitForFinished(1200):
                        process.terminate()
                        if not process.waitForFinished(1200):
                            process.kill()
                            process.waitForFinished(1200)
            except RuntimeError:
                pass

    def closeEvent(self, event):
        self._reset_escape_sequence()
        self._stop_output()
        self._scene_timer.stop()
        super().closeEvent(event)
