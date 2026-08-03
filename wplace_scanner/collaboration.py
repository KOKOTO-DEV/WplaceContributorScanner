from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import COLLABORATION_FORMAT_VERSION

COLLAB_JOB_TYPE = "WplaceContributorScannerCollaborationJob"
COLLAB_RESULT_TYPE = "WplaceContributorScannerCollaborationResult"


class CollaborationPackage:
    """Validated collaboration ZIP using the current root-level package layout."""

    def __init__(self, path: Path):
        self.path = path
        if not path.exists() or not path.is_file():
            raise ValueError("협업 ZIP 파일을 찾을 수 없습니다.")
        if not zipfile.is_zipfile(path):
            raise ValueError(
                "올바른 ZIP 파일이 아닙니다. 브라우저 다운로드가 끝나지 않았거나 "
                "오류 응답이 ZIP 대신 저장됐을 수 있습니다."
            )
        try:
            self.zf = zipfile.ZipFile(path)
            bad_member = self.zf.testzip()
            if bad_member:
                raise ValueError(f"ZIP이 손상되었습니다: {bad_member}")
            names = [name for name in self.zf.namelist() if not name.endswith("/")]
            self.file_names = frozenset(names)
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise ValueError(f"협업 ZIP에 안전하지 않은 경로가 있습니다: {name}")
            if "manifest.json" not in names:
                raise ValueError("ZIP 루트에 manifest.json이 없습니다.")
            try:
                raw = self.zf.read("manifest.json").decode("utf-8")
                self.manifest: dict[str, Any] = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"manifest.json을 읽을 수 없습니다: {exc}") from exc
            if not isinstance(self.manifest, dict):
                raise ValueError("manifest.json 형식이 올바르지 않습니다.")
            if int(self.manifest.get("formatVersion", -1)) != COLLABORATION_FORMAT_VERSION:
                raise ValueError(
                    "지원하지 않는 협업 파일 형식입니다. "
                    "Wplace Contributor Scanner 1.5.1에서 생성한 파일을 사용하세요."
                )
        except Exception:
            try:
                self.zf.close()
            except Exception:
                pass
            raise

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "CollaborationPackage":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def package_type(self) -> str:
        return str(self.manifest.get("type") or "")

    def has(self, name: str) -> bool:
        return name in self.file_names

    def read(self, name: str) -> bytes:
        try:
            return self.zf.read(name)
        except KeyError as exc:
            raise ValueError(f"협업 ZIP에 {name} 파일이 없습니다.") from exc

    def read_json(self, name: str) -> Any:
        try:
            return json.loads(self.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"협업 ZIP의 {name} 파일을 읽을 수 없습니다: {exc}") from exc


def verify_created_zip(path: Path, expected_type: str) -> None:
    """Fail before offering a package if the freshly created ZIP is invalid."""
    try:
        with CollaborationPackage(path) as package:
            if package.package_type != expected_type:
                raise ValueError("생성된 협업 ZIP의 패키지 종류가 올바르지 않습니다.")
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
