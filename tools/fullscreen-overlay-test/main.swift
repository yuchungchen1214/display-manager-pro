import AppKit

@MainActor
final class OutlineView: NSView {
    let displayLabel: String

    init(frame: NSRect, displayLabel: String) {
        self.displayLabel = displayLabel
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let border = bounds.insetBy(dx: 14, dy: 14)
        let path = NSBezierPath(roundedRect: border, xRadius: 18, yRadius: 18)
        path.lineWidth = 10
        NSColor.systemOrange.withAlphaComponent(0.98).setStroke()
        path.stroke()

        let title = displayLabel as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 21, weight: .bold),
            .foregroundColor: NSColor.black,
        ]
        let textSize = title.size(withAttributes: attributes)
        let pill = NSRect(x: 34, y: bounds.maxY - 78,
                          width: textSize.width + 30, height: 42)
        let pillPath = NSBezierPath(roundedRect: pill, xRadius: 10, yRadius: 10)
        NSColor.systemOrange.setFill()
        pillPath.fill()
        title.draw(at: NSPoint(x: pill.minX + 15,
                               y: pill.minY + (pill.height - textSize.height) / 2),
                   withAttributes: attributes)
    }
}

@MainActor
final class OverlayController {
    private var panels: [NSPanel] = []
    private var expiryTimer: Timer?

    func showForTenSeconds() -> Int {
        hide()
        let screens = NSScreen.screens
        panels = screens.map { screen in
            let displayNumber = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
            let label = displayNumber.map { "DISPLAY \($0.uint32Value)" } ?? "DISPLAY"
            let panel = NSPanel(
                contentRect: screen.frame,
                styleMask: [.borderless, .nonactivatingPanel],
                backing: .buffered,
                defer: false,
                screen: screen
            )
            panel.contentView = OutlineView(frame: NSRect(origin: .zero, size: screen.frame.size),
                                            displayLabel: label)
            panel.backgroundColor = .clear
            panel.isOpaque = false
            panel.hasShadow = false
            panel.ignoresMouseEvents = true
            panel.hidesOnDeactivate = false
            panel.isFloatingPanel = true
            panel.level = .screenSaver
            panel.collectionBehavior = [
                .canJoinAllSpaces,
                .canJoinAllApplications,
                .fullScreenAuxiliary,
                .stationary,
                .ignoresCycle,
            ]
            panel.orderFrontRegardless()
            return panel
        }
        expiryTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.hide() }
        }
        return panels.count
    }

    func hide() {
        expiryTimer?.invalidate()
        expiryTimer = nil
        panels.forEach { $0.orderOut(nil) }
        panels.removeAll()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let overlays = OverlayController()
    private var window: NSWindow!
    private var statusLabel: NSTextField!

    func applicationDidFinishLaunching(_ notification: Notification) {
        // This test app is an agent so its nonactivating panels can join other
        // apps' full-screen Spaces. The production app is not changed.
        NSApp.setActivationPolicy(.accessory)

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 470, height: 230))
        let title = NSTextField(labelWithString: "Fullscreen & Spaces Overlay Test")
        title.font = .boldSystemFont(ofSize: 19)
        title.frame = NSRect(x: 24, y: 184, width: 420, height: 26)

        let instructions = NSTextField(wrappingLabelWithString:
            "Click the button, then switch to another Space or a full-screen app while the outlines are visible.")
        instructions.frame = NSRect(x: 24, y: 128, width: 420, height: 45)

        let showButton = NSButton(title: "Outline all displays (10 seconds)", target: self,
                                  action: #selector(showOutlines))
        showButton.bezelStyle = .rounded
        showButton.frame = NSRect(x: 24, y: 76, width: 270, height: 34)

        let hideButton = NSButton(title: "Hide outlines", target: self, action: #selector(hideOutlines))
        hideButton.bezelStyle = .rounded
        hideButton.frame = NSRect(x: 305, y: 76, width: 139, height: 34)

        statusLabel = NSTextField(labelWithString: "No extra permissions are used by this test.")
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.frame = NSRect(x: 24, y: 34, width: 420, height: 24)

        [title, instructions, showButton, hideButton, statusLabel].forEach(content.addSubview)
        window = NSWindow(contentRect: content.frame,
                          styleMask: [.titled, .closable],
                          backing: .buffered,
                          defer: false)
        window.title = "Overlay Test"
        window.contentView = content
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    @objc private func showOutlines() {
        let count = overlays.showForTenSeconds()
        statusLabel.stringValue = "Showing outlines on \(count) display(s) for 10 seconds…"
    }

    @objc private func hideOutlines() {
        overlays.hide()
        statusLabel.stringValue = "Outlines hidden."
    }

    func applicationWillTerminate(_ notification: Notification) {
        overlays.hide()
    }
}

@main
struct FullscreenOverlayTestApp {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.run()
    }
}
