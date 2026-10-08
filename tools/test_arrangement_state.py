import os
import json
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QKeyEvent, QKeySequence, QPalette, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QLineEdit, QListWidget, QMainWindow, QMenu, QStyle, QTabWidget,
    QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from display_mode_ui import (
    APP_TITLE, SETTINGS_NAME, ApplicationShortcutFilter, MacLetterShortcutMonitor, DisplayArrangementCanvas,
    ARRANGEMENT_HIGHLIGHT_COLOR, AppearanceController, DisplayArrangementDialog, DisplayInspectorWindow,
    ExplicitForegroundDelegate,
    ModeSummaryTable, SHORTCUT_HELP, scaled_high_dpi_icon,
    appearance_hex, appearance_stylesheet, _transform_appearance_markup,
    arrangement_display_records, arrangement_resolution_text, display_mirror_roles,
    display_role_menu_options, identify_overlay_groups,
    mirror_group_name_lines, resolution_rows_for_display, same_desktop_resolution,
)


class ShortcutFocusRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_native_f_shortcut_closes_the_focused_frame_window(self):
        frame = unittest.mock.Mock(isVisible=lambda: True)
        owner = SimpleNamespace(arrangement_dialog=None, frame_dialog=frame)
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner

        with patch.object(ApplicationShortcutFilter, "_has_focus_in",
                          side_effect=lambda window: window is frame), \
                patch.object(ApplicationShortcutFilter, "_is_text_entry",
                              return_value=False):
            self.assertTrue(monitor._handle_key(3, True))

        frame.close.assert_called_once()

    def test_native_command_option_f_toggles_focused_frame_output(self):
        frame = unittest.mock.Mock(isVisible=lambda: True)
        owner = SimpleNamespace(arrangement_dialog=None, frame_dialog=frame)
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner

        with patch.object(ApplicationShortcutFilter, "_has_focus_in",
                          side_effect=lambda window: window is frame):
            self.assertTrue(monitor._handle_key(3, True, command_option=True))

        frame.toggle_output.assert_called_once()

    def test_native_command_w_closes_the_focused_frame_window(self):
        frame = unittest.mock.Mock(isVisible=lambda: True)
        owner = SimpleNamespace(arrangement_dialog=None, frame_dialog=frame)
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner

        with patch.object(ApplicationShortcutFilter, "_has_focus_in",
                          side_effect=lambda window: window is frame):
            self.assertTrue(monitor._handle_key(13, True, command_only=True))

        frame.close.assert_called_once()

    def test_explicit_orange_foreground_survives_selection_in_both_themes(self):
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("Current")
        delegate = ExplicitForegroundDelegate(table)
        index = table.model().index(0, 0)

        for hex_color in ("#d99a3e", "#a85f00"):
            with self.subTest(theme_accent=hex_color):
                expected = QColor(hex_color)
                item = QTableWidgetItem("Current")
                item.setForeground(QBrush(expected))
                table.setItem(0, 0, item)
                option = QStyleOptionViewItem()
                option.state |= QStyle.StateFlag.State_Selected
                delegate.initStyleOption(option, index)
                self.assertEqual(
                    option.palette.color(QPalette.ColorGroup.Active,
                                         QPalette.ColorRole.HighlightedText),
                    expected)
                self.assertEqual(
                    option.palette.color(QPalette.ColorGroup.Inactive,
                                         QPalette.ColorRole.HighlightedText),
                    expected)

    def test_selected_table_text_uses_theme_appropriate_contrast(self):
        table = QTableWidget(1, 1)
        table.setItem(0, 0, QTableWidgetItem("Selected"))
        delegate = ExplicitForegroundDelegate(table)
        index = table.model().index(0, 0)
        for appearance, expected_hex in (("dark", "#f2f2f2"),
                                         ("light", "#0d0d0d")):
            with self.subTest(appearance=appearance), patch(
                    "display_mode_ui._ACTIVE_APPEARANCE", appearance):
                option = QStyleOptionViewItem()
                option.state |= QStyle.StateFlag.State_Selected
                delegate.initStyleOption(option, index)
                self.assertEqual(
                    option.palette.color(QPalette.ColorGroup.Active,
                                         QPalette.ColorRole.HighlightedText),
                    QColor(expected_hex))

    def _focus_context_for(self, child):
        owner = QMainWindow()
        owner.display_list = None
        owner.setCentralWidget(child)
        owner.show()
        child.setFocus()
        self.app.processEvents()
        context = ApplicationShortcutFilter(owner)._main_focus_context()
        owner.close()
        return context

    def test_table_focus_keeps_global_shortcuts_available(self):
        self.assertEqual(self._focus_context_for(QTableWidget()), "normal")

    def test_text_entry_focus_preserves_typing_and_cursor_keys(self):
        self.assertEqual(self._focus_context_for(QLineEdit()), "text-entry")

    def test_bare_letter_shortcut_yields_to_text_entry_but_works_elsewhere(self):
        owner = QMainWindow()
        container = QWidget()
        layout = QVBoxLayout(container)
        editor = QLineEdit()
        canvas = QWidget()
        canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(editor)
        layout.addWidget(canvas)
        owner.setCentralWidget(container)
        shortcut = QShortcut(QKeySequence("R"), owner)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        triggered = []
        shortcut.activated.connect(lambda: triggered.append(True))
        owner.show()

        editor.setFocus()
        self.app.processEvents()
        self.app.sendEvent(editor, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier, "r"))
        self.assertEqual(editor.text(), "r")
        self.assertFalse(triggered)

        canvas.setFocus()
        self.app.processEvents()
        self.app.sendEvent(canvas, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier, "r"))
        self.assertTrue(triggered)
        owner.close()

    def test_qt_ctrl_shortcut_maps_to_command_on_mac(self):
        if sys.platform != "darwin":
            self.skipTest("Qt's Command mapping is specific to Apple platforms")
        self.assertEqual(
            QKeySequence("Ctrl+S").toString(QKeySequence.SequenceFormat.NativeText), "⌘S")

    def test_help_menu_contains_shortcut_appearance_and_about(self):
        window = DisplayInspectorWindow()
        self.assertEqual(window.windowTitle(), "Display Manager Pro")
        self.assertEqual(APP_TITLE, "Display Manager Pro")
        self.assertEqual(window.friendly_names.applicationName(), SETTINGS_NAME)
        self.assertEqual(window.refresh_button.text(), "Refresh")
        self.assertEqual(window.arrange_button.text(), "Arrange")
        self.assertEqual(window.export_button.text(), "Export")
        menus = window.menuBar().actions()
        self.assertEqual([action.text() for action in menus], ["Help"])
        self.assertEqual([action.text() for action in menus[0].menu().actions()],
                         ["Shortcut", "Appearance", "About"])
        self.assertEqual([action.text() for action in window.appearance_menu.actions()],
                         ["Dark", "Light", "System"])
        self.assertEqual(window.shortcut_action.shortcut(), QKeySequence("Ctrl+/"))
        self.assertEqual(window.about_action.menuRole(),
                         window.about_action.MenuRole.NoRole)
        self.assertIn(QKeySequence("Ctrl+E"),
                      [shortcut.key() for shortcut, _button in window._keyboard_shortcuts])
        window.shortcut_action.trigger()
        self.assertTrue(window.shortcuts_dialog.isVisible())
        self.assertEqual(len(window.shortcuts_dialog._shortcut_labels),
                         sum(bool(shortcut) for _, shortcut in SHORTCUT_HELP))
        window.show_shortcuts_dialog()
        self.assertFalse(window.shortcuts_dialog.isVisible())
        window.close()

    def test_light_appearance_exactly_inverts_grayscale_and_deepens_accent(self):
        self.assertEqual(appearance_hex("#1b1b1b", "light"), "#e4e4e4")
        self.assertEqual(appearance_hex("#f2f2f2", "light"), "#0d0d0d")
        self.assertEqual(appearance_hex("#888", "light"), "#777")
        self.assertEqual(appearance_hex("#d99a3e", "light"), "#A85F00")
        self.assertEqual(appearance_hex("#ff0000", "light"), "#ff0000")
        self.assertIn("#e4e4e4", appearance_stylesheet("light"))
        self.assertEqual(_transform_appearance_markup(
            "#e4e4e4 #A85F00", "light", "dark"), "#1b1b1b #d99a3e")

    def test_refresh_busy_buttons_keep_their_enabled_appearance(self):
        dark = appearance_stylesheet("dark")
        light = appearance_stylesheet("light")
        for stylesheet, expected_background, expected_text in (
                (dark, "#2d2d2d", "#f2f2f2"),
                (light, "#d2d2d2", "#0d0d0d")):
            selector = 'QPushButton[refreshBusy="true"]:disabled {'
            rule = stylesheet.split(selector, 1)[1].split("}", 1)[0]
            self.assertIn(f"background-color: {expected_background};", rule)
            self.assertIn(f"color: {expected_text};", rule)

    def test_arrangement_and_identify_highlight_remains_original_orange(self):
        self.assertEqual(ARRANGEMENT_HIGHLIGHT_COLOR.name(), "#d99a3e")
        self.assertEqual(appearance_hex("#d99a3e", "light"), "#A85F00")

    def test_appearance_menu_switches_and_restores_system_mode(self):
        window = DisplayInspectorWindow()
        previous = window.appearance_controller.mode
        try:
            window.appearance_actions["light"].trigger()
            self.assertEqual(window.appearance_controller.mode, "light")
            self.assertEqual(appearance_hex("#1b1b1b"), "#e4e4e4")
            self.assertTrue(window.appearance_actions["light"].isChecked())
            window.appearance_actions["system"].trigger()
            self.assertEqual(window.appearance_controller.mode, "system")
        finally:
            window.appearance_actions[previous].trigger()
            window.close()

    def test_startup_installs_stylesheet_when_initial_scheme_matches_default(self):
        class MemorySettings:
            def value(self, _key, default=None):
                return "dark"

            def setValue(self, _key, _value):
                pass

        original_stylesheet = self.app.styleSheet()
        original_scheme = self.app.styleHints().colorScheme()
        controller = None
        try:
            self.app.setStyleSheet("")
            with patch("display_mode_ui._ACTIVE_APPEARANCE", "dark"):
                controller = AppearanceController(self.app, MemorySettings())
                self.assertEqual(self.app.styleSheet(), appearance_stylesheet("dark"))
        finally:
            if controller is not None:
                try:
                    self.app.styleHints().colorSchemeChanged.disconnect(
                        controller._system_scheme_changed)
                except (RuntimeError, TypeError):
                    pass
                controller.deleteLater()
            self.app.styleHints().setColorScheme(original_scheme)
            self.app.setStyleSheet(original_stylesheet)
            self.app.processEvents()

    def test_about_icon_is_rendered_at_retina_pixel_density(self):
        source = QPixmap(1024, 1024)
        source.fill(Qt.GlobalColor.white)
        icon = scaled_high_dpi_icon(source, 88, 2.0)
        self.assertEqual((icon.width(), icon.height()), (176, 176))
        self.assertEqual(icon.devicePixelRatioF(), 2.0)

    def test_mac_letter_shortcut_dispatch_bypasses_ime_only_outside_text_entry(self):
        owner = SimpleNamespace(
            arrangement_dialog=None,
            refresh_button=unittest.mock.Mock(isEnabled=lambda: True),
            arrange_button=unittest.mock.Mock(isEnabled=lambda: True),
        )
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner
        with patch.object(ApplicationShortcutFilter, "_has_focus_in", return_value=True), \
                patch.object(ApplicationShortcutFilter, "_is_text_entry", return_value=False):
            self.assertTrue(monitor._handle_key(15, True))
        owner.refresh_button.click.assert_called_once()

        owner.refresh_button.click.reset_mock()
        with patch.object(ApplicationShortcutFilter, "_has_focus_in", return_value=True), \
                patch.object(ApplicationShortcutFilter, "_is_text_entry", return_value=True):
            self.assertFalse(monitor._handle_key(15, True))
        owner.refresh_button.click.assert_not_called()

    def test_mac_arrange_shortcuts_route_a_m_and_identify_hold(self):
        dialog = unittest.mock.Mock()
        dialog.isVisible.return_value = True
        dialog.identify_button.isEnabled.return_value = True
        dialog._identify_keyboard_held = True
        owner = SimpleNamespace(arrangement_dialog=dialog)
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner
        with patch.object(ApplicationShortcutFilter, "_has_focus_in", return_value=True), \
                patch.object(ApplicationShortcutFilter, "_is_text_entry", return_value=False):
            self.assertTrue(monitor._handle_key(0, True))
            self.assertTrue(monitor._handle_key(46, True))
            self.assertTrue(monitor._handle_key(34, True))
            self.assertTrue(monitor._handle_key(34, False))
            self.assertTrue(monitor._handle_key(1, True, True))
            self.assertTrue(monitor._handle_key(13, True, True))
        dialog.cycle_all_mapping.assert_called_once()
        dialog._set_identify_keyboard_held.assert_any_call(True)
        dialog._set_identify_keyboard_held.assert_any_call(False)
        dialog.toggle_snap.assert_called_once()
        self.assertEqual(dialog.close_with_shortcut.call_count, 2)

    def test_mac_arrange_shortcuts_work_with_its_context_menu_open(self):
        dialog = QDialog()
        dialog.show()
        dialog.cycle_all_mapping = unittest.mock.Mock()
        dialog.toggle_coordinates = unittest.mock.Mock()
        dialog.toggle_snap = unittest.mock.Mock()
        dialog.close_with_shortcut = unittest.mock.Mock()
        dialog._set_identify_keyboard_held = unittest.mock.Mock()
        dialog.identify_button = unittest.mock.Mock()
        dialog.identify_button.isEnabled.return_value = True
        root = QMenu(dialog)
        middle = root.addMenu("Color mode")
        deep = middle.addMenu("Display 1")
        owner = SimpleNamespace(arrangement_dialog=dialog, shortcuts_dialog=None,
                                show_shortcuts_dialog=unittest.mock.Mock())
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner
        with patch.object(QApplication, "activePopupWidget", return_value=deep):
            self.assertTrue(monitor._handle_key(46, True))
            self.assertTrue(monitor._handle_key(8, True))
            self.assertTrue(monitor._handle_key(34, True))
            self.assertTrue(monitor._handle_key(1, True, True))
            self.assertTrue(monitor._handle_key(0, True))
            self.assertTrue(monitor._handle_key(13, True, True))
            self.assertTrue(monitor._handle_key(44, True, True))
        dialog.cycle_all_mapping.assert_called_once()
        dialog.toggle_coordinates.assert_called_once()
        dialog._set_identify_keyboard_held.assert_called_once_with(True)
        dialog.toggle_snap.assert_called_once()
        self.assertEqual(dialog.close_with_shortcut.call_count, 2)
        owner.show_shortcuts_dialog.assert_called_once()
        dialog.close()

    def test_main_shortcuts_work_with_nested_context_menu_open(self):
        owner = QMainWindow()
        owner.arrangement_dialog = None
        owner.shortcuts_dialog = None
        owner.show_shortcuts_dialog = unittest.mock.Mock()
        owner.refresh_button = unittest.mock.Mock(isEnabled=lambda: True)
        owner.arrange_button = unittest.mock.Mock(isEnabled=lambda: True)
        owner.export_button = unittest.mock.Mock(isEnabled=lambda: True)
        root = QMenu(owner)
        middle = root.addMenu("Display")
        deep = middle.addMenu("Modes")
        monitor = object.__new__(MacLetterShortcutMonitor)
        monitor.owner = owner
        with patch.object(QApplication, "activePopupWidget", return_value=deep):
            self.assertTrue(ApplicationShortcutFilter._has_focus_in(owner))
            self.assertTrue(monitor._handle_key(15, True))
            self.assertTrue(monitor._handle_key(0, True))
            self.assertTrue(monitor._handle_key(14, True, True))
            self.assertTrue(monitor._handle_key(44, True, True))
        owner.refresh_button.click.assert_called_once()
        owner.arrange_button.click.assert_called_once()
        owner.export_button.click.assert_called_once()
        owner.show_shortcuts_dialog.assert_called_once()
        owner.close()

    def test_main_arrow_navigation_works_from_nested_context_menu(self):
        owner = QMainWindow()
        owner.tabs = QTabWidget()
        owner.tabs.addTab(QWidget(), "One")
        owner.tabs.addTab(QWidget(), "Two")
        owner.display_list = QListWidget()
        owner.display_list.addItems(["First", "Second"])
        owner.display_list.setCurrentRow(0)
        owner.refresh_button = QWidget()
        owner.arrange_button = QWidget()
        owner.export_button = QWidget()
        root = QMenu(owner)
        middle = root.addMenu("Display")
        deep = middle.addMenu("Modes")
        route = ApplicationShortcutFilter(owner)
        with patch.object(QApplication, "activePopupWidget", return_value=deep):
            self.assertTrue(route.eventFilter(deep, QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Right,
                Qt.KeyboardModifier.NoModifier)))
            self.assertTrue(route.eventFilter(deep, QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Down,
                Qt.KeyboardModifier.NoModifier)))
        self.assertEqual(owner.tabs.currentIndex(), 1)
        self.assertEqual(owner.display_list.currentRow(), 1)
        owner.close()

    def test_menu_window_focus_fallback_and_unrelated_popup_are_scoped(self):
        owner = QMainWindow()
        root = QMenu(owner)
        deep = root.addMenu("Nested")
        other = QDialog()
        unrelated = QMenu(other)
        with patch.object(QApplication, "activePopupWidget", return_value=None), \
                patch.object(QApplication, "activeWindow", return_value=deep):
            self.assertTrue(ApplicationShortcutFilter._has_focus_in(owner))
        with patch.object(QApplication, "activePopupWidget", return_value=unrelated), \
                patch.object(QApplication, "activeWindow", return_value=owner):
            self.assertFalse(ApplicationShortcutFilter._has_focus_in(owner))
        owner.close()
        other.close()


class ArrangementPositionHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.screens = [
            {"displayId": "1", "name": "Main", "x": 0, "y": 0,
             "width": 1000, "height": 800, "main": True,
             "mirrorsDisplayID": "0", "desktopModes": [],
             "framebufferMode": {}, "colorProfile": {}},
            {"displayId": "2", "name": "External", "x": 1000, "y": 0,
             "width": 800, "height": 600, "main": False,
             "mirrorsDisplayID": "0", "desktopModes": [],
             "framebufferMode": {}, "colorProfile": {}},
        ]
        self.dialog = DisplayArrangementDialog(self.screens, 0)

    def test_arrangement_menu_labels_and_mapping_order(self):
        self.assertEqual(self.dialog.windowTitle(), "Arrangement")
        captured = {}
        def inspect_menu(menu, _position, _was_visible):
            captured["root"] = [action.text() for action in menu.actions()]
            captured["mapping"] = [
                action.text() for action in self.dialog._active_mapping_menu.actions()[:3]]
        for screen, expected in ((None, ["Mapping all"]),
                                 (self.screens[0], ["Mapping", "Resolution", "Orientation",
                                                    "Color", "ICC", "Display role"])):
            with self.subTest(screen=screen), patch.object(
                    self.dialog, "_finish_display_menu_build", side_effect=inspect_menu):
                self.dialog._show_display_menu(screen, QPoint(100, 100))
                self.assertEqual(captured["root"], expected)
                self.assertEqual(captured["mapping"],
                                 ["Off", "Semi-transparent On", "Opaque On"])


    def test_mirror_member_offers_main_extended_and_other_group_source(self):
        screens = [
            {"displayId": "1", "productName": "Main", "main": True,
             "mirrorsDisplayID": "0"},
            {"displayId": "2", "productName": "Receiver", "main": False,
             "mirrorsDisplayID": "1"},
            {"displayId": "3", "productName": "Other", "main": False,
             "mirrorsDisplayID": "0"},
        ]
        options = display_role_menu_options(screens[1], screens)
        self.assertEqual([item[0] for item in options], [
            "Main Display", "Extended Display", "Mirror for Main", "Mirror for Other"])
        self.assertEqual(options[0][1:4], ("main", "", False))
        self.assertEqual(options[1][1:4], ("extended", "", False))
        self.assertEqual(options[2][1:4], ("mirror", "1", True))
        self.assertFalse(options[2][4])
        self.assertEqual(options[3][1:4], ("mirror", "3", False))

    def test_primary_display_has_current_extended_role_marked(self):
        screen = {"displayId": "1", "productName": "Main", "main": True,
                  "mirrorsDisplayID": "0"}
        options = display_role_menu_options(screen, [screen])
        self.assertEqual(options[0][0:4], ("Main Display", "main", "", True))
        self.assertEqual(options[1][0:4], ("Extended Display", "extended", "", False))
        self.assertFalse(options[1][4])

    def test_arrangement_role_menu_uses_checked_disabled_current_role(self):
        menu = QMenu(self.dialog)
        self.dialog._populate_display_role_menu(menu, self.screens[0])
        actions = {action.text(): action for action in menu.actions()}
        self.assertEqual(set(actions), {
            "Main Display", "Extended Display", "Mirror for External"})
        self.assertTrue(actions["Main Display"].isChecked())
        self.assertFalse(actions["Main Display"].isEnabled())
        self.assertTrue(actions["Extended Display"].isEnabled())
        self.assertTrue(all(action.property("closeDisplayMenuAfterTrigger")
                            for action in actions.values()))

    def test_display_role_selection_does_not_reopen_context_menu(self):
        menu = QMenu(self.dialog)
        action = menu.addAction("Extended Display")
        action.setProperty("closeDisplayMenuAfterTrigger", True)
        self.dialog._active_display_menu = menu
        self.dialog._display_menu_reopen_requested = True
        self.dialog._display_menu_reopen_screen = self.screens[0]
        self.dialog._display_menu_reopen_path = ("Display role",)

        self.dialog._display_menu_action_triggered(("Display role",), action)

        self.assertFalse(self.dialog._display_menu_reopen_requested)
        self.assertIsNone(self.dialog._display_menu_reopen_screen)
        self.assertEqual(self.dialog._display_menu_reopen_path, ())

    def test_identify_process_callbacks_ignore_stale_deleted_objects(self):
        class DeletedProcess:
            def error(self):
                raise RuntimeError("Internal C++ object already deleted")

            def deleteLater(self):
                raise RuntimeError("Internal C++ object already deleted")

        stale = DeletedProcess()
        self.dialog._identify_process = None
        self.dialog._identify_build_process = None
        self.dialog._identify_helper_error(stale)
        self.dialog._identify_helper_finished(stale)
        self.dialog._identify_build_error(stale)
        self.dialog._identify_build_finished(stale, 1, None)

    def test_arrangement_context_menu_reopens_in_selected_submenu(self):
        screen = self.screens[0]
        position = QPoint(100, 100)
        self.dialog._active_display_menu = QMenu(self.dialog)
        self.dialog._context_screen = screen
        self.dialog._display_menu_position = position
        path = ("ICC", "Built-in Monitor")
        self.dialog._display_menu_action_triggered(path, None)
        self.dialog._display_menu_action_triggered(("ICC",), None)
        self.assertEqual(self.dialog._display_menu_reopen_path, path)

        with patch.object(self.dialog, "_show_display_menu") as reopen_root, \
                patch.object(self.dialog, "_reopen_display_menu_path") as reopen_submenu:
            self.dialog._display_menu_hidden()
            self.app.processEvents()
            self.app.processEvents()

        reopen_root.assert_called_once_with(screen, position)
        reopen_submenu.assert_called_once_with(path)

    def test_batch_mapping_option_updates_every_display_independently(self):
        self.dialog._mapping_settings = {
            "1": {"enabled": "opaque", "background": "black", "line": "white"},
            "2": {"enabled": "off", "background": "white", "line": "blue"},
        }
        with patch.object(self.dialog, "_ensure_identify_helper", return_value=True) as ensure:
            self.dialog._set_mapping_option(None, "line", "red")

        self.assertEqual(self.dialog._mapping_settings["1"], {
            "enabled": "opaque", "background": "black", "line": "red"})
        self.assertEqual(self.dialog._mapping_settings["2"], {
            "enabled": "off", "background": "white", "line": "red"})
        ensure.assert_called_once()

    def test_f_cycles_all_displays_off_translucent_opaque_off(self):
        with patch.object(self.dialog, "_ensure_identify_helper", return_value=True):
            for expected in ("translucent", "opaque", "off"):
                self.dialog.cycle_all_mapping()
                self.assertEqual(
                    {settings["enabled"] for settings in self.dialog._mapping_settings.values()},
                    {expected})

    def test_m_updates_checkmarks_in_open_mapping_menu(self):
        menu = QMenu(self.dialog)
        mapping_menu = menu.addMenu("Mapping")
        actions = {}
        for value in ("off", "opaque", "translucent"):
            action = mapping_menu.addAction(value)
            action.setCheckable(True)
            action.setChecked(value == "off")
            action.setProperty("mappingCategory", "enabled")
            action.setProperty("mappingValue", value)
            actions[value] = action
        self.dialog._active_display_menu = menu
        self.dialog._active_mapping_menu = mapping_menu
        self.dialog._display_menu_submenus = [mapping_menu]
        self.dialog._context_screen = None
        menu.show()
        try:
            with patch.object(self.dialog, "_ensure_identify_helper", return_value=True):
                self.dialog.cycle_all_mapping()
            self.assertTrue(actions["translucent"].isChecked())
            self.assertFalse(actions["off"].isChecked())
        finally:
            menu.hide()

    def test_keyboard_identify_releases_without_interrupting_mouse_hold(self):
        self.dialog._start_identifying_displays()
        self.dialog._set_identify_keyboard_held(True)
        self.dialog._stop_identifying_displays()
        self.assertTrue(self.dialog._identify_pressed)
        self.dialog._set_identify_keyboard_held(False)
        self.assertFalse(self.dialog._identify_pressed)

    def test_escape_turns_off_mapping_on_all_displays(self):
        self.dialog._mapping_settings = {
            "1": {"enabled": "opaque", "background": "black", "line": "white"},
            "2": {"enabled": "translucent", "background": "white", "line": "red"},
        }
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                          Qt.KeyboardModifier.NoModifier)

        handled = self.dialog.eventFilter(self.dialog, event)

        self.assertTrue(handled)
        self.assertTrue(all(settings["enabled"] == "off"
                            for settings in self.dialog._mapping_settings.values()))

    def test_mirror_role_policy_keeps_receiver_resolutions_visible_but_disabled(self):
        modes = [
            {"modeID": 1, "width": 1920, "height": 1080, "pixelWidth": 1920,
             "pixelHeight": 1080, "refreshRate": 60, "ioFlags": 0},
            {"modeID": 2, "width": 1920, "height": 1080, "pixelWidth": 1920,
             "pixelHeight": 1080, "refreshRate": 75, "ioFlags": 0},
            {"modeID": 3, "width": 1280, "height": 720, "pixelWidth": 1280,
             "pixelHeight": 720, "refreshRate": 60, "ioFlags": 0},
        ]
        master = {"displayId": "1", "mirrorsDisplayID": "0"}
        receiver = {"displayId": "2", "mirrorsDisplayID": "1",
                    "desktopModes": modes,
                    "framebufferMode": dict(modes[0])}
        displays = [master, receiver]

        self.assertEqual(display_mirror_roles(master, displays), (False, True))
        self.assertEqual(display_mirror_roles(receiver, displays), (True, False))
        rows = resolution_rows_for_display(receiver, displays)
        self.assertEqual(len(rows), 2)
        current_row = next(row for row in rows if row["current"])
        unavailable_row = next(row for row in rows if not row["current"])
        self.assertTrue(current_row["selectable"])
        self.assertTrue(current_row["refreshRateOnly"])
        self.assertEqual(current_row["refreshRateItems"], ["60 Hz", "75 Hz"])
        self.assertFalse(unavailable_row["selectable"])
        self.assertEqual(unavailable_row["values"][0], "1280 × 720")
        self.assertEqual(unavailable_row["unavailableReason"],
                         "Unavailable while mirrored.")

    def test_same_desktop_resolution_compares_framebuffer_size_too(self):
        current = {"width": 1920, "height": 1080,
                   "pixelWidth": 3840, "pixelHeight": 2160}
        refresh_variant = dict(current, refreshRate=75)
        changed_scale = dict(current, pixelWidth=1920, pixelHeight=1080)
        self.assertTrue(same_desktop_resolution(current, refresh_variant))
        self.assertFalse(same_desktop_resolution(current, changed_scale))

    def test_current_row_autoscroll_respects_manual_scroll_position(self):
        table = ModeSummaryTable(("Mode",), "")
        table.resize(320, 180)
        table.set_rows([
            {"values": (f"Mode {index}",), "current": index == 0,
             "search": f"mode {index}"}
            for index in range(40)
        ])
        table.show()
        self.app.processEvents()
        scrollbar = table.table.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        value_before = scrollbar.value()
        table._manual_current_scroll = True

        table.scroll_to_current()

        self.assertEqual(scrollbar.value(), value_before)
        table.reset_current_scroll()
        table.scroll_to_current()
        self.assertTrue(table.table.viewport().rect().contains(
            table.table.visualItemRect(table.table.item(0, 0))))
        table.close()

    def test_mirror_group_names_cap_at_four_lines(self):
        four = [{"name": f"Display {index}"} for index in range(1, 5)]
        five = [{"name": f"Display {index}"} for index in range(1, 6)]
        self.assertEqual(mirror_group_name_lines(four),
                         ["Display 1", "Display 2", "Display 3", "Display 4"])
        self.assertEqual(mirror_group_name_lines(five),
                         ["Display 1", "Display 2", "Display 3", "..."])

    def test_mirror_group_resolution_uses_primary_role_mode(self):
        self.assertEqual(arrangement_resolution_text({
            "width": 800, "height": 600,
            "framebufferMode": {"width": 1920, "height": 1080},
            "rotation": 90,
        }), "1920 × 1080, 90°")

    def test_arrangement_records_include_current_and_available_color_modes(self):
        available = [{"modeID": 10, "width": 1920, "height": 1080,
                      "refreshRate": 60.0, "colorMode": "RGB_8BitFullRange",
                      "bitDepth": 8, "hdrMode": "SDR"}]
        display = {
            "displayId": "1", "name": "Main", "availableModes": available,
            "currentMode": available[0], "desktop": {
                "x": 0, "y": 0, "width": 1920, "height": 1080,
                "main": True, "mirrorsDisplayID": 0,
            },
        }

        records, omitted = arrangement_display_records([display])

        self.assertEqual(omitted, 0)
        self.assertEqual(records[0]["availableModes"], available)
        self.assertEqual(records[0]["currentMode"], available[0])

    def test_arrangement_records_retain_original_and_friendly_names_for_identification(self):
        display = {
            "displayId": "1", "productName": "Panel Model", "friendlyName": "Desk Screen",
            "desktop": {"x": 0, "y": 0, "width": 1920, "height": 1080,
                        "main": True, "mirrorsDisplayID": 0},
        }
        records, _omitted = arrangement_display_records([display])
        self.assertEqual(records[0]["name"], "Desk Screen")
        self.assertEqual(records[0]["originalName"], "Panel Model")
        self.assertEqual(records[0]["friendlyName"], "Desk Screen")

    def test_identification_groups_mirrored_display_names_together(self):
        screens = [
            {"displayId": "11", "name": "Portable", "originalName": "Portable",
             "friendlyName": "Travel", "mirrorsDisplayID": 10},
            {"displayId": "10", "name": "Primary", "originalName": "Primary",
             "friendlyName": "Desk", "mirrorsDisplayID": 0},
        ]
        groups = identify_overlay_groups(screens)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["displayIDs"], ["10", "11"])
        self.assertEqual(groups[0]["members"], [
            {"displayID": "10", "name": "Desk", "friendlyName": ""},
            {"displayID": "11", "name": "Travel", "friendlyName": ""},
        ])

    def test_identification_uses_original_name_when_friendly_name_is_empty(self):
        groups = identify_overlay_groups([{
            "displayId": "23", "name": "Panel label", "originalName": "Panel model",
            "friendlyName": "  ", "mirrorsDisplayID": 0,
        }])
        self.assertEqual(groups[0]["members"], [
            {"displayID": "23", "name": "Panel model", "friendlyName": ""},
        ])

    def test_identification_keeps_independent_displays_as_separate_overlays(self):
        screens = [
            {"displayId": "10", "name": "Main", "mirrorsDisplayID": 0},
            {"displayId": "11", "name": "Side", "mirrorsDisplayID": 0},
            {"displayId": "12", "name": "Top", "mirrorsDisplayID": 0},
        ]
        groups = identify_overlay_groups(screens)
        self.assertEqual([group["displayIDs"] for group in groups],
                         [["10"], ["11"], ["12"]])

    def test_background_snapshot_cannot_reposition_local_drop(self):
        self.dialog._hold_local_layout = True
        self.dialog.canvas._layout_frame = (17, 23, 0.5, 40, 50)
        stale = [dict(screen, x=0, y=0) for screen in self.screens]
        stale[1]["name"] = "Updated name"

        self.dialog.update_displays(stale, 0)

        self.assertEqual((self.dialog.canvas.screens[1]["x"],
                          self.dialog.canvas.screens[1]["y"]), (1000, 0))
        self.assertEqual(self.dialog.canvas.screens[1]["name"], "Updated name")
        self.assertEqual(self.dialog.canvas._layout_frame, (17, 23, 0.5, 40, 50))

    def test_external_geometry_is_applied_after_local_hold_is_released(self):
        self.dialog._hold_local_layout = False
        self.dialog.canvas._layout_frame = (17, 23, 0.5, 40, 50)
        external = [dict(screen) for screen in self.screens]
        external[1]["x"] = 1400

        self.dialog.update_displays(external, 0)

        self.assertEqual(self.dialog.canvas.screens[1]["x"], 1400)
        self.assertIsNone(self.dialog.canvas._layout_frame)

    def _preview_drop_with_snapping(self, enabled):
        canvas = self.dialog.canvas
        dragged = canvas.screens[1]
        dragged["x"], dragged["y"] = 1500, 900
        canvas.set_snap_enabled(enabled)
        canvas._drag_screen = dragged
        canvas._drag_layout_screen = dragged
        canvas._drag_origin = (1500, 900)
        canvas._drag_point = QPointF(0, 0)
        canvas._layout_scale = 1
        canvas._update_drag_preview(QPointF(1437, 1077))
        return dragged["x"], dragged["y"]

    def test_snap_toggle_only_controls_drag_assistance(self):
        self.assertEqual(self._preview_drop_with_snapping(False), (2937, 1977))
        self.assertEqual(self._preview_drop_with_snapping(True), (2937, 2000))

    def test_legal_drop_preserves_non_overlapping_pointer_position(self):
        canvas = self.dialog.canvas
        dragged = canvas.screens[1]
        canvas.set_snap_enabled(False)
        canvas._drag_screen = dragged
        canvas._drag_layout_screen = dragged
        canvas._drag_origin = (1500, 900)
        canvas._drag_point = QPointF(0, 0)
        canvas._layout_scale = 1
        canvas._update_drag_preview(QPointF(1437, 1077))

        # With snapping disabled the exact pointer-derived CG point is kept;
        # mandatory legal placement must not alter an already valid layout.
        self.assertEqual((dragged["x"], dragged["y"]), (2937, 1977))

    def test_mouse_release_attaches_to_nearest_screen_independent_of_snap_toggle(self):
        canvas = DisplayArrangementCanvas([dict(screen) for screen in self.screens])
        dragged = canvas.screens[1]
        canvas.set_snap_enabled(False)
        canvas._drag_screen = dragged
        canvas._drag_layout_screen = dragged
        canvas._drag_origin = (1000, 0)
        canvas._drag_point = QPointF(0, 0)
        canvas._layout_scale = 1
        canvas._drag_frame = (0, 0, 1, 0, 0)
        sent = []
        paint_state = []
        canvas.moveRequested.connect(lambda *args: sent.append(args))
        canvas.moveRequested.connect(lambda *_args: paint_state.append(
            (canvas._preserve_layout_frame, canvas._view_origin_offset)))

        class Release:
            button = lambda self: Qt.MouseButton.LeftButton
            position = lambda self: QPointF(-500, 500)
            accept = lambda self: None

        with patch.object(canvas, "repaint") as synchronous_paint, \
                patch.object(canvas, "update") as scheduled_paint:
            canvas.mouseReleaseEvent(Release())

        # The raw drop (500, 500) overlaps Main. The closest legal edge is
        # the bottom edge at y=800, preserving x=500.
        self.assertEqual((dragged["x"], dragged["y"]), (500, 800))
        self.assertEqual(sent, [("2", 500, 800, 1000, 0)])
        self.assertEqual(paint_state, [(False, (0, 0))])
        self.assertTrue(canvas._cards_fully_visible())
        synchronous_paint.assert_not_called()
        scheduled_paint.assert_called_once()

    def test_primary_arrangement_drop_refits_before_background_apply(self):
        canvas = self.dialog.canvas
        canvas.resize(640, 480)
        self.dialog._pending_view_offset = (100, 50)
        with patch.object(self.dialog, "_start_arrangement_apply") as apply:
            self.dialog.apply_primary_arrangement_offset(100, 50)

        apply.assert_called_once()
        self.assertIsNone(self.dialog._pending_view_offset)
        self.assertTrue(canvas._cards_fully_visible())

    def test_primary_drag_keeps_origin_label_zero_and_offsets_peers(self):
        canvas = self.dialog.canvas
        main, peer = canvas.screens
        main["_previewX"], main["_previewY"] = 150, -75
        canvas._drag_screen = main
        canvas._drag_primary_offset = (150, -75)

        self.assertEqual(canvas._displayed_screen_coordinates(main), (0, 0))
        self.assertEqual(canvas._displayed_screen_coordinates(peer), (850, 75))

    def test_coordinate_label_is_fixed_above_right_of_origin(self):
        marker = QPointF(200, 200)
        label = DisplayArrangementCanvas._coordinate_label_rect(
            marker, 70, 18)

        self.assertEqual(label.topLeft(), QPointF(208, 174))

    def test_release_attachment_uses_closest_non_overlapping_edge(self):
        canvas = self.dialog.canvas
        dragged = canvas.screens[1]

        # This location is in empty space. Nearest attach point is the right
        # edge of Main, while retaining the requested vertical alignment.
        self.assertEqual(canvas._attach_drop_to_nearest_screen(dragged, 1300, 100),
                         (1000, 100))

        # The mandatory release attachment is independent of the optional
        # drag-time snapping setting.
        canvas.set_snap_enabled(True)
        self.assertEqual(canvas._attach_drop_to_nearest_screen(dragged, 1300, 100),
                         (1000, 100))

    def test_release_attachment_ignores_mirrored_members_as_targets(self):
        canvas = self.dialog.canvas
        mirrored = dict(self.screens[1], displayId="3", name="Mirror",
                        x=1000, y=0, mirrorsDisplayID="1")
        canvas.screens.append(mirrored)
        selected = canvas.screens[1]

        # The mirrored member is part of the dragged physical layout group,
        # not an independent display edge to attach against.
        self.assertEqual(canvas._attach_drop_to_nearest_screen(selected, 1400, 900),
                         (1000, 800))

    def _mirror_canvas(self):
        screens = [
            dict(self.screens[0], main=False, mirrorsDisplayID="0"),
            dict(self.screens[0], displayId="3", name="Mirror", main=False,
                 mirrorsDisplayID="1"),
        ]
        canvas = DisplayArrangementCanvas(screens)
        canvas.resize(800, 600)
        rect = canvas._geometry()["1"]
        point = QPointF(rect.center().x(), rect.center().y())
        events = []
        canvas.displaySelected.connect(events.append)
        return canvas, point, events

    @staticmethod
    def _mouse_event(button, point):
        return SimpleNamespace(
            button=lambda: button,
            position=lambda: point,
            globalPosition=lambda: point,
            accept=lambda: None,
        )

    def test_stationary_mirror_click_does_not_switch_display_info(self):
        canvas, point, selected = self._mirror_canvas()
        identified = []
        canvas.displayIdentifyRequested.connect(identified.append)
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            self.assertEqual(selected, [])
            self.assertEqual(identified, ["1"])
            canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            self.app.processEvents()

        self.assertEqual(selected, [])
        self.assertEqual(identified, ["1", ""])
        self.assertEqual(canvas._front_group_id, "1")

    def test_primary_menu_bar_handle_does_not_identify_display(self):
        canvas = DisplayArrangementCanvas([dict(self.screens[0], main=True)])
        canvas.resize(800, 600)
        rect = canvas._geometry()["1"]
        point = QPointF(rect.center().x(), rect.top() + 5)
        identify_events = []
        canvas.displayIdentifyRequested.connect(identify_events.append)
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            self.assertNotIn("1", identify_events)
            canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
        self.assertNotIn("1", identify_events)

    def test_repeated_mirror_clicks_do_not_cycle_display_info(self):
        canvas, point, selected = self._mirror_canvas()
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            for _ in range(3):
                canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
                canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
                self.app.processEvents()

        self.assertEqual(selected, [])
        self.assertEqual(canvas._front_group_id, "1")

    def test_clicking_exposed_mirror_card_raises_group_without_switching_info(self):
        canvas, _point, selected = self._mirror_canvas()
        base = canvas._geometry()["1"]
        point = QPointF(base.right() + 2, base.center().y())
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            self.app.processEvents()

        self.assertEqual(selected, [])
        self.assertEqual(canvas._front_group_id, "1")
        stack = canvas._mirror_stack_members(canvas.screens[0])
        self.assertEqual(str(stack[0]["displayId"]), "1")

    def _overlapping_group_canvas(self):
        screens = [dict(self.screens[0], main=True, mirrorsDisplayID="0"),
                   dict(self.screens[1], x=800, y=0, main=False,
                        mirrorsDisplayID="0")]
        canvas = DisplayArrangementCanvas(screens)
        canvas.resize(800, 600)
        return canvas

    @staticmethod
    def _point_in_left_only_group(canvas):
        left = canvas._geometry()["1"]
        return QPointF(left.left() + left.width() * 0.4, left.center().y())

    def test_pressing_display_group_raises_it_above_other_groups_immediately(self):
        canvas = self._overlapping_group_canvas()
        point = self._point_in_left_only_group(canvas)
        painted_orders = []
        with patch.object(canvas, "repaint", side_effect=lambda:
                          painted_orders.append([str(screen["displayId"])
                                                 for screen in canvas._screen_paint_order()])), \
                patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))

        self.assertEqual(canvas._front_group_id, "1")
        self.assertEqual([str(screen["displayId"]) for screen in canvas._screen_paint_order()],
                         ["2", "1"])
        self.assertEqual(painted_orders[0], ["2", "1"])

    def test_right_clicking_display_group_raises_it_above_other_groups(self):
        canvas = self._overlapping_group_canvas()
        point = self._point_in_left_only_group(canvas)
        painted_orders = []
        with patch.object(canvas, "repaint", side_effect=lambda:
                          painted_orders.append([str(screen["displayId"])
                                                 for screen in canvas._screen_paint_order()])):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.RightButton, point))

        self.assertEqual(canvas._front_group_id, "1")
        self.assertEqual(canvas._context_display_id, "1")
        self.assertEqual(painted_orders[0], ["2", "1"])

    def test_primary_bar_drop_target_is_painted_on_top_during_drag(self):
        canvas = self._overlapping_group_canvas()
        canvas._front_group_id = "1"
        canvas._primary_bar_dragging = True
        canvas._primary_drop_target = canvas.screens[1]

        self.assertEqual([str(screen["displayId"]) for screen in canvas._screen_paint_order()],
                         ["1", "2"])

    def test_mirror_stack_exposed_area_hits_the_same_group_target(self):
        canvas, _point, _selected = self._mirror_canvas()
        base = canvas._geometry()["1"]
        point = QPointF(base.right() + 2, base.center().y())
        self.assertEqual(str(canvas._hit_test_screen(point)["displayId"]), "1")

    def test_primary_drop_on_any_mirror_member_targets_group_master(self):
        screens = [
            dict(self.screens[0], displayId="9", name="Built-in", x=0, y=0,
                 main=True, mirrorsDisplayID="0"),
            dict(self.screens[1], displayId="1", name="Mirror master", x=1000, y=0,
                 main=False, mirrorsDisplayID="0"),
            dict(self.screens[1], displayId="3", name="Mirror member", x=1000, y=0,
                 main=False, mirrorsDisplayID="1"),
        ]
        canvas = DisplayArrangementCanvas(screens)
        canvas.resize(900, 600)
        canvas._selected_display_id = "3"
        member_rect = canvas._geometry()["3"]

        target = canvas._primary_target_at(member_rect.center())

        self.assertEqual(str(target["displayId"]), "1")

    def test_primary_bar_is_drawable_for_main_member_of_mirror_group(self):
        screens = [
            dict(self.screens[0], displayId="1", name="Mirror master", x=0, y=0,
                 main=False, mirrorsDisplayID="0"),
            dict(self.screens[1], displayId="3", name="Primary mirror member", x=0, y=0,
                 main=True, mirrorsDisplayID="1"),
        ]
        canvas = DisplayArrangementCanvas(screens)
        canvas.resize(800, 600)
        canvas._selected_display_id = "1"
        rect = canvas._geometry()["1"]
        point = QPointF(rect.center().x(), rect.top() + 4)

        with patch.object(canvas, "repaint"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))

        self.assertTrue(canvas._primary_bar_dragging)
        self.assertEqual(str(canvas._primary_drop_target["displayId"]), "1")

    def test_display_group_paint_order_is_independent_of_mirror_member_selection(self):
        canvas, _point, _selected = self._mirror_canvas()
        canvas._front_group_id = "1"
        canvas._selected_display_id = "1"

        self.assertEqual(str(canvas._screen_paint_order()[0]["displayId"]), "1")
        self.assertEqual(str(canvas._mirror_stack_members(canvas.screens[0])[0]["displayId"]), "1")

    def test_mirror_drag_away_and_back_does_not_switch_selection(self):
        canvas, point, selected = self._mirror_canvas()
        moved = QPointF(point.x() + 2, point.y())
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            canvas.mouseMoveEvent(self._mouse_event(Qt.MouseButton.NoButton, moved))
            canvas.mouseMoveEvent(self._mouse_event(Qt.MouseButton.NoButton, point))
            canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))

        self.assertEqual(selected, [])
        self.assertIsNone(canvas._selected_display_id)

    def test_mirror_drag_release_commits_position_without_click_callback(self):
        canvas, point, selected = self._mirror_canvas()
        canvas.set_snap_enabled(False)
        requested = []
        canvas.moveRequested.connect(lambda *args: requested.append(args))
        destination = QPointF(point.x() + 24, point.y() + 36)
        with patch.object(canvas, "repaint"), patch.object(canvas, "update"):
            canvas.mousePressEvent(self._mouse_event(Qt.MouseButton.LeftButton, point))
            canvas._layout_scale = 1
            canvas.mouseMoveEvent(self._mouse_event(Qt.MouseButton.NoButton, destination))
            canvas.mouseReleaseEvent(self._mouse_event(Qt.MouseButton.LeftButton, destination))
            self.app.processEvents()

        self.assertEqual((canvas.screens[0]["x"], canvas.screens[0]["y"]), (24, 36))
        self.assertEqual(requested, [("mirror:1", 24, 36, 0, 0)])
        self.assertEqual(selected, [])
        self.assertIsNone(canvas._selected_display_id)

    def test_identical_background_snapshot_does_not_repaint_canvas(self):
        snapshot = [dict(screen) for screen in self.screens]
        with patch.object(self.dialog.canvas, "update") as repaint:
            self.dialog.update_displays(snapshot, 0)
        repaint.assert_not_called()

    def test_successful_apply_uses_actual_coregraphics_origin(self):
        screens_before = self.dialog.canvas.screens
        screen_before = screens_before[1]
        screen_before["x"], screen_before["y"] = 500, 500
        actual = [
            {"displayId": "1", "x": 0, "y": 0, "width": 1000,
             "height": 800, "main": True, "mirrorsDisplayID": 0},
            {"displayId": "2", "x": 1000, "y": 760, "width": 800,
             "height": 600, "main": False, "mirrorsDisplayID": 0},
        ]
        process = SimpleNamespace(
            readAllStandardOutput=lambda: json.dumps(actual).encode(),
            readAllStandardError=lambda: b"",
            deleteLater=lambda: None,
        )
        self.dialog._apply_process = process

        with patch.object(self.dialog.canvas, "update") as repaint:
            self.dialog._arrangement_finished(0, None)

        self.assertIs(self.dialog.canvas.screens, screens_before)
        self.assertIs(self.dialog.canvas.screens[1], screen_before)
        self.assertEqual((self.dialog.canvas.screens[1]["x"],
                          self.dialog.canvas.screens[1]["y"]), (1000, 760))
        repaint.assert_called_once()

    def test_pending_apply_queue_keeps_only_latest_drop(self):
        self.dialog._apply_process = object()
        first_snapshot = [dict(screen) for screen in self.screens]
        latest_snapshot = [dict(screen) for screen in self.screens]

        self.dialog._start_arrangement_apply(
            ["2", "1200", "300"], original_screens=first_snapshot)
        self.dialog._start_arrangement_apply(
            ["2", "1800", "700"], original_screens=latest_snapshot)

        self.assertEqual(len(self.dialog._apply_queue), 1)
        self.assertEqual(self.dialog._apply_queue[0][0], ["2", "1800", "700"])
        self.assertIs(self.dialog._apply_queue[0][1], latest_snapshot)

    def test_apply_completion_does_not_resume_refresh_during_next_drag(self):
        owner = SimpleNamespace(_arrangement_interaction_active=lambda: True)

        DisplayInspectorWindow._arrangement_apply_finished(owner)

    def test_set_friendly_name_opens_editor_after_menu_hides(self):
        item = object()
        owner = SimpleNamespace(
            _friendly_name_menu_app=None,
            _friendly_name_menu=SimpleNamespace(deleteLater=lambda: None),
            _friendly_name_menu_item=item,
            _edit_friendly_name=lambda target: self.assertIs(target, item),
        )

        DisplayInspectorWindow._friendly_name_menu_hidden(owner)
        with patch("display_mode_ui.QTimer.singleShot") as schedule:
            DisplayInspectorWindow._set_context_friendly_name(owner)

        schedule.assert_called_once()
        schedule.call_args.args[1]()

    def test_drag_end_does_not_resume_refresh_while_apply_is_queued(self):
        dialog = SimpleNamespace(isVisible=lambda: True)
        finish = lambda: None
        owner = SimpleNamespace(
            arrangement_dialog=dialog,
            _arrangement_interaction_active=lambda: True,
            _finish_arrangement_interaction=finish,
        )

        with patch("display_mode_ui.QTimer.singleShot") as schedule:
            DisplayInspectorWindow._arrangement_drag_state_changed(owner, False)

        schedule.assert_called_once_with(0, finish)


if __name__ == "__main__":
    unittest.main()
