// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "DisplayModeProbe",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "display-mode-probe", targets: ["DisplayModeProbe"])],
    targets: [.executableTarget(name: "DisplayModeProbe")]
)
