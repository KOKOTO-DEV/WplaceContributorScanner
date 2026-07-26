from __future__ import annotations

import mmap
import os
import struct
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

from .constants import TILE_SIZE
from .template import BlueMarbleTemplate, decode_tile

UINT32 = struct.Struct("<I")
INT64 = struct.Struct("<q")
OWNER_PENDING = 0
OWNER_NO_AUTHOR = -1
CELL_SIZE = 64
TOP_COLORS = 5


class AnalysisCancelled(RuntimeError):
    """Raised when a running scan invalidates an in-progress analysis."""


def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise AnalysisCancelled("analysis cancelled")


def analysis_method() -> str:
    return (
        f"사용자 픽셀을 {CELL_SIZE}px 격자로 묶고 서로 맞닿은 격자를 같은 영역으로 분류한 뒤, "
        "픽셀 수가 가장 많은 영역의 중심에 가장 가까운 실제 소유 픽셀을 대표 좌표로 선택"
    )


def _color_summary(counter: Counter[int], total: int) -> list[dict[str, Any]]:
    if total <= 0:
        return []
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    top = ordered[:TOP_COLORS]
    result = [
        {"hex": f"#{rgb:06X}", "count": count, "percent": count / total * 100.0}
        for rgb, count in top
    ]
    other = total - sum(count for _, count in top)
    if other > 0:
        result.append({"hex": "기타", "count": other, "percent": other / total * 100.0})
    return result


def ensure_target_color_cache(
    template: BlueMarbleTemplate,
    path: Path,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Create a dense RGB cache indexed by template-local linear pixel position."""
    _check_cancel(should_cancel)
    expected_size = template.width * template.height * 3
    if path.exists() and path.stat().st_size == expected_size:
        return path
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    origin_gx = template.coords[0] * TILE_SIZE + template.coords[2]
    origin_gy = template.coords[1] * TILE_SIZE + template.coords[3]
    with tmp.open("w+b") as f:
        if expected_size:
            f.seek(expected_size - 1)
            f.write(b"\0")
            f.flush()
            os.fsync(f.fileno())
            colors = mmap.mmap(f.fileno(), 0)
            try:
                for template_tile in template.tiles:
                    _check_cancel(should_cancel)
                    target = decode_tile(template_tile)
                    pixels = target.load()
                    scale = template_tile.image_scale
                    logical_w, logical_h = target.width // scale, target.height // scale
                    gx0 = template_tile.tx * TILE_SIZE + template_tile.start_px
                    gy0 = template_tile.ty * TILE_SIZE + template_tile.start_py
                    local_x0, local_y0 = gx0 - origin_gx, gy0 - origin_gy
                    for y in range(logical_h):
                        if y % 128 == 0:
                            _check_cancel(should_cancel)
                        local_y = local_y0 + y
                        row_base = (local_y * template.width + local_x0) * 3
                        for x in range(logical_w):
                            rgba = pixels[x * scale + scale // 2, y * scale + scale // 2]
                            if rgba[3] == 0:
                                continue
                            offset = row_base + x * 3
                            colors[offset:offset + 3] = bytes(rgba[:3])
                    target.close()
                colors.flush()
            finally:
                colors.close()
    # Multiple status/analysis triggers can reach this cache creation at nearly the
    # same time. A per-thread temporary path prevents one worker from replacing or
    # deleting another worker's temporary file.
    if path.exists() and path.stat().st_size == expected_size:
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(path)
    return path


def _iter_owned_records(candidates_path: Path, owners_path: Path, total: int) -> Iterable[tuple[int, int]]:
    with candidates_path.open("rb") as cf, owners_path.open("rb") as of:
        for _ in range(total):
            linear_raw = cf.read(UINT32.size)
            owner_raw = of.read(INT64.size)
            if len(linear_raw) != UINT32.size or len(owner_raw) != INT64.size:
                break
            owner = INT64.unpack(owner_raw)[0]
            if owner > 0:
                yield UINT32.unpack(linear_raw)[0], owner


def compute_contributor_analysis(
    template: BlueMarbleTemplate,
    candidates_path: Path,
    owners_path: Path,
    target_colors_path: Path,
    total_candidates: int,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[int, dict[str, Any]]:
    """Compute dominant spatial region, representative coordinate, and color usage.

    The spatial classifier uses occupied 64px cells with 8-neighbor connectivity. This
    deliberately tolerates small holes caused by other contributors while separating
    distant artwork. Two sequential passes avoid keeping every pixel coordinate in RAM.
    """
    _check_cancel(should_cancel)
    ensure_target_color_cache(template, target_colors_path, should_cancel)
    _check_cancel(should_cancel)
    grid_width = max(1, (template.width + CELL_SIZE - 1) // CELL_SIZE)
    cells_by_owner: dict[int, dict[int, list[int]]] = {}
    total_by_owner: Counter[int] = Counter()
    overall_colors: dict[int, Counter[int]] = {}
    progress_total = max(1, total_candidates * 2)
    processed = 0

    with target_colors_path.open("rb") as color_file:
        colors = mmap.mmap(color_file.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for linear, owner in _iter_owned_records(candidates_path, owners_path, total_candidates):
                processed += 1
                local_y, local_x = divmod(linear, template.width)
                cell_id = (local_y // CELL_SIZE) * grid_width + (local_x // CELL_SIZE)
                owner_cells = cells_by_owner.setdefault(owner, {})
                cell = owner_cells.get(cell_id)
                if cell is None:
                    owner_cells[cell_id] = [1, local_x, local_y]
                else:
                    cell[0] += 1
                    cell[1] += local_x
                    cell[2] += local_y
                total_by_owner[owner] += 1
                offset = linear * 3
                rgb = (colors[offset] << 16) | (colors[offset + 1] << 8) | colors[offset + 2]
                overall_colors.setdefault(owner, Counter())[rgb] += 1
                if processed % 50000 == 0:
                    _check_cancel(should_cancel)
                    if progress:
                        progress(processed, progress_total)

            winning_cells: dict[int, set[int]] = {}
            region_centroids: dict[int, tuple[float, float]] = {}
            region_counts: dict[int, int] = {}
            for uid, cell_data in cells_by_owner.items():
                _check_cancel(should_cancel)
                remaining = set(cell_data)
                best_cells: set[int] = set()
                best_count = -1
                best_sum_x = best_sum_y = 0
                while remaining:
                    _check_cancel(should_cancel)
                    seed = remaining.pop()
                    component = {seed}
                    stack = [seed]
                    component_count = 0
                    component_sum_x = component_sum_y = 0
                    while stack:
                        cell_id = stack.pop()
                        count, sum_x, sum_y = cell_data[cell_id]
                        component_count += count
                        component_sum_x += sum_x
                        component_sum_y += sum_y
                        cy, cx = divmod(cell_id, grid_width)
                        for dy in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                if dx == 0 and dy == 0:
                                    continue
                                nx, ny = cx + dx, cy + dy
                                if nx < 0 or ny < 0:
                                    continue
                                neighbor = ny * grid_width + nx
                                if neighbor in remaining:
                                    remaining.remove(neighbor)
                                    component.add(neighbor)
                                    stack.append(neighbor)
                    if component_count > best_count or (
                        component_count == best_count and len(component) > len(best_cells)
                    ):
                        best_cells = component
                        best_count = component_count
                        best_sum_x = component_sum_x
                        best_sum_y = component_sum_y
                winning_cells[uid] = best_cells
                region_counts[uid] = max(0, best_count)
                divisor = max(1, best_count)
                region_centroids[uid] = (best_sum_x / divisor, best_sum_y / divisor)

            origin_gx = template.coords[0] * TILE_SIZE + template.coords[2]
            origin_gy = template.coords[1] * TILE_SIZE + template.coords[3]
            best_distance = {uid: float("inf") for uid in total_by_owner}
            representatives: dict[int, tuple[int, int]] = {}
            region_colors: dict[int, Counter[int]] = {uid: Counter() for uid in total_by_owner}
            empty_set: set[int] = set()
            for linear, owner in _iter_owned_records(candidates_path, owners_path, total_candidates):
                processed += 1
                local_y, local_x = divmod(linear, template.width)
                cell_id = (local_y // CELL_SIZE) * grid_width + (local_x // CELL_SIZE)
                if cell_id in winning_cells.get(owner, empty_set):
                    cx, cy = region_centroids[owner]
                    distance = (local_x - cx) ** 2 + (local_y - cy) ** 2
                    if distance < best_distance[owner]:
                        best_distance[owner] = distance
                        representatives[owner] = (origin_gx + local_x, origin_gy + local_y)
                    offset = linear * 3
                    rgb = (colors[offset] << 16) | (colors[offset + 1] << 8) | colors[offset + 2]
                    region_colors[owner][rgb] += 1
                if processed % 50000 == 0:
                    _check_cancel(should_cancel)
                    if progress:
                        progress(processed, progress_total)
        finally:
            colors.close()

    _check_cancel(should_cancel)
    if progress:
        progress(progress_total, progress_total)
    result: dict[int, dict[str, Any]] = {}
    for uid, count in total_by_owner.items():
        gx, gy = representatives.get(uid, (0, 0))
        region_count = region_counts.get(uid, count)
        result[uid] = {
            "gx": gx,
            "gy": gy,
            "regionPixels": region_count,
            "regionShare": region_count / max(1, count) * 100.0,
            "overallColors": _color_summary(overall_colors.get(uid, Counter()), count),
            "regionColors": _color_summary(region_colors.get(uid, Counter()), region_count),
        }
    return result
