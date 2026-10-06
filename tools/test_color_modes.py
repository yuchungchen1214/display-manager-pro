import importlib.util
import pathlib
import unittest

ui_path = pathlib.Path(__file__).resolve().parents[1] / "display_mode_ui.py"
spec = importlib.util.spec_from_file_location("display_mode_ui", ui_path)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class ColorModeSwitchOptionsTests(unittest.TestCase):
    def test_connection_labels_use_explicit_metadata_and_hide_generic_other(self):
        self.assertEqual(ui.display_connection_technology({'cgBuiltIn': True,
                                                            'transportType': 'other'}),
                         'Built-in')
        self.assertEqual(ui.display_connection_interface({'cgBuiltIn': True,
                                                           'transportType': 'other'}), '-')
        sidecar = {'cgBuiltIn': False, 'transportType': 'other',
                   'systemProfilerConnectionType': 'spdisplays_airplay'}
        self.assertEqual(ui.display_connection_technology(sidecar), 'AirPlay / Sidecar')
        self.assertEqual(ui.display_connection_interface(sidecar), '-')
        self.assertEqual(ui.display_connection_interface({'cgBuiltIn': False,
                                                           'transportType': 'dp'}),
                         'DisplayPort (DP)')
        self.assertEqual(ui.display_connection_interface({'cgBuiltIn': False,
                                                           'transportType': 'other'}), '-')

    def test_original_display_name_prefers_specific_system_name_over_virtual_placeholder(self):
        self.assertEqual(ui.display_original_label({
            'cgBuiltIn': False, 'deviceName': 'Virtual-2',
            'systemProfilerName': 'Sidecar Display'}), 'Sidecar Display')
        self.assertEqual(ui.display_original_label({
            'cgBuiltIn': False, 'deviceName': 'Virtual-1',
            'systemProfilerName': 'Monitor',
            'coreDisplayInfo': {'DisplayProductName': {'en_US': 'Monitor'}}}), 'Virtual-1')

    def test_natural_sort_key_compares_numeric_parts_as_numbers(self):
        values = ["800 × 600", "3840 × 2160", "1920 × 1080"]
        self.assertEqual(
            sorted(values, key=ui.natural_sort_key, reverse=True),
            ["3840 × 2160", "1920 × 1080", "800 × 600"])

    def test_rotation_labels(self):
        self.assertEqual(ui.orientation_label(0), "0°")
        self.assertEqual(ui.orientation_label(90), "90°")
        self.assertEqual(ui.orientation_label(180), "180°")
        self.assertEqual(ui.orientation_label(270), "270°")
        self.assertEqual(ui.orientation_label(360), "0°")

    def test_color_page_only_shows_modes_available_at_current_timing(self):
        current = dict(width=3840, height=2160, refreshRate=60, colorMode="RGBFullRange",
                       bitDepth=10, hdrMode="SDR", modeID="current")
        modes = [
            current,
            dict(width=3840, height=2160, refreshRate=60, colorMode="YCbCr422LimitedRange",
                 bitDepth=10, hdrMode="SDR", modeID="same-timing-target"),
            dict(width=3840, height=2160, refreshRate=30, colorMode="RGBFullRange",
                 bitDepth=8, hdrMode="SDR", modeID="different-refresh"),
        ]

        rows = ui.color_mode_groups(modes, "current", current)
        row_by_encoding = {row["values"][0]: row for row in rows}
        self.assertEqual(sum(int(row["values"][4]) for row in rows), 2)
        targets = {row["switchMode"]["modeID"] for row in rows if row.get("switchMode")}
        self.assertEqual(targets, {"same-timing-target"})
        self.assertTrue(next(row for row in rows if row["current"]).get("switchMode") is None)
        self.assertIn("YCbCr 4:2:2", row_by_encoding)
        self.assertNotIn("RGB 4:4:4", [row["values"][0] for row in rows if row["values"][2] == "8-bit"])

    def test_missing_current_timing_disables_switch_options(self):
        mode = dict(width=1920, height=1080, refreshRate=60, colorMode="RGBFullRange",
                    bitDepth=8, hdrMode="SDR", modeID="mode")
        rows = ui.color_mode_groups([mode], "mode", {})
        self.assertTrue(all(row.get("switchMode") is None for row in rows))

    def test_color_switch_accepts_grouped_color_row(self):
        target = {"modeID": "target", "colorMode": "RGBFullRange"}
        self.assertIs(ui.color_switch_target({"switchMode": target}), target)

    def test_color_switch_accepts_exact_mode_from_arrange_menu(self):
        target = {"modeID": "target", "colorMode": "RGBFullRange"}
        self.assertIs(ui.color_switch_target(target), target)

    def test_color_switch_rejects_rows_without_an_applicable_mode(self):
        self.assertIsNone(ui.color_switch_target({"modeID": "group", "switchMode": None}))
        self.assertIsNone(ui.color_switch_target({"colorMode": "RGBFullRange"}))


class AllModeMirrorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.current = dict(modeID="current", width=1920, height=1080, refreshRate=60,
                            colorMode="RGBFullRange", bitDepth=10, hdrMode="SDR",
                            isVRR=False, colorGamut="sRGB", pixelAspectRatio=1,
                            preferredScale=1)

    def test_extended_display_can_apply_any_complete_mode(self):
        target = dict(self.current, modeID="new", width=2560, height=1440, refreshRate=75)
        self.assertEqual(ui.all_mode_availability(target, self.current, False), (True, ""))

    def test_mirror_receiver_can_change_refresh_rate_only(self):
        target = dict(self.current, modeID="new-rate", refreshRate=75)
        self.assertEqual(ui.all_mode_availability(target, self.current, True), (True, ""))

    def test_mirror_receiver_cannot_change_resolution_or_color_attributes(self):
        for change in ({"width": 2560}, {"colorMode": "YCbCr422"},
                       {"bitDepth": 8}, {"hdrMode": "HDR10"}, {"isVRR": True}):
            with self.subTest(change=change):
                target = dict(self.current, modeID="blocked", refreshRate=75, **change)
                allowed, reason = ui.all_mode_availability(target, self.current, True)
                self.assertFalse(allowed)
                self.assertEqual(reason, ui.MIRROR_RESOLUTION_UNAVAILABLE)

    def test_current_and_incomplete_rows_are_not_applicable(self):
        self.assertEqual(ui.all_mode_availability(self.current, self.current, True), (False, ""))
        incomplete = dict(self.current, modeID="missing", hdrMode=None)
        self.assertEqual(ui.all_mode_availability(incomplete, self.current, False),
                         (False, "Mode details unavailable."))

    def test_first_column_sort_values_group_current_available_and_unavailable(self):
        self.assertEqual(ui.availability_sort_value(current=True), 1)
        self.assertEqual(ui.availability_sort_value(current=False, selectable=True), 0)
        self.assertEqual(ui.availability_sort_value(current=False, selectable=False), -1)
        self.assertEqual(sorted((1, 0, -1), reverse=True), [1, 0, -1])


if __name__ == "__main__":
    unittest.main()
