#!/usr/bin/env python3
"""Build the app's PNG, ICNS, and ICO icons from assets/icon.svg."""
from __future__ import annotations

import struct
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QGuiApplication


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode an icon image as PNG")
    buffer.close()
    return bytes(data)


def scaled(image: QImage, size: int) -> QImage:
    return image.scaled(QSize(size, size), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def main() -> None:
    app = QGuiApplication([])
    renderer = QSvgRenderer(str(ASSETS / "icon.svg"))
    if not renderer.isValid():
        raise RuntimeError("Could not read assets/icon.svg")

    image = QImage(QSize(1024, 1024), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    if not image.save(str(ASSETS / "icon.png"), "PNG"):
        raise RuntimeError("Could not write assets/icon.png")

    icns_chunks = bytearray()
    for chunk_type, size in ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
                             (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                             (b"ic10", 1024)):
        data = png_bytes(scaled(image, size))
        icns_chunks.extend(chunk_type + struct.pack(">I", len(data) + 8) + data)
    icns = b"icns" + struct.pack(">I", len(icns_chunks) + 8) + icns_chunks
    (ASSETS / "icon.icns").write_bytes(icns)

    ico_images = [png_bytes(scaled(image, size)) for size in (16, 24, 32, 48, 64, 128, 256)]
    offset = 6 + 16 * len(ico_images)
    header = struct.pack("<HHH", 0, 1, len(ico_images))
    entries = bytearray()
    for size, data in zip((16, 24, 32, 48, 64, 128, 256), ico_images):
        entries.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(data), offset))
        offset += len(data)
    (ASSETS / "icon.ico").write_bytes(header + entries + b"".join(ico_images))


if __name__ == "__main__":
    main()
