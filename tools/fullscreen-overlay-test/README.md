# Fullscreen Overlay Test

This standalone macOS test app briefly draws a click-through orange outline on
every connected display. It is an accessory/agent app so the main Display Mode
Inspector app and its Dock/menu-bar behavior remain unchanged.

Run `zsh tools/fullscreen-overlay-test/run.sh`, then click **Outline all displays
(10 seconds)** and quickly switch Spaces or enter a full-screen app. The
**Hide outlines** button dismisses the panels immediately. No screen capture,
accessibility, or other privacy permission is requested.

The test intentionally uses a high window level. Keep the outline brief and
use it only to check visibility above Spaces and full-screen content.
