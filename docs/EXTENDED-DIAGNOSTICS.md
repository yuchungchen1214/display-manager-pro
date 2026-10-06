# Extended diagnostic data

An explicit diagnostic export can include supplementary display, profile, EDID,
IORegistry, USB, and Thunderbolt information when macOS exposes it. These fields
are intended to help investigate a particular setup; they are not guaranteed to be
available on every Mac or display.

## Interpretation

- OS-provided EDID and I2C-read EDID are kept as separate sources. They may differ,
  be incomplete, or be unavailable.
- EDID video and HDR fields describe capabilities encoded in the report. They do
  not establish which signal format is currently being transmitted.
- Driver properties such as link rate, lane count, or compression-related fields
  are reported as evidence, not treated as proof of a specific active signal unless
  the system provides direct, unambiguous evidence.
- A matching display identifier can support a display-to-transport association.
  Duplicate or incomplete identifiers remain ambiguous; USB/Thunderbolt inventory
  alone is not sufficient to assign a display route.
- Raw registry and driver values may be useful for troubleshooting but can be
  hardware-specific and are not necessarily comparable across models or macOS
  releases.

Optional probes run separately from the basic display listing so a failure to read
supplementary data does not imply that the display itself is unavailable. Reports
can contain sensitive device identifiers and system paths. Review exported files
before sharing them.

## References

The implementation uses public macOS frameworks where available and supplements
them with system interfaces whose behavior may be undocumented. Relevant external
references include:

- [Apple I/O Kit Fundamentals: The I/O Registry](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/TheRegistry/TheRegistry.html)
- [v4l-utils EDID decoder](https://github.com/gjasny/v4l-utils/tree/master/utils/edid-decode)

These references inform data interpretation. The application does not execute
BetterDisplay or incorporate its binaries.
