# Display connection routes

The Connection view can show a display-to-transport route when the system provides
enough evidence to associate them. Route details are supplementary diagnostics,
not a complete inventory of every adapter or physical cable in the signal path.

## Association policy

- Display identity and active transport information are compared using system
  identifiers and available EDID identity fields.
- An association is shown only when the evidence supports a unique match.
- Duplicate identifiers, missing registry relationships, or conflicting values
  leave the route unassigned instead of inferring a connection from device names
  or enumeration order.
- USB and Thunderbolt inventory alone does not prove which device carries a
  particular display's video signal.

The route describes the evidence available from macOS and the display driver. It
may not identify every hub, adapter, port, or physical socket between the Mac and
the display. Hardware and macOS versions can expose different levels of detail.

## Further reading

- [Apple I/O Kit Fundamentals: The I/O Registry](https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/TheRegistry/TheRegistry.html)
- [CableScope](https://github.com/tzzs/cablescope)
- [Thunderbolt DROM format research](https://gist.github.com/joevt/4f6d4d97b560efab9603ac509bf00122)
