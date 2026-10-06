import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from desktop_modes import preferred_mode_for_resolution, resolution_groups


class DesktopModeTests(unittest.TestCase):
    def test_resolution_list_preserves_scale_variants_and_marks_current_rate(self):
        current = dict(width=1920, height=1080, pixelWidth=3840, pixelHeight=2160,
                       refreshRate=60, modeID='89', ioFlags=0)
        duplicate = dict(current, pixelWidth=1920, pixelHeight=1080, refreshRate=30, modeID='90')
        rate_variant = dict(current, refreshRate=30, modeID='92')
        other = dict(width=3840, height=2160, pixelWidth=3840, pixelHeight=2160,
                     refreshRate=60, modeID='91', ioFlags=0)
        rows = resolution_groups(dict(framebufferMode=current,
            desktopModes=[duplicate, current, rate_variant, other],
            availableModes=[dict(width=3840, height=2160, modeID='89')]))
        self.assertEqual(len(rows), 3)
        active = [r for r in rows if r['current']]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['values'], ('1920 × 1080', '2×', '3840 × 2160', '30 Hz / 60 Hz'))
        self.assertEqual(active[0]['currentRefreshRate'], '60 Hz')
        self.assertEqual({row['values'][0] for row in rows}, {'1920 × 1080', '3840 × 2160'})
        scale_variant = next(row for row in rows if row['values'][0] == '1920 × 1080' and row['values'][1] == '1×')
        self.assertEqual(scale_variant['values'][3], '30 Hz')

    def test_current_resolution_is_preserved_without_enumeration(self):
        current = dict(width=800, height=1280, pixelWidth=1600, pixelHeight=2560, refreshRate=60)
        rows = resolution_groups(dict(framebufferMode=current, availableModes=[dict(width=2560, height=1600)]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['values'], ('800 × 1280', '2×', '1600 × 2560', '60 Hz'))
        self.assertTrue(rows[0]['current'])
        self.assertEqual(resolution_groups(dict(availableModes=[dict(width=3840, height=2160)])), [])

    def test_missing_refresh_rate_does_not_affect_resolution(self):
        mode = dict(width=1920, height=1080, pixelWidth=3840, pixelHeight=2160, refreshRate=0)
        self.assertEqual(resolution_groups(dict(framebufferMode=mode))[0]['values'],
                         ('1920 × 1080', '2×', '3840 × 2160', ''))

    def test_only_modes_usable_for_desktop_and_with_exact_switch_data_are_listed(self):
        good = dict(width=1920, height=1080, pixelWidth=1920, pixelHeight=1080,
                    refreshRate=60, modeID='41', ioFlags=0, usableForDesktopGUI=True)
        gui_hint_false = dict(good, refreshRate=120, modeID='42', usableForDesktopGUI=False)
        unidentified = dict(good, refreshRate=30, modeID=None)
        row = resolution_groups({'desktopModes': [good, gui_hint_false, unidentified]})[0]
        self.assertEqual(row['refreshRateItems'], ['60 Hz'])
        self.assertEqual(row['switchModesByRate'], {'60 Hz': good})

    def test_current_unselectable_mode_remains_visible_but_not_as_an_option(self):
        current = dict(width=1920, height=1080, pixelWidth=3840, pixelHeight=2160,
                       refreshRate=60, modeID='41', ioFlags=0,
                       usableForDesktopGUI=False)
        unavailable = dict(width=2560, height=1440, pixelWidth=5120, pixelHeight=2880,
                           refreshRate=60, modeID='42', ioFlags=0,
                           usableForDesktopGUI=False)
        rows = resolution_groups({'framebufferMode': current,
                                  'desktopModes': [current, unavailable]})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['current'])
        self.assertEqual(rows[0]['refreshRateItems'], ['60 Hz'])
        self.assertEqual(rows[0]['switchModesByRate'], {})

    def test_arrange_resolution_keeps_rate_or_automatically_steps_down(self):
        mode_30 = {'refreshRate': 30, 'modeID': '30'}
        mode_60 = {'refreshRate': 60, 'modeID': '60'}
        row = {'switchModesByRate': {'30 Hz': mode_30, '60 Hz': mode_60}}
        self.assertIs(preferred_mode_for_resolution(row, 60), mode_60)
        self.assertIs(preferred_mode_for_resolution(row, 120), mode_60)
        self.assertIs(preferred_mode_for_resolution(row, 24), mode_30)
        self.assertIs(preferred_mode_for_resolution(row, None), mode_30)


if __name__ == '__main__':
    unittest.main()
