# Display data and capture

Display Manager Pro reads display state reported by macOS and the active display
drivers. The graphical app can inspect this information and provides controls for
settings that the current system reports as available. The separate `capture.py`
command collects a diagnostic snapshot; it does not change display settings.

## Data sources

- CoreGraphics supplies online display state, desktop dimensions, framebuffer size,
  scale, orientation, arrangement, and desktop display modes.
- CoreDisplay and QuartzCore provide additional display and connection-mode details
  on systems where those interfaces are available.
- ColorSync provides active profile information. The explicit Export workflow can
  collect profile and hardware details for a diagnostic report.
- IORegistry and EDID sources may provide hardware identity and connection evidence.
  A route is shown only when the available identifiers support an unambiguous match.

The app keeps desktop modes separate from connection modes: they describe different
parts of the display pipeline and their identifiers are not interchangeable. Mode
lists reflect what macOS and the driver report; the app does not claim that every
reported entry is usable in every display configuration.

## Capture from Terminal

On macOS, with Python 3 and Apple's Command Line Tools installed:

```sh
python3 capture.py
```

Without `--out`, the command prints a summary and does not save a report. To save a
capture, provide a new destination directory:

```sh
python3 capture.py --out /path/to/new-folder
```

The destination must not already exist. Captures may contain display identifiers,
EDID, registry paths, profile metadata, and other device information. Review them
before sharing.

## Known limits

- The report reflects what macOS and drivers expose; it is not an electrical
  measurement of the video signal after an adapter or dock.
- A USB or Thunderbolt device appearing in the system inventory does not by itself
  prove that it carries a particular display's video signal.
- Duplicate or incomplete identifiers can prevent a unique display-to-transport
  association. In that case, the app leaves the route unavailable rather than
  guessing.
- Some supplementary data sources are undocumented or vary by hardware and macOS
  release. Their availability and behavior are not guaranteed.
- EDID capability fields describe reported capabilities, not necessarily the mode
  currently transmitted. The app does not infer unreported signal properties.
