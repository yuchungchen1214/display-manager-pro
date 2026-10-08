import AppKit
import CoreImage
import CoreMedia
import CoreVideo
import ScreenCaptureKit
import CoreGraphics

struct Placement: Equatable {
    let displayID: CGDirectDisplayID
    var x: CGFloat
    var y: CGFloat
    var width: CGFloat
    var height: CGFloat
    var showsCursor: Bool
    var rotation: CGFloat

    init?(json: [String: Any]) {
        guard let raw = json["displayID"] as? String,
              let id = UInt32(raw),
              let x = (json["x"] as? NSNumber)?.doubleValue,
              let y = (json["y"] as? NSNumber)?.doubleValue,
              let width = (json["width"] as? NSNumber)?.doubleValue,
              let height = (json["height"] as? NSNumber)?.doubleValue,
              width > 0, height > 0 else { return nil }
        self.displayID = id
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.showsCursor = (json["showCursor"] as? Bool) ?? true
        self.rotation = CGFloat((json["rotation"] as? NSNumber)?.doubleValue ?? 0)
    }
}

struct OutputTarget: Equatable {
    let displayID: CGDirectDisplayID
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat

    init?(json: [String: Any]) {
        guard let raw = json["displayID"] as? String,
              let id = UInt32(raw),
              let x = (json["x"] as? NSNumber)?.doubleValue,
              let y = (json["y"] as? NSNumber)?.doubleValue,
              let width = (json["width"] as? NSNumber)?.doubleValue,
              let height = (json["height"] as? NSNumber)?.doubleValue,
              width > 0, height > 0 else { return nil }
        self.displayID = id
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

final class OutputView: NSView {
    var placements: [Placement] = []
    var images: [CGDirectDisplayID: CGImage] = [:]
    var cursorOverlays: [CursorOverlay] = []
    var target: OutputTarget?

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.black.setFill()
        bounds.fill()
        guard let target, target.width > 0, target.height > 0 else { return }
        let scaleX = bounds.width / target.width
        let scaleY = bounds.height / target.height
        NSGraphicsContext.current?.imageInterpolation = .high
        for placement in placements {
            guard let image = images[placement.displayID] else { continue }
            let rect = NSRect(x: (placement.x - target.x) * scaleX,
                              y: (placement.y - target.y) * scaleY,
                              width: placement.width * scaleX,
                              height: placement.height * scaleY)
            NSGraphicsContext.saveGraphicsState()
            let transform = NSAffineTransform()
            transform.translateX(by: rect.midX, yBy: rect.midY)
            transform.rotate(byDegrees: placement.rotation)
            transform.concat()
            NSImage(cgImage: image, size: NSSize(width: rect.width, height: rect.height))
                .draw(in: NSRect(x: -rect.width / 2, y: -rect.height / 2,
                                 width: rect.width, height: rect.height),
                      from: .zero, operation: .sourceOver, fraction: 1,
                      respectFlipped: true, hints: [.interpolation: NSImageInterpolation.high])
            NSGraphicsContext.restoreGraphicsState()
        }
        for overlay in cursorOverlays {
            NSGraphicsContext.saveGraphicsState()
            let transform = NSAffineTransform()
            transform.translateX(by: overlay.point.x, yBy: overlay.point.y)
            transform.rotate(byDegrees: overlay.rotation)
            transform.concat()
            let rect = NSRect(x: -overlay.hotSpot.width,
                              y: -overlay.hotSpot.height,
                              width: overlay.size.width, height: overlay.size.height)
            overlay.image.draw(in: rect, from: .zero, operation: .sourceOver,
                               fraction: 1, respectFlipped: true,
                               hints: [.interpolation: NSImageInterpolation.high])
            NSGraphicsContext.restoreGraphicsState()
        }
    }
}

struct CursorOverlay {
    let image: NSImage
    let point: CGPoint
    let hotSpot: CGSize
    let size: CGSize
    let rotation: CGFloat
}

final class StreamOutput: NSObject, SCStreamOutput {
    let displayID: CGDirectDisplayID
    weak var owner: FrameController?
    private let context = CIContext(options: [.useSoftwareRenderer: false])

    init(displayID: CGDirectDisplayID, owner: FrameController) {
        self.displayID = displayID
        self.owner = owner
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .screen, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: buffer)
        guard let cgImage = context.createCGImage(image, from: image.extent) else { return }
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.owner?.present(cgImage, from: self.displayID)
        }
    }
}

final class CaptureDelegate: NSObject, SCStreamDelegate {
    let displayID: CGDirectDisplayID
    weak var owner: FrameController?

    init(displayID: CGDirectDisplayID, owner: FrameController) {
        self.displayID = displayID
        self.owner = owner
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.owner?.emit(["event": "error", "message":
                "Capture stopped for display \(self.displayID): \(error.localizedDescription)"])
        }
    }
}

@MainActor
final class FrameController: NSObject, NSApplicationDelegate {
    private var inputBuffer = Data()
    private var outputPanels: [CGDirectDisplayID: NSPanel] = [:]
    private var outputViews: [CGDirectDisplayID: OutputView] = [:]
    private var targets: [OutputTarget] = []
    private var placements: [Placement] = []
    private var streams: [CGDirectDisplayID: SCStream] = [:]
    private var outputs: [CGDirectDisplayID: StreamOutput] = [:]
    private var delegates: [CGDirectDisplayID: CaptureDelegate] = [:]
    private var sceneRevision = 0
    private var pendingScene: [String: Any]?
    private var applyingScene = false
    private var cursorTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        NotificationCenter.default.addObserver(self, selector: #selector(screenParametersChanged),
            name: NSApplication.didChangeScreenParametersNotification, object: nil)
        FileHandle.standardInput.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            DispatchQueue.main.async {
                guard let self else { return }
                if data.isEmpty {
                    self.stopOutput()
                    NSApp.terminate(nil)
                } else {
                    self.consume(data)
                }
            }
        }
        emit(["event": "ready"])
    }

    func applicationWillTerminate(_ notification: Notification) {
        FileHandle.standardInput.readabilityHandler = nil
        stopOutput()
    }

    private func consume(_ data: Data) {
        inputBuffer.append(data)
        while let newline = inputBuffer.firstIndex(of: 10) {
            let line = inputBuffer[..<newline]
            inputBuffer.removeSubrange(...newline)
            guard let object = try? JSONSerialization.jsonObject(with: Data(line)),
                  let command = object as? [String: Any] else { continue }
            switch command["action"] as? String {
            case "scene":
                pendingScene = command
                if !applyingScene {
                    applyingScene = true
                    Task { await drainScenes() }
                }
            case "stop":
                stopOutput()
            case "quit":
                stopOutput()
                NSApp.terminate(nil)
            default:
                break
            }
        }
    }

    private func drainScenes() async {
        while let scene = pendingScene {
            pendingScene = nil
            await apply(scene)
        }
        applyingScene = false
        if pendingScene != nil {
            applyingScene = true
            Task { await drainScenes() }
        }
    }

    private func apply(_ scene: [String: Any]) async {
        sceneRevision += 1
        let revision = sceneRevision
        guard (scene["enabled"] as? Bool) == true,
              let rawTargets = scene["outputs"] as? [[String: Any]] else {
            stopOutput()
            return
        }
        let targets = rawTargets.compactMap(OutputTarget.init(json:))
        guard !targets.isEmpty else { stopOutput(); return }
        let placements = (scene["sources"] as? [[String: Any]] ?? []).compactMap(Placement.init(json:))
        guard !placements.isEmpty else {
            stopOutput()
            return
        }
        let wanted = Set(placements.map(\.displayID))
        if self.targets == targets, !outputPanels.isEmpty,
           Set(streams.keys) == wanted {
            self.placements = placements
            for view in outputViews.values {
                view.placements = placements
                view.needsDisplay = true
            }
            emit(["event": "active"])
            return
        }

        guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
            emit(["event": "error", "message":
                "Screen Recording permission is required. Enable it for Display Manager Pro in System Settings, then restart the app."])
            stopOutput()
            return
        }

        if self.targets != targets || outputPanels.isEmpty {
            stopStreams()
            outputPanels.values.forEach { $0.orderOut(nil) }
            outputPanels.removeAll()
            outputViews.removeAll()
            let screens = NSScreen.screens
            for target in targets {
                guard let screen = screens.first(where: { screenID($0) == target.displayID }) else {
                    emit(["event": "error", "message": "A destination display is no longer available."])
                    stopOutput()
                    return
                }
                let panel = NSPanel(contentRect: screen.frame,
                                    styleMask: [.borderless, .nonactivatingPanel],
                                    backing: .buffered, defer: false, screen: screen)
                panel.isOpaque = true
                panel.backgroundColor = .black
                panel.hasShadow = false
                panel.ignoresMouseEvents = true
                panel.level = .screenSaver
                panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
                let view = OutputView(frame: NSRect(origin: .zero, size: screen.frame.size))
                view.target = target
                panel.contentView = view
                panel.setFrame(screen.frame, display: true)
                panel.orderFrontRegardless()
                outputPanels[target.displayID] = panel
                outputViews[target.displayID] = view
            }
            self.targets = targets
        }

        self.placements = placements
        for view in outputViews.values {
            view.placements = placements
            view.needsDisplay = true
        }

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                             onScreenWindowsOnly: false)
        } catch {
            emit(["event": "error", "message": "Unable to list capture sources: \(error.localizedDescription)"])
            stopOutput()
            return
        }
        guard revision == sceneRevision else { return }
        let ownWindowIDs = Set(outputPanels.values.map { CGWindowID($0.windowNumber) })
        let excluded = content.windows.filter { ownWindowIDs.contains($0.windowID) }
        for id in streams.keys where !wanted.contains(id) {
            await stopStream(id)
        }
        guard revision == sceneRevision else { return }

        for id in wanted where streams[id] == nil {
            guard let source = content.displays.first(where: { $0.displayID == id }),
                  let sourceMode = CGDisplayCopyDisplayMode(id) else {
                emit(["event": "error", "message": "A selected source display is no longer available."])
                stopOutput()
                return
            }
            let filter = SCContentFilter(display: source, excludingWindows: excluded)
            let config = SCStreamConfiguration()
            config.width = sourceMode.pixelWidth
            config.height = sourceMode.pixelHeight
            config.pixelFormat = kCVPixelFormatType_32BGRA
            config.minimumFrameInterval = CMTime(value: 1, timescale: 60)
            config.queueDepth = 3
            config.showsCursor = false
            let output = StreamOutput(displayID: id, owner: self)
            let delegate = CaptureDelegate(displayID: id, owner: self)
            let stream = SCStream(filter: filter, configuration: config, delegate: delegate)
            do {
                try stream.addStreamOutput(output, type: .screen,
                    sampleHandlerQueue: DispatchQueue(label: "display-frame-output.\(id)"))
                try await stream.startCapture()
                guard revision == sceneRevision else {
                    try? await stream.stopCapture()
                    return
                }
                streams[id] = stream
                outputs[id] = output
                delegates[id] = delegate
            } catch {
                emit(["event": "error", "message": "Unable to capture display \(id): \(error.localizedDescription)"])
                stopOutput()
                return
            }
        }
        guard revision == sceneRevision else { return }
        startCursorTracking()
        emit(["event": "active"])
    }

    func present(_ image: CGImage, from displayID: CGDirectDisplayID) {
        guard placements.contains(where: { $0.displayID == displayID }) else { return }
        for view in outputViews.values {
            view.images[displayID] = image
            view.needsDisplay = true
        }
        updateCursorOverlays()
    }

    private func startCursorTracking() {
        guard cursorTimer == nil else { return }
        cursorTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0,
                                           repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.updateCursorOverlays()
            }
        }
        RunLoop.main.add(cursorTimer!, forMode: .common)
        updateCursorOverlays()
    }

    private func updateCursorOverlays() {
        guard !outputViews.isEmpty else {
            return
        }
        let mouse = NSEvent.mouseLocation
        guard let sourceScreen = NSScreen.screens.first(where: { $0.frame.contains(mouse) }),
              let sourceID = screenID(sourceScreen),
              let cursor = NSCursor.currentSystem else {
            for view in outputViews.values {
                view.cursorOverlays = []
                view.needsDisplay = true
            }
            return
        }
        let screenFrame = sourceScreen.frame
        let fractionX = min(1, max(0, (mouse.x - screenFrame.minX) / screenFrame.width))
        let fractionY = min(1, max(0, (screenFrame.maxY - mouse.y) / screenFrame.height))
        let matchingPlacements = placements.filter {
            $0.displayID == sourceID && $0.showsCursor
        }
        let cursorImage = cursor.image
        let backingScale = max(1, sourceScreen.backingScaleFactor)

        for (outputID, view) in outputViews {
            guard let target = view.target,
                  let outputScreen = NSScreen.screens.first(where: { screenID($0) == outputID }) else {
                view.cursorOverlays = []
                continue
            }
            let scaleX = view.bounds.width / target.width
            let scaleY = view.bounds.height / target.height
            var overlays: [CursorOverlay] = []
            var mappedOnThisOutput = false
            for placement in matchingPlacements {
                let relativeX = (fractionX - 0.5) * placement.width
                let relativeY = (fractionY - 0.5) * placement.height
                let radians = placement.rotation * .pi / 180
                let x = placement.x + placement.width / 2
                    + relativeX * cos(radians) - relativeY * sin(radians)
                let y = placement.y + placement.height / 2
                    + relativeX * sin(radians) + relativeY * cos(radians)
                guard x >= target.x, x <= target.x + target.width,
                      y >= target.y, y <= target.y + target.height,
                      let captured = view.images[sourceID] else { continue }
                let pixelScaleX = placement.width / CGFloat(captured.width)
                let pixelScaleY = placement.height / CGFloat(captured.height)
                overlays.append(CursorOverlay(
                    image: cursorImage,
                    point: CGPoint(x: (x - target.x) * scaleX,
                                   y: (y - target.y) * scaleY),
                    hotSpot: CGSize(width: cursor.hotSpot.x * backingScale * pixelScaleX * scaleX,
                                    height: cursor.hotSpot.y * backingScale * pixelScaleY * scaleY),
                    size: CGSize(width: cursorImage.size.width * backingScale * pixelScaleX * scaleX,
                                 height: cursorImage.size.height * backingScale * pixelScaleY * scaleY),
                    rotation: placement.rotation))
                mappedOnThisOutput = true
            }

            // If the pointer is over an output display but is not being
            // represented by a source card there, redraw it at its native
            // desktop location on top of the opaque output panel.
            if !mappedOnThisOutput, outputScreen.frame.contains(mouse) {
                let localX = (mouse.x - outputScreen.frame.minX) / outputScreen.frame.width
                let localY = (outputScreen.frame.maxY - mouse.y) / outputScreen.frame.height
                overlays.append(CursorOverlay(
                    image: cursorImage,
                    point: CGPoint(x: localX * view.bounds.width,
                                   y: localY * view.bounds.height),
                    hotSpot: CGSize(width: cursor.hotSpot.x, height: cursor.hotSpot.y),
                    size: cursorImage.size,
                    rotation: 0))
            }
            view.cursorOverlays = overlays
            view.needsDisplay = true
        }
    }

    private func stopStreams() {
        let current = streams
        streams.removeAll()
        outputs.removeAll()
        delegates.removeAll()
        for stream in current.values { Task { try? await stream.stopCapture() } }
    }

    private func stopStream(_ id: CGDirectDisplayID) async {
        if let stream = streams.removeValue(forKey: id) { try? await stream.stopCapture() }
        outputs.removeValue(forKey: id)
        delegates.removeValue(forKey: id)
        for view in outputViews.values {
            view.images.removeValue(forKey: id)
            view.needsDisplay = true
        }
    }

    private func stopOutput() {
        sceneRevision += 1
        cursorTimer?.invalidate()
        cursorTimer = nil
        for view in outputViews.values { view.cursorOverlays = [] }
        pendingScene = nil
        stopStreams()
        outputPanels.values.forEach { $0.orderOut(nil) }
        outputPanels.removeAll()
        outputViews.removeAll()
        targets = []
        placements = []
        emit(["event": "stopped"])
    }

    @objc private func screenParametersChanged() {
        guard !targets.isEmpty, !placements.isEmpty else { return }
        stopOutput()
        emit(["event": "topologyChanged"])
    }

    func emit(_ event: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: event),
              var line = String(data: data, encoding: .utf8) else { return }
        line.append("\n")
        FileHandle.standardOutput.write(Data(line.utf8))
    }
}

private func screenID(_ screen: NSScreen) -> CGDirectDisplayID? {
    (screen.deviceDescription[.init("NSScreenNumber")] as? NSNumber)?.uint32Value
}

@main
struct DisplayFrameOutputApp {
    static func main() {
        MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = FrameController()
    app.delegate = delegate
    app.run()
        }
    }
}
