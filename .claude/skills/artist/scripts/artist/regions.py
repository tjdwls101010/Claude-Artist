"""Percent regions to pixel boxes, one rounding rule for every command that crops or patches."""

from __future__ import annotations

import math


def to_pixels(region: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    """`(x, y, w, h)` in percent → `(x0, y0, x1, y1)` in pixels. Start rounds down and end rounds up, so the box always covers the region asked for."""
    x, y, w, h = region
    x0 = max(0, math.floor(x * width / 100))
    y0 = max(0, math.floor(y * height / 100))
    x1 = min(width, math.ceil((x + w) * width / 100))
    y1 = min(height, math.ceil((y + h) * height / 100))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"region {region} is empty on a {width}x{height} image")
    return x0, y0, x1, y1
