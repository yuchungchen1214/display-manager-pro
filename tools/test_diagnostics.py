import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from diagnostics import (parse_edid, attach_transport, match_framebuffer,
                         parse_system_profiler_displays)


def edid(extension=True):
    base = bytearray(128)
    base[:8] = bytes.fromhex('00ffffffffffff00')
    base[8:10] = bytes.fromhex('09d1')
    base[10:12] = (42).to_bytes(2, 'little')
    base[18:20] = bytes([1, 4])
    base[20] = 0xa0
    base[126] = int(extension)
    base[127] = -sum(base) & 255
    if not extension:
        return bytes(base)
    cta = bytearray(128)
    cta[:8] = bytes([2, 3, 8, 0x30, 0xe3, 6, 0x0d, 1])
    cta[127] = -sum(cta) & 255
    return bytes(base + cta)


class DiagnosticsTests(unittest.TestCase):
    def test_system_profiler_display_metadata_uses_explicit_display_id(self):
        payload = {'SPDisplaysDataType': [{
            'spdisplays_ndrvs': [
                {'_spdisplays_displayID': '8', '_name': 'Sidecar Display',
                 'spdisplays_connection_type': 'spdisplays_airplay',
                 'spdisplays_virtualdevice': 'spdisplays_yes'},
                {'_spdisplays_displayID': '7', '_name': 'Monitor'},
                {'_name': 'Unmatched display'}]}]}
        result = parse_system_profiler_displays(payload)
        self.assertEqual(result['8']['systemProfilerName'], 'Sidecar Display')
        self.assertEqual(result['8']['systemProfilerConnectionType'], 'spdisplays_airplay')
        self.assertEqual(result['7'], {'systemProfilerName': 'Monitor',
                                      'systemProfilerDisplayID': '7'})
        self.assertEqual(set(result), {'7', '8'})

    def test_edid_validation_and_hdr_capabilities(self):
        result = parse_edid(edid())
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['manufacturer'], 'BNQ')
        self.assertEqual(result['declaredBitDepth'], 8)
        self.assertEqual(result['extensions'][0]['dataBlocks'][0]['eotfCapabilities'], ['SDR', 'PQ', 'HLG'])

    def test_truncation_and_bad_checksums_are_not_valid(self):
        self.assertEqual(parse_edid(b'bad')['status'], 'invalid')
        self.assertNotEqual(parse_edid(edid()[:128])['status'], 'valid')
        bad = bytearray(edid()); bad[20] ^= 1
        self.assertNotEqual(parse_edid(bad)['status'], 'valid')

    def test_invalid_cta_bounds_and_unknown_blocks_preserved(self):
        raw = bytearray(edid()); raw[130] = 250
        result = parse_edid(raw)
        self.assertTrue(any('CTA offset' in s for s in result['warnings']))
        raw[128] = 0x70
        self.assertEqual(parse_edid(raw)['extensions'][0]['status'], 'preserved-raw')

    def test_registry_identity_conflict_is_not_attached(self):
        record = dict(path='IOService:/display', properties={'DisplayAttributes': {'ProductAttributes': {'ProductName': 'Other'}}})
        match, info = match_framebuffer(dict(ioDisplayLocation=record['path'], productName='Monitor'), [record])
        self.assertIsNone(match)
        self.assertEqual(info['status'], 'identity-conflict')

    def test_same_edid_on_two_displays_is_not_a_unique_route(self):
        import base64
        parsed = parse_edid(edid())
        displays = [dict(displayId=i, edid={'sources': [{'parsed': parsed}]}, connectionPath={}) for i in (1, 2)]
        service = dict(path='port', name='DP', **{'class': 'IOPortTransportStateDisplayPort'},
                       properties={'Active': True, 'EDID': {'base64': base64.b64encode(edid()).decode()}}, ancestors=[])
        with tempfile.TemporaryDirectory() as folder:
            attach_transport(displays, [service], pathlib.Path(folder))
        self.assertTrue(all('connectionPath' not in d for d in displays))

    def test_unique_active_port_is_attached_and_binary_preserved(self):
        import base64
        data = edid()
        display = dict(displayId=4, edid={'sources': [{'parsed': parse_edid(data)}]},
                       connectionPath={})
        service = dict(path='port', ancestors=[], **{'class': 'IOPortTransportStateDisplayPort'},
                       properties={'Active': True, 'EDID': {'base64': base64.b64encode(data).decode()},
                                   'LaneCount': 4, 'ParentBuiltInPortNumber': 3})
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            attach_transport([display], [service], out)
            self.assertEqual((out / 'display-4-registry-edid.bin').read_bytes(), data)
        self.assertEqual(display['connectionPath']['transportAssociation'], 'unique-complete-EDID-match')
        self.assertEqual(display['connectionPath']['activeLink']['LaneCount'], 4)

    def test_hdmi_deep_color_is_bits_per_component(self):
        raw = bytearray(edid())
        raw[128:139] = bytes([2, 3, 11, 0, 0x66, 3, 12, 0, 0, 0, 0x78])
        raw[255] = -sum(raw[128:255]) & 255
        flags = parse_edid(raw)['extensions'][0]['dataBlocks'][0]['hdmiDeepColor']
        self.assertEqual(flags, dict(bpc10=True, bpc12=True, bpc16=True, ycbcr444=True))


if __name__ == '__main__':
    unittest.main()
