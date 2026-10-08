import os
import math
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QContextMenuEvent, QImage, QKeyEvent,
                           QMouseEvent, QPalette, QWheelEvent)
from PySide6.QtWidgets import QApplication, QLabel, QMenu
from PySide6.QtTest import QTest
from frame_window import FrameCanvas, FrameWindow, PersistentToggleMenu


class FrameWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.displays = [
            {"displayId": 1, "name": "Built-in", "desktop": {"x": 0, "y": 0, "width": 1512, "height": 982},
             "currentMode": {"pixelWidth": 1512, "pixelHeight": 982},
             "framebufferMode": {"pixelWidth": 3024, "pixelHeight": 1964}},
            {"displayId": 2, "name": "External", "desktop": {"x": 1512, "y": 0, "width": 1920, "height": 1080},
             "framebufferMode": {"pixelWidth": 1920, "pixelHeight": 1080}},
        ]
        self.window = FrameWindow(self.displays)

    def tearDown(self):
        self.window.close()

    def test_all_displays_form_one_layout_and_source_can_be_any_display(self):
        self.assertEqual(len(self.window.source_buttons), 2)
        self.assertEqual([button.text() for button in self.window.source_buttons],
                         ["Built-in", "External"])
        self.assertEqual(self.window.canvas.output_pixels, (6864.0, 2160.0))
        self.assertEqual(self.window.canvas.layout_bounds, QRectF(0.0, 0.0, 6864.0, 2160.0))
        self.assertEqual(
            [(d["x"], d["y"], d["width"], d["height"])
             for d in self.window.canvas.layout_displays],
            [(0.0, 0.0, 3024.0, 1964.0),
             (3024.0, 0.0, 3840.0, 2160.0)])
        self.window._add_source()
        self.assertEqual(self.window.canvas.sources[0]["displayID"], "1")
        self.assertEqual((self.window.canvas.sources[0]["x"],
                          self.window.canvas.sources[0]["y"]), (0.0, 0.0))

    def test_copy_paste_preserves_group_layout_and_selects_copies(self):
        canvas = self.window.canvas
        canvas.sources = [
            {"displayID": "1", "name": "Built-in", "x": 10.0, "y": 20.0,
             "width": 100.0, "height": 80.0, "rotation": 15,
             "showCursor": False},
            {"displayID": "2", "name": "External", "x": 140.0, "y": 50.0,
             "width": 60.0, "height": 40.0, "rotation": 0,
             "showCursor": True},
        ]
        canvas.resize(800, 600)
        canvas._set_selection({0, 1}, 0)
        bounds = canvas._selection_bounds([0, 1])
        view_scale = canvas._canvas_rect().width() / canvas.layout_bounds.width()
        expected_offset = 16.0 / view_scale

        canvas.copy_selected()
        canvas.paste_sources()

        self.assertEqual(len(canvas.sources), 4)
        self.assertEqual(canvas._selection_indices(), [2, 3])
        self.assertAlmostEqual(canvas.sources[2]["x"], 10.0 + expected_offset)
        self.assertAlmostEqual(canvas.sources[2]["y"], 20.0 + expected_offset)
        self.assertAlmostEqual(canvas.sources[3]["x"] - canvas.sources[2]["x"], 130.0)
        self.assertAlmostEqual(canvas.sources[3]["y"] - canvas.sources[2]["y"], 30.0)
        self.assertEqual(canvas.sources[2]["rotation"], 15)
        self.assertFalse(canvas.sources[2]["showCursor"])

    def test_context_menu_toggles_stream_cursor_for_selected_group(self):
        canvas = self.window.canvas
        canvas.resize(800, 600)
        canvas.sources = [
            {"displayID": str(index), "name": f"Display {index}",
             "x": 100.0 + index * 220.0, "y": 100.0,
             "width": 160.0, "height": 100.0, "showCursor": True}
            for index in range(3)
        ]
        canvas._set_selection({0, 1, 2}, 0)
        point = canvas._to_canvas_rect(canvas.sources[1]).center().toPoint()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, point)
        seen = {}

        def turn_off_selected(menu, _position):
            seen["layer_actions_enabled"] = [
                action.isEnabled() for action in menu.actions()[:4]
            ]
            action = menu.actions()[-1]
            seen["label"] = action.text()
            seen["was_checked"] = action.isChecked()
            action.setChecked(False)
            return action

        from unittest.mock import patch
        with patch.object(canvas, "_exec_context_menu",
                          side_effect=turn_off_selected):
            canvas.contextMenuEvent(event)

        self.assertEqual(seen, {
            "layer_actions_enabled": [False, False, False, False],
            "label": "Show Stream Cursor for Selection",
            "was_checked": True,
        })
        self.assertEqual(canvas._selection_indices(), [0, 1, 2])
        self.assertEqual(canvas.selected, 1)
        self.assertTrue(all(not source["showCursor"]
                            for source in canvas.sources))

    def test_context_menu_mixed_cursor_state_turns_entire_selection_on(self):
        canvas = self.window.canvas
        canvas.resize(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 160.0, "height": 100.0, "showCursor": True},
            {"displayID": "2", "name": "B", "x": 320.0, "y": 100.0,
             "width": 160.0, "height": 100.0, "showCursor": False},
        ]
        canvas._set_selection({0, 1}, 0)
        point = canvas._to_canvas_rect(canvas.sources[1]).center().toPoint()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, point)
        seen = {}

        def turn_on_selected(menu, _position):
            action = menu.actions()[-1]
            seen["label"] = action.text()
            seen["was_checked"] = action.isChecked()
            marker = menu.findChild(QLabel, "mixedCursorStateMark")
            seen["has_mixed_indicator"] = marker is not None and marker.text() == "−"
            seen["has_extra_icon_column"] = not action.icon().isNull()
            action.setChecked(True)
            return action

        from unittest.mock import patch
        with patch.object(canvas, "_exec_context_menu",
                          side_effect=turn_on_selected):
            canvas.contextMenuEvent(event)

        self.assertEqual(seen, {
            "label": "Show Stream Cursor for Selection",
            "was_checked": False,
            "has_mixed_indicator": True,
            "has_extra_icon_column": False,
        })
        self.assertTrue(all(source["showCursor"] for source in canvas.sources))

    def test_delete_key_removes_selected_sources(self):
        canvas = self.window.canvas
        canvas.sources = [
            {"displayID": str(index), "name": "Display", "x": index * 100.0,
             "y": 0.0, "width": 80.0, "height": 60.0}
            for index in range(3)
        ]
        canvas._set_selection({0, 2}, 2)

        from PySide6.QtGui import QKeyEvent
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                          Qt.KeyboardModifier.NoModifier)
        canvas.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(len(canvas.sources), 1)

    def test_frame_window_shortcuts_work_when_canvas_has_focus(self):
        window = self.window
        canvas = window.canvas
        canvas.sources = [
            {"displayID": "1", "name": "Display", "x": 0.0, "y": 0.0,
             "width": 200.0, "height": 100.0, "rotation": 0,
             "showCursor": True},
        ]
        canvas._set_selection({0}, 0)
        window.show()
        canvas.setFocus()
        self.app.processEvents()

        QTest.keyClick(canvas, Qt.Key.Key_C, Qt.KeyboardModifier.MetaModifier)
        QTest.keyClick(canvas, Qt.Key.Key_V, Qt.KeyboardModifier.MetaModifier)
        self.assertEqual(len(canvas.sources), 2)
        self.assertEqual(canvas._selection_indices(), [1])
        QTest.keyClick(canvas, Qt.Key.Key_A, Qt.KeyboardModifier.MetaModifier)
        self.assertEqual(canvas._selection_indices(), [0, 1])
        QTest.keyClick(canvas, Qt.Key.Key_Delete)
        self.assertEqual(len(canvas.sources), 0)

    def test_spacebar_holds_preview_only_while_pressed(self):
        window = self.window
        canvas = window.canvas
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 0.0,
                           "y": 0.0, "width": 100.0, "height": 80.0}]
        calls = []

        def start():
            calls.append("start")
            window._output_active = True

        def stop():
            calls.append("stop")
            window._output_active = False

        window._start_output = start
        window._stop_output = stop
        window.show()
        canvas.setFocus()
        self.app.processEvents()

        QTest.keyPress(canvas, Qt.Key.Key_Space)
        self.assertTrue(window._space_preview_active)
        QTest.keyRelease(canvas, Qt.Key.Key_Space)
        self.assertEqual(calls, ["start", "stop"])
        self.assertFalse(window._space_preview_active)

    def test_frame_output_shortcut_toggles_and_escape_requires_three_presses(self):
        window = self.window
        calls = []

        def start():
            calls.append("start")
            window._output_active = True

        def stop():
            calls.append("stop")
            window._output_active = False

        window._start_output = start
        window._stop_output = stop
        window.show()
        window.canvas.setFocus()
        self.app.processEvents()
        modifiers = Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier
        QTest.keyClick(window.canvas, Qt.Key.Key_F, modifiers)
        self.assertEqual(calls, ["start"])
        QTest.keyClick(window.canvas, Qt.Key.Key_F, modifiers)
        self.assertEqual(calls, ["start", "stop"])

        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        QTest.keyClick(window.canvas, Qt.Key.Key_C)
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(calls, ["start", "stop"])
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(calls, ["start", "stop", "stop"])

    def test_escape_sequence_resets_after_timeout(self):
        window = self.window
        calls = []
        window._stop_output = lambda: calls.append("stop")
        window._escape_reset_timer.setInterval(30)
        window.show()
        window.canvas.setFocus()
        self.app.processEvents()

        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        QTest.qWait(50)
        self.assertEqual(window._escape_press_count, 0)
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(calls, [])
        QTest.keyClick(window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(calls, ["stop"])

    def test_plain_f_closes_the_frame_window(self):
        window = self.window
        window.show()
        window.canvas.setFocus()
        self.app.processEvents()
        QTest.keyClick(window.canvas, Qt.Key.Key_F)
        self.assertFalse(window.isVisible())
        self.assertEqual(len(window.canvas.layout_displays), 2)

    def test_command_w_closes_the_frame_window(self):
        window = self.window
        window.show()
        window.canvas.setFocus()
        self.app.processEvents()

        QTest.keyClick(window.canvas, Qt.Key.Key_W,
                       Qt.KeyboardModifier.MetaModifier)

        self.assertFalse(window.isVisible())
        window.show()
        window.canvas.setFocus()
        self.app.processEvents()
        QTest.keyClick(window.canvas, Qt.Key.Key_F)
        self.assertFalse(window.isVisible())

    def test_frame_shortcuts_are_routed_while_context_menu_is_open(self):
        window = self.window
        canvas = window.canvas
        canvas.sources = [
            {"displayID": str(index), "name": "Display", "x": index * 100.0,
             "y": 0.0, "width": 80.0, "height": 60.0}
            for index in range(2)
        ]
        menu = QMenu(canvas)
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                          Qt.KeyboardModifier.MetaModifier)

        handled = window.eventFilter(menu, event)

        self.assertTrue(handled)
        self.assertEqual(canvas._selection_indices(), [0, 1])

    def test_stream_cursor_menu_toggle_keeps_context_menu_open(self):
        menu = PersistentToggleMenu()
        action = menu.addAction("Show Stream Cursor for Selection")
        action.setCheckable(True)
        menu.set_persistent_toggle_action(action)
        menu.adjustSize()
        menu.show()
        self.app.processEvents()
        position = menu.actionGeometry(action).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(position),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier)

        menu.mouseReleaseEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertTrue(menu.isVisible())
        self.assertTrue(action.isChecked())
        menu.close()

    def test_new_source_is_centered_on_its_own_arranged_output(self):
        canvas = self.window.canvas
        canvas.add_source("2", "External", 960, 540)
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["y"]), (3984.0, 540.0))

    def test_duplicate_source_cards_are_offset_by_sixteen_view_points(self):
        canvas = self.window.canvas
        canvas.resize(800, 600)
        canvas.add_source("2", "External", 960, 540)
        original = dict(canvas.sources[0])
        view_scale = canvas._canvas_rect().width() / canvas.layout_bounds.width()

        canvas.add_source("2", "External", 960, 540)
        second = canvas.sources[1]
        self.assertAlmostEqual((second["x"] - original["x"]) * view_scale, 16.0)
        self.assertAlmostEqual((second["y"] - original["y"]) * view_scale, 16.0)

        canvas.add_source("2", "External", 960, 540)
        third = canvas.sources[2]
        self.assertAlmostEqual((third["x"] - original["x"]) * view_scale, 32.0)
        self.assertAlmostEqual((third["y"] - original["y"]) * view_scale, 32.0)

    def test_workspace_is_exactly_4000_pixels_larger_in_each_dimension(self):
        canvas = self.window.canvas
        self.assertEqual(canvas.workspace_bounds.width(), canvas.layout_bounds.width() + 4000)
        self.assertEqual(canvas.workspace_bounds.height(), canvas.layout_bounds.height() + 4000)
        self.assertEqual(canvas.workspace_bounds.center(), canvas.layout_bounds.center())

    def test_zoom_keeps_arrange_group_centered(self):
        canvas = self.window.canvas
        canvas.resize(900, 600)
        before = canvas._canvas_rect().center()
        expected_zoom = canvas._clamp_zoom(canvas.zoom_factor * 2.0)
        canvas._zoom_by(2.0)
        after = canvas._canvas_rect().center()
        self.assertEqual(before, after)
        self.assertEqual(canvas.zoom_factor, expected_zoom)

    def test_max_zoom_keeps_entire_arrange_group_inside_stage_with_margin(self):
        canvas = FrameCanvas()
        canvas.set_output_size(4944, 1964)
        canvas.resize(900, 600)
        canvas.show()
        self.app.processEvents()
        canvas._zoom_by(1000.0)

        stage = canvas._stage_rect()
        margin = min(24.0, max(12.0, min(stage.width(), stage.height()) * 0.025))
        safe_area = stage.adjusted(margin, margin, -margin, -margin)
        arranged = canvas._canvas_rect()
        self.assertGreaterEqual(arranged.left(), safe_area.left() - 0.01)
        self.assertGreaterEqual(arranged.top(), safe_area.top() - 0.01)
        self.assertLessEqual(arranged.right(), safe_area.right() + 0.01)
        self.assertLessEqual(arranged.bottom(), safe_area.bottom() + 0.01)

        canvas.resize(620, 440)
        self.app.processEvents()
        stage = canvas._stage_rect()
        margin = min(24.0, max(12.0, min(stage.width(), stage.height()) * 0.025))
        safe_area = stage.adjusted(margin, margin, -margin, -margin)
        arranged = canvas._canvas_rect()
        self.assertGreaterEqual(arranged.left(), safe_area.left() - 0.01)
        self.assertGreaterEqual(arranged.top(), safe_area.top() - 0.01)
        self.assertLessEqual(arranged.right(), safe_area.right() + 0.01)
        self.assertLessEqual(arranged.bottom(), safe_area.bottom() + 0.01)

    def test_zoomed_contents_are_clipped_to_the_black_work_surface(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        palette = QPalette(canvas.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#111111"))
        canvas.setPalette(palette)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 0.0,
                           "y": 0.0, "width": 800.0, "height": 600.0}]
        canvas.zoom_factor = 3.0

        image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        canvas.render(image)

        self.assertEqual(image.pixelColor(2, 10).name(), "#111111")
        self.assertEqual(image.pixelColor(100, 100).name(), "#2c2c2c")
        self.assertEqual(canvas._hit_test(QPointF(2, 10)), "")
        self.assertEqual(canvas._source_index_at(QPointF(2, 10)), -1)

    def test_mouse_wheel_zoom_changes_view_without_moving_arrange(self):
        canvas = self.window.canvas
        canvas.resize(900, 600)
        before_center = canvas._canvas_rect().center()
        before_zoom = canvas.zoom_factor
        event = QWheelEvent(
            QPointF(200, 200), QPointF(200, 200), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        canvas.wheelEvent(event)
        self.assertGreater(canvas.zoom_factor, before_zoom)
        self.assertEqual(canvas._canvas_rect().center(), before_center)

    def test_source_card_fill_does_not_follow_system_accent_button_color(self):
        canvas = self.window.canvas
        palette = QPalette(canvas.palette())
        palette.setColor(QPalette.ColorRole.Button, QColor("#D99A3E"))
        canvas.setPalette(palette)
        self.assertNotEqual(canvas._source_card_color().name(), "#d99a3e")
        self.assertEqual(canvas._source_card_color().alpha(), 235)

    def test_drawing_selected_card_does_not_fill_following_card_orange(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        palette = QPalette(canvas.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#1B1B1B"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF"))
        canvas.setPalette(palette)
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "Selected", "x": 80.0, "y": 80.0,
             "width": 200.0, "height": 160.0},
            {"displayID": "2", "name": "Unselected", "x": 480.0, "y": 80.0,
             "width": 200.0, "height": 160.0},
        ]
        canvas.selected = 0
        image = QImage(canvas.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        canvas.render(image)
        second_rect = canvas._to_canvas_rect(canvas.sources[1])
        pixel = image.pixelColor(round(second_rect.left() + 20),
                                 round(second_rect.top() + 40))
        self.assertNotEqual(pixel.name(), "#d99a3e")
        self.assertEqual(pixel.red(), pixel.green())
        self.assertEqual(pixel.green(), pixel.blue())

    def test_source_dimensions_are_visible_only_for_selected_or_hovered_cards(self):
        canvas = FrameCanvas()
        canvas.sources = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        canvas.selected = 1
        canvas._hovered = 2
        self.assertFalse(canvas._source_dimensions_visible(0))
        self.assertTrue(canvas._source_dimensions_visible(1))
        self.assertTrue(canvas._source_dimensions_visible(2))
        canvas.selected = -1
        canvas._hovered = -1
        self.assertFalse(canvas._source_dimensions_visible(1))

    def test_dragged_card_is_painted_above_all_other_cards_only_while_dragging(self):
        canvas = FrameCanvas()
        canvas.sources = [{"name": "Bottom"}, {"name": "Top"}, {"name": "Middle"}]
        canvas.selected = 0
        self.assertEqual(canvas._paint_order(), [0, 1, 2])
        canvas._drag = "move"
        self.assertEqual(canvas._paint_order(), [1, 2, 0])
        canvas._drag = ""
        self.assertEqual(canvas._paint_order(), [0, 1, 2])

    def test_layer_commands_reorder_sources_and_preserve_selection(self):
        canvas = FrameCanvas()
        canvas.sources = [{"name": name} for name in ("Back", "Middle", "Front")]
        canvas.selected = 1
        self.assertTrue(canvas._move_selected_layer("forward"))
        self.assertEqual([item["name"] for item in canvas.sources],
                         ["Back", "Front", "Middle"])
        self.assertEqual(canvas.sources[canvas.selected]["name"], "Middle")
        self.assertTrue(canvas._move_selected_layer("back"))
        self.assertEqual([item["name"] for item in canvas.sources],
                         ["Middle", "Back", "Front"])
        self.assertEqual(canvas.selected, 0)
        self.assertFalse(canvas._move_selected_layer("back"))
        self.assertTrue(canvas._move_selected_layer("front"))
        self.assertEqual([item["name"] for item in canvas.sources],
                         ["Back", "Front", "Middle"])

    def test_context_hit_test_selects_topmost_overlapping_card(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "Bottom", "x": 100.0, "y": 100.0,
             "width": 300.0, "height": 250.0},
            {"displayID": "2", "name": "Top", "x": 200.0, "y": 180.0,
             "width": 300.0, "height": 250.0},
        ]
        point = canvas._to_canvas_rect(canvas.sources[0]).center()
        self.assertEqual(canvas.sources[canvas._source_index_at(point)]["name"], "Top")

    def test_clicking_empty_black_output_clears_selection(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 100.0, "width": 200.0, "height": 160.0}]
        canvas.selected = 0
        output = canvas._canvas_rect()
        empty_black_point = QPointF(output.left() + 8, output.top() + 8)
        self.assertEqual(canvas._hit_test(empty_black_point), "")
        self.assertEqual(canvas.selected, -1)

    def test_output_and_source_use_friendly_name_then_original_name(self):
        displays = [
            {"displayId": 1, "name": "Raw display name", "frameLabel": "Friendly name",
             "desktop": {"width": 100, "height": 100}},
            {"displayId": 2, "name": "Original display name",
             "desktop": {"width": 100, "height": 100}},
        ]
        window = FrameWindow(displays)
        try:
            expected = ["Friendly name", "Original display name"]
            self.assertEqual([button.text() for button in window.source_buttons], expected)
            window.source_buttons[0].click()
            self.assertEqual(window.canvas.sources[0]["name"], "Friendly name")
            window.source_buttons[1].click()
            self.assertEqual(window.canvas.sources[1]["name"], "Original display name")
        finally:
            window.close()

    def test_multiple_regions_can_use_same_source_display(self):
        self.window._add_source()
        self.window._add_source()
        self.assertEqual(len(self.window.canvas.sources), 2)
        self.assertEqual({source["displayID"] for source in self.window.canvas.sources}, {"1"})

    def test_new_source_starts_at_full_arranged_display_size(self):
        self.window.source_buttons[1].click()
        source = self.window.canvas.sources[0]
        self.assertEqual((source["width"], source["height"]), (3840.0, 2160.0))
        self.assertEqual((source["width"] * source["pixelScaleX"],
                          source["height"] * source["pixelScaleY"]), (1920, 1080))
        output = self.window.canvas.layout_displays[1]
        scene = self.window.canvas.source_scene()[0]
        for key in ("x", "y", "width", "height"):
            self.assertEqual(scene[key], output[key])

    def test_new_source_uses_logical_arrangement_dimensions_on_retina_screen(self):
        self.window.source_buttons[0].click()
        source = self.window.canvas.sources[0]
        self.assertEqual((source["width"], source["height"]), (3024.0, 1964.0))

    def test_group_move_keeps_all_selected_source_offsets(self):
        canvas = FrameCanvas()
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0},
        ]
        canvas._set_selection({0, 1}, 0)
        canvas._move_selected_by(50.0, 30.0)
        self.assertEqual((canvas.sources[0]["x"], canvas.sources[0]["y"]),
                         (150.0, 130.0))
        self.assertEqual((canvas.sources[1]["x"], canvas.sources[1]["y"]),
                         (390.0, 150.0))

    def test_macos_command_click_adds_a_source_to_the_selection(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 50.0, "y": 50.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 400.0, "y": 300.0,
             "width": 200.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        point = canvas._to_canvas_rect(canvas.sources[1]).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier)
        canvas.mousePressEvent(event)
        self.assertEqual(canvas._selection_indices(), [0, 1])

    def test_macos_command_click_toggles_source_out_of_selection(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 50.0, "y": 50.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 400.0, "y": 300.0,
             "width": 200.0, "height": 100.0},
        ]
        canvas._set_selection({0, 1}, 0)
        point = canvas._to_canvas_rect(canvas.sources[1]).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier)
        canvas.mousePressEvent(event)
        self.assertEqual(canvas._selection_indices(), [0])

    def test_shift_click_adds_a_source_to_the_selection(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 50.0, "y": 50.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 400.0, "y": 300.0,
             "width": 200.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        point = canvas._to_canvas_rect(canvas.sources[1]).center()
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress, point,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier)

        canvas.mousePressEvent(event)

        self.assertEqual(canvas._selection_indices(), [0, 1])

    def test_group_resize_scales_cards_and_spacing_proportionally(self):
        canvas = FrameCanvas()
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0},
        ]
        canvas._set_selection({0, 1}, 0)
        canvas._resize_selected_group(34.0, 10.0, "br")
        self.assertAlmostEqual(canvas.sources[0]["width"], 220.0)
        self.assertAlmostEqual(canvas.sources[0]["height"], 110.0)
        self.assertAlmostEqual(canvas.sources[1]["x"], 364.0)
        self.assertAlmostEqual(canvas.sources[1]["y"], 122.0)
        self.assertAlmostEqual(canvas.sources[1]["width"], 110.0)
        self.assertAlmostEqual(canvas.sources[1]["height"], 88.0)

    def test_option_group_resize_keeps_group_center_fixed(self):
        canvas = FrameCanvas()
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0},
        ]
        canvas._set_selection({0, 1}, 0)
        canvas._resize_selected_group(34.0, 10.0, "br", centered=True)
        bounds = canvas._selection_bounds()
        self.assertAlmostEqual(bounds["x"] + bounds["width"] / 2, 270.0)
        self.assertAlmostEqual(bounds["y"] + bounds["height"] / 2, 150.0)
        self.assertAlmostEqual(canvas.sources[0]["width"], 240.0)
        self.assertAlmostEqual(canvas.sources[1]["x"], 354.0)

    def test_shift_freeform_group_resize_scales_axes_independently(self):
        canvas = FrameCanvas()
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0},
        ]
        canvas._set_selection({0, 1}, 0)
        canvas._resize_selected_group(34.0, 0.0, "mr", constrain=False)
        self.assertAlmostEqual(canvas.sources[0]["width"], 220.0)
        self.assertAlmostEqual(canvas.sources[0]["height"], 100.0)
        self.assertAlmostEqual(canvas.sources[1]["x"], 364.0)
        self.assertAlmostEqual(canvas.sources[1]["height"], 80.0)

    def test_option_shift_group_resize_is_centered_and_freeform(self):
        canvas = FrameCanvas()
        canvas.set_output_size(1000, 800)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0},
        ]
        canvas._set_selection({0, 1}, 0)
        canvas._resize_selected_group(34.0, 0.0, "mr", constrain=False,
                                      centered=True)
        bounds = canvas._selection_bounds()
        self.assertAlmostEqual(bounds["x"] + bounds["width"] / 2, 270.0)
        self.assertAlmostEqual(bounds["y"], 100.0)
        self.assertAlmostEqual(bounds["height"], 100.0)
        self.assertAlmostEqual(canvas.sources[0]["width"], 240.0)

    def test_group_move_snaps_outer_edge_to_output_boundary(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 485.0, "y": 100.0,
             "width": 100.0, "height": 200.0},
            {"displayID": "2", "name": "B", "x": 585.0, "y": 100.0,
             "width": 200.0, "height": 200.0},
        ]
        canvas._set_selection({0, 1}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(10.0, 0.0)
        canvas._snap_group_move_to_edges(previous, (10.0, 0.0))
        self.assertAlmostEqual(canvas._selection_bounds()["x"] +
                               canvas._selection_bounds()["width"], 800.0)
        self.assertAlmostEqual(canvas.sources[1]["x"] - canvas.sources[0]["x"], 100.0)

    def test_group_move_snaps_outer_edge_to_unselected_source(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 200.0, "y": 100.0,
             "width": 200.0, "height": 100.0},
            {"displayID": "3", "name": "C", "x": 410.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0, 1}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(5.0, 0.0)
        canvas._snap_group_move_to_edges(previous, (5.0, 0.0))
        self.assertAlmostEqual(canvas.sources[1]["x"] +
                               canvas.sources[1]["width"], 410.0)
        self.assertEqual(canvas.sources[2]["x"], 410.0)

    def test_group_resize_snaps_outer_edge_to_output_boundary(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 300.0, "height": 200.0},
            {"displayID": "2", "name": "B", "x": 400.0, "y": 100.0,
             "width": 380.0, "height": 200.0},
        ]
        canvas._set_selection({0, 1}, 0)
        previous = canvas._selection_bounds()
        canvas._resize_selected_group(5.0, 0.0, "mr", constrain=True)
        canvas._snap_group_resize_to_edges("mr", previous, constrain=True)
        current = canvas._selection_bounds()
        self.assertAlmostEqual(current["x"] + current["width"], 800.0)

    def test_single_source_center_snaps_to_output_center_point(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 250.0,
                           "y": 250.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(95.0, 0.0)
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (95.0, 0.0)),
                         {"x"})
        source = canvas.sources[0]
        self.assertEqual((source["x"] + source["width"] / 2,
                          source["y"] + source["height"] / 2), (400.0, 300.0))

    def test_center_axis_snaps_without_requiring_both_coordinates_to_match(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 345.0,
                           "y": 280.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        previous = {"x": 335.0, "y": 280.0, "width": 100.0, "height": 100.0}
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (10.0, 0.0)),
                         {"x"})
        source = canvas.sources[0]
        self.assertEqual(source["x"] + source["width"] / 2, 400.0)
        self.assertEqual(source["y"] + source["height"] / 2, 330.0)

    def test_group_center_snaps_to_a_non_main_output_center(self):
        canvas = FrameCanvas()
        canvas.set_layout_displays([
            {"displayID": "1", "name": "Main", "x": 0.0, "y": 0.0,
             "width": 800.0, "height": 600.0},
            {"displayID": "2", "name": "External", "x": 800.0, "y": 0.0,
             "width": 800.0, "height": 600.0},
        ])
        canvas.sources = [
            {"displayID": "3", "name": "A", "x": 1040.0, "y": 250.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "4", "name": "B", "x": 1140.0, "y": 250.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0, 1}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(45.0, 0.0)
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (45.0, 0.0)),
                         {"x"})
        bounds = canvas._selection_bounds()
        self.assertEqual(bounds["x"] + bounds["width"] / 2, 1200.0)

    def test_center_snap_catches_a_fast_drag_crossing(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 250.0,
                           "y": 250.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(100.0, 0.0)
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (100.0, 0.0)),
                         {"x"})
        self.assertEqual(canvas.sources[0]["x"] + 50.0, 400.0)

    def test_center_snap_remains_latched_until_cumulative_drag_exits_release_range(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 350.0,
                           "y": 250.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        canvas._center_snap_latches["x"] = {"target": 400.0, "drift": 0.0}

        previous = canvas._selection_bounds()
        canvas._move_selected_by(3.0, 0.0)
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (3.0, 0.0)),
                         {"x"})
        self.assertEqual(canvas.sources[0]["x"] + 50.0, 400.0)

        previous = canvas._selection_bounds()
        canvas._move_selected_by(45.0, 0.0)
        self.assertEqual(canvas._snap_selection_center_to_output(previous, (45.0, 0.0)),
                         set())
        self.assertNotIn("x", canvas._center_snap_latches)
        self.assertEqual(canvas.sources[0]["x"] + 50.0, 448.0)

    def test_move_snaps_outward_from_inside_to_output_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 485.0,
                           "y": 100.0, "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(10.0, 0.0)
        canvas._snap_selected_to_edges("move", previous, (10.0, 0.0))
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"], 800.0)

    def test_source_corner_snaps_to_output_edge_when_approaching_from_inside(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.resize(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 690.0,
                           "y": 100.0, "width": 100.0, "height": 200.0}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (15.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners_to_output_edges(
            previous, movement)
        self.assertEqual(snapped_axes, {"x"})
        self.assertAlmostEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"],
                               800.0)

    def test_source_corner_does_not_snap_to_output_edge_when_already_outside(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.resize(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 705.0,
                           "y": 100.0, "width": 100.0, "height": 200.0}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (2.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners_to_output_edges(
            previous, movement)
        self.assertEqual(snapped_axes, set())
        self.assertEqual(canvas.sources[0]["x"], 707.0)

    def test_source_edge_snap_wins_over_nearer_output_edge_candidate(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 490.0, "y": 100.0,
             "width": 300.0, "height": 200.0},
            {"displayID": "2", "name": "B", "x": 798.0, "y": 100.0,
             "width": 100.0, "height": 200.0},
        ]
        canvas._set_selection({0}, 0)
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(4.0, 0.0)
        snapped_axes = canvas._snap_selected_to_edges("move", previous, (4.0, 0.0))
        self.assertEqual(snapped_axes, {"x"})
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"], 798.0)

    def test_output_edge_snap_wins_over_simultaneous_output_center_snap(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 0.0,
                           "y": 100.0, "width": 792.0, "height": 200.0}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        canvas._move_selected_by(4.0, 0.0)
        edge_snapped_axes = canvas._snap_selected_to_edges(
            "move", previous, (4.0, 0.0))
        self.assertEqual(edge_snapped_axes, {"x"})
        canvas._snap_selection_center_to_output(
            previous, (4.0, 0.0), skip_axes=edge_snapped_axes)
        source = canvas.sources[0]
        self.assertEqual(source["x"] + source["width"], 800.0)
        self.assertEqual(source["x"] + source["width"] / 2, 404.0)

    def test_rotated_source_output_edge_snap_uses_visible_rotated_bounds(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 650.0,
                           "y": 250.0, "width": 100.0, "height": 100.0,
                           "rotation": 45}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (35.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selected_to_edges(
            "move", previous, movement)
        bounds = canvas._selection_bounds()
        self.assertEqual(snapped_axes, {"x"})
        self.assertAlmostEqual(bounds["x"] + bounds["width"], 800.0)

    def test_rotated_source_can_snap_output_edge_when_only_its_visible_span_overlaps(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 40.0,
                           "y": -55.0, "width": 100.0, "height": 100.0,
                           "rotation": 45}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (-25.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selected_to_edges(
            "move", previous, movement)
        bounds = canvas._selection_bounds()
        self.assertEqual(snapped_axes, {"x"})
        self.assertAlmostEqual(bounds["x"], 0.0)

    def test_rotated_source_center_snaps_using_its_geometric_center(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 300.0,
                           "y": 195.0, "width": 200.0, "height": 100.0,
                           "rotation": 45}]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (0.0, 50.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_center_to_output(
            previous, movement)
        source = canvas.sources[0]
        self.assertEqual(snapped_axes, {"y"})
        self.assertAlmostEqual(source["x"] + source["width"] / 2, 400.0)
        self.assertAlmostEqual(source["y"] + source["height"] / 2, 300.0)

    def test_move_from_outside_toward_output_does_not_snap(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 805.0,
                           "y": 100.0, "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(-10.0, 0.0)
        canvas._snap_selected_to_edges("move", previous, (-10.0, 0.0))
        self.assertEqual(canvas.sources[0]["x"], 795.0)

    def test_source_cards_snap_when_approaching_from_separate_positions(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(5.0, 0.0)
        canvas._snap_selected_to_edges("move", previous, (5.0, 0.0))
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"], 208.0)

        canvas.sources[1]["x"] = 195.0  # Already overlapping before the drag.
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(1.0, 0.0)
        canvas._snap_selected_to_edges("move", previous, (1.0, 0.0))
        self.assertEqual(canvas.sources[0]["x"], previous["x"] + 1.0)

    def test_source_corners_snap_when_approaching_diagonally(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 208.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (5.0, 5.0)
        canvas._move_selected_by(*movement)
        corner_axes = canvas._snap_selection_corners(previous, movement)
        edge_axes = canvas._snap_selected_to_edges(
            "move", previous, movement, skip_axes=corner_axes)
        self.assertEqual(corner_axes, {"x", "y"})
        self.assertEqual(edge_axes, set())
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"],
                         canvas.sources[1]["x"])
        self.assertEqual(canvas.sources[0]["y"] + canvas.sources[0]["height"],
                         canvas.sources[1]["y"])

    def test_source_corner_snap_catches_a_card_sliding_along_an_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 208.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)

        # The selected card is already aligned with the target's left edge;
        # moving vertically brings its lower-right corner to the target corner.
        canvas.sources[0]["x"] = 108.0
        previous = canvas._selection_bounds()
        movement = (0.0, 5.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners(previous, movement)
        self.assertEqual(snapped_axes, {"x", "y"})
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"],
                         canvas.sources[1]["x"])
        self.assertEqual(canvas.sources[0]["y"] + canvas.sources[0]["height"],
                         canvas.sources[1]["y"])

    def test_source_corner_snaps_to_finite_interior_of_another_card_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.resize(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 120.0,
             "width": 100.0, "height": 160.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (5.0, 0.0)
        canvas._move_selected_by(*movement)
        self.assertEqual(canvas._snap_selection_corners(previous, movement), set())
        snapped_axes = canvas._snap_selection_corners_to_source_edges(
            previous, movement)
        self.assertEqual(snapped_axes, {"x", "y"})
        selected_br = canvas._rotated_source_corners(canvas.sources[0])[3]
        target_left_x = canvas.sources[1]["x"]
        self.assertAlmostEqual(selected_br.x(), target_left_x)
        self.assertAlmostEqual(selected_br.y(), 200.0)

    def test_corner_to_source_edge_snap_stays_latched_while_sliding(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.resize(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 120.0,
             "width": 100.0, "height": 160.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (5.0, 0.0)
        canvas._move_selected_by(*movement)
        canvas._snap_selection_corners_to_source_edges(previous, movement)
        previous = canvas._selection_bounds()
        movement = (0.0, 5.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners_to_source_edges(
            previous, movement)
        selected_br = canvas._rotated_source_corners(canvas.sources[0])[3]
        self.assertEqual(snapped_axes, {"x", "y"})
        self.assertAlmostEqual(selected_br.x(), canvas.sources[1]["x"])
        self.assertAlmostEqual(selected_br.y(), 205.0)

    def test_source_corner_snap_holds_until_drag_leaves_release_range(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 208.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (5.0, 5.0)
        canvas._move_selected_by(*movement)
        canvas._snap_selection_corners(previous, movement)

        previous = canvas._selection_bounds()
        movement = (1.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners(previous, movement)
        self.assertEqual(snapped_axes, {"x", "y"})
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"],
                         canvas.sources[1]["x"])
        self.assertEqual(canvas.sources[0]["y"] + canvas.sources[0]["height"],
                         canvas.sources[1]["y"])

    def test_rotated_source_corner_snaps_using_visible_rotated_vertices(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 50.0, "rotation": 90},
            {"displayID": "2", "name": "B", "x": 180.0, "y": 180.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (50.0, 3.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_selection_corners(previous, movement)
        self.assertEqual(snapped_axes, {"x", "y"})

        rotated_corner = canvas._rotated_source_corners(canvas.sources[0])[3]
        target_corner = canvas._rotated_source_corners(canvas.sources[1])[0]
        self.assertAlmostEqual(rotated_corner.x(), target_corner.x())
        self.assertAlmostEqual(rotated_corner.y(), target_corner.y())

    def test_source_edge_snap_uses_rotated_target_visual_bounds(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 217.0, "y": 90.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 300.0, "y": 100.0,
             "width": 100.0, "height": 50.0, "rotation": 90},
        ]
        canvas._set_selection({0}, 0)
        movement = (3.0, 0.0)
        canvas._move_selected_by(*movement)
        snapped_axes = canvas._snap_source_edges_by_vector(movement)
        source_points = canvas._rotated_source_corners(canvas.sources[0])
        target_points = canvas._rotated_source_corners(canvas.sources[1])
        source_right = max(point.x() for point in source_points)
        target_left = min(point.x() for point in target_points)
        self.assertEqual(snapped_axes, {"x", "y"})
        self.assertAlmostEqual(source_right, target_left)

    def test_rotated_cards_snap_parallel_visible_edges_along_edge_normal(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        diagonal = 150.0 / math.sqrt(2.0)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 60.0, "rotation": 45},
            {"displayID": "2", "name": "B",
             "x": 150.0 + diagonal - 90.0,
             "y": 130.0 + diagonal - 200.0,
             "width": 180.0, "height": 400.0, "rotation": 45},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (4.0 / math.sqrt(2.0), 4.0 / math.sqrt(2.0))
        canvas._move_selected_by(*movement)
        corner_axes = canvas._snap_selection_corners(previous, movement)
        self.assertEqual(corner_axes, set())
        edge_axes = canvas._snap_source_edges_by_vector(movement)
        self.assertEqual(edge_axes, {"x", "y"})

        selected = canvas._rotated_source_corners(canvas.sources[0])
        target = canvas._rotated_source_corners(canvas.sources[1])
        selected_mid = QPointF((selected[1].x() + selected[3].x()) / 2,
                               (selected[1].y() + selected[3].y()) / 2)
        target_mid = QPointF((target[2].x() + target[0].x()) / 2,
                             (target[2].y() + target[0].y()) / 2)
        normal = QPointF(1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
        self.assertAlmostEqual((target_mid.x() - selected_mid.x()) * normal.x()
                               + (target_mid.y() - selected_mid.y()) * normal.y(),
                               0.0, places=6)

    def test_rotated_edge_snap_stays_latched_while_sliding_along_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        diagonal = 150.0 / math.sqrt(2.0)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 60.0, "rotation": 45},
            {"displayID": "2", "name": "B",
             "x": 150.0 + diagonal - 90.0,
             "y": 130.0 + diagonal - 200.0,
             "width": 180.0, "height": 400.0, "rotation": 45},
        ]
        canvas._set_selection({0}, 0)
        normal_approach = (4.0 / math.sqrt(2.0), 4.0 / math.sqrt(2.0))
        canvas._move_selected_by(*normal_approach)
        self.assertEqual(canvas._snap_source_edges_by_vector(normal_approach),
                         {"x", "y"})

        tangent_slide = (-5.0 / math.sqrt(2.0), 5.0 / math.sqrt(2.0))
        canvas._move_selected_by(*tangent_slide)
        self.assertEqual(canvas._snap_source_edges_by_vector(tangent_slide),
                         {"x", "y"})
        selected = canvas._rotated_source_corners(canvas.sources[0])
        target = canvas._rotated_source_corners(canvas.sources[1])
        selected_mid = QPointF((selected[1].x() + selected[3].x()) / 2,
                               (selected[1].y() + selected[3].y()) / 2)
        target_mid = QPointF((target[2].x() + target[0].x()) / 2,
                             (target[2].y() + target[0].y()) / 2)
        normal = QPointF(1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0))
        self.assertAlmostEqual((target_mid.x() - selected_mid.x()) * normal.x()
                               + (target_mid.y() - selected_mid.y()) * normal.y(),
                               0.0, places=6)

    def test_source_corners_do_not_snap_when_drag_moves_away(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 208.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0}, 0)
        previous = canvas._selection_bounds()
        movement = (-1.0, -1.0)
        canvas._move_selected_by(*movement)
        canvas._snap_selected_to_edges("move", previous, movement)
        before_corner_snap = dict(canvas.sources[0])
        snapped_axes = canvas._snap_selection_corners(previous, movement)
        self.assertEqual(snapped_axes, set())
        self.assertEqual(canvas.sources[0], before_corner_snap)

    def test_group_outer_corner_snaps_to_source_corner(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 200.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "3", "name": "C", "x": 308.0, "y": 208.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas._set_selection({0, 1}, 0)
        previous = canvas._selection_bounds()
        movement = (5.0, 5.0)
        canvas._move_selected_by(*movement)
        corner_axes = canvas._snap_selection_corners(previous, movement)
        edge_axes = canvas._snap_group_move_to_edges(
            previous, movement, skip_axes=corner_axes)
        bounds = canvas._selection_bounds()
        self.assertEqual(corner_axes, {"x", "y"})
        self.assertEqual(edge_axes, set())
        self.assertEqual(bounds["x"] + bounds["width"], canvas.sources[2]["x"])
        self.assertEqual(bounds["y"] + bounds["height"], canvas.sources[2]["y"])

    def test_resize_snaps_source_edges_when_approaching_from_outside(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._resize_selected(5.0, 0.0, "r")
        canvas._snap_selected_to_edges("r", previous)
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"], 208.0)

    def test_source_edges_do_not_snap_along_infinite_extension_lines(self):
        canvas = FrameCanvas()
        canvas.set_output_size(2000, 1000)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 100.0, "height": 100.0},
            {"displayID": "2", "name": "B", "x": 208.0, "y": 250.0,
             "width": 100.0, "height": 100.0},
        ]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(5.0, 0.0)
        canvas._snap_selected_to_edges("move", previous, (5.0, 0.0))
        self.assertEqual(canvas.sources[0]["x"], 105.0)

        canvas.sources[1] = {"displayID": "2", "name": "B", "x": 250.0,
                             "y": 208.0, "width": 100.0, "height": 100.0}
        previous = dict(canvas.sources[0])
        canvas._move_selected_by(0.0, 5.0)
        canvas._snap_selected_to_edges("move", previous, (0.0, 5.0))
        self.assertEqual(canvas.sources[0]["y"], 105.0)

    def test_resize_snaps_outward_to_output_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 100.0,
                           "y": 100.0, "width": 680.0, "height": 200.0}]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._resize_selected(5.0, 0.0, "r")
        canvas._snap_selected_to_edges("r", previous)
        self.assertEqual(canvas.sources[0]["x"] + canvas.sources[0]["width"], 800.0)

    def test_constrained_resize_keeps_ratio_when_snapping_to_output_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 800)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 100.0,
                           "y": 50.0, "width": 680.0, "height": 680.0}]
        canvas.selected = 0
        previous = dict(canvas.sources[0])
        canvas._resize_selected(5.0, 5.0, "br", constrain=True)
        canvas._snap_selected_to_edges("br", previous, constrain=True)
        source = canvas.sources[0]
        self.assertAlmostEqual(source["x"] + source["width"], 800.0)
        self.assertAlmostEqual(source["width"], source["height"])

    def test_rotated_corner_resize_snaps_to_output_edge_and_preserves_anchor(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.resize(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 580.0,
                           "y": 100.0, "width": 316.0, "height": 158.0,
                           "rotation": 45.0}]
        canvas._set_selection({0}, 0)
        previous = {**canvas.sources[0], "width": 310.0, "height": 155.0}
        before_anchor = canvas._rotated_source_corners(canvas.sources[0])[0]
        snapped_axes = canvas._snap_rotated_resize_to_output_edge(
            "br", previous, constrain=True)
        source = canvas.sources[0]
        corners = canvas._rotated_source_corners(source)
        self.assertEqual(snapped_axes, {"x"})
        self.assertAlmostEqual(corners[3].x(), 800.0, places=6)
        self.assertAlmostEqual(corners[0].x(), before_anchor.x(), places=6)
        self.assertAlmostEqual(corners[0].y(), before_anchor.y(), places=6)
        self.assertAlmostEqual(source["width"] / source["height"], 2.0)

    def test_rotated_freeform_corner_resize_snaps_to_output_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.resize(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 580.0,
                           "y": 100.0, "width": 316.0, "height": 158.0,
                           "rotation": 45.0}]
        canvas._set_selection({0}, 0)
        previous = {**canvas.sources[0], "x": 570.0}
        snapped_axes = canvas._snap_rotated_resize_to_output_edge(
            "br", previous, constrain=False)
        source = canvas.sources[0]
        active_corner = canvas._rotated_source_corners(source)[3]
        self.assertEqual(snapped_axes, {"x"})
        self.assertAlmostEqual(active_corner.x(), 800.0, places=6)

    def test_rotated_corner_resize_mouse_drag_reaches_output_snap(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.resize(800, 600)
        canvas.sources = [{"displayID": "1", "name": "A", "x": 660.0,
                           "y": 100.0, "width": 200.0, "height": 100.0,
                           "rotation": 45.0}]
        canvas._set_selection({0}, 0)
        previous_corners = canvas._rotated_source_corners(canvas.sources[0])
        fixed_corner_before = previous_corners[0]
        press_position = canvas._scene_to_canvas_point(previous_corners[3])
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, press_position,
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        canvas.mousePressEvent(press)
        self.assertEqual(canvas._drag, "resize_br")
        scale = canvas._canvas_rect().width() / canvas.layout_bounds.width()
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            press_position + QPointF(15.0 * scale, 15.0 * scale),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)

        canvas.mouseMoveEvent(event)

        corners = canvas._rotated_source_corners(canvas.sources[0])
        self.assertAlmostEqual(corners[3].x(), 800.0, places=6)
        self.assertAlmostEqual(corners[0].x(), fixed_corner_before.x(), places=6)
        self.assertAlmostEqual(corners[0].y(), fixed_corner_before.y(), places=6)

    def test_rotated_resize_does_not_snap_ungrabbed_corner(self):
        for modifiers in (Qt.KeyboardModifier.NoModifier,
                          Qt.KeyboardModifier.ShiftModifier,
                          Qt.KeyboardModifier.AltModifier):
            with self.subTest(modifiers=modifiers):
                canvas = FrameCanvas()
                canvas.set_output_size(800, 600)
                canvas.resize(800, 600)
                canvas.sources = [{"displayID": "1", "name": "A", "x": 590.0,
                                   "y": 100.0, "width": 200.0, "height": 100.0,
                                   "rotation": 45.0}]
                canvas._set_selection({0}, 0)
                before = canvas._rotated_source_corners(canvas.sources[0])
                position = canvas._scene_to_canvas_point(before[3])
                canvas.mousePressEvent(QMouseEvent(
                    QEvent.Type.MouseButtonPress, position, position,
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier))
                self.assertEqual(canvas._drag, "resize_br")
                scale = canvas._canvas_rect().width() / canvas.layout_bounds.width()
                # Only enlarge. The adjacent top-right corner reaches the
                # output while the grabbed bottom-right corner is far inside.
                for step in (1.0, 2.0, 3.0):
                    target = position + QPointF(step * scale, step * scale)
                    canvas.mouseMoveEvent(QMouseEvent(
                        QEvent.Type.MouseMove, target, target,
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                        modifiers))
                    corners = canvas._rotated_source_corners(canvas.sources[0])
                    self.assertNotAlmostEqual(corners[1].x(), 800.0, places=6)
                    self.assertLess(corners[3].x(), 750.0)
                    if modifiers == Qt.KeyboardModifier.AltModifier:
                        self.assertAlmostEqual(canvas.sources[0]["x"] +
                                               canvas.sources[0]["width"] / 2, 690.0)
                        self.assertAlmostEqual(canvas.sources[0]["y"] +
                                               canvas.sources[0]["height"] / 2, 150.0)
                    else:
                        self.assertAlmostEqual(corners[0].x(), before[0].x())
                        self.assertAlmostEqual(corners[0].y(), before[0].y())

    def test_bottom_right_resize_can_continue_beyond_canvas_edges(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(1000, 1000, "br")
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["y"]), (100.0, 80.0))
        self.assertEqual((source["width"], source["height"]), (1300.0, 1200.0))

    def test_source_can_be_moved_beyond_all_desktop_edges(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._move_selected_by(-2000, -2000)
        self.assertEqual((canvas.sources[0]["x"], canvas.sources[0]["y"]),
                         (-1900.0, -1920.0))
        canvas._move_selected_by(5000, 5000)
        self.assertEqual((canvas.sources[0]["x"], canvas.sources[0]["y"]),
                         (canvas.workspace_bounds.right() - 300,
                          canvas.workspace_bounds.bottom() - 200))
        canvas._move_selected_by(-10000, -10000)
        self.assertEqual((canvas.sources[0]["x"], canvas.sources[0]["y"]),
                         (canvas.workspace_bounds.left(), canvas.workspace_bounds.top()))

    def test_left_and_top_resize_can_extend_past_desktop_origin(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(-500, -500, "tl")
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["y"]), (-400.0, -420.0))
        self.assertEqual((source["width"], source["height"]), (800.0, 700.0))

    def test_corner_resize_preserves_ratio_and_fixed_opposite_corner(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(100, 50, "br", constrain=True)
        source = canvas.sources[0]
        self.assertAlmostEqual(source["width"] / source["height"], 1.5)
        self.assertEqual((source["x"], source["y"]), (100.0, 80.0))
        self.assertAlmostEqual(source["width"], 400.0)
        self.assertAlmostEqual(source["height"], 800 / 3)

    def test_option_corner_resize_preserves_ratio_and_centers_item(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(75.0, 50.0, "br", constrain=True,
                                centered=True)
        source = canvas.sources[0]
        self.assertAlmostEqual(source["width"] / source["height"], 1.5)
        self.assertAlmostEqual(source["x"] + source["width"] / 2, 250.0)
        self.assertAlmostEqual(source["y"] + source["height"] / 2, 180.0)
        self.assertAlmostEqual(source["width"], 450.0)
        self.assertAlmostEqual(source["height"], 300.0)

    def test_side_midpoint_resize_preserves_ratio_and_centers_other_axis(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(60, 0, "mr", constrain=True)
        source = canvas.sources[0]
        self.assertAlmostEqual(source["width"] / source["height"], 1.5)
        self.assertEqual(source["x"], 100.0)
        self.assertAlmostEqual(source["y"] + source["height"] / 2, 180.0)
        self.assertAlmostEqual(source["width"], 360.0)
        self.assertAlmostEqual(source["height"], 240.0)

    def test_option_side_resize_expands_symmetrically_and_preserves_ratio(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(30.0, 0.0, "mr", constrain=True,
                                centered=True)
        source = canvas.sources[0]
        self.assertAlmostEqual(source["width"], 360.0)
        self.assertAlmostEqual(source["height"], 240.0)
        self.assertAlmostEqual(source["x"] + source["width"] / 2, 250.0)
        self.assertAlmostEqual(source["y"] + source["height"] / 2, 180.0)

    def test_option_shift_resize_is_centered_and_freeform(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(50.0, 20.0, "br", centered=True)
        source = canvas.sources[0]
        self.assertEqual((source["width"], source["height"]), (400.0, 240.0))
        self.assertEqual((source["x"] + source["width"] / 2,
                          source["y"] + source["height"] / 2), (250.0, 180.0))

    def test_option_center_resize_can_grow_past_workspace_bounds(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(3000.0, 0.0, "mr", constrain=False,
                                centered=True)
        source = canvas.sources[0]
        self.assertGreater(source["width"], canvas.workspace_bounds.width())
        self.assertAlmostEqual(source["x"] + source["width"] / 2, 250.0)

    def test_option_proportional_center_resize_can_grow_past_workspace_bounds(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(3000.0, 0.0, "mr", constrain=True,
                                centered=True)
        source = canvas.sources[0]
        self.assertGreater(source["width"], canvas.workspace_bounds.width())
        self.assertAlmostEqual(source["x"] + source["width"] / 2, 250.0)

    def test_rotation_handle_is_outside_corner_and_does_not_replace_resize(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0,
                           "rotation": 0}]
        canvas._set_selection({0}, 0)
        corners = canvas._source_canvas_corners(canvas.sources[0])
        rotate_point = canvas._rotation_handle_points(
            canvas._source_canvas_bounds(canvas.sources[0]), corners)[0]
        self.assertEqual(canvas._rotation_handle_at(rotate_point)["kind"], "source")
        # The expanded target should still activate closer to the corner,
        # while the corner itself remains dedicated to resizing.
        closer_point = QPointF(
            rotate_point.x() - (rotate_point.x() - corners[0].x()) * 0.4,
            rotate_point.y() - (rotate_point.y() - corners[0].y()) * 0.4)
        self.assertEqual(canvas._rotation_handle_at(closer_point)["kind"], "source")
        self.assertIsNone(canvas._rotation_handle_at(corners[0]))
        self.assertEqual(canvas._hit_test(corners[0]), "resize_tl")

    def test_resize_hover_uses_directional_resize_cursors(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0,
                           "rotation": 0}]
        canvas._set_selection({0}, 0)
        corners = canvas._source_canvas_corners(canvas.sources[0])
        self.assertEqual(canvas._resize_cursor_at(corners[0]),
                         Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(canvas._resize_cursor_at(corners[1]),
                         Qt.CursorShape.SizeBDiagCursor)
        self.assertEqual(canvas._resize_cursor_at(canvas._midpoint(corners[0], corners[1])),
                         Qt.CursorShape.SizeVerCursor)
        self.assertEqual(canvas._resize_cursor_at(canvas._midpoint(corners[0], corners[2])),
                         Qt.CursorShape.SizeHorCursor)

    def test_rotation_hover_shows_source_angle_and_zero_for_group(self):
        canvas = FrameCanvas()
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 300.0, "height": 200.0, "rotation": 270},
            {"displayID": "2", "name": "B", "x": 400.0, "y": 100.0,
             "width": 300.0, "height": 200.0, "rotation": 90},
        ]
        self.assertEqual(canvas._rotation_hover_angle(
            {"kind": "source", "index": 0}), 270)
        canvas._set_selection({0, 1}, 0)
        self.assertEqual(canvas._rotation_hover_angle(
            {"kind": "group", "corner": "tl"}), 0)
        self.assertIsNone(canvas._rotation_hover_angle(None))

    def test_rotation_angle_badge_tracks_pointer_and_flips_at_canvas_edges(self):
        canvas = FrameCanvas()
        canvas.resize(700, 500)
        rect = canvas._rotation_label_rect(QPointF(100, 100), 60, 30)
        self.assertEqual((rect.left(), rect.top()), (108.0, 62.0))

        right_edge_rect = canvas._rotation_label_rect(QPointF(685, 100), 60, 30)
        self.assertLess(right_edge_rect.right(), 685)
        top_edge_rect = canvas._rotation_label_rect(QPointF(100, 5), 60, 30)
        self.assertEqual(top_edge_rect.top(), 13.0)

    def test_rotation_uses_native_open_and_closed_hand_cursors(self):
        canvas = FrameCanvas()
        canvas._rotate_hover = {"kind": "group", "corner": "tl"}
        canvas._set_interaction_cursor()
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        canvas._set_interaction_cursor(rotating=True)
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ClosedHandCursor)

    def test_rotated_dimension_labels_follow_the_card_top_and_right_edges(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        source = {"displayID": "1", "name": "Display", "x": 100.0,
                  "y": 100.0, "width": 300.0, "height": 200.0,
                  "rotation": 30}
        canvas.sources = [source]
        bounds = canvas._source_canvas_bounds(source)
        name_bounds = QRectF(bounds.center().x() - 45,
                             bounds.center().y() - 12, 90, 24)
        width_label, height_label, labels_inside = canvas._rotated_dimension_label_rects(
            source, 60, 60, 16, name_bounds)
        self.assertIsInstance(labels_inside, bool)
        corners = canvas._source_canvas_corners(source)
        center = canvas._to_canvas_rect(source).center()
        top_midpoint = canvas._midpoint(corners[0], corners[1])
        right_midpoint = canvas._midpoint(corners[1], corners[3])

        for label, midpoint in ((width_label, top_midpoint),
                                (height_label, right_midpoint)):
            dx = label.center().x() - midpoint.x()
            dy = label.center().y() - midpoint.y()
            nx, ny = midpoint.x() - center.x(), midpoint.y() - center.y()
            self.assertAlmostEqual(dx * ny - dy * nx, 0.0, places=6)

    def test_rotated_dimension_labels_keep_the_requested_inside_or_outside_side(self):
        canvas = FrameCanvas()
        source = {"x": 100.0, "y": 100.0, "width": 300.0,
                  "height": 200.0, "rotation": 30}
        name_bounds = QRectF(220, 190, 70, 20)

        inside = canvas._rotated_dimension_label_rects(
            source, 60, 60, 16, name_bounds, inside_override=True)
        outside = canvas._rotated_dimension_label_rects(
            source, 60, 60, 16, name_bounds, inside_override=False)

        self.assertTrue(inside[2])
        self.assertFalse(outside[2])
        self.assertNotEqual(inside[:2], outside[:2])

    def test_rotation_captures_each_cards_dimension_label_side(self):
        canvas = FrameCanvas()
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0,
                           "rotation": 0}]
        canvas._set_selection({0}, 0)
        canvas._dimension_inside_cache[0] = False

        canvas._begin_rotation(QPointF(500, 300))

        self.assertEqual(canvas._rotation_state["dimension_inside"], {0: False})

    def test_source_rotation_snaps_to_integer_quarter_turns_and_rotates_content(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display", "x": 100.0,
                           "y": 80.0, "width": 300.0, "height": 200.0,
                           "rotation": 0}]
        canvas._set_selection({0}, 0)
        view = canvas._canvas_rect()

        def view_point(x, y):
            return QPointF(view.left() + x * view.width() / 800.0,
                           view.top() + y * view.height() / 600.0)

        center = QPointF(250.0, 180.0)
        canvas._begin_rotation(view_point(center.x() + 100, center.y()))
        canvas._rotate_selection(view_point(center.x(), center.y() + 100))
        source = canvas.sources[0]
        self.assertEqual(source["rotation"], 90)
        self.assertEqual((source["x"], source["y"]), (100.0, 80.0))
        self.assertEqual(canvas._rotation_angle, 90)
        self.assertEqual(canvas.source_scene()[0]["rotation"], 90)

    def test_multi_source_rotation_orbits_cards_around_group_center(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [
            {"displayID": "1", "name": "A", "x": 100.0, "y": 100.0,
             "width": 200.0, "height": 100.0, "rotation": 0},
            {"displayID": "2", "name": "B", "x": 340.0, "y": 120.0,
             "width": 100.0, "height": 80.0, "rotation": 0},
        ]
        canvas._set_selection({0, 1}, 0)
        view = canvas._canvas_rect()

        def view_point(x, y):
            return QPointF(view.left() + x * view.width() / 800.0,
                           view.top() + y * view.height() / 600.0)

        canvas._begin_rotation(view_point(370.0, 150.0))
        canvas._rotate_selection(view_point(270.0, 250.0))
        first, second = canvas.sources
        self.assertAlmostEqual(first["x"] + first["width"] / 2, 270.0)
        self.assertAlmostEqual(first["y"] + first["height"] / 2, 80.0)
        self.assertAlmostEqual(second["x"] + second["width"] / 2, 260.0)
        self.assertAlmostEqual(second["y"] + second["height"] / 2, 270.0)
        self.assertEqual((first["rotation"], second["rotation"]), (90, 90))

    def test_top_left_resize_keeps_opposite_bottom_right_corner_fixed(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(-40, -30, "tl")
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["y"]), (60.0, 50.0))
        self.assertEqual((source["x"] + source["width"],
                          source["y"] + source["height"]), (400.0, 280.0))

    def test_left_edge_handle_resizes_only_width_and_keeps_right_edge_fixed(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(-40, 0, "ml")
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["width"]), (60.0, 340.0))
        self.assertEqual((source["y"], source["height"]), (80.0, 200.0))

    def test_top_edge_handle_resizes_only_height_and_keeps_bottom_edge_fixed(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        canvas.selected = 0
        canvas._resize_selected(0, -30, "tm")
        source = canvas.sources[0]
        self.assertEqual((source["y"], source["height"]), (50.0, 230.0))
        self.assertEqual((source["x"], source["width"]), (100.0, 300.0))

    def test_edge_midpoints_are_resize_handles(self):
        canvas = FrameCanvas()
        canvas.resize(800, 600)
        canvas.set_output_size(800, 600)
        canvas.sources = [{"displayID": "1", "name": "Display",
                           "x": 100.0, "y": 80.0,
                           "width": 300.0, "height": 200.0}]
        rect = canvas._to_canvas_rect(canvas.sources[0])
        for suffix, point in (
                ("tm", QPointF(rect.center().x(), rect.top())),
                ("bm", QPointF(rect.center().x(), rect.bottom())),
                ("ml", QPointF(rect.left(), rect.center().y())),
                ("mr", QPointF(rect.right(), rect.center().y()))):
            with self.subTest(handle=suffix):
                self.assertEqual(canvas._hit_test(point), "resize_" + suffix)

    def test_dimension_labels_move_as_a_pair_when_either_overlaps_name(self):
        rect = QRectF(100, 100, 300, 200)
        crowded_name_bounds = QRectF(210, 106, 80, 24)
        clear_name_bounds = QRectF(210, 185, 80, 24)
        fits = FrameCanvas._dimension_labels_fit_inside
        self.assertFalse(fits(rect, crowded_name_bounds, 60, 50, 20))
        self.assertTrue(fits(rect, clear_name_bounds, 60, 50, 20))

    def test_drag_guides_measure_from_each_source_edge_to_nearest_edges(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.set_layout_displays([
            {"displayID": "1", "name": "Output A", "x": 0.0,
             "y": 0.0, "width": 400.0, "height": 300.0},
            {"displayID": "2", "name": "Output B", "x": 400.0,
             "y": 0.0, "width": 400.0, "height": 300.0},
        ])
        canvas.sources = [{"displayID": "1", "name": "Source", "x": 100.0,
                           "y": 80.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        canvas._drag = "move"
        polygons, excluded = canvas._measurement_anchor()
        specs = canvas._measurement_guide_specs(polygons, excluded)
        by_direction = {
            ("left" if end.x() < start.x() else "right" if end.x() > start.x()
             else "up" if end.y() < start.y() else "down"): label
            for start, end, label, _ in specs
        }
        self.assertEqual(by_direction,
                         {"left": "100 px", "right": "200 px",
                          "up": "80 px", "down": "120 px"})

    def test_drag_guides_start_at_midpoint_of_each_source_edge(self):
        canvas = FrameCanvas()
        canvas.set_output_size(800, 600)
        canvas.set_layout_displays([
            {"displayID": "1", "name": "Output", "x": 0.0,
             "y": 0.0, "width": 800.0, "height": 600.0},
        ])
        canvas.sources = [{"displayID": "1", "name": "Source", "x": 100.0,
                           "y": 80.0, "width": 100.0, "height": 100.0}]
        canvas._set_selection({0}, 0)
        canvas._drag = "move"
        polygons, excluded = canvas._measurement_anchor()
        specs = canvas._measurement_guide_specs(polygons, excluded)
        self.assertEqual(len(specs), 4)
        starts = [canvas._from_canvas_point(spec[0]) for spec in specs]
        self.assertEqual(starts, [QPointF(150.0, 80.0), QPointF(200.0, 130.0),
                                  QPointF(150.0, 180.0), QPointF(100.0, 130.0)])

    def test_unified_layout_preserves_negative_desktop_origins(self):
        canvas = FrameCanvas()
        canvas.set_layout_displays([
            {"displayID": "1", "name": "Left", "x": -1200, "y": -300,
             "width": 1200, "height": 800},
            {"displayID": "2", "name": "Main", "x": 0, "y": 0,
             "width": 1600, "height": 900},
        ])
        self.assertEqual(canvas.layout_bounds, QRectF(-1200, -300, 2800, 1200))
        canvas.add_source("1", "Left", 1200, 800)
        source = canvas.sources[0]
        self.assertEqual((source["x"], source["y"]), (-1200.0, -300.0))

    def test_mirrored_screens_are_one_canvas_tile_but_all_are_output_targets(self):
        window = FrameWindow([
            {"displayId": 1, "name": "Main", "desktop": {"x": 0, "y": 0,
             "width": 1000, "height": 700}},
            {"displayId": 2, "name": "Mirror", "desktop": {"x": 0, "y": 0,
             "width": 1000, "height": 700, "mirrorsDisplayID": 1}},
        ])
        try:
            self.assertEqual(len(window.canvas.layout_displays), 2)
            self.assertEqual(len(window.canvas._visible_layout_displays), 1)
            command = {"outputs": window.canvas.layout_displays}
            self.assertEqual(len(command["outputs"]), 2)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
