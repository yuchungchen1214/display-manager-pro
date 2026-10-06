"""Trace display routes using registry ancestry and explicit Thunderbolt identities.

Use only the active display transport's registry ancestry and identity matches.
"""
import hashlib
import json


def ancestors(service, index):
    return [index[a['path']] for a in service.get('ancestors', []) if a['path'] in index]


def props(service):
    return service.get('properties', {})


def node(service, kind, label=None):
    p = props(service)
    return dict(kind=kind, label=label or p.get('Device Model Name') or service['name'],
                serviceClass=service['class'],
                registryPath=service['path'], entryID=service.get('entryID'),
                vendor=p.get('Device Vendor Name'), model=p.get('Device Model Name'),
                uid=str(p['UID']) if p.get('UID') is not None else None)


def is_switch(service):
    return service['class'].startswith('IOThunderboltSwitch')


def external_switches(service, index):
    return [s for s in reversed(ancestors(service, index))
            if is_switch(s) and props(s).get('Depth', 0) > 0]


def fingerprint(services):
    """Ignore counters/event logs; track identity and active routing properties."""
    classes = ('IOPortTransportState', 'IOThunderboltSwitch', 'IOThunderboltPort',
               'AppleThunderboltDPInAdapter', 'AppleThunderboltDPOutAdapter')
    keys = ('Active', 'UID', 'EDID', 'Index', 'ParentBuiltInPortNumber', 'ParentBuiltInPortType',
            'Route String', 'Router ID', 'Reserved Client', 'HPD Plug State', 'DP State', 'Port Number',
            'TRM Transport ID', 'Socket ID')
    values = sorted((s['path'], s.get('entryID'), {k: props(s)[k] for k in keys if k in props(s)})
                    for s in services if s['class'].startswith(classes))
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def attach_routes(displays, services):
    index = {s['path']: s for s in services}
    for display in displays:
        connection = display.get('connectionPath', {})
        if display.get('cgBuiltIn'):
            route = dict(nodes=[], evidence=[])
            connection = display.setdefault('connectionPath', {})
            connection['route'] = route
            route.update(status='internal', nodes=[dict(kind='host', label='macOS'),
                dict(kind='display', label='Built-in display')])
            continue
        name = display.get('productName') or f"Display {display.get('displayId')}"
        endpoint = dict(kind='display', label=name, displayID=display.get('displayId'))
        transport = index.get(connection.get('transportRegistryPath'))
        association = connection.get('transportAssociation')
        if association not in ('unique-complete-EDID-match', 'unique-product-identity-match') or not transport:
            connection.pop('route', None)
            if not connection:
                display.pop('connectionPath', None)
            continue
        route = dict(nodes=[], evidence=[])
        connection['route'] = route
        p = props(transport)
        port_type = p.get('ParentBuiltInPortTypeDescription')
        port_number = p.get('ParentBuiltInPortNumber')
        port_values = [str(v) for v in (port_type, port_number) if v is not None]
        route['nodes'] = [dict(kind='host', label='macOS')]
        if port_values:
            route['nodes'].append(dict(kind='host-port', label=' '.join(port_values),
                osPortNumber=port_number, osPortType=port_type))
        detail = ('Complete EDID uniquely matches this display; host port comes from ParentBuiltInPort fields.'
                  if association == 'unique-complete-EDID-match' else
                  'The active transport EDID manufacturer/product identity uniquely matches this display; the published serial is also checked when available. Host port comes from ParentBuiltInPort fields.')
        route['evidence'].append(dict(source=transport['path'], detail=detail))
        parents = ancestors(transport, index)
        cio = [s for s in parents if s['class'] == 'IOPortTransportStateCIO' and props(s).get('Active') is True]
        if cio:
            # Closest CIO parent is the OS-reported transport owner. Its UID links
            # to the Thunderbolt switch tree without relying on enumeration order.
            owner = cio[0]
            owner_props = props(owner)
            route['evidence'].append(dict(source=owner['path'], detail='Active CIO ancestor owns this DisplayPort transport.'))
            uid = owner_props.get('UID', owner_props.get('Metadata', {}).get('UID'))
            matches = [s for s in services if is_switch(s) and uid is not None
                       and props(s).get('Depth', 0) > 0 and props(s).get('UID') == uid]
            if len(matches) == 1:
                dock = matches[0]
                chain = external_switches(dock, index) + [dock]
                route['nodes'] += [node(s, 'dock') for s in chain]
                route['evidence'].append(dict(source=dock['path'], detail='CIO UID matches one Thunderbolt switch.', uid=str(uid)))
                route['displayStreamPath'] = transport['path']
            else:
                route['nodes'].append(node(owner, 'transport', label=owner['name']))
        route['nodes'].append(endpoint)
        route['summary'] = ' → '.join(n['label'] for n in route['nodes'])


def route_text(route):
    if not route:
        return ''
    lines = ['Connection route', '']
    for i, item in enumerate(route.get('nodes', [])):
        lines.append(('  → ' if i else '') + item['label'])
        if item.get('vendor'):
            lines.append('      Manufacturer: ' + item['vendor'])
    lines += ['', 'Association evidence:']
    for item in route.get('evidence', []):
        lines += [item['detail'], item['source'], '']
    return '\n'.join(lines)
