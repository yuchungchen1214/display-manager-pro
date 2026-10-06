# All-Display Overlay Test v2

This is a separate, normal foreground macOS app. It does not modify or launch
Display Manager Pro. Press **Show on all displays** to create one
high-contrast, click-through outline on every `NSScreen`; the outlines remain
until **Hide outlines** or the tool is quit.

Unlike the first test, this version does not run as an accessory/agent and does
not change activation policy while showing overlays. The control window checks
panel visibility, screen assignment, and frame after AppKit has had time to
present them; it also writes the per-screen results to the launching terminal.

Run it with:

```sh
zsh tools/fullscreen-overlay-test-v2/run.sh
```

No screen recording, Accessibility, or other privacy permission is requested.
