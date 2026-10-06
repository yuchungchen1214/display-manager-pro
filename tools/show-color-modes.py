#!/usr/bin/env python3
"""Summarize current and available display color modes from a probe capture."""
import json
import sys
from pathlib import Path

ENCODINGS = {
    0: "RGB",
    2: "YCbCr 4:2:2",
    3: "YCbCr 4:4:4",
}
RANGES = {0: "Full", 1: "Limited"}
EOTFS = {0: "SDR", 1: "Gamma", 2: "HDR10/PQ", 3: "HLG (provisional)"}


def value(obj, key):
    try:
        return int(obj.get(key))
    except (TypeError, ValueError):
        return None


def format_color(color):
    encoding = value(color, "PixelEncoding")
    depth = value(color, "Depth")
    dynamic_range = value(color, "DynamicRange")
    eotf = value(color, "EOTF")
    parts = [ENCODINGS.get(encoding, f"PixelEncoding {encoding}")]
    if depth is not None:
        parts.append(f"{depth}-bit")
    if dynamic_range is not None:
        parts.append(RANGES.get(dynamic_range, f"range={dynamic_range}"))
    if eotf is not None:
        parts.append(EOTFS.get(eotf, f"EOTF={eotf}"))
    return " ".join(parts)


def color_key(color):
    return tuple(value(color, key) for key in ("PixelEncoding", "Depth", "DynamicRange", "EOTF", "Colorimetry"))


def timing_refresh(timing):
    vertical = timing.get("VerticalAttributes", {})
    fixed = vertical.get("PreciseSyncRate") or vertical.get("SyncRate")
    try:
        rate = int(fixed) / 65536
        return rate if rate > 0 else None
    except (TypeError, ValueError):
        return None


def timing_label(timing):
    h = timing.get("HorizontalAttributes", {}).get("Active", "?")
    v = timing.get("VerticalAttributes", {}).get("Active", "?")
    rate = timing_refresh(timing)
    return f"{h}x{v}" + (f" @ {rate:.2f} Hz" if rate else "")


def color_from_link(link):
    # WindowServer LinkDescription uses BitDepth rather than AppleCLCD2's Depth.
    return {
        "PixelEncoding": link.get("PixelEncoding"),
        "Depth": link.get("BitDepth"),
        "DynamicRange": link.get("Range"),
        "EOTF": link.get("EOTF"),
    }


def active_link_descriptions(report):
    """Match persisted WindowServer configurations against the captured active displays."""
    root = report.get("windowServerDisplayConfig", {})
    groups = []
    for set_name in ("DisplayAnyUserSets", "DisplaySets"):
        for config in root.get(set_name, {}).get("Configs", []):
            entries = config.get("DisplayConfig", [])
            signature = []
            for entry in entries:
                info = entry.get("CurrentInfo", {})
                rotation = int(entry.get("Rotation", 0) or 0) % 180
                width, height = int(info.get("Wide", 0)), int(info.get("High", 0))
                if rotation:
                    width, height = height, width
                signature.append((width, height, round(float(info.get("Hz", 0)), 2)))
            groups.append((tuple(sorted(signature)), entries, set_name))

    active_signature = tuple(sorted(
        (int(d.get("currentMode", {}).get("width", 0)),
         int(d.get("currentMode", {}).get("height", 0)),
         round(float(d.get("currentMode", {}).get("refreshRate", 0)), 2))
        for d in report.get("displays", []) if d.get("isActive")
    ))
    # A stale saved layout may contain all active modes plus historical displays.
    # Only an exact topology match is eligible to describe the current link.
    matches = [(entries, set_name) for signature, entries, set_name in groups if signature == active_signature]
    by_identity = {}
    for entries, set_name in matches:
        for entry in entries:
            link = entry.get("LinkDescription")
            if not link:
                continue
            # LinkDescription is only safely attributed by active timing and rotation;
            # retain UUID/config provenance and avoid guessing a monitor from UUID.
            key = (int(entry.get("CurrentInfo", {}).get("Wide", 0)),
                   int(entry.get("CurrentInfo", {}).get("High", 0)),
                   round(float(entry.get("CurrentInfo", {}).get("Hz", 0)), 2),
                   int(entry.get("Rotation", 0) or 0) % 180)
            by_identity.setdefault(key, []).append({
                "link": link, "uuid": entry.get("UUID"), "set": set_name
            })

    result = {}
    for display in report.get("displays", []):
        if not display.get("isActive") or display.get("isBuiltIn"):
            continue
        current = display.get("currentMode", {})
        key_matches = []
        for key, candidates in by_identity.items():
            width, height, hz, rotation = key
            expected = (height, width) if rotation else (width, height)
            actual = (int(current.get("width", 0)), int(current.get("height", 0)))
            if expected == actual and abs(hz - float(current.get("refreshRate", 0))) < 0.02:
                key_matches.extend(candidates)
        distinct = {json.dumps(item["link"], sort_keys=True) for item in key_matches}
        if len(distinct) == 1:
            selected = key_matches[0]
            result[display.get("displayID")] = {
                "status": "candidate", "color": color_from_link(selected["link"]),
                "source": "WindowServer LinkDescription", "uuid": selected["uuid"],
                "configSets": sorted({item["set"] for item in key_matches}),
            }
        elif len(distinct) > 1:
            result[display.get("displayID")] = {"status": "conflict", "source": "WindowServer LinkDescription"}
        else:
            result[display.get("displayID")] = {"status": "unavailable", "source": "WindowServer LinkDescription"}
    return result


def parameter_value(parameters, key):
    entry = parameters.get(key)
    if isinstance(entry, dict):
        return entry.get("value", entry)
    return entry


def main(report_path):
    report = json.loads(Path(report_path).read_text())
    services = report.get("ioRegistry", {}).get("AppleCLCD2", [])
    displays = [d for d in report.get("displays", []) if not d.get("isBuiltIn")]
    current_links = active_link_descriptions(report)
    if not services:
        raise SystemExit("No AppleCLCD2 data. Create a capture without --no-ioregistry.")

    print("AppleCLCD2 color descriptors for each active physical timing")
    print("These raw descriptors are not guaranteed to equal BetterDisplay's filtered compatible-mode list.")
    print("Current link mode is shown only when macOS exposes an unambiguous active LinkDescription.")
    print("Encoding 0/2/3 and range 0/1 are cross-checked against BetterDisplay captures; raw values are preserved.")
    service_identity = {}
    service_by_port = {}
    service_by_dcp_index = {}
    service_by_location = {}
    for service_index, service in enumerate(services):
        props = service.get("properties", {})
        attrs = props.get("DisplayAttributes", {}).get("ProductAttributes", {})
        if attrs:
            service_identity.setdefault((value(attrs, "LegacyManufacturerID"), value(attrs, "ProductID")), []).append((service_index, attrs))
        registry = service.get("registry", {})
        path = registry.get("properties", {}).get("IODisplayLocation")
        if isinstance(path, str):
            service_by_location[path] = (service_index, attrs)
        service_path = service.get("servicePath", "")
        # AppleCLCD2's DCPIndex is the stable counterpart of dispextN.
        # Use the IORegistry service path to retain it in the capture and
        # avoid merging identical EDIDs on separate outputs.
        port = next((part.split(":", 1)[0] for part in service_path.split("/")
                     if part.startswith("dispext") and ":" in part), None)
        if port is not None:
            service_by_port.setdefault(port, []).append((service_index, attrs))
        dcp_index = value(props, "DCPIndex")
        if dcp_index is not None:
            service_by_dcp_index.setdefault(dcp_index, []).append((service_index, attrs))
    for display in displays:
        identity = (value(display, "vendorNumber"), value(display, "modelNumber"))
        location = display.get("ioDisplayLocation")
        port = next((part.split("@", 1)[0] for part in location.split("/")
                     if part.startswith("dispext") and "@" in part), None) if isinstance(location, str) else None
        hits = service_by_port.get(port, []) if port is not None else []
        if not hits and port is not None:
            # AppleCLCD2 uses DCPIndex 0 for the built-in panel on this
            # platform; external dispextN paths map to DCPIndex N+1.
            try:
                port_number = int(port.removeprefix("dispext"))
                hits = service_by_dcp_index.get(port_number + 1, [])
            except ValueError:
                pass
        if isinstance(location, str):
            exact = service_by_location.get(location)
            if exact:
                hits = [exact]
        # EDID identity is only a fallback when the report predates service paths.
        if not hits:
            hits = [hit for key, records in service_identity.items() if identity == key for hit in records]
        # Keep duplicates visible only when no Apple display port was available.
        print(f"\nExternal display ID {display.get('displayID')}: "
              f"{hits[0][1].get('ProductName') if hits else 'AppleCLCD2 identity unavailable'}")
        if location:
            print(f"Apple display location: {location}")
        mode = display.get("currentMode", {})
        print(f"Desktop timing: {mode.get('width')}x{mode.get('height')} logical; "
              f"physical output: {mode.get('pixelWidth')}x{mode.get('pixelHeight')} @ {mode.get('refreshRate')} Hz")
        link = current_links.get(display.get("displayID"), {"status": "unavailable"})
        if link["status"] == "candidate":
            print(f"Current color mode candidate: {format_color(link['color'])} [WindowServer; UUID {link['uuid']}]")
        elif link["status"] == "conflict":
            print("Current color mode: ambiguous WindowServer records disagree")
        else:
            print("Current color mode: unavailable (no matching WindowServer LinkDescription)")
        link_state = display.get("connectionColorState", {})
        parameters = link_state.get("parameters", {})
        if parameters:
            print(f"IOGraphics parameter read: {link_state.get('readStatus', 'unknown')}")
            for key, description in (("cmod", "selected-color-mode key"),
                                     ("cyuv", "connection color-mode attribute"),
                                     ("colr", "supported connection color modes"),
                                     (" bpc", "supported connection bit depths")):
                raw = parameter_value(parameters, key)
                if raw is not None:
                    print(f"  {description} ({key.strip()}): raw={raw}")
        elif link_state:
            print(f"IOGraphics parameter read: {link_state.get('readStatus', 'unavailable')}")
        av_state = display.get("ioAVServiceState", {})
        print(f"DCP IOAVService properties: {av_state.get('readStatus', 'not captured')}")
        for candidate in av_state.get("candidates", []):
            print(f"  service: {candidate.get('servicePath')}")
            raw_properties = candidate.get("ioAVServiceProperties")
            if raw_properties is not None:
                print(f"  raw properties: {json.dumps(raw_properties, ensure_ascii=False, sort_keys=True)}")
        if not hits:
            print("Matching AppleCLCD2 output: unavailable")
            continue
        for service_index, attrs in hits:
            props = services[service_index].get("properties", {})
            print(f"AppleCLCD2 service {service_index}; transport: {props.get('Transport', 'not reported')}")
            # AppleCLCD2 TimingElements describe the physical link timing, not
            # the scaled logical desktop dimensions reported by CGDisplayMode.width/height.
            current_w = int(mode.get("pixelWidth", mode.get("width", 0)))
            current_h = int(mode.get("pixelHeight", mode.get("height", 0)))
            current_hz = float(mode.get("refreshRate", 0))
            matching_timings = []
            for timing in props.get("TimingElements", []):
                h = int(timing.get("HorizontalAttributes", {}).get("Active", 0))
                v = int(timing.get("VerticalAttributes", {}).get("Active", 0))
                rate = timing_refresh(timing)
                if (h, v) == (current_w, current_h) and rate is not None and abs(rate - current_hz) <= 0.10:
                    matching_timings.append(timing)
            modes = {}
            for timing in matching_timings:
                for color in timing.get("ColorModes", []):
                    key = color_key(color)
                    entry = modes.setdefault(key, {"color": color, "ids": set(), "virtual": set()})
                    entry["ids"].add(value(color, "ID"))
                    entry["virtual"].add(bool(color.get("IsVirtual", False)))
            if not matching_timings:
                print("  No AppleCLCD2 timing matches this active resolution / refresh rate.")
                continue
            print(f"  AppleCLCD2 descriptors for this timing: {len(modes)} distinct descriptors")
            for key in sorted(modes, key=lambda k: tuple(-1 if x is None else x for x in k)):
                entry = modes[key]
                ids = ",".join(str(i) for i in sorted(x for x in entry["ids"] if x is not None))
                color = entry["color"]
                print(f"    {format_color(color)}; PixelEncoding={value(color, 'PixelEncoding')}, "
                      f"Range={value(color, 'DynamicRange')}, EOTF={value(color, 'EOTF')}, "
                      f"Colorimetry={value(color, 'Colorimetry')}; Apple ID(s) {ids}; "
                      f"virtual={any(entry['virtual'])}")
                for raw in matching_timings:
                    for raw_color in raw.get("ColorModes", []):
                        if color_key(raw_color) == key:
                            print(f"      raw ElementData={raw_color.get('ElementData')}")
                            break
                    else:
                        continue
                    break


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} path/to/display-report.json")
    main(sys.argv[1])
