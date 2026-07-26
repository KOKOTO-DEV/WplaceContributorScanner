from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .constants import USER_AGENT


class StopScanning(RuntimeError):
    """Raised for responses that should pause the scanner immediately."""


class ProtectiveResponse(RuntimeError):
    """Raised for temporary protection responses that may be retried later."""

    def __init__(self, status: int, retry_after_seconds: float | None, detail: str):
        super().__init__(detail)
        self.status = int(status)
        self.retry_after_seconds = retry_after_seconds
        self.detail = detail


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class WplaceClient:
    def __init__(self, tile_url: str, pixel_url: str, timeout: float = 30.0):
        self.tile_url = tile_url
        self.pixel_url = pixel_url
        self.timeout = timeout

    def _get(self, url: str) -> HttpResult:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,image/png,*/*;q=0.8",
                "Referer": "https://wplace.live/",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return HttpResult(
                    int(response.status),
                    response.read(),
                    {k.lower(): v for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp else b""
            return HttpResult(int(exc.code), body, {k.lower(): v for k, v in exc.headers.items()})

    def download_tile(self, tx: int, ty: int, destination: Path) -> None:
        url = self.tile_url.format(tx=tx, ty=ty)
        result = self._get(url)
        if result.status != 200:
            raise RuntimeError(f"타일 {tx},{ty} 다운로드 실패: HTTP {result.status}")
        if not result.body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"타일 {tx},{ty} 응답이 PNG가 아닙니다.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_bytes(result.body)
        tmp.replace(destination)

    def inspect_pixel(self, tx: int, ty: int, px: int, py: int) -> dict:
        """Fetch one pixel without mutating scanner state and preserve diagnostics."""
        url = self.pixel_url.format(tx=tx, ty=ty, px=px, py=py)
        result = self._get(url)
        text = result.body.decode("utf-8", errors="replace")
        payload = None
        json_error = ""
        if text:
            try:
                payload = json.loads(text)
            except Exception as exc:
                json_error = str(exc)
        safe_headers = {
            key: value for key, value in result.headers.items()
            if key in {"content-type", "date", "last-modified", "etag", "age", "cache-control", "expires"}
            or key.startswith("x-")
        }
        return {
            "url": url,
            "status": result.status,
            "headers": safe_headers,
            "payload": payload,
            "bodyText": text[:20000] if payload is None else "",
            "jsonError": json_error,
        }

    def get_pixel(self, tx: int, ty: int, px: int, py: int) -> tuple[int | None, dict]:
        url = self.pixel_url.format(tx=tx, ty=ty, px=px, py=py)
        result = self._get(url)
        if result.status == 200:
            try:
                payload = json.loads(result.body.decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(f"픽셀 응답 JSON 해석 실패: {exc}") from exc
            painted = payload.get("paintedBy")
            if not painted:
                return None, payload
            user_id = painted.get("id")
            return (int(user_id) if user_id is not None else None), payload
        if result.status in (401, 403, 429, 451):
            retry_header = result.headers.get("retry-after")
            retry_after = parse_retry_after(retry_header)
            detail = f"HTTP {result.status}"
            if retry_header:
                detail += f" (Retry-After: {retry_header})"
            raise ProtectiveResponse(result.status, retry_after, detail)
        if result.status == 404:
            return None, {}
        raise RuntimeError(f"픽셀 조회 실패: HTTP {result.status}")


def sleep_with_jitter(base_seconds: float, jitter_ratio: float, stop_event) -> None:
    delay = base_seconds * (1.0 + random.uniform(-jitter_ratio, jitter_ratio))
    stop_event.wait(max(0.05, delay))
