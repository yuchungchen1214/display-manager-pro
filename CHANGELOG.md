# Changelog

## 1.1.0 — 2026-10-06

- Add configurable Mapping overlays, including per-display and all-display controls, in the Arrangement view.
- Add identify overlays across connected displays and keyboard shortcuts that remain available while context menus are open.
- Add display-role controls for individual displays and mirrored groups.
- Add the Shortcut help window, rename the app to Display Manager Pro, and refine Arrangement context menus.
- Add persistent Dark, Light, and System appearance choices with theme-aware selected-item contrast.
- Keep Arrangement and Identify highlights consistent across appearances, and prevent refresh controls from flashing disabled colors.
- Keep the main-window actions labeled Refresh, Arrange, and Export while work is in progress.
- Improve the About icon's Retina rendering and preserve existing friendly-name settings across the app rename.

## 1.0.8 — 2026-10-05

- Show every display name in a mirrored group card, up to three names plus an ellipsis, followed by the mirror group's master resolution.
- Remove single-click switching of displayed mirror-member information in Arrange Displays while keeping display groups raiseable and draggable.
- Keep unavailable mirror-receiver resolutions visible but disabled, with a concise tooltip on hover or click.
- In Arrange context menus, allow resolution operations and provide nested per-display Color mode and ICC profile choices for mirrored groups; disable Orientation for mirror groups.
- Automatically scroll current entries into view after refreshed display data is rendered.

## 1.0.7 — 2026-10-05

- Raise an operated display group above other groups in Arrange Displays while keeping mirror-stack order fixed.
- Show the active ICC profile in Overview and preserve its current marker in the ICC list.
- Refresh display data in the background while the app is open, including current modes and ICC assignments; use system display-change notifications for a full refresh.
- Keep mode tables populated during refresh and avoid rebuilding unchanged rows, preserving their sort order and scroll position.
- Improve current-item auto-scrolling after tab switches, sorting, and background ICC profile loading.
- Add a live, draggable display arrangement view that applies display positions and primary-display changes, and refreshes when macOS reports external arrangement changes.
- Fit every display box inside the arrangement canvas after placement while leaving room around the layout for easier dragging.
- Add optional coordinate axes, a 100-point grid, origin coordinates, and orange crosshair markers.
- Add toggleable snapping to display edges (within 40 points) and 100-point grid positions (within 30 points); edge-aligned crosshair arms indicate the snap direction.
- Show the primary display with a menu-bar motif along its top edge in Arrange Displays.
- Make Arrange Displays non-modal so the main window remains usable while it is open; refresh its display layout when reopened.
- Remove the explanatory subtitle from Arrange Displays.

## 1.0.6 — 2026-10-05

- Add a live, draggable display arrangement view that applies display positions and primary-display changes, and refreshes when macOS reports external arrangement changes.
- Fit every display box inside the arrangement canvas after placement while leaving room around the layout for easier dragging.
- Add optional coordinate axes, a 100-point grid, origin coordinates, and orange crosshair markers.
- Add toggleable snapping to display edges (within 40 points) and 100-point grid positions (within 30 points); edge-aligned crosshair arms indicate the snap direction.
- Show the primary display with a menu-bar motif along its top edge in Arrange Displays.
- Make Arrange Displays non-modal so the main window remains usable while it is open; refresh its display layout when reopened.
- Remove the explanatory subtitle from Arrange Displays.
- Snap a released display immediately to the nearest legal neighboring edge, independent of optional drag snapping.
- Open the friendly-name editor after choosing Set friendly name from the context menu.

## 1.0.5 — 2026-10-05

- Add double-click sorting to Resolution, Color, and All modes columns; current items stay visible after sorting.
- Add mode switching from the Color and All modes lists, plus resolution/refresh-rate and orientation controls.
- Apply a selected mode immediately without a Keep/Revert confirmation dialog; resolution changes are saved, while color, connection-mode, and orientation changes are session-only.
- Refresh display details after mode changes and EDID reads so Overview stays current.
- Align Overview value columns, keep Detected route on one line, and simplify the available-mode label.
- Match table header typography to its rows, use gray column titles, and remove native text-selection menus from Overview and Hardware values.

## 1.0.4 — 2026-10-04

- Combine desktop resolutions and refresh rates in one table, with scale, framebuffer pixels, and current-rate highlighting.
- Automatically scroll to the current item when opening Resolution, Color, and All modes.
- Simplify tab titles and remove redundant tab explanations and search placeholder text.
- Add a consistent app icon in PNG, ICNS, and ICO formats.

## 1.0.0-beta3 — 2026-10-04

- Fix Resolution to enumerate CoreGraphics desktop modes, showing logical desktop dimensions, framebuffer pixels and scale separately. Preserve 1×/2× variants and mark the current CoreGraphics mode.
- Retain CADisplay modes for connection timings and color information; remove the connection-mode Preferred marker from desktop resolutions.

- Trace displays through their active CIO owner into a uniquely identified Thunderbolt Dock.
- Capture Thunderbolt transport properties and registry ancestry.
- Emit a per-display route only when the active transport uniquely matches a complete EDID.
- Never label a CIO owner as a Dock without a matching Thunderbolt switch UID; physical dock sockets are not identified.
- List all `CADisplay.availableModes` instead of presenting an app-filtered subset as the full color-mode list.
- Add separate Resolution, Refresh rates and Color modes summaries; grouped counts refer to original system mode entries.
- Remove unqueried DPCD/DSC/DDC/CEC and EOTF placeholder rows and omit absent values from the UI.
- Recheck topology after capture to flag hotplug or routing changes.
- Format displayed refresh rates without unnecessary trailing zeroes (for example, `24 Hz` and `23.98 Hz`).

## 1.0.0-beta2 — 2026-10-04

- Add per-service OS/I2C EDID capture, binary exports, validation and partial CTA decoding.
- Record registry ancestry, unique-EDID port matches and driver-reported link status.
- Export current ColorSync ICC profiles and desktop/framebuffer state.
- Add plain EDID, Connection and Profile views, with explicit unavailable/ambiguous states.
- Preserve the original v1.0.0-beta snapshot; this version includes the extended read-only diagnostics.

## 1.0.0-beta — 2026-10-04

- First locally versioned beta of the PySide6 display inspector.
- Lists active displays and their current driver-reported output mode.
- Shows current-timing color modes and the complete available mode list.
- Preserves raw CoreDisplay, AppleCLCD2, DCPAVServiceProxy, USB, and Thunderbolt captures locally.
- Read-only; connection-mode switching is not implemented.
