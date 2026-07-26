from __future__ import annotations

APP_VERSION = "1.5.0"
PROJECT_FORMAT_VERSION = 1
ANALYSIS_FORMAT_VERSION = 1
COLLABORATION_FORMAT_VERSION = 1
SNAPSHOT_TEMPLATE_FORMAT_VERSION = 1

TILE_SIZE = 1000
WORLD_TILE_COUNT = 2048
TRANSPARENT_TEMPLATE_RGB = (0xDE, 0xFA, 0xCE)
DEFAULT_TILE_URL = "https://backend.wplace.live/files/s0/tiles/{tx}/{ty}.png"
DEFAULT_PIXEL_URL = "https://backend.wplace.live/s0/pixel/{tx}/{ty}?x={px}&y={py}"
USER_AGENT = f"WplaceContributorScanner/{APP_VERSION} (read-only statistics; parallel collaboration)"
