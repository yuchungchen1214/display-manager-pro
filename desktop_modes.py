"""Desktop mode presentation, separate from CADisplay connection modes."""


def format_hz(rate):
    if rate is None:
        return ""
    value = f'{float(rate):.4f}'.rstrip('0').rstrip('.')
    return f'{value} Hz'


def desktop_mode_is_selectable(mode):
    """Whether a CoreGraphics desktop mode can be safely offered for switching."""
    return (mode.get('usableForDesktopGUI') is not False and
            mode.get('modeID') is not None and mode.get('ioFlags') is not None)


def preferred_mode_for_resolution(row, current_rate):
    """Keep the current rate when possible; otherwise choose the nearest lower rate."""
    modes = list(row.get('switchModesByRate', {}).values())
    if not modes:
        return None
    if current_rate is None:
        return min(modes, key=lambda mode: float(mode.get('refreshRate') or 0))
    try:
        current_rate = float(current_rate)
    except (TypeError, ValueError):
        return min(modes, key=lambda mode: float(mode.get('refreshRate') or 0))
    return min(
        modes,
        key=lambda mode: (
            0 if float(mode.get('refreshRate') or 0) <= current_rate + 0.01 else 1,
            -float(mode.get('refreshRate') or 0)
            if float(mode.get('refreshRate') or 0) <= current_rate + 0.01
            else float(mode.get('refreshRate') or 0),
        ),
    )


def resolution_groups(display):
    current = display.get('framebufferMode') or {}
    modes = list(display.get('desktopModes') or [])
    # Preserve the directly read current mode even if enumeration omits it.
    if current and current not in modes:
        modes.append(current)
    groups = {}
    for mode in modes:
        signature = (mode.get('width'), mode.get('height'),
                     mode.get('pixelWidth'), mode.get('pixelHeight'))
        if any(value is None or value <= 0 for value in signature[:2]):
            continue
        if current.get('modeID') is not None and mode.get('modeID') is not None:
            active = str(current['modeID']) == str(mode['modeID'])
        else:
            active = (signature == (current.get('width'), current.get('height'),
                                    current.get('pixelWidth'), current.get('pixelHeight'))
                      and (current.get('refreshRate') is None or
                           mode.get('refreshRate') == current.get('refreshRate')))
        selectable = desktop_mode_is_selectable(mode)
        if not selectable and not active:
            continue
        group = groups.setdefault(signature, {'current': False, 'rates': set(), 'modesByRate': {}})
        group['current'] |= active
        rate = mode.get('refreshRate')
        if rate is not None and rate > 0:
            rate_label = format_hz(rate)
            if selectable or active:
                group['rates'].add(rate_label)
            if selectable:
                group['modesByRate'].setdefault(rate_label, mode)
        if active and rate is not None and rate > 0:
            group['currentRefreshRate'] = format_hz(rate)
    rows = []
    for signature in sorted(groups, reverse=True):
        width, height, pixel_width, pixel_height = signature
        data = groups[signature]
        resolution = f'{width} × {height}'
        if pixel_width and pixel_height:
            scale_x, scale_y = pixel_width / width, pixel_height / height
            scale = f'{scale_x:g}×' if scale_x == scale_y else f'{scale_x:g}× / {scale_y:g}×'
            pixels = f'{pixel_width} × {pixel_height}'
        else:
            scale = pixels = ''
        rates = sorted(data['rates'], key=lambda value: float(value.removesuffix(' Hz')))
        rate_text = ' / '.join(rates)
        values = (resolution, scale, pixels, rate_text)
        rows.append({'values': values, 'current': data['current'],
                     'refreshRateItems': rates,
                     'switchModesByRate': data['modesByRate'],
                     'currentRefreshRate': data.get('currentRefreshRate'),
                     'search': ' '.join(values)})
    return rows
