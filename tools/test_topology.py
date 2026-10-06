import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from topology import attach_routes, fingerprint, route_text


def fixture():
    services = []

    def add(path, cls, properties):
        parents = sorted([s for s in services if path.startswith(s['path'] + '/')],
                         key=lambda s: len(s['path']), reverse=True)
        service = dict(path=path, name=path.split('/')[-1], entryID=str(len(services) + 1),
                       properties=properties, ancestors=[{'path': s['path']} for s in parents],
                       **{'class': cls})
        services.append(service)
        return service

    add('/controller', 'IOThunderboltControllerType5', {})
    add('/controller/root', 'IOThunderboltSwitchType5', {'Depth': 0, 'Router ID': 2})
    add('/controller/root/dock', 'IOThunderboltSwitchType3',
        {'UID': 42, 'Depth': 1, 'Route String': 1, 'Device Model Name': 'Test Dock'})
    for i, number in enumerate((8, 11)):
        add(f'/controller/root/in{i}', 'IOThunderboltPort', {'Port Number': i + 5})
        add(f'/controller/root/in{i}/adapter', 'AppleThunderboltDPInAdapterOS',
            {'Reserved Client': f'<2:0x00000001:0x{number:08x}>'})
        add(f'/controller/root/dock/out{number}', 'IOThunderboltPort', {'Port Number': number})
        add(f'/controller/root/dock/out{number}/adapter', 'AppleThunderboltDPOutAdapterCM',
            {'HPD Plug State': True})
    add('/port', 'IOPort', {})
    add('/port/cio', 'IOPortTransportStateCIO', {'UID': 42, 'Active': True, 'Device Model Name': 'Test Dock'})
    add('/port/cio/dp0', 'IOPortTransportStateDisplayPort',
        {'Active': True, 'Index': 0, 'ParentBuiltInPortNumber': 3, 'ParentBuiltInPortTypeDescription': 'USB-C'})
    display = dict(displayId=4, productName='Screen', connectionPath={
        'transportAssociation': 'unique-complete-EDID-match', 'transportRegistryPath': '/port/cio/dp0'})
    return display, services


class TopologyTests(unittest.TestCase):
    def test_dock_matches_across_registry_branches(self):
        display, services = fixture()
        attach_routes([display], services)
        route = display['connectionPath']['route']
        self.assertEqual([n['label'] for n in route['nodes'] if n['kind'] == 'dock'], ['Test Dock'])
        self.assertIn('Screen', route_text(route))

    def test_same_name_wrong_uid_cannot_match_switch(self):
        display, services = fixture()
        services[2]['properties']['UID'] = 99
        attach_routes([display], services)
        route = display['connectionPath']['route']
        self.assertFalse(any(n['kind'] == 'dock' for n in route['nodes']))
        self.assertTrue(any(n['kind'] == 'transport' for n in route['nodes']))

    def test_ambiguous_display_edid_does_not_attach_dock(self):
        display, services = fixture()
        display['connectionPath']['transportAssociation'] = 'ambiguous'
        attach_routes([display], services)
        self.assertNotIn('route', display['connectionPath'])

    def test_topology_fingerprint_detects_route_change_ignores_counters(self):
        _, services = fixture()
        original = fingerprint(services)
        changed = copy.deepcopy(services)
        changed[2]['properties']['EventLog'] = ['new event']
        self.assertEqual(original, fingerprint(changed))
        changed[2]['properties']['UID'] = 43
        self.assertNotEqual(original, fingerprint(changed))

    def test_direct_port_does_not_inherit_unrelated_dock(self):
        display, services = fixture()
        services[-1]['ancestors'] = [{'path': '/port'}]
        attach_routes([display], services)
        route = display['connectionPath']['route']
        self.assertFalse(any(n['kind'] == 'dock' for n in route['nodes']))

    def test_unmatched_display_has_no_synthesized_route(self):
        display, services = fixture()
        display['connectionPath']['transportAssociation'] = 'unmatched'
        attach_routes([display], services)
        self.assertNotIn('route', display['connectionPath'])

if __name__ == '__main__':
    unittest.main()
