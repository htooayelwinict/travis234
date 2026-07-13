"""Terminal color helpers ported from Pi TUI."""

from __future__ import annotations

import re


_OSC11_BACKGROUND_COLOR_RESPONSE = re.compile(r"^\x1b\]11;([^\x07\x1b]*)(?:\x07|\x1b\\)$", re.I)


def _hex_to_rgb(hex_value: str) -> dict[str, int]:
    normalized = hex_value[1:] if hex_value.startswith("#") else hex_value
    return {
        "r": int(normalized[0:2], 16),
        "g": int(normalized[2:4], 16),
        "b": int(normalized[4:6], 16),
    }


def _parse_osc_hex_channel(channel: str) -> int | None:
    if not re.match(r"^[0-9a-f]+$", channel, re.I):
        return None
    maximum = 16 ** len(channel) - 1
    if maximum <= 0:
        return None
    return int((int(channel, 16) / maximum) * 255 + 0.5)


def is_osc11_background_color_response(data: str) -> bool:
    return bool(_OSC11_BACKGROUND_COLOR_RESPONSE.match(data))


def parse_osc11_background_color(data: str) -> dict[str, int] | None:
    match = _OSC11_BACKGROUND_COLOR_RESPONSE.match(data)
    if not match:
        return None

    value = match.group(1).strip()
    if value.startswith("#"):
        hex_value = value[1:]
        if re.match(r"^[0-9a-f]{6}$", hex_value, re.I):
            return _hex_to_rgb(value)
        if re.match(r"^[0-9a-f]{12}$", hex_value, re.I):
            r = _parse_osc_hex_channel(hex_value[0:4])
            g = _parse_osc_hex_channel(hex_value[4:8])
            b = _parse_osc_hex_channel(hex_value[8:12])
            return {"r": r, "g": g, "b": b} if r is not None and g is not None and b is not None else None
        return None

    rgb_value = re.sub(r"^rgba?:", "", value, flags=re.I)
    parts = rgb_value.split("/")
    if len(parts) < 3:
        return None
    r = _parse_osc_hex_channel(parts[0])
    g = _parse_osc_hex_channel(parts[1])
    b = _parse_osc_hex_channel(parts[2])
    return {"r": r, "g": g, "b": b} if r is not None and g is not None and b is not None else None


isOsc11BackgroundColorResponse = is_osc11_background_color_response
parseOsc11BackgroundColor = parse_osc11_background_color
