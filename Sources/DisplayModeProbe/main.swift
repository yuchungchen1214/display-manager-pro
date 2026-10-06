import Foundation
import CoreGraphics
import IOKit
import IOKit.graphics
import IOKit.pwr_mgt
import Darwin

struct Options {
    var outputDirectory: URL?
    var includeIORegistry = true
    var jsonOutput = false
}

func usage() {
    print("""
    display-mode-probe — read-only macOS display diagnostics

    Usage:
      display-mode-probe [--out DIRECTORY] [--json] [--no-ioregistry]

    Writes a report and raw IORegistry dump for each connected display when --out is used.
    No display settings are changed.
    """)
}

func parseOptions() -> Options {
    var o = Options(); var args = Array(CommandLine.arguments.dropFirst());
    while !args.isEmpty {
        let a = args.removeFirst()
        switch a {
        case "--help", "-h": usage(); exit(0)
        case "--no-ioregistry": o.includeIORegistry = false
        case "--json": o.jsonOutput = true
        case "--out":
            guard let path = args.first else { fputs("--out needs a directory\n", stderr); exit(2) }
            args.removeFirst(); o.outputDirectory = URL(fileURLWithPath: path, isDirectory: true)
        default: fputs("Unknown option: \(a)\n", stderr); usage(); exit(2)
        }
    }
    return o
}

func plistObject(_ value: Any) -> Any {
    if let data = value as? Data { return data.base64EncodedString() }
    if let dict = value as? NSDictionary {
        var result: [String: Any] = [:]
        for (key, item) in dict { result[String(describing: key)] = plistObject(item) }
        return result
    }
    if let array = value as? NSArray { return array.map(plistObject) }
    if let dict = value as? [String: Any] { return dict.mapValues(plistObject) }
    if let array = value as? [Any] { return array.map(plistObject) }
    if value is NSNull || value is String || value is NSNumber || value is Bool { return value }
    return String(describing: value)
}

func ioProperties(_ service: io_service_t) -> [String: Any] {
    var props: Unmanaged<CFMutableDictionary>?
    guard IORegistryEntryCreateCFProperties(service, &props, kCFAllocatorDefault, 0) == KERN_SUCCESS,
          let retained = props?.takeRetainedValue(),
          let raw = retained as NSDictionary as? [String: Any] else { return [:] }
    return raw.mapValues(plistObject)
}

func serviceName(_ service: io_service_t) -> String {
    var buffer = [CChar](repeating: 0, count: 128)
    IORegistryEntryGetName(service, &buffer)
    return String(cString: buffer)
}

// CoreDisplay exposes the same per-display identity dictionary used by
// BetterDisplay and several open-source display utilities. In particular,
// IODisplayLocation distinguishes identical EDIDs on different DCP ports.
typealias CoreDisplayInfoFunction = @convention(c) (CGDirectDisplayID) -> Unmanaged<CFDictionary>?

func coreDisplayInfo(_ displayID: CGDirectDisplayID) -> [String: Any] {
    guard let handle = dlopen("/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay", RTLD_LAZY),
          let symbol = dlsym(handle, "CoreDisplay_DisplayCreateInfoDictionary") else {
        return [:]
    }
    let function = unsafeBitCast(symbol, to: CoreDisplayInfoFunction.self)
    guard let unmanaged = function(displayID) else { return [:] }
    return plistObject(unmanaged.takeRetainedValue()) as? [String: Any] ?? [:]
}

func appleCLCD2Location(for displayID: CGDirectDisplayID) -> String? {
    coreDisplayInfo(displayID)["IODisplayLocation"] as? String
}

typealias IOAVCreateWithServiceFunction = @convention(c) (CFAllocator?, io_service_t) -> Unmanaged<CFTypeRef>?
typealias IOAVCopyPropertiesFunction = @convention(c) (CFTypeRef) -> Unmanaged<CFDictionary>?

func ioAVProperties(forDisplayLocation displayLocation: String) -> [String: Any] {
    // CoreDisplay reports a full path ending in AppleCLCD2; the physical DCP
    // port is the preceding dispextN@... segment (or dispN@... internally).
    let portSegment = displayLocation.split(separator: "/").first {
        ($0.hasPrefix("dispext") || $0.hasPrefix("disp")) && $0.contains("@")
    }
    guard let port = portSegment?.split(separator: "@").first.map(String.init),
          !port.isEmpty,
          let handle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY),
          let createSymbol = dlsym(handle, "IOAVServiceCreateWithService"),
          let copyPropertiesSymbol = dlsym(handle, "IOAVServiceCopyProperties") else { return [:] }
    let createService = unsafeBitCast(createSymbol, to: IOAVCreateWithServiceFunction.self)
    let copyProperties = unsafeBitCast(copyPropertiesSymbol, to: IOAVCopyPropertiesFunction.self)

    var iterator: io_iterator_t = 0
    guard let matching = IOServiceMatching("DCPAVServiceProxy"),
          IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else { return [:] }
    defer { IOObjectRelease(iterator) }

    var candidates: [[String: Any]] = []
    while case let service = IOIteratorNext(iterator), service != 0 {
        var pathBuffer = [CChar](repeating: 0, count: Int(MemoryLayout<io_string_t>.size))
        let pathStatus = IORegistryEntryGetPath(service, kIOServicePlane, &pathBuffer)
        let servicePath = pathStatus == KERN_SUCCESS ? String(cString: pathBuffer) : ""
        let properties = ioProperties(service)
        let location = properties["Location"] as? String
        guard servicePath.contains("/\(port):"), location == "External",
              let avService = createService(kCFAllocatorDefault, service) else {
            IOObjectRelease(service); continue
        }
        let avServiceObject = avService.takeRetainedValue()
        let rawProperties = copyProperties(avServiceObject)?.takeRetainedValue()
        var record: [String: Any] = ["servicePath": servicePath, "registryProperties": properties]
        if let rawProperties {
            record["ioAVServiceProperties"] = plistObject(rawProperties)
        }
        candidates.append(record)
        IOObjectRelease(service)
    }
    return ["portToken": port, "readStatus": candidates.isEmpty ? "no external DCPAVServiceProxy matched this AppleCLCD2 port" : "success", "candidates": candidates]
}

// Resolve an IODisplayConnect by the same EDID identity CoreGraphics reports.
// This avoids the deprecated CGDisplayIOServicePort API and works when the
// system publishes a matching display connection service.
func displayConnectService(for displayID: CGDirectDisplayID) -> (service: io_service_t?, ambiguous: Bool) {
    var iterator: io_iterator_t = 0
    guard let matching = IOServiceMatching("IODisplayConnect"),
          IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else { return (nil, false) }
    defer { IOObjectRelease(iterator) }
    let vendor = CGDisplayVendorNumber(displayID)
    let product = CGDisplayModelNumber(displayID)
    let serial = CGDisplaySerialNumber(displayID)
    var fallback: io_service_t = 0
    var fallbackCount = 0
    var exact: io_service_t = 0
    while case let service = IOIteratorNext(iterator), service != 0 {
        guard let unmanagedInfo = IODisplayCreateInfoDictionary(service, IOOptionBits(kIODisplayOnlyPreferredName)) else {
            IOObjectRelease(service); continue
        }
        let info = unmanagedInfo.takeRetainedValue() as NSDictionary
        let serviceVendor = (info[kDisplayVendorID] as? NSNumber)?.uint32Value ?? 0
        let serviceProduct = (info[kDisplayProductID] as? NSNumber)?.uint32Value ?? 0
        let serviceSerial = (info[kDisplaySerialNumber] as? NSNumber)?.uint32Value ?? 0
        guard serviceVendor == vendor && serviceProduct == product else { IOObjectRelease(service); continue }
        if serviceSerial == serial {
            if exact != 0 {
                IOObjectRelease(exact); IOObjectRelease(service)
                if fallback != 0 { IOObjectRelease(fallback) }
                return (nil, true)
            }
            exact = service
        } else {
            fallbackCount += 1
            if fallback == 0 { fallback = service } else { IOObjectRelease(service) }
        }
    }
    if exact != 0 {
        if fallback != 0 { IOObjectRelease(fallback) }
        return (exact, false)
    }
    if fallbackCount == 1 { return (fallback, false) }
    if fallback != 0 { IOObjectRelease(fallback) }
    return (nil, fallbackCount > 1)
}

func servicePath(_ service: io_service_t) -> String {
    var buffer = [CChar](repeating: 0, count: Int(MemoryLayout<io_string_t>.size))
    guard IORegistryEntryGetPath(service, kIOServicePlane, &buffer) == KERN_SUCCESS else { return "" }
    return String(cString: buffer)
}

func appleCLCD2Services() -> [[String: Any]] {
    var result: [[String: Any]] = []
    var iterator: io_iterator_t = 0
    guard let matching = IOServiceMatching("AppleCLCD2"),
          IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) == KERN_SUCCESS else { return result }
    defer { IOObjectRelease(iterator) }
    while case let service = IOIteratorNext(iterator), service != 0 {
        result.append(["name": serviceName(service), "servicePath": servicePath(service), "properties": ioProperties(service)])
        IOObjectRelease(service)
    }
    return result
}

func displayName(_ display: [String: Any]) -> String {
    let info = display["coreDisplayInfo"] as? [String: Any] ?? [:]
    return (info["DisplayName"] as? String) ?? (info["ProductName"] as? String) ?? "Display \(Int(number(display["displayID"])))"
}

func safeFileComponent(_ input: String) -> String {
    let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
    let mapped = input.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" }
    return String(mapped)
}

func writeCapture(displayRecords: [[String: Any]], services: [[String: Any]], to directory: URL) throws {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let activeExternal = displayRecords.filter {
        ($0["isBuiltIn"] as? NSNumber)?.boolValue == false && ($0["isActive"] as? NSNumber)?.boolValue == true
    }
    let matched = activeExternal.map { display -> [String: Any] in
        let location = display["ioDisplayLocation"] as? String ?? ""
        let service = services.first { ($0["servicePath"] as? String).map { !location.isEmpty && location.contains($0) } ?? false }
        return ["display": display, "appleCLCD2": service ?? NSNull()]
    }
    let report: [String: Any] = [
        "schemaVersion": 2,
        "capturedAt": ISO8601DateFormatter().string(from: Date()),
        "host": ["model": "see system_profiler", "architecture": "Apple Silicon expected"],
        "summary": [
            "activeExternalDisplayCount": activeExternal.count,
            "requestedSetup": "two external HDMI displays connected through hubs",
            "note": "TimingElements/ColorModes describe system-recognized candidates. DPTimingModeId identifies the active timing, not the currently selected connection ColorMode."
        ],
        "externalDisplays": matched,
        "activeDisplays": displayRecords,
        "appleCLCD2Services": services
    ]
    let encoder = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
    try encoder.write(to: directory.appendingPathComponent("display-report.json"), options: .atomic)
    for (index, item) in matched.enumerated() {
        guard let service = item["appleCLCD2"] as? [String: Any] else { continue }
        let display = item["display"] as? [String: Any] ?? [:]
        let name = safeFileComponent(displayName(display))
        let id = Int(number(display["displayID"]))
        let raw = try JSONSerialization.data(withJSONObject: service, options: [.prettyPrinted, .sortedKeys])
        try raw.write(to: directory.appendingPathComponent("external-\(index + 1)-display-\(id)-\(name)-AppleCLCD2.json"), options: .atomic)
    }
}

func displayRecord(_ id: CGDirectDisplayID) -> [String: Any] {
    let mode = CGDisplayCopyDisplayMode(id)
    let allModes = CGDisplayCopyAllDisplayModes(id, nil) as? [CGDisplayMode] ?? []
    func modeObject(_ m: CGDisplayMode) -> [String: Any] {
        ["width": NSNumber(value: m.width), "height": NSNumber(value: m.height),
         "refreshRate": NSNumber(value: m.refreshRate), "pixelWidth": NSNumber(value: m.pixelWidth),
         "pixelHeight": NSNumber(value: m.pixelHeight), "isUsableForDesktopGUI": NSNumber(value: m.isUsableForDesktopGUI())]
    }
    let bounds = CGDisplayBounds(id)
    let coreInfo = coreDisplayInfo(id)
    let displayLocation = coreInfo["IODisplayLocation"] as? String
    let serviceMatch = displayConnectService(for: id)
    let framebuffer = serviceMatch.service
    var linkState: [String: Any] = ["readStatus": serviceMatch.ambiguous ? "ambiguous: multiple matching IODisplayConnect services" : "unavailable: no unique IODisplayConnect service"]
    if let framebuffer {
        let framebufferName = serviceName(framebuffer)
        let displayService = IODisplayForFramebuffer(framebuffer, 0)
        let readService = displayService == 0 ? framebuffer : displayService
        let displayInfo = ioProperties(framebuffer)
        var parameters: Unmanaged<CFDictionary>?
        let parameterStatus = IODisplayCopyParameters(readService, 0, &parameters)
        let parameterObject = parameters?.takeRetainedValue()
        if let parameterObject {
            linkState["parameters"] = plistObject(parameterObject)
        }
        linkState["readStatus"] = parameterStatus == KERN_SUCCESS ? "success" : "IODisplayCopyParameters error \(parameterStatus)"
        linkState["framebufferServiceName"] = framebufferName
        linkState["displayServiceName"] = serviceName(readService)
        linkState["displayInfo"] = displayInfo
        if displayService != 0 { IOObjectRelease(displayService) }
        IOObjectRelease(framebuffer)
    }
    let record: [String: Any] = [
        "displayID": NSNumber(value: id),
        "vendorNumber": NSNumber(value: CGDisplayVendorNumber(id)),
        "modelNumber": NSNumber(value: CGDisplayModelNumber(id)),
        "serialNumber": NSNumber(value: CGDisplaySerialNumber(id)),
        "isBuiltIn": NSNumber(value: CGDisplayIsBuiltin(id) != 0),
        "isOnline": NSNumber(value: CGDisplayIsOnline(id) != 0),
        "isActive": NSNumber(value: CGDisplayIsActive(id) != 0),
        "bounds": ["x": NSNumber(value: bounds.origin.x), "y": NSNumber(value: bounds.origin.y), "width": NSNumber(value: bounds.width), "height": NSNumber(value: bounds.height)],
        "pixelEncoding": "see IORegistry",
        "coreDisplayInfo": coreInfo,
        "ioDisplayLocation": coreInfo["IODisplayLocation"] ?? NSNull(),
        "ioAVServiceState": displayLocation.map { ioAVProperties(forDisplayLocation: $0) } ?? [:],
        "connectionColorState": linkState,
        "currentMode": mode.map(modeObject) ?? [:],
        "coreGraphicsModes": allModes.map(modeObject)
    ]
    return record
}

func number(_ value: Any?, _ defaultValue: Double = 0) -> Double {
    (value as? NSNumber)?.doubleValue ?? defaultValue
}

func printSummary(_ displays: [[String: Any]]) {
    print("Display Mode Probe — current and supported CoreGraphics modes")
    print("============================================================")
    for (index, display) in displays.enumerated() {
        let id = Int(number(display["displayID"]))
        let builtin = (display["isBuiltIn"] as? NSNumber)?.boolValue ?? false
        let current = display["currentMode"] as? [String: Any] ?? [:]
        let label = builtin ? "Built-in" : "External"
        let currentText = "\(Int(number(current["width"])))x\(Int(number(current["height"]))) @ \(number(current["refreshRate"])) Hz, pixel \(Int(number(current["pixelWidth"])))x\(Int(number(current["pixelHeight"])))"
        print("\n[\(index + 1)] Display ID \(id) — \(label)")
        print("Current: \(currentText)")
        let modes = (display["coreGraphicsModes"] as? [[String: Any]] ?? []).sorted {
            let a = (Int(number($0["width"])), Int(number($0["height"])), number($0["refreshRate"]))
            let b = (Int(number($1["width"])), Int(number($1["height"])), number($1["refreshRate"]))
            return a.0 != b.0 ? a.0 < b.0 : (a.1 != b.1 ? a.1 < b.1 : a.2 < b.2)
        }
        print("Supported CoreGraphics modes (\(modes.count)):")
        for mode in modes {
            let marker = Int(number(mode["width"])) == Int(number(current["width"])) &&
                Int(number(mode["height"])) == Int(number(current["height"])) &&
                number(mode["refreshRate"]) == number(current["refreshRate"]) ? "*" : " "
            print(String(format: "  %@ %4dx%-4d @ %6.2f Hz   pixel %4dx%-4d", marker,
                         Int(number(mode["width"])), Int(number(mode["height"])), number(mode["refreshRate"]),
                         Int(number(mode["pixelWidth"])), Int(number(mode["pixelHeight"]))))
        }
    }
    print("\n* Current mode")
}

let options = parseOptions()
var displayIDs = [CGDirectDisplayID](repeating: 0, count: 32); var count: UInt32 = 0
CGGetActiveDisplayList(32, &displayIDs, &count)
let displays = (0..<Int(count)).map { displayRecord(displayIDs[$0]) }
let externalCount = displays.filter {
    ($0["isBuiltIn"] as? NSNumber)?.boolValue == false && ($0["isActive"] as? NSNumber)?.boolValue == true
}.count
let services = options.includeIORegistry ? appleCLCD2Services() : []
let report: [String: Any] = ["displays": displays]
if let out = options.outputDirectory {
    try writeCapture(displayRecords: displays, services: services, to: out)
    print("External displays: \(externalCount) (configured target: two displays connected through HDMI hubs)")
    printSummary(displays.filter { ($0["isBuiltIn"] as? NSNumber)?.boolValue == false })
    print("\nComplete report: \(out.appendingPathComponent("display-report.json").path)")
    print("AppleCLCD2 raw data: \(out.path)")
    if externalCount != 2 { print("\nNote: detected \(externalCount) active external displays. Confirm that both hubs are connected and enabled in macOS Display Settings.") }
} else if options.jsonOutput {
    let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
} else {
    print("Detected \(externalCount) active external displays. Add --out <directory> to save per-display raw data.")
    printSummary(displays.filter { ($0["isBuiltIn"] as? NSNumber)?.boolValue == false })
}
