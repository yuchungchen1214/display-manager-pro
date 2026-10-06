"""Read-only supplementary diagnostics; raw data and association evidence are retained."""
import base64
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from topology import attach_routes, fingerprint

ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))


def helper_binary(name):
    if getattr(sys, "frozen", False):
        return RESOURCE_ROOT / name
    return ROOT / '.build' / name


def helper_needs_build(name, source):
    binary = helper_binary(name)
    if not binary.is_file():
        return True
    if getattr(sys, "frozen", False):
        return False
    source_path = ROOT / source
    return source_path.is_file() and source_path.stat().st_mtime_ns > binary.stat().st_mtime_ns


def decode_data(value):
    if not isinstance(value, dict) or not isinstance(value.get('base64'), str):
        return b''
    try:
        return base64.b64decode(value['base64'], validate=True)
    except ValueError:
        return b''


def parse_edid(data):
    """Decode base identity and bounded CTA blocks, preserving unsupported payloads."""
    result = dict(byteLength=len(data), sha256=hashlib.sha256(data).hexdigest(),
                  sourceMeaning='Capabilities advertised to the host; not current output state',
                  warnings=[], blocks=[])
    if len(data) < 128:
        return {**result, 'status': 'invalid', 'warnings': ['Base block is incomplete']}
    result['headerValid'] = data[:8] == bytes.fromhex('00ffffffffffff00')
    expected = 128 * (data[126] + 1)
    result.update(extensionCount=data[126], expectedBytes=expected, complete=len(data) >= expected)
    if not result['headerValid']:
        result['warnings'].append('Invalid EDID header')
    if len(data) != expected:
        result['warnings'].append('Byte length differs from advertised block count')
    for pos in range(0, len(data) // 128 * 128, 128):
        block = data[pos:pos + 128]
        result['blocks'].append(dict(index=pos // 128, tag=block[0], checksumValid=sum(block) % 256 == 0))
    result['status'] = ('valid' if result['headerValid'] and len(data) == expected and
                        all(b['checksumValid'] for b in result['blocks']) else 'invalid-or-incomplete')
    if not result['headerValid']:
        return result
    vendor = int.from_bytes(data[8:10], 'big')
    result.update(manufacturer=''.join(chr(64 + ((vendor >> shift) & 31)) for shift in (10, 5, 0)),
                  vendorID=vendor, productID=int.from_bytes(data[10:12], 'little'),
                  serialNumber=int.from_bytes(data[12:16], 'little'),
                  week=data[16], year=1990 + data[17], version=f'{data[18]}.{data[19]}',
                  digital=bool(data[20] & 128), gamma=None if data[23] == 255 else (data[23] + 100) / 100,
                  descriptors=[], detailedTimings=[], extensions=[])
    if data[18:20] >= bytes([1, 4]) and result['digital']:
        result['declaredBitDepth'] = {1: 6, 2: 8, 3: 10, 4: 12, 5: 14, 6: 16}.get((data[20] >> 4) & 7)
    def timing(block):
        clock = int.from_bytes(block[:2], 'little') * 10000
        if not clock:
            return
        w = block[2] | ((block[4] >> 4) << 8)
        h = block[5] | ((block[7] >> 4) << 8)
        hb = block[3] | ((block[4] & 15) << 8)
        vb = block[6] | ((block[7] & 15) << 8)
        total = (w + hb) * (h + vb)
        result['detailedTimings'].append(dict(width=w, height=h, pixelClockHz=clock,
            horizontalBlanking=hb, verticalBlanking=vb, interlaced=bool(block[17] & 128),
            frameRateHz=clock / total if total else None))
    for offset in range(54, 126, 18):
        block = data[offset:offset + 18]
        if block[:2] != b'\0\0':
            timing(block)
        else:
            descriptor = dict(tag=block[3], hex=block.hex())
            if block[3] in (0xfc, 0xff, 0xfe):
                descriptor['text'] = block[5:18].decode('ascii', errors='replace').strip(' \n\x00')
                if block[3] == 0xfc:
                    result['productName'] = descriptor['text']
                elif block[3] == 0xff:
                    result['serialText'] = descriptor['text']
            result['descriptors'].append(descriptor)
    for index in range(1, min(len(data) // 128, data[126] + 1)):
        block = data[index * 128:(index + 1) * 128]
        extension = dict(index=index, tag=block[0], hex=block.hex())
        result['extensions'].append(extension)
        if block[0] != 2:
            extension['status'] = 'preserved-raw'
            continue
        extension.update(type='CTA-861', revision=block[1], ycbcr444=bool(block[3] & 32),
                         ycbcr422=bool(block[3] & 16), basicAudio=bool(block[3] & 64), dataBlocks=[])
        end = block[2]
        if end == 0:
            continue
        if not 4 <= end <= 127:
            result['warnings'].append(f'Invalid CTA offset in block {index}')
            continue
        pos = 4
        while pos < end:
            tag, count = block[pos] >> 5, block[pos] & 31
            pos += 1
            if pos + count > end:
                result['warnings'].append(f'Truncated CTA data block {index}')
                break
            payload = block[pos:pos + count]; pos += count
            item = dict(tag=tag, hex=payload.hex())
            extension['dataBlocks'].append(item)
            if tag == 2:
                item['videoCodesRaw'] = list(payload)
            if tag == 3 and len(payload) >= 3:
                item['vendorOUI'] = int.from_bytes(payload[:3], 'little')
                if item['vendorOUI'] == 0x000c03 and len(payload) >= 6:
                    item['hdmiDeepColor'] = dict(bpc10=bool(payload[5] & 16), bpc12=bool(payload[5] & 32),
                        bpc16=bool(payload[5] & 64), ycbcr444=bool(payload[5] & 8))
            if tag == 7 and payload:
                item['extendedTag'] = payload[0]
                if payload[0] == 6 and len(payload) >= 3:
                    item['eotfCapabilities'] = [name for bit, name in enumerate(('SDR', 'Traditional HDR', 'PQ', 'HLG')) if payload[1] & (1 << bit)]
                    item['staticMetadataDescriptorBits'] = payload[2]
                    item['luminanceCodesRaw'] = list(payload[3:6])
                elif payload[0] == 5:
                    item['colorimetryBitsRaw'] = list(payload[1:])
                elif payload[0] in (14, 15):
                    item['ycbcr420DataRaw'] = list(payload[1:])
        for pos in range(end, 110, 18):
            timing(block[pos:pos + 18])
    return result


def run_json(command, target, timeout):
    record = dict(command=[str(x) for x in command], file=target.name,
                  startedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
    try:
        proc = subprocess.run(record['command'], capture_output=True, timeout=timeout)
        target.write_bytes(proc.stdout)
        record.update(returnCode=proc.returncode, stderr=proc.stderr.decode(errors='replace'))
        if proc.returncode:
            return None, {**record, 'status': 'error'}
        value = json.loads(proc.stdout)
        return value, {**record, 'status': 'read'}
    except subprocess.TimeoutExpired:
        return None, {**record, 'status': 'timeout'}
    except (ValueError, OSError) as exc:
        return None, {**record, 'status': 'error', 'error': str(exc)}


def port_token(path):
    match = re.search(r'/(disp(?:ext)?\d+)[@:]', path or '')
    return match.group(1) if match else None


def evidence_fields(value, prefix=''):
    """Return named evidence with property paths; capabilities are never called active state."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f'{prefix}/{key}'
            if re.search(r'dpcd|dsc|link.?rate|lane.?count|bandwidth|hdcp|eotf|dither|cec|scdc|tmds|frl', key, re.I):
                found.append(dict(property=path, value=item, interpretation='raw-driver-property'))
            elif isinstance(item, (dict, list)):
                found.extend(evidence_fields(item, path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(evidence_fields(item, f'{prefix}/{i}'))
    return found


def match_framebuffer(display, services):
    path = display.get('ioDisplayLocation')
    matches = [s for s in services if s['path'] == path] if path else []
    if len(matches) != 1:
        return None, dict(status='unmatched', reason='No unique registry entry at CoreDisplay path')
    framebuffer = matches[0]
    product = framebuffer['properties'].get('DisplayAttributes', {}).get('ProductAttributes', {})
    expected, actual = display.get('productName'), product.get('ProductName')
    if expected and actual and expected.casefold() != actual.casefold():
        return None, dict(status='identity-conflict', reason='CADisplay and registry product names differ',
                          registryPath=path, registryProductName=actual)
    return framebuffer, dict(status='path-matched', source='CoreDisplay.IODisplayLocation',
                              productNameCorroborated=bool(expected and actual))


def parse_system_profiler_displays(payload):
    """Index explicitly reported display metadata by CoreGraphics display ID."""
    result = {}
    for gpu in payload.get('SPDisplaysDataType', []) if isinstance(payload, dict) else []:
        if not isinstance(gpu, dict):
            continue
        for record in gpu.get('spdisplays_ndrvs', []):
            if not isinstance(record, dict):
                continue
            display_id = record.get('_spdisplays_displayID') or record.get('_spdisplays_CGSDID')
            if display_id is None:
                continue
            detail = {}
            for source_key, target_key in (
                    ('_name', 'systemProfilerName'),
                    ('spdisplays_connection_type', 'systemProfilerConnectionType'),
                    ('spdisplays_display_type', 'systemProfilerDisplayType'),
                    ('spdisplays_virtualdevice', 'systemProfilerVirtualDevice')):
                value = record.get(source_key)
                if value not in (None, '', 'spdisplays_no'):
                    detail[target_key] = value
            if detail:
                detail['systemProfilerDisplayID'] = str(display_id)
                result[str(display_id)] = detail
    return result


def collect_connection_background(displays):
    """Read deferred CoreGraphics and registry details without EDID getters or files."""
    display_details = {}
    # system_profiler publishes some display technologies (for example AirPlay)
    # that CADisplay.transportType reports only as "other". Match only by its
    # explicit CoreGraphics display ID; never infer a physical route from a
    # nearby USB/Thunderbolt device.
    try:
        profiler = subprocess.run(
            ['/usr/sbin/system_profiler', 'SPDisplaysDataType', '-json', '-detailLevel', 'mini'],
            capture_output=True, timeout=30)
        if profiler.returncode == 0:
            profiler_displays = parse_system_profiler_displays(json.loads(profiler.stdout))
            display_details.update(profiler_displays)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        pass
    display_binary = helper_binary('connection-probe')
    if helper_needs_build('connection-probe', 'tools/connection-probe.m'):
        if getattr(sys, "frozen", False):
            raise FileNotFoundError('Bundled helper is missing: connection-probe')
        display_binary.parent.mkdir(exist_ok=True)
        command = ['clang', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'QuartzCore',
                   '-framework', 'ApplicationServices', '-framework', 'CoreGraphics',
                   '-framework', 'ColorSync', str(ROOT / 'tools/connection-probe.m'),
                   '-o', str(display_binary)]
        subprocess.run(command, check=True, timeout=60)
    display_proc = subprocess.run([str(display_binary), '--full'], capture_output=True, timeout=50)
    if display_proc.returncode == 0:
        for record in json.loads(display_proc.stdout).get('displays', []):
            profile = record.get('colorProfile')
            if profile:
                profile = dict(profile)
                profile_data = decode_data(profile.pop('data', None))
                if profile_data:
                    profile['byteLength'] = len(profile_data)
                    profile['sha256'] = hashlib.sha256(profile_data).hexdigest()
                record['colorProfile'] = profile
            detail = {'desktopModesStatus': 'read' if isinstance(record.get('desktopModes'), list)
                      else 'unavailable', 'colorProfile': profile}
            if isinstance(record.get('desktopModes'), list):
                detail['desktopModes'] = record['desktopModes']
            display_details.setdefault(str(record.get('displayId')), {}).update(detail)
    binary = helper_binary('hardware-probe')
    if helper_needs_build('hardware-probe', 'tools/hardware-probe.m'):
        if getattr(sys, "frozen", False):
            raise FileNotFoundError('Bundled helper is missing: hardware-probe')
        binary.parent.mkdir(exist_ok=True)
        command = ['clang', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'IOKit',
                   str(ROOT / 'tools/hardware-probe.m'), '-o', str(binary)]
        subprocess.run(command, check=True, timeout=60)
    proc = subprocess.run([str(binary)], capture_output=True, timeout=25)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode(errors='replace') or 'IORegistry inventory failed')
    inventory = json.loads(proc.stdout)
    services = inventory.get('services', [])
    by_display = {}
    for display in displays:
        frame, association = match_framebuffer(display, services)
        entry = {'registryAssociation': association}
        if frame:
            token = port_token(frame['path'])
            props = frame.get('properties', {})
            peers = [service for service in services if token and service['class'] == 'DCPAVServiceProxy'
                     and port_token(service['path']) == token]
            connection = dict(status='driver-path-read', registryPath=frame['path'],
                registryEntryID=frame['entryID'], dcpToken=token,
                transport=props.get('Transport'), ancestors=frame.get('ancestors', []),
                ioavCandidates=[dict(path=s['path'], entryID=s['entryID']) for s in peers],
                explanation='AppleCLCD2 transport metadata reported by macOS.')
            evidence = evidence_fields(props)
            if evidence:
                connection['linkEvidence'] = evidence
            entry['connectionPath'] = connection
            entry['displayAttributes'] = props.get('DisplayAttributes', {})
        by_display[str(display.get('displayId'))] = entry
    active_transports = []
    transport_keys = ('Active', 'LinkRate', 'LinkRateDescription', 'LaneCount', 'MaxLaneCount',
                      'SinkCount', 'Tunneled', 'TransportDescription', 'ParentBuiltInPortNumber',
                      'ParentBuiltInPortTypeDescription', 'SinkDeviceID', 'SinkDeviceOUI')
    for service in services:
        if service['class'] != 'IOPortTransportStateDisplayPort':
            continue
        props = service.get('properties', {})
        if props.get('Active') is not True:
            continue
        edid_bytes = decode_data(props.get('EDID'))
        parsed_edid = parse_edid(edid_bytes) if edid_bytes else {}
        active_transports.append(dict(path=service['path'], entryID=service['entryID'],
            properties={key: props[key] for key in transport_keys if key in props},
            ancestors=service.get('ancestors', []), _parsedEDID=parsed_edid))
    # Match on exact manufacturer/product identity, using serial as an additional
    # discriminator when both sides publish one. Require a one-to-one result.
    proposed = {}
    for display in displays:
        if display.get('cgBuiltIn'):
            continue
        connection = by_display.get(str(display.get('displayId')), {}).get('connectionPath')
        if not connection:
            continue
        attrs = by_display[str(display.get('displayId'))].get('displayAttributes', {})
        product = attrs.get('ProductAttributes', {})
        manufacturer = product.get('ManufacturerID')
        legacy_vendor = product.get('LegacyManufacturerID', display.get('cgVendorID'))
        product_id = product.get('ProductID', display.get('cgProductID'))
        serial = product.get('SerialNumber', display.get('cgSerialNumber'))
        matches = []
        for index, transport in enumerate(active_transports):
            parsed = transport.get('_parsedEDID', {})
            if parsed.get('status') != 'valid' or product_id is None:
                continue
            vendor_matches = ((manufacturer is not None and parsed.get('manufacturer') == manufacturer) or
                              (legacy_vendor is not None and parsed.get('vendorID') == int(legacy_vendor)))
            if not vendor_matches or parsed.get('productID') != int(product_id):
                continue
            transport_serial = parsed.get('serialNumber')
            if serial not in (None, 0, '0') and transport_serial not in (None, 0) and int(serial) != int(transport_serial):
                continue
            matches.append(index)
        if len(matches) == 1:
            proposed[str(display.get('displayId'))] = matches[0]
    counts = {}
    for index in proposed.values():
        counts[index] = counts.get(index, 0) + 1
    for display in displays:
        did = str(display.get('displayId'))
        index = proposed.get(did)
        if index is None or counts[index] != 1:
            continue
        transport = active_transports[index]
        props = transport['properties']
        connection = by_display[did].get('connectionPath')
        if not connection:
            continue
        connection.update(transportAssociation='unique-product-identity-match',
            transportRegistryPath=transport['path'], transportAncestors=transport['ancestors'],
            osPortNumber=props.get('ParentBuiltInPortNumber'),
            osPortType=props.get('ParentBuiltInPortTypeDescription'),
            activeLink={key: props.get(key) for key in transport_keys if key in props})
        transport['assignedDisplayID'] = display.get('displayId')
    route_displays = []
    for display in displays:
        detail = by_display.get(str(display.get('displayId')), {})
        route_displays.append({**display, **detail})
    attach_routes(route_displays, services)
    for routed in route_displays:
        by_display[str(routed.get('displayId'))]['connectionPath'] = routed.get('connectionPath', {})
    for transport in active_transports:
        transport.pop('_parsedEDID', None)
    peripherals = [service for service in services if any(token in service['class']
        for token in ('USBHostDevice', 'Thunderbolt', 'TypeC', 'IOPort'))]
    return dict(status='read' if inventory.get('status') == 'read' else 'error',
        displayDetails=display_details,
        displays=by_display, activeDisplayPortTransports=active_transports,
        peripheralInventory=peripherals,
        note='Display-to-transport links are reported only when system-published manufacturer/product identity yields a unique one-to-one match.')


def attach_transport(displays, services, out):
    """Only attach a port when a complete EDID matches one display and one active port."""
    def hashes(display):
        return {s['parsed']['sha256'] for s in display.get('edid', {}).get('sources', [])
                if s.get('parsed', {}).get('status') == 'valid'}
    for display in displays:
        own = hashes(display)
        candidates = []
        for service in services:
            if service['class'] != 'IOPortTransportStateDisplayPort' or not service['properties'].get('Active'):
                continue
            data = decode_data(service['properties'].get('EDID'))
            if data and hashlib.sha256(data).hexdigest() in own:
                candidates.append(service)
        duplicate = any(other is not display and own & hashes(other) for other in displays)
        if len(candidates) != 1 or duplicate:
            if not display.get('connectionPath'):
                display.pop('connectionPath', None)
            continue
        connection = display.setdefault('connectionPath', {})
        service = candidates[0]; props = service['properties']
        connection.update(transportAssociation='unique-complete-EDID-match',
            transportRegistryPath=service['path'], transportAncestors=service['ancestors'],
            osPortNumber=props.get('ParentBuiltInPortNumber'), osPortType=props.get('ParentBuiltInPortTypeDescription'),
            activeLink={k: props.get(k) for k in ('Active', 'LinkRate', 'LinkRateDescription', 'LaneCount',
                'MaxLaneCount', 'SinkCount', 'Tunneled', 'TransportDescription', 'SinkDeviceID', 'SinkDeviceOUI')})
        data = decode_data(props.get('EDID'))
        filename = f'display-{display["displayId"]}-registry-edid.bin'
        (out / filename).write_bytes(data)
        display['edid']['sources'].append(dict(kind='registryEDID', source=service['path'] + '/EDID',
            status='read', file=filename, parsed=parse_edid(data)))


def collect(report, out):
    """Optional failures cannot discard the successfully acquired connection modes."""
    binary = helper_binary('hardware-probe')
    records = []
    if helper_needs_build('hardware-probe', 'tools/hardware-probe.m'):
        if getattr(sys, "frozen", False):
            return dict(status='unavailable', error='Bundled helper is missing: hardware-probe')
        command = ['clang', '-fobjc-arc', '-framework', 'Foundation', '-framework', 'IOKit',
                   str(ROOT / 'tools/hardware-probe.m'), '-o', str(binary)]
        try:
            built = subprocess.run(command, capture_output=True, timeout=60)
            records.append(dict(command=command, status='read' if built.returncode == 0 else 'error',
                                returnCode=built.returncode, stderr=built.stderr.decode(errors='replace')))
            if built.returncode:
                return dict(status='unavailable', acquisition=records)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return dict(status='unavailable', error=str(exc))
    inventory, acquisition = run_json([binary], out / 'registry-inventory.json', 20)
    records.append(acquisition)
    if inventory is None:
        return dict(status='unavailable', acquisition=records)
    services = inventory.get('services', [])
    results = {}
    for service in services:
        if service['class'] != 'DCPAVServiceProxy' or service['properties'].get('Location') != 'External':
            continue
        ident = service['entryID']
        value, acquisition = run_json([binary, '--edid', ident], out / f'ioav-{ident}.json', 12)
        records.append(acquisition)
        results[ident] = value or {'status': acquisition['status']}
        for key in ('osEDID', 'i2cEDID'):
            entry = results[ident].get(key)
            if not entry:
                continue
            data = decode_data(entry.get('data'))
            if data:
                filename = f'edid-{ident}-{key}.bin'
                (out / filename).write_bytes(data)
                entry.update(file=filename, parsed=parse_edid(data))
    (out / 'edid-report.json').write_text(json.dumps(results, indent=2))
    for display in report['devices']:
        frame, association = match_framebuffer(display, services)
        display['registryAssociation'] = association
        display['edid'] = dict(status='unavailable', sources=[])
        if frame:
            token = port_token(frame['path'])
            peers = [s for s in services if token and s['class'] == 'DCPAVServiceProxy' and port_token(s['path']) == token]
            props = frame['properties']
            display['displayAttributes'] = props.get('DisplayAttributes', {})
            display['connectionPath'] = dict(status='driver-path-read', registryPath=frame['path'],
                registryEntryID=frame['entryID'], dcpToken=token, transport=props.get('Transport'),
                ancestors=frame['ancestors'],
                ioavCandidates=[dict(path=p['path'], entryID=p['entryID']) for p in peers],
                explanation='AppleCLCD2 transport metadata reported by macOS.')
            link_evidence = evidence_fields(props)
            if link_evidence:
                display['linkEvidence'] = link_evidence
            # Token matching links driver services; retain ambiguity instead of picking one.
            if len(peers) == 1:
                result = results.get(peers[0]['entryID'], {})
                sources = [{**result[k], 'kind': k} for k in ('osEDID', 'i2cEDID') if k in result]
                hashes = [x['parsed']['sha256'] for x in sources if x.get('parsed', {}).get('status') == 'valid']
                display['edid'] = dict(status='read' if any(x.get('parsed') for x in sources) else result.get('status', 'unavailable'),
                    sources=sources, association='unique-DCP-token', serviceEntryID=peers[0]['entryID'],
                    sourcesAgree=(len(set(hashes)) == 1) if len(hashes) > 1 else None)
            elif len(peers) > 1:
                display['edid']['status'] = 'ambiguous-service'
        profile = display.get('colorProfile', {})
        data = decode_data(profile.pop('data', None))
        if data:
            name = f'display-{display["displayId"]}-profile.icc'
            (out / name).write_bytes(data)
            profile.update(file=name, byteLength=len(data), sha256=hashlib.sha256(data).hexdigest())
    attach_transport(report['devices'], services, out)
    attach_routes(report['devices'], services)
    routes = {str(d['displayId']): d['connectionPath']['route'] for d in report['devices']
              if 'route' in d.get('connectionPath', {})}
    if routes:
        (out / 'connection-routes.json').write_text(json.dumps(routes, indent=2))
    peripherals = [s for s in services if 'USBHostDevice' in s['class'] or 'Thunderbolt' in s['class'] or 'TypeC' in s['class'] or 'IOPort' in s['class']]
    return dict(status='read', acquisition=records, edidServices=results,
        topologyFingerprint=fingerprint(services),
        peripheralInventory=peripherals, inventoryFile='registry-inventory.json')


def verify_routes(report, out):
    before = report.get('diagnostics', {}).get('topologyFingerprint')
    if before is None:
        return
    inventory, record = run_json([helper_binary('hardware-probe')], out / 'topology-after.json', 20)
    report['diagnostics']['acquisition'].append(record)
    stable = (fingerprint(inventory['services']) == before
              if inventory and inventory.get('status') == 'read' else None)
    if stable is not None:
        report['diagnostics']['topologySnapshotStable'] = stable
    for display in report['devices']:
        route = display.get('connectionPath', {}).get('route', {})
        if route and stable is not None:
            route['snapshotStable'] = stable
    routes = {str(d['displayId']): d['connectionPath']['route'] for d in report['devices']
              if 'route' in d.get('connectionPath', {})}
    if routes:
        (out / 'connection-routes.json').write_text(json.dumps(routes, indent=2))
