from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .constants import SNAPSHOT_TEMPLATE_FORMAT_VERSION, TILE_SIZE

_TILE_KEY_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


@dataclass(frozen=True)
class TemplateTile:
    tx: int
    ty: int
    start_px: int
    start_py: int
    image_b64: str
    image_scale: int = 3


@dataclass(frozen=True)
class BlueMarbleTemplate:
    source_name: str
    template_key: str
    name: str
    coords: tuple[int, int, int, int]
    width: int
    height: int
    valid_pixel_count: int
    tiles: tuple[TemplateTile, ...]
    source_hash: str
    match_mode: str = "color"
    template_format: str = "blue-marble"

    @property
    def project_id(self) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.name).strip("._") or "template"
        return f"{safe}-{self.source_hash[:12]}"


def _load_json_from_path(path: Path) -> tuple[dict, str, bytes]:
    raw = path.read_bytes()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".json") and not n.endswith("/")]
            if not names:
                raise ValueError("ZIP 안에 JSON 파일이 없습니다.")

            # Blue Marble exports can include more than one JSON file, and an
            # unrelated metadata file may also be named template.json. Inspect
            # every JSON and select a document that actually contains templates
            # instead of relying on its filename.
            candidates: list[tuple[int, int, str, dict, bytes]] = []
            decode_errors: list[str] = []
            for name in names:
                try:
                    json_raw = zf.read(name)
                    doc = json.loads(json_raw.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    decode_errors.append(f"{name}: {exc}")
                    continue
                if not isinstance(doc, dict):
                    continue
                templates = doc.get("templates")
                if not isinstance(templates, dict) or not templates:
                    continue
                is_scanner_template = int(doc.get("format") == "WplaceContributorScannerTemplate")
                candidates.append((is_scanner_template, zf.getinfo(name).file_size, name, doc, json_raw))

            if not candidates:
                detail = f" ({'; '.join(decode_errors[:3])})" if decode_errors else ""
                raise ValueError(f"ZIP 안에서 Blue Marble templates 객체가 있는 JSON을 찾지 못했습니다.{detail}")

            _, _, name, doc, json_raw = max(candidates, key=lambda item: (item[0], item[1]))
            # Project identity must be based on the selected JSON document, not on
            # the outer ZIP container. The same Blue Marble template therefore
            # resolves to the same project whether imported as JSON or ZIP.
            return doc, f"{path.name}:{name}", json_raw
    doc = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("템플릿 JSON 최상위 항목은 객체여야 합니다.")
    return doc, path.name, raw


def _parse_coords(value: object) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError("템플릿 coords 형식을 알 수 없습니다.")
    if len(parts) != 4:
        raise ValueError("coords에는 Tl X, Tl Y, Px X, Px Y 네 값이 필요합니다.")
    tx, ty, px, py = (int(v) for v in parts)
    if not (0 <= px < TILE_SIZE and 0 <= py < TILE_SIZE):
        raise ValueError(f"픽셀 좌표가 범위를 벗어났습니다: {px}, {py}")
    return tx, ty, px, py


def load_blue_marble_templates(path: str | Path) -> list[BlueMarbleTemplate]:
    path = Path(path)
    doc, source_name, source_raw = _load_json_from_path(path)
    document_format = str(doc.get("format") or "blue-marble")
    if document_format == "WplaceContributorScannerTemplate":
        if int(doc.get("formatVersion", -1)) != SNAPSHOT_TEMPLATE_FORMAT_VERSION:
            raise ValueError(
                "지원하지 않는 스크린샷 템플릿 형식입니다. "
                "Wplace Contributor Scanner 1.5에서 생성한 템플릿을 사용하세요."
            )
        if set(doc) != {"format", "formatVersion", "sourceDigest", "templates"}:
            raise ValueError("스크린샷 템플릿 항목 구성이 올바르지 않습니다.")
    templates_obj = doc.get("templates")
    if not isinstance(templates_obj, dict) or not templates_obj:
        raise ValueError("Blue Marble templates 객체를 찾지 못했습니다.")

    source_digest = hashlib.sha256(source_raw).hexdigest()
    declared_digest = str(doc.get("sourceDigest") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", declared_digest):
        source_digest = declared_digest
    result: list[BlueMarbleTemplate] = []
    for key, item in templates_obj.items():
        if not isinstance(item, dict):
            continue
        if document_format == "WplaceContributorScannerTemplate" and set(item) != {
            "name", "coords", "validPixelCount", "imageScale", "matchMode", "tiles"
        }:
            raise ValueError("스크린샷 템플릿 프로젝트 항목 구성이 올바르지 않습니다.")
        coords = _parse_coords(item.get("coords"))
        tiles_obj = item.get("tiles")
        if not isinstance(tiles_obj, dict) or not tiles_obj:
            raise ValueError(f"템플릿 {key!r}에 tiles 데이터가 없습니다.")

        parsed_tiles: list[TemplateTile] = []
        image_scale = int(item.get("imageScale", 3))
        if image_scale not in (1, 3):
            raise ValueError(f"템플릿 {key!r}의 imageScale은 1 또는 3이어야 합니다.")
        match_mode = str(item.get("matchMode") or "color").strip().lower()
        if match_mode not in ("color", "region"):
            raise ValueError(f"템플릿 {key!r}의 matchMode는 color 또는 region이어야 합니다.")
        min_gx = min_gy = None
        max_gx = max_gy = None
        counted_valid = 0

        for tile_key, image_b64 in tiles_obj.items():
            match = _TILE_KEY_RE.match(str(tile_key))
            if not match:
                raise ValueError(f"알 수 없는 타일 키 형식: {tile_key}")
            tx, ty, start_px, start_py = map(int, match.groups())
            if not isinstance(image_b64, str):
                raise ValueError(f"타일 {tile_key}의 이미지가 Base64 문자열이 아닙니다.")
            try:
                image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            except Exception as exc:  # pragma: no cover - Pillow gives many concrete errors
                raise ValueError(f"타일 {tile_key} PNG를 읽지 못했습니다: {exc}") from exc
            if image.width % image_scale or image.height % image_scale:
                raise ValueError(f"타일 {tile_key} 크기가 imageScale={image_scale} 형식과 맞지 않습니다: {image.size}")
            logical_w, logical_h = image.width // image_scale, image.height // image_scale
            if start_px + logical_w > TILE_SIZE or start_py + logical_h > TILE_SIZE:
                raise ValueError(f"타일 {tile_key}가 1000x1000 경계를 벗어납니다.")

            gx0, gy0 = tx * TILE_SIZE + start_px, ty * TILE_SIZE + start_py
            gx1, gy1 = gx0 + logical_w, gy0 + logical_h
            min_gx = gx0 if min_gx is None else min(min_gx, gx0)
            min_gy = gy0 if min_gy is None else min(min_gy, gy0)
            max_gx = gx1 if max_gx is None else max(max_gx, gx1)
            max_gy = gy1 if max_gy is None else max(max_gy, gy1)
            # Blue Marble exports validPixelCount. Only perform the expensive center-dot
            # count when that field is absent.
            if item.get("validPixelCount") is None:
                rgba_image = image.convert("RGBA")
                pixels = rgba_image.load()
                counted_valid += sum(
                    1
                    for y in range(logical_h)
                    for x in range(logical_w)
                    if pixels[x * image_scale + image_scale // 2, y * image_scale + image_scale // 2][3] > 0
                )
                rgba_image.close()
            image.close()
            parsed_tiles.append(TemplateTile(tx, ty, start_px, start_py, image_b64, image_scale))

        assert min_gx is not None and min_gy is not None and max_gx is not None and max_gy is not None
        origin_gx = coords[0] * TILE_SIZE + coords[2]
        origin_gy = coords[1] * TILE_SIZE + coords[3]
        if (min_gx, min_gy) != (origin_gx, origin_gy):
            raise ValueError(
                f"coords 원점({origin_gx},{origin_gy})과 타일 원점({min_gx},{min_gy})이 다릅니다."
            )
        width, height = max_gx - min_gx, max_gy - min_gy
        declared = item.get("validPixelCount")
        declared_valid = int(declared) if declared is not None else counted_valid
        valid = declared_valid
        result.append(
            BlueMarbleTemplate(
                source_name=source_name,
                template_key=str(key),
                name=str(item.get("name") or key),
                coords=coords,
                width=width,
                height=height,
                valid_pixel_count=valid,
                tiles=tuple(sorted(parsed_tiles, key=lambda t: (t.ty, t.tx, t.start_py, t.start_px))),
                source_hash=hashlib.sha256((source_digest + "\0" + str(key)).encode()).hexdigest(),
                match_mode=match_mode,
                template_format=document_format,
            )
        )
    if not result:
        raise ValueError("사용 가능한 Blue Marble 템플릿이 없습니다.")
    return result


def decode_tile(tile: TemplateTile) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(tile.image_b64))).convert("RGBA")

