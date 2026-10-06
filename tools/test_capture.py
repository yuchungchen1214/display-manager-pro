import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('capture', pathlib.Path(__file__).resolve().parents[1] / 'capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)

class CaptureTests(unittest.TestCase):
    def test_export_context_keeps_friendly_names_and_connection_scan(self):
        report = {'devices': [{'displayId': 7}, {'displayId': 8}]}
        connection = {'status': 'read', 'displays': {'7': {'connectionPath': {'route': []}}}}
        capture.apply_ui_context(report, {
            'friendlyNamesByDisplayId': {'7': 'Studio Display', '8': '  '},
            'connectionDiagnostics': connection,
        })
        self.assertEqual(report['devices'][0]['friendlyName'], 'Studio Display')
        self.assertNotIn('friendlyName', report['devices'][1])
        self.assertIs(report['connectionDiagnostics'], connection)

    def test_export_capture_writes_report_with_current_ui_metadata(self):
        mode = {'width': 1920, 'height': 1080, 'refreshRate': 60, 'modeID': 10}
        raw = {'displays': [{
            'displayId': 7, 'cgOnline': True, 'currentMode': mode,
            'availableModes': [mode], 'productName': 'Panel', 'desktop': {'main': True},
        }], 'capturedAt': 'now', 'macOS': 'test', 'source': 'fixture'}
        connection = {'status': 'read', 'displays': {'7': {'connectionPath': {'route': []}}}}

        def fake_run(command, **kwargs):
            if '--full' in command:
                pathlib.Path(command[-1]).write_text(json.dumps(raw), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, b'', b'')
            return subprocess.CompletedProcess(command, 0, b'fixture', b'')

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = pathlib.Path(temp_dir) / 'export'
            with patch.object(capture, 'require_helper', return_value=pathlib.Path('/fake/helper')), \
                 patch.object(capture.subprocess, 'run', side_effect=fake_run), \
                 patch.object(capture.diagnostics, 'collect', return_value={'status': 'read'}), \
                 patch.object(capture.diagnostics, 'run_json', return_value=(
                     {'displays': [raw['displays'][0]]}, {'status': 'read'})), \
                 patch.object(capture.diagnostics, 'verify_routes'):
                report, summary = capture.export_capture(destination, {
                    'friendlyNamesByDisplayId': {'7': 'Studio'},
                    'connectionDiagnostics': connection,
                })

            saved_report = json.loads((destination / 'display-report.json').read_text(encoding='utf-8'))
            saved_display = json.loads((destination / 'display-7.json').read_text(encoding='utf-8'))
            self.assertEqual(report['devices'][0]['friendlyName'], 'Studio')
            self.assertEqual(saved_report['connectionDiagnostics'], connection)
            self.assertEqual(saved_display['friendlyName'], 'Studio')
            self.assertTrue((destination / 'display-7.txt').is_file())
            self.assertTrue((destination / 'summary.txt').is_file())
            self.assertIn('Studio', summary)

    def test_offline_slots_excluded_unknown_mode_preserved(self):
        mode = dict(width=3840, height=2160, refreshRate=60, colorMode='future-format', modeID='18446744073709551615')
        d = dict(currentMode=mode, availableModes=[mode], cgOnline=True, snapshotStable=False)
        raw = dict(displays=[d, {**d, 'cgOnline': False}], capturedAt='', macOS='', source='')
        report = capture.summarize(raw)
        self.assertEqual(len(report['devices']), 1)
        self.assertIs(report['devices'][0]['snapshotStable'], False)
        self.assertNotIn('currentModeStatus', report['devices'][0])
        self.assertIn('future-format', capture.label(mode))
        self.assertEqual(report['devices'][0]['currentMode']['modeID'], '18446744073709551615')

    def test_missing_identity_and_mode_id_do_not_create_status_fields(self):
        mode = dict(width=1920, height=1080, refreshRate=60)
        display = dict(currentMode=mode, availableModes=[mode], cgOnline=True)
        report = capture.summarize(dict(displays=[display], capturedAt='', macOS='', source=''))
        result = report['devices'][0]
        self.assertNotIn('identityStatus', result)
        self.assertNotIn('currentModeInAvailableModes', result)
        self.assertNotIn('snapshotStable', result)

if __name__ == '__main__':
    unittest.main()
