# Project layout

- `Sources/`: Swift package sources and native display helpers
- Root Python files: the UI entry point and display diagnostics modules
- `tools/`: Objective-C probes, test utilities, and overlay sources
- `tools/test_*.py`: Python tests for capture, topology, diagnostics, arrangement state, and display modes
- `assets/`: application icons, screenshots, and DMG artwork
- `docs/`: technical notes and release documentation
- `output/`: local display captures and machine-specific diagnostics; treat as private data
- `../../Artifacts/`: local build/release staging; currently has no Display Manager Pro build artifacts
- `../../Archives/display_mode_probe/`: historical bundles and prototype builds retained under the original project name
- NAS `Releases/artifacts/display-manager-pro/`: archived `.build`, PyInstaller `build`, and versioned release output, including v1.1.0

The build scripts may recreate `.build`, `build`, and `dist-v<VERSION>` in this project. Those directories are ignored and should not be committed.
