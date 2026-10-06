import AppKit

@MainActor
final class ScreenHighlightView: NSView {
    let title: String

    init(frame: NSRect, title: String) {
        self.title = title
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        let frame = bounds.insetBy(dx: 14, dy: 14)
        let outline = NSBezierPath(roundedRect: frame, xRadius: 20, yRadius: 20)
        outline.lineWidth = 12
        NSColor.systemOrange.withAlphaComponent(0.98).setStroke()
        outline.stroke()

        let text = title as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 23, weight: .bold),
            .foregroundColor: NSColor.white,
        ]
        let textSize = text.size(withAttributes: attributes)
        let badge = NSRect(x: 34, y: bounds.maxY - 92,
                           width: textSize.width + 34, height: 52)
        NSColor.systemOrange.setFill()
        NSBezierPath(roundedRect: badge, xRadius: 12, yRadius: 12).fill()
        text.draw(at: NSPoint(x: badge.minX + 17,
                              y: badge.minY + (badge.height - textSize.height) / 2),
                  withAttributes: attributes)
    }
}

@MainActor
final class OverlayController {
    private(set) var panels: [NSPanel] = []

    func showOnEveryScreen() -> [NSPanel] {
        hide()

        let screens = NSScreen.screens
        panels = screens.map { screen in
            let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)
                .map { " · \($0.uint32Value)" } ?? ""
            let title = "\(screen.localizedName)\(id)"
            let panel = NSPanel(
                // With the screen: initializer, contentRect's origin is
                // relative to that screen. Passing screen.frame here applies
                // the non-primary screen origin twice, placing its panel off
                // the display (the primary screen masks this bug at (0, 0)).
                contentRect: NSRect(origin: .zero, size: screen.frame.size),
                styleMask: [.borderless, .nonactivatingPanel],
                backing: .buffered,
                defer: false,
                screen: screen
            )
            panel.contentView = ScreenHighlightView(
                frame: NSRect(origin: .zero, size: screen.frame.size), title: title)
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

        return panels
    }

    func hide() {
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
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 520, height: 260))
        let title = NSTextField(labelWithString: "All-Display Overlay Test v2")
        title.font = .boldSystemFont(ofSize: 20)
        title.frame = NSRect(x: 24, y: 214, width: 460, height: 28)

        let instructions = NSTextField(wrappingLabelWithString:
            "Show one bright outline on every connected display at the same time. " +
            "It stays visible until you hide it or quit this tool.")
        instructions.frame = NSRect(x: 24, y: 158, width: 470, height: 44)

        let showButton = NSButton(title: "Show on all displays", target: self,
                                  action: #selector(showOverlays))
        showButton.bezelStyle = .rounded
        showButton.frame = NSRect(x: 24, y: 96, width: 220, height: 38)

        let hideButton = NSButton(title: "Hide outlines", target: self,
                                  action: #selector(hideOverlays))
        hideButton.bezelStyle = .rounded
        hideButton.frame = NSRect(x: 254, y: 96, width: 150, height: 38)

        statusLabel = NSTextField(wrappingLabelWithString: "Ready. No additional permissions are used.")
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.frame = NSRect(x: 24, y: 32, width: 470, height: 48)

        [title, instructions, showButton, hideButton, statusLabel].forEach(content.addSubview)
        window = NSWindow(contentRect: content.frame,
                          styleMask: [.titled, .closable],
                          backing: .buffered,
                          defer: false)
        window.title = "All-Display Overlay Test v2"
        window.contentView = content
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    @objc private func showOverlays() {
        let panels = overlays.showOnEveryScreen()
        statusLabel.stringValue = "Requested \(panels.count) screen panels. Checking after display…"
        // isVisible immediately after orderFrontRegardless() only reports the
        // ordering request on some macOS versions. Inspect after AppKit has
        // completed a display pass so the diagnostic reflects actual window
        // visibility, screen assignment, and frame.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            guard let self else { return }
            let descriptions = panels.map { panel in
                let name = panel.screen?.localizedName ?? "Unknown screen"
                let visible = panel.isVisible
                let frame = NSStringFromRect(panel.frame)
                return "\(name): \(visible ? "visible" : "not visible") · \(frame)"
            }
            let visibleCount = panels.filter(\.isVisible).count
            self.statusLabel.stringValue = "Panels visible: \(visibleCount) / \(panels.count)\n" +
                descriptions.joined(separator: "\n")
            fputs("Overlay v2: visible \(visibleCount) / \(panels.count) screen panels\n", stderr)
            descriptions.forEach { fputs("  \($0)\n", stderr) }
        }
    }

    @objc private func hideOverlays() {
        overlays.hide()
        statusLabel.stringValue = "Outlines hidden."
    }

    func applicationWillTerminate(_ notification: Notification) {
        overlays.hide()
    }
}

@main
struct FullscreenOverlayTestV2App {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.run()
    }
}
