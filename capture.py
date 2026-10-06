#!/usr/bin/env python3
"""Read-only connection-mode capture. No BetterDisplay dependency or setters."""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys
import diagnostics

ROOT = pathlib.Path(__file__).resolve().parent
RESOURCE_ROOT = pathlib.Path(getattr(sys, "_MEIPASS", ROOT))


def helper_binary(name):
    if getattr(sys, "frozen", False):
        return RESOURCE_ROOT / name
    return ROOT / '.build' / name


def require_helper(name, source, frameworks):
    binary = helper_binary(name)
    source_path = ROOT / source
    if (binary.is_file() and (getattr(sys, "frozen", False) or not source_path.is_file() or
                              source_path.stat().st_mtime_ns <= binary.stat().st_mtime_ns)):
        return binary
    if getattr(sys, "frozen", False):
        raise FileNotFoundError(f"Bundled helper is missing: {name}")
    binary.parent.mkdir(exist_ok=True)
    subprocess.run(['clang', '-fobjc-arc', *[arg for framework in frameworks
                    for arg in ('-framework', framework)], str(ROOT / source),
                    '-o', str(binary)], check=True, timeout=60)
    return binary

def label(m):
    color = m.get('colorMode')
    names = {'RGBFullRange': 'RGB 4:4:4 Full', 'RGBLimitedRange': 'RGB 4:4:4 Limited',
             'YCbCr444LimitedRange': 'YCbCr 4:4:4 Limited', 'YCbCr444FullRange': 'YCbCr 4:4:4 Full',
             'YCbCr422LimitedRange': 'YCbCr 4:2:2 Limited', 'YCbCr422FullRange': 'YCbCr 4:2:2 Full',
             'YCbCr420LimitedRange': 'YCbCr 4:2:0 Limited', 'YCbCr420FullRange': 'YCbCr 4:2:0 Full'}
    parts = []
    if m.get('width') is not None and m.get('height') is not None:
        parts.append(f"{m['width']}×{m['height']}")
    if m.get('refreshRate') is not None:
        value = f"{float(m['refreshRate']):.4f}".rstrip("0").rstrip(".")
        parts.append(f"{value} Hz")
    if color is not None:
        parts.append(names.get(color, color))
    if m.get('bitDepth') is not None:
        parts.append(f"{m['bitDepth']} bit")
    if m.get('hdrMode') is not None:
        parts.append(str(m['hdrMode']))
    if m.get('modeID') is not None:
        parts.append(f"ID {m['modeID']}")
    return ' | '.join(parts)

def summarize(raw):
    devices, excluded = [], []
    for d in raw['displays']:
        m = d['currentMode']
        if not d.get('cgOnline') or not m.get('width') or not m.get('height'):
            excluded.append(d)
            continue
        uuid, cg_uuid = d.get('uniqueId'), d.get('cgUUID')
        device = {**d}
        io_location = (d.get('coreDisplayInfo') or {}).get('IODisplayLocation')
        if io_location is not None:
            device['ioDisplayLocation'] = io_location
        if uuid and cg_uuid:
            device['identityStatus'] = 'UUID-match' if uuid.upper() == cg_uuid.upper() else 'UUID-mismatch'
        if m.get('modeID') is not None:
            device['currentModeInAvailableModes'] = any(
                x.get('modeID') == m['modeID'] for x in d['availableModes'])
        if isinstance(d.get('snapshotStable'), bool):
            device['snapshotStable'] = d['snapshotStable']
        devices.append(device)
    return {'schemaVersion': 5, 'capturedAt': raw['capturedAt'], 'macOS': raw['macOS'],
            'source': raw['source'], 'devices': devices, 'excludedInactiveSlots': excluded,
            'limitations': ['Private API may change between macOS versions.',
                'Driver-reported state is not measurement of the HDMI signal after a hub converter.',
                'Available modes are the modes reported by CADisplay.availableModes; they are driver-reported options, not a measurement of the signal on the cable.',
                'USB/Thunderbolt inventory does not prove which hub feeds which monitor.']}

def read_display_edid(display):
    """Read EDID only for the explicitly selected display; do not persist data."""
    binary = require_helper('hardware-probe', 'tools/hardware-probe.m', ['Foundation', 'IOKit'])
    inventory_proc = subprocess.run([str(binary)], capture_output=True, timeout=20)
    if inventory_proc.returncode:
        raise RuntimeError(inventory_proc.stderr.decode(errors='replace') or 'Unable to read display registry')
    services = json.loads(inventory_proc.stdout).get('services', [])
    frame, association = diagnostics.match_framebuffer(display, services)
    result = {'status': 'unavailable', 'sources': [], 'registryAssociation': association}
    if frame:
        props = frame.get('properties', {})
        registry_data = diagnostics.decode_data(props.get('EDID'))
        if registry_data:
            result['sources'].append({'kind': 'registryEDID', 'source': frame['path'] + '/EDID',
                                      'status': 'read', 'parsed': diagnostics.parse_edid(registry_data)})
        token = diagnostics.port_token(frame['path'])
        peers = [service for service in services if token and service['class'] == 'DCPAVServiceProxy'
                 and diagnostics.port_token(service['path']) == token]
        if len(peers) == 1:
            proc = subprocess.run([str(binary), '--edid', str(peers[0]['entryID'])],
                                  capture_output=True, timeout=15)
            if proc.returncode == 0:
                raw = json.loads(proc.stdout)
                for key in ('osEDID', 'i2cEDID'):
                    entry = raw.get(key)
                    if not entry:
                        continue
                    data = diagnostics.decode_data(entry.get('data'))
                    source = {k: v for k, v in entry.items() if k != 'data'}
                    source['kind'] = key
                    if data:
                        source['parsed'] = diagnostics.parse_edid(data)
                    result['sources'].append(source)
    if result['sources']:
        result['status'] = 'read'
    return result

def capture_current_state():
    binary = require_helper('connection-probe', 'tools/connection-probe.m',
        ['Foundation', 'QuartzCore', 'ApplicationServices', 'CoreGraphics', 'ColorSync'])
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    return summarize(json.loads(result.stdout))


def capture_live_state():
    binary = require_helper('connection-probe', 'tools/connection-probe.m',
        ['Foundation', 'QuartzCore', 'ApplicationServices', 'CoreGraphics', 'ColorSync'])
    result = subprocess.run([str(binary), '--live'], capture_output=True, text=True,
                            check=True, timeout=20)
    report = summarize(json.loads(result.stdout))
    for device in report['devices']:
        device.pop('currentModeInAvailableModes', None)
    report['diagnostics'] = {'status': 'not-exported'}
    report['completedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not report['devices']:
        raise RuntimeError('No active displays were returned by the system.')
    return report


def apply_ui_context(report, ui_context=None):
    context = ui_context if isinstance(ui_context, dict) else {}
    friendly_names = context.get('friendlyNamesByDisplayId', {})
    if isinstance(friendly_names, dict):
        for device in report.get('devices', []):
            friendly_name = str(friendly_names.get(str(device.get('displayId')), '') or '').strip()
            if friendly_name:
                device['friendlyName'] = friendly_name
    if isinstance(context.get('connectionDiagnostics'), dict):
        report['connectionDiagnostics'] = context['connectionDiagnostics']
    return report


def export_capture(out, ui_context=None):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=False)
    binary = require_helper('connection-probe', 'tools/connection-probe.m',
        ['Foundation', 'QuartzCore', 'ApplicationServices', 'CoreGraphics', 'ColorSync'])
    raw_path = out / 'cadisplay-raw.json'
    subprocess.run([str(binary), '--full', str(raw_path)], check=True, timeout=30)
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    report = summarize(raw)
    try:
        report['diagnostics'] = diagnostics.collect(report, out)
    except Exception as exc:
        # Optional hardware reads must not prevent access to current/available modes.
        report['diagnostics'] = {'status': 'error', 'error': str(exc)}
    acquisition = []
    commands = [
        ('AppleCLCD2.plist', ['/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'AppleCLCD2']),
        ('DCPAVServiceProxy.plist', ['/usr/sbin/ioreg', '-a', '-l', '-r', '-c', 'DCPAVServiceProxy']),
        ('hardware.json', ['/usr/sbin/system_profiler', 'SPDisplaysDataType', 'SPUSBDataType', 'SPThunderboltDataType', '-json', '-detailLevel', 'full'])]
    for name, command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=45)
            (out / name).write_bytes(result.stdout)
            acquisition.append({'file': name, 'command': command, 'returncode': result.returncode,
                                'stderr': result.stderr.decode(errors='replace')})
        except subprocess.TimeoutExpired:
            acquisition.append({'file': name, 'status': 'timeout'})
    try:
        hardware = json.loads((out / 'hardware.json').read_text(encoding='utf-8'))
        profiler_displays = diagnostics.parse_system_profiler_displays(hardware)
        for device in report['devices']:
            device.update(profiler_displays.get(str(device.get('displayId')), {}))
    except (OSError, ValueError, TypeError):
        pass
    report['acquisition'] = acquisition
    hardware_binary = require_helper('hardware-probe', 'tools/hardware-probe.m', ['Foundation', 'IOKit'])
    after, after_record = diagnostics.run_json([hardware_binary], out / 'cadisplay-after.json', 30)
    acquisition.append(after_record)
    after_devices = {d.get('displayId'): d for d in (after or {}).get('displays', [])}
    for device in report['devices']:
        final = after_devices.get(device['displayId'])
        stable = bool(final and all(device.get(key) == final.get(key) for key in
                      ('uniqueId', 'connectionSeed', 'currentMode', 'coreDisplayInfo', 'framebufferMode', 'desktop')))
        if after is not None:
            device['diagnosticsSnapshotStable'] = stable
        if after is not None and not stable:
            device['diagnosticsWarning'] = 'Connection changed or final snapshot unavailable; recapture before trusting hardware association.'
    diagnostics.verify_routes(report, out)

    apply_ui_context(report, ui_context)

    report['completedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (out / 'display-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for device in report['devices']:
        prefix = out / ('display-' + str(device['displayId']))
        prefix.with_suffix('.json').write_text(json.dumps(device, ensure_ascii=False, indent=2), encoding='utf-8')
        prefix.with_suffix('.txt').write_text('\n'.join(label(mode) for mode in device['availableModes']), encoding='utf-8')
    lines = ['macOS Display Modes (System-Reported)', '']
    for device in report['devices']:
        display_name = device.get('friendlyName') or device.get('productName') or device.get('deviceName') or device.get('name')
        heading = f"Display {device['displayId']}"
        if display_name:
            heading += f" — {display_name}"
        if isinstance(device.get('cgBuiltIn'), bool):
            heading += ' (Built-in)' if device['cgBuiltIn'] else ' (External)'
        lines.append(heading)
        if device.get('identityStatus'):
            lines.append(f"Cross-API UUID check: {device['identityStatus']}")
        lines += ['Current: ' + label(device['currentMode']),
                  f"System-reported available modes: {len(device['availableModes'])}"]
        lines += ['  ' + label(mode) for mode in device['availableModes']]
        lines.append('')
    if not report['devices']:
        lines.append('No active displays were read. Run this from a Terminal session in the logged-in desktop; this does not prove that no external display is connected.')
    summary = '\n'.join(lines)
    (out / 'summary.txt').write_text(summary, encoding='utf-8')
    return report, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path, help='Save complete diagnostic data to a new directory')
    parser.add_argument('--get-edid', action='store_true', help='Read EDID for one display supplied on standard input')
    parser.add_argument('--connection-diagnostics', action='store_true', help='Read live connection registry data without writing files')
    parser.add_argument('--live-state', action='store_true', help='Read current modes and assigned ICC profiles without enumerating available modes')
    args = parser.parse_args()
    if args.connection_diagnostics:
        payload = json.load(sys.stdin)
        result = diagnostics.collect_connection_background(payload.get('devices', []))
        print(json.dumps({'connectionDiagnostics': result}, ensure_ascii=False))
        return 0
    if args.get_edid:
        display = json.load(sys.stdin)
        print(json.dumps({'edid': read_display_edid(display)}, ensure_ascii=False))
        return 0
    if args.live_state:
        report = capture_live_state()
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.out:
        report, summary = export_capture(args.out)
        print(summary)
        print('Complete data: ' + str(args.out))
        return 0 if report['devices'] else 2
    report = capture_current_state()
    report['diagnostics'] = {'status': 'not-exported'}
    report['completedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['devices'] else 2

if __name__ == '__main__':
    sys.exit(main())
