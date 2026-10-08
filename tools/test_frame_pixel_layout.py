"""Regression coverage for mixed-density Frame placement."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frame_window import _pixel_layout_displays


class PixelLayoutTests(unittest.TestCase):
    def test_mixed_density_alignment_and_seams(self):
        for axis in ("x", "y"):
            for direction in (-1, 1):
                for fraction in (0, 0.25, 0.5, 1):
                    for main_id in (1, 2):
                        with self.subTest(axis=axis, direction=direction,
                                          fraction=fraction, main=main_id):
                            a = {"displayId": 1, "desktop": {
                                "x": 0, "y": 0, "width": 1512, "height": 982,
                                "main": main_id == 1}, "framebufferMode": {
                                "pixelWidth": 3024, "pixelHeight": 1964}}
                            b = {"displayId": 2, "desktop": {
                                "x": 0, "y": 0, "width": 3840, "height": 2160,
                                "main": main_id == 2}, "framebufferMode": {
                                "pixelWidth": 3840, "pixelHeight": 2160}}
                            size = "width" if axis == "x" else "height"
                            orth = "y" if axis == "x" else "x"
                            other_size = "height" if axis == "x" else "width"
                            b["desktop"][axis] = (a["desktop"][size] if direction > 0
                                                   else -b["desktop"][size])
                            b["desktop"][orth] = fraction * (
                                a["desktop"][other_size] - b["desktop"][other_size])
                            first, second = _pixel_layout_displays([a, b])
                            left, right = ((first, second) if direction > 0
                                           else (second, first))
                            self.assertAlmostEqual(left[axis] + left[size], right[axis])
                            self.assertAlmostEqual(
                                first[orth] + fraction * first[other_size],
                                second[orth] + fraction * second[other_size])
                            scale = 2 if main_id == 1 else 1
                            for original, mapped in ((a, first), (b, second)):
                                for key in ("x", "y", "width", "height"):
                                    self.assertEqual(mapped[key], original["desktop"][key] * scale)
                            self.assertEqual((first["pixelWidth"], first["pixelHeight"]), (3024, 1964))
                            self.assertEqual((second["pixelWidth"], second["pixelHeight"]), (3840, 2160))
                            if axis == "x" and direction == 1 and fraction == 1 and main_id == 1:
                                self.assertEqual((second["x"], second["y"]), (3024, -2356))


if __name__ == "__main__":
    unittest.main()
