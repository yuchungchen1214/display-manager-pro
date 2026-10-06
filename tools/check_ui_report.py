"""Offscreen UI data smoke test. Pass an existing display-report.json; no capture or desktop access."""
import json
import os
import pathlib
import sys
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import display_mode_ui as ui


def main():
    path = pathlib.Path(sys.argv[1]).resolve()
    report = json.loads(path.read_text())
    if 'displays' in report:
        from capture import summarize
        report = summarize(report)
    app = QApplication([])
    # Never change the user's saved display selection or window preferences.
    with tempfile.TemporaryDirectory() as folder:
        ui.QSettings = lambda *args: QSettings(str(pathlib.Path(folder) / 'test.ini'), QSettings.Format.IniFormat)
        window = ui.DisplayInspectorWindow()
        window._capture_complete(report, str(path.parent))
        assert window.display_list.count() == len(report['devices'])
        assert [window.tabs.tabText(i) for i in range(5)] == [
            'Overview', 'Resolution', 'Refresh rates', 'Color modes', 'All modes']
        for row, display in enumerate(report['devices']):
            window.show_display(row)
            mode_count = len(display.get('availableModes', []))
            assert window.all_page.table.rowCount() == mode_count
            modes = display.get('availableModes', [])
            current_id = (display.get('currentMode') or {}).get('modeID')
            resolution_rows = ui.resolution_groups(display)
            refresh_rows = ui.refresh_rate_groups(modes, current_id)
            color_rows = ui.color_mode_groups(modes, current_id)
            assert window.resolution_page.table.rowCount() == len(resolution_rows)
            assert window.refresh_page.table.rowCount() == len(refresh_rows)
            assert window.color_page.table.rowCount() == len(color_rows)
            framebuffer = display.get('framebufferMode') or {}
            if framebuffer.get('width') and framebuffer.get('pixelWidth'):
                current_rows = [entry for entry in resolution_rows if entry['current']]
                assert len(current_rows) == 1
                assert current_rows[0]['values'][0] == f"{framebuffer['width']} × {framebuffer['height']}"
                assert current_rows[0]['values'][1] == f"{framebuffer['pixelWidth']} × {framebuffer['pixelHeight']}"
            assert sum(int(row['values'][3]) for row in refresh_rows) == sum(
                mode.get('refreshRate') is not None for mode in modes)
            if display.get('edid'):
                assert window.edid_text.toPlainText()
            if display.get('connectionPath'):
                assert window.connection_text.toPlainText()
            if display.get('colorProfile'):
                assert window.profile_text.toPlainText()
            for source in display.get('edid', {}).get('sources', []):
                if source.get('file'):
                    assert source['file'] in window.edid_text.toPlainText()
            assert window.worker is None
        assert not window.open_folder_button.isEnabled()
        window.close()
    print(f"PASS: {len(report['devices'])} displays, diagnostics tabs, no capture started")


if __name__ == '__main__':
    main()
