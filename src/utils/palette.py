# -*- coding: utf-8 -*-
"""Per-plane colour palette.

Deliberately free of any STK import: this module hands back plain ``(r, g, b)``
tuples, and the STK layer wraps them with ``agi.stk12.utilities.colors``.

Hues are walked with the golden-ratio sequence, which maximises the colour
distance between *consecutive* planes -- exactly what you want when the eye has
to tell one neighbouring orbital ring from the next.
"""

from __future__ import annotations

import colorsys
from typing import Sequence

#: Conjugate of the golden ratio; the classic low-discrepancy hue increment.
_GOLDEN_RATIO_CONJUGATE = 0.618033988749895

RGB = tuple[int, int, int]


def hsv_to_rgb(h: float, s: float, v: float) -> RGB:
    """HSV (each 0..1) to an 8-bit RGB triple."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return round(r * 255), round(g * 255), round(b * 255)


def distinct_rgb_colors(
    count: int,
    *,
    saturation: float = 0.80,
    value: float = 0.95,
    hue_offset: float = 0.0,
) -> list[RGB]:
    """``count`` visually distinct colours, ordered so neighbours differ most.

    Alternating light/dark on every other colour adds a second, luminance-based
    cue, which keeps planes apart even for someone who cannot separate the hues.
    """
    if count < 1:
        return []

    colors: list[RGB] = []
    hue = hue_offset
    for index in range(count):
        # Two interleaved brightness levels => adjacent planes also differ in value.
        v = value if index % 2 == 0 else value * 0.62
        s = saturation if index % 2 == 0 else min(1.0, saturation * 1.15)
        colors.append(hsv_to_rgb(hue, s, v))
        hue = (hue + _GOLDEN_RATIO_CONJUGATE) % 1.0
    return colors


def to_hex(color: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)


def describe(colors: Sequence[RGB]) -> str:
    return ", ".join(to_hex(c) for c in colors)
