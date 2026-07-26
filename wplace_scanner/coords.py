from __future__ import annotations

import math
from .constants import TILE_SIZE, WORLD_TILE_COUNT


def tile_pixel_to_global(tx: int, ty: int, px: int, py: int) -> tuple[int, int]:
    return tx * TILE_SIZE + px, ty * TILE_SIZE + py


def global_to_tile_pixel(gx: int, gy: int) -> tuple[int, int, int, int]:
    return gx // TILE_SIZE, gy // TILE_SIZE, gx % TILE_SIZE, gy % TILE_SIZE


def tile_pixel_to_latlng(tx: int, ty: int, px: int, py: int) -> tuple[float, float]:
    """Convert Wplace tile/pixel coordinates to Web-Mercator latitude/longitude.

    Wplace's s0 canvas uses a 2048 x 2048 tile grid. A half-pixel offset points
    at the center of the selected pixel.
    """
    gx = tx * TILE_SIZE + px + 0.5
    gy = ty * TILE_SIZE + py + 0.5
    world_size = WORLD_TILE_COUNT * TILE_SIZE
    x = gx / world_size
    y = gy / world_size
    lng = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y))))
    return lat, lng


def wplace_link(tx: int, ty: int, px: int, py: int, zoom: float = 18.0) -> str:
    lat, lng = tile_pixel_to_latlng(tx, ty, px, py)
    return f"https://wplace.live/?lat={lat:.10f}&lng={lng:.10f}&zoom={zoom:g}&select=1"
