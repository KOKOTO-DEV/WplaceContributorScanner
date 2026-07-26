from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .constants import DEFAULT_TILE_URL, SNAPSHOT_TEMPLATE_FORMAT_VERSION, TILE_SIZE
from .coords import global_to_tile_pixel, tile_pixel_to_global
from .network import WplaceClient

CAPTURE_FORMAT = "WplaceContributorScannerSnapshotCapture"
TEMPLATE_FORMAT = "WplaceContributorScannerTemplate"
MAX_CAPTURE_PIXELS = 32_000_000
MAX_CAPTURE_SIDE = 12_000


@dataclass(frozen=True)
class CaptureBounds:
    left_gx: int
    top_gy: int
    right_gx: int
    bottom_gy: int

    @property
    def width(self) -> int:
        return self.right_gx - self.left_gx + 1

    @property
    def height(self) -> int:
        return self.bottom_gy - self.top_gy + 1

    @property
    def top_left(self) -> tuple[int, int, int, int]:
        return global_to_tile_pixel(self.left_gx, self.top_gy)

    @property
    def bottom_right(self) -> tuple[int, int, int, int]:
        return global_to_tile_pixel(self.right_gx, self.bottom_gy)

    def as_json(self) -> dict[str, Any]:
        return {
            "leftGx": self.left_gx,
            "topGy": self.top_gy,
            "rightGx": self.right_gx,
            "bottomGy": self.bottom_gy,
        }


def bounds_from_json(value: Any) -> CaptureBounds:
    if not isinstance(value, dict) or set(value) != {"leftGx", "topGy", "rightGx", "bottomGy"}:
        raise ValueError("스크린샷 템플릿의 캡처 범위 정보가 올바르지 않습니다.")
    bounds = CaptureBounds(
        int(value["leftGx"]), int(value["topGy"]),
        int(value["rightGx"]), int(value["bottomGy"]),
    )
    if bounds.width <= 0 or bounds.height <= 0:
        raise ValueError("스크린샷 템플릿의 캡처 범위 정보가 올바르지 않습니다.")
    return bounds


def bounds_from_payload(payload: dict[str, Any]) -> CaptureBounds:
    def point(prefix: str) -> tuple[int, int]:
        tx = int(payload[f"{prefix}TileX"])
        ty = int(payload[f"{prefix}TileY"])
        px = int(payload[f"{prefix}PixelX"])
        py = int(payload[f"{prefix}PixelY"])
        if not (0 <= px < TILE_SIZE and 0 <= py < TILE_SIZE):
            raise ValueError("픽셀 좌표는 0~999 범위여야 합니다.")
        return tile_pixel_to_global(tx, ty, px, py)

    left_gx, top_gy = point("topLeft")
    right_gx, bottom_gy = point("bottomRight")
    if right_gx < left_gx or bottom_gy < top_gy:
        raise ValueError("우하단 좌표는 좌상단 좌표보다 오른쪽 아래에 있어야 합니다.")
    bounds = CaptureBounds(left_gx, top_gy, right_gx, bottom_gy)
    if bounds.width > MAX_CAPTURE_SIDE or bounds.height > MAX_CAPTURE_SIDE:
        raise ValueError(f"캡처 한 변은 최대 {MAX_CAPTURE_SIDE:,}픽셀까지 지원합니다.")
    if bounds.width * bounds.height > MAX_CAPTURE_PIXELS:
        raise ValueError(f"캡처 범위는 최대 {MAX_CAPTURE_PIXELS:,}픽셀까지 지원합니다.")
    return bounds


class SnapshotTemplateService:
    def __init__(self, data_root: Path, templates_root: Path):
        self.root = data_root / "captures"
        self.templates_root = templates_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.templates_root.mkdir(parents=True, exist_ok=True)

    def _capture_dir(self, capture_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", capture_id):
            raise ValueError("캡처 ID가 올바르지 않습니다.")
        path = self.root / capture_id
        if not path.is_dir():
            raise FileNotFoundError("캡처 작업을 찾지 못했습니다. 다시 캡처하세요.")
        return path

    def capture(
        self, bounds: CaptureBounds, *, tile_url: str = DEFAULT_TILE_URL, timeout: float = 30.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        capture_id = secrets.token_urlsafe(12).replace("-", "_")
        path = self.root / capture_id
        tiles_dir = path / "tiles"
        path.mkdir(parents=True, exist_ok=False)
        tiles_dir.mkdir()
        client = WplaceClient(tile_url, "", timeout)
        canvas = Image.new("RGBA", (bounds.width, bounds.height), (0, 0, 0, 0))
        min_tx = bounds.left_gx // TILE_SIZE
        max_tx = bounds.right_gx // TILE_SIZE
        min_ty = bounds.top_gy // TILE_SIZE
        max_ty = bounds.bottom_gy // TILE_SIZE
        total_tiles = (max_tx - min_tx + 1) * (max_ty - min_ty + 1)
        done = 0
        try:
            for ty in range(min_ty, max_ty + 1):
                for tx in range(min_tx, max_tx + 1):
                    tile_path = tiles_dir / f"{tx}_{ty}.png"
                    client.download_tile(tx, ty, tile_path)
                    tile = Image.open(tile_path).convert("RGBA")
                    if tile.size != (TILE_SIZE, TILE_SIZE):
                        tile.close()
                        raise RuntimeError(f"현재 타일 {tx},{ty} 크기가 {tile.size}입니다; 1000x1000이 필요합니다.")
                    tile_left = tx * TILE_SIZE
                    tile_top = ty * TILE_SIZE
                    left = max(bounds.left_gx, tile_left)
                    top = max(bounds.top_gy, tile_top)
                    right = min(bounds.right_gx + 1, tile_left + TILE_SIZE)
                    bottom = min(bounds.bottom_gy + 1, tile_top + TILE_SIZE)
                    crop = tile.crop((left - tile_left, top - tile_top, right - tile_left, bottom - tile_top))
                    canvas.alpha_composite(crop, (left - bounds.left_gx, top - bounds.top_gy))
                    crop.close()
                    tile.close()
                    done += 1
                    if done < total_tiles:
                        time.sleep(max(0.1, min(5.0, float(interval_seconds))))
            original = path / "original.png"
            canvas.save(original, "PNG", optimize=True)
            meta = {
                "format": CAPTURE_FORMAT,
                "formatVersion": SNAPSHOT_TEMPLATE_FORMAT_VERSION,
                "captureId": capture_id,
                "bounds": bounds.as_json(),
            }
            (path / "capture.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return meta
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        finally:
            canvas.close()

    def metadata(self, capture_id: str) -> dict[str, Any]:
        path = self._capture_dir(capture_id)
        return json.loads((path / "capture.json").read_text(encoding="utf-8"))

    def image_path(self, capture_id: str, kind: str = "original") -> Path:
        path = self._capture_dir(capture_id)
        filename = "edited.png" if kind == "edited" else "original.png"
        image = path / filename
        if not image.exists():
            raise FileNotFoundError("캡처 이미지를 찾지 못했습니다.")
        return image

    @staticmethod
    def _safe_name(name: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z가-힣ぁ-んァ-ン一-龥._ -]+", "_", name).strip(" ._")
        return cleaned[:100] or "snapshot-template"


    def cleanup(self, capture_id: str) -> None:
        path = self._capture_dir(capture_id)
        shutil.rmtree(path, ignore_errors=True)

    def reopen_template_capture(
        self, template_path: Path, *, cache_dir: Path | None = None,
        template_name: str = "", match_mode: str = "region",
    ) -> dict[str, Any]:
        """Restore a generated snapshot template into a temporary editable capture.

        The original project is not mutated. Saving the edited mask creates a new
        template project, which keeps existing scan progress safe.
        """
        template_path = Path(template_path)
        if not template_path.is_file():
            raise FileNotFoundError("스크린샷 템플릿 원본 ZIP을 찾지 못했습니다.")
        try:
            with zipfile.ZipFile(template_path) as zf:
                required = {"capture.json", "original.png", "mask.png"}
                missing = sorted(required.difference(zf.namelist()))
                if missing:
                    raise ValueError(f"스크린샷 템플릿 ZIP에 필요한 파일이 없습니다: {', '.join(missing)}")
                meta = json.loads(zf.read("capture.json").decode("utf-8"))
                if not isinstance(meta, dict) or set(meta) != {"format", "formatVersion", "captureId", "bounds"}:
                    raise ValueError("스크린샷 템플릿의 capture.json 형식이 올바르지 않습니다.")
                if meta.get("format") != CAPTURE_FORMAT or int(meta.get("formatVersion", -1)) != SNAPSHOT_TEMPLATE_FORMAT_VERSION:
                    raise ValueError(
                        "지원하지 않는 스크린샷 템플릿 형식입니다. "
                        "Wplace Contributor Scanner 1.5에서 생성한 템플릿을 사용하세요."
                    )
                original_raw = zf.read("original.png")
                mask_raw = zf.read("mask.png")
        except zipfile.BadZipFile as exc:
            raise ValueError("스크린샷 템플릿 ZIP이 손상되었습니다.") from exc

        bounds = bounds_from_json(meta.get("bounds"))
        expected_size = (bounds.width, bounds.height)
        for label, raw in (("원본", original_raw), ("마스크", mask_raw)):
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != "PNG" or image.size != expected_size:
                        raise ValueError(
                            f"{label} 이미지 크기가 캡처 범위와 다릅니다. "
                            f"예상 {expected_size[0]}x{expected_size[1]}, 실제 {image.width}x{image.height}"
                        )
                    image.verify()
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"{label} 이미지를 읽지 못했습니다: {exc}") from exc

        capture_id = secrets.token_urlsafe(12).replace("-", "_")
        capture_path = self.root / capture_id
        tiles_path = capture_path / "tiles"
        capture_path.mkdir(parents=True, exist_ok=False)
        tiles_path.mkdir()
        try:
            (capture_path / "original.png").write_bytes(original_raw)
            (capture_path / "edited.png").write_bytes(mask_raw)
            if cache_dir and Path(cache_dir).is_dir():
                for tile in Path(cache_dir).glob("*.png"):
                    shutil.copy2(tile, tiles_path / tile.name)
            restored = dict(meta)
            restored["captureId"] = capture_id
            (capture_path / "capture.json").write_text(
                json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            response = dict(restored)
            response["templateName"] = template_name
            response["matchMode"] = match_mode
            return response
        except Exception:
            shutil.rmtree(capture_path, ignore_errors=True)
            raise

    def create_template(self, capture_id: str, edited_png: bytes, *, name: str, match_mode: str) -> Path:
        capture_path = self._capture_dir(capture_id)
        meta = self.metadata(capture_id)
        if not isinstance(meta, dict) or set(meta) != {"format", "formatVersion", "captureId", "bounds"}:
            raise ValueError("스크린샷 템플릿의 capture.json 형식이 올바르지 않습니다.")
        if meta.get("format") != CAPTURE_FORMAT or int(meta.get("formatVersion", -1)) != SNAPSHOT_TEMPLATE_FORMAT_VERSION:
            raise ValueError(
                "지원하지 않는 스크린샷 템플릿 형식입니다. "
                "Wplace Contributor Scanner 1.5에서 생성한 템플릿을 사용하세요."
            )
        bounds = bounds_from_json(meta["bounds"])
        match_mode = str(match_mode).strip().lower()
        if match_mode not in ("region", "color"):
            raise ValueError("계산 모드는 region 또는 color여야 합니다.")
        try:
            image = Image.open(io.BytesIO(edited_png)).convert("RGBA")
        except Exception as exc:
            raise ValueError(f"편집 PNG를 읽지 못했습니다: {exc}") from exc
        if image.size != (bounds.width, bounds.height):
            image.close()
            raise ValueError(
                f"편집 이미지 크기가 캡처 범위와 다릅니다. 예상 {bounds.width}x{bounds.height}, 실제 {image.width}x{image.height}"
            )
        alpha = image.getchannel("A")
        # The editor mask is binary: erased pixels are transparent and retained pixels are opaque.
        binary_alpha = alpha.point(lambda value: 255 if value >= 128 else 0)
        alpha.close()
        image.putalpha(binary_alpha)
        alpha_values = binary_alpha.get_flattened_data() if hasattr(binary_alpha, "get_flattened_data") else binary_alpha.getdata()
        valid_pixels = sum(1 for value in alpha_values if value > 0)
        binary_alpha.close()
        if valid_pixels <= 0:
            image.close()
            raise ValueError("남아 있는 불투명 픽셀이 없습니다. 그림 영역을 하나 이상 남겨야 합니다.")

        safe_name = self._safe_name(name)
        digest = hashlib.sha256(edited_png + b"\0" + match_mode.encode("ascii") + b"\0" + json.dumps(bounds.as_json(), sort_keys=True).encode()).hexdigest()
        template_key = f"snapshot-{digest[:16]}"
        tiles: dict[str, str] = {}
        min_tx = bounds.left_gx // TILE_SIZE
        max_tx = bounds.right_gx // TILE_SIZE
        min_ty = bounds.top_gy // TILE_SIZE
        max_ty = bounds.bottom_gy // TILE_SIZE
        for ty in range(min_ty, max_ty + 1):
            for tx in range(min_tx, max_tx + 1):
                tile_left = tx * TILE_SIZE
                tile_top = ty * TILE_SIZE
                left = max(bounds.left_gx, tile_left)
                top = max(bounds.top_gy, tile_top)
                right = min(bounds.right_gx + 1, tile_left + TILE_SIZE)
                bottom = min(bounds.bottom_gy + 1, tile_top + TILE_SIZE)
                crop = image.crop((left - bounds.left_gx, top - bounds.top_gy, right - bounds.left_gx, bottom - bounds.top_gy))
                output = io.BytesIO()
                crop.save(output, "PNG", optimize=True)
                crop.close()
                key = f"{tx}, {ty}, {left - tile_left}, {top - tile_top}"
                tiles[key] = base64.b64encode(output.getvalue()).decode("ascii")
        edited_path = capture_path / "edited.png"
        image.save(edited_path, "PNG", optimize=True)
        image.close()

        tx, ty, px, py = bounds.top_left
        doc = {
            "format": TEMPLATE_FORMAT,
            "formatVersion": SNAPSHOT_TEMPLATE_FORMAT_VERSION,
            "sourceDigest": digest,
            "templates": {
                template_key: {
                    "name": safe_name,
                    "coords": [tx, ty, px, py],
                    "validPixelCount": valid_pixels,
                    "imageScale": 1,
                    "matchMode": match_mode,
                    "tiles": tiles,
                }
            },
        }
        filename = f"snapshot-{safe_name}-{digest[:12]}.zip"
        out = self.templates_root / filename
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("template.json", json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
            zf.writestr("capture.json", json.dumps(meta, ensure_ascii=False, indent=2))
            zf.write(capture_path / "original.png", "original.png")
            zf.write(edited_path, "mask.png")
        return out
