from __future__ import annotations

import hashlib
import io
import json
import mmap
import os
import shutil
import struct
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .project import INT64, UINT32, OWNER_PENDING, ScannerProject, utc_now
from .constants import COLLABORATION_FORMAT_VERSION, DEFAULT_TILE_URL
from .collaboration import CollaborationPackage, COLLAB_JOB_TYPE, COLLAB_RESULT_TYPE, verify_created_zip
from .template import load_blue_marble_templates
from .snapshot_template import TEMPLATE_FORMAT, SnapshotTemplateService, bounds_from_payload

def _require_manifest_fields(manifest: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    expected = set(fields)
    actual = set(manifest)
    if actual != expected:
        details: list[str] = []
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            details.append(f"누락: {', '.join(missing)}")
        if unexpected:
            details.append(f"불필요: {', '.join(unexpected)}")
        raise ValueError(f"{label} manifest.json 항목 구성이 올바르지 않습니다. " + " / ".join(details))


class ProjectManager:
    def __init__(self, app_root: Path):
        self.app_root = app_root
        self.data_root = app_root / "data"
        self.inbox = self.data_root / "inbox"
        self.projects_root = self.data_root / "projects"
        self.templates_root = self.data_root / "templates"
        self.deleted_projects_path = self.data_root / "deleted-projects.json"
        self.deleted_project_ids = self._load_deleted_project_ids()
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.templates_root.mkdir(parents=True, exist_ok=True)
        self.snapshot_templates = SnapshotTemplateService(self.data_root, self.templates_root)
        self.projects: dict[str, ScannerProject] = {}
        self.active_id: str | None = None
        self._discover()

    def _load_deleted_project_ids(self) -> set[str]:
        try:
            payload = json.loads(self.deleted_projects_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"formatVersion", "projectIds"}:
                return set()
            if int(payload.get("formatVersion", -1)) != 1 or not isinstance(payload.get("projectIds"), list):
                return set()
            return {str(value) for value in payload["projectIds"] if str(value).strip()}
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
            return set()

    def _save_deleted_project_ids(self) -> None:
        payload = {"formatVersion": 1, "projectIds": sorted(self.deleted_project_ids)}
        temp = self.deleted_projects_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.deleted_projects_path)

    def _discover(self) -> None:
        seen_paths: set[Path] = set()
        for base in (self.inbox, self.templates_root):
            for path in sorted(base.iterdir()):
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                if resolved in seen_paths or path.suffix.lower() not in (".json", ".zip"):
                    continue
                seen_paths.add(resolved)
                try:
                    # Collaboration packages are imported explicitly from the UI.
                    if path.suffix.lower() == ".zip":
                        try:
                            with zipfile.ZipFile(path) as zf:
                                if "manifest.json" in zf.namelist():
                                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                                    if str(manifest.get("type", "")).startswith("WplaceContributorScannerCollaboration"):
                                        continue
                        except Exception:
                            pass
                    self.import_path(
                        path,
                        copy_source=(base == self.inbox),
                        restore_deleted=False,
                        replace_invalid_project_data=(base == self.inbox),
                    )
                except Exception:
                    continue
        if self.projects and self.active_id is None:
            self.active_id = next(iter(self.projects))
        if self.active:
            self.active.resume_pending_analysis()

    def import_path(
        self,
        path: Path,
        copy_source: bool = True,
        *,
        restore_deleted: bool = True,
        replace_invalid_project_data: bool = True,
    ) -> list[ScannerProject]:
        templates = load_blue_marble_templates(path)
        if restore_deleted:
            restored = {template.project_id for template in templates} & self.deleted_project_ids
            if restored:
                self.deleted_project_ids.difference_update(restored)
                self._save_deleted_project_ids()
        if copy_source:
            destination = self.templates_root / path.name
            if path.resolve() != destination.resolve():
                if destination.exists() and destination.read_bytes() != path.read_bytes():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                    destination = self.templates_root / f"{path.stem}-{digest}{path.suffix}"
                if not destination.exists():
                    shutil.copy2(path, destination)
        imported = []
        for template in templates:
            if not restore_deleted and template.project_id in self.deleted_project_ids:
                continue
            project = self._open_imported_project(
                template,
                replace_invalid_project_data=replace_invalid_project_data,
            )
            self.projects[template.project_id] = project
            imported.append(project)
            if self.active_id is None:
                self.active_id = template.project_id
        return imported

    def _open_imported_project(
        self,
        template,
        *,
        replace_invalid_project_data: bool,
    ) -> ScannerProject:
        existing = self.projects.get(template.project_id)
        if existing is not None:
            return existing

        project_path = self.projects_root / template.project_id
        try:
            return ScannerProject(self.projects_root, template)
        except (ValueError, UnicodeError):
            # An explicitly imported current template must remain usable even when
            # a project directory from an unsupported pre-release data format is
            # still present. Only replace directories that cannot be opened at all;
            # valid current-format progress is always preserved above.
            if not replace_invalid_project_data or not project_path.exists():
                raise
            shutil.rmtree(project_path)
            return ScannerProject(self.projects_root, template)


    def capture_snapshot_region(self, payload: dict[str, Any]) -> dict[str, Any]:
        bounds = bounds_from_payload(payload)
        active = self.active
        settings = active.meta.get("settings", {}) if active else {}
        tile_url = str(payload.get("tileUrl") or settings.get("tileUrl") or DEFAULT_TILE_URL)
        timeout = float(payload.get("timeoutSeconds") or settings.get("timeoutSeconds") or 30.0)
        interval = float(payload.get("requestIntervalSeconds") or settings.get("requestIntervalSeconds") or 1.0)
        return self.snapshot_templates.capture(
            bounds, tile_url=tile_url, timeout=timeout, interval_seconds=interval
        )

    def reopen_active_snapshot_template(self) -> dict[str, Any]:
        project = self.active
        if not project:
            raise RuntimeError("선택된 프로젝트가 없습니다.")
        if project.running:
            raise RuntimeError("수집을 일시정지한 뒤 템플릿을 편집하세요.")
        if project.template.template_format != TEMPLATE_FORMAT:
            raise ValueError("현재 프로젝트는 스크린샷 영역 템플릿이 아닙니다.")
        source_filename = project.template.source_name.rsplit(":", 1)[0]
        template_path = self.templates_root / source_filename
        return self.snapshot_templates.reopen_template_capture(
            template_path,
            cache_dir=project.cache_dir,
            template_name=project.name,
            match_mode=project.template.match_mode,
        )

    def create_snapshot_template_project(
        self, capture_id: str, edited_png: bytes, *, name: str, match_mode: str
    ) -> ScannerProject:
        template_path = self.snapshot_templates.create_template(
            capture_id, edited_png, name=name, match_mode=match_mode
        )
        imported = self.import_path(template_path, copy_source=False)
        if not imported:
            raise RuntimeError("스크린샷 템플릿 프로젝트를 만들지 못했습니다.")
        project = imported[0]
        capture_dir = self.snapshot_templates._capture_dir(capture_id)
        source_tiles = capture_dir / "tiles"
        project.cache_dir.mkdir(parents=True, exist_ok=True)
        for tile in source_tiles.glob("*.png"):
            shutil.copy2(tile, project.cache_dir / tile.name)
        # Keep the edited, masked image as the initial project snapshot. Recompare can
        # later refresh this from cached/current tiles without changing the template.
        shutil.copy2(capture_dir / "edited.png", project.path / "canvas-snapshot.png")
        self.active_id = project.template.project_id
        self.snapshot_templates.cleanup(capture_id)
        return project

    @property
    def active(self) -> ScannerProject | None:
        return self.projects.get(self.active_id) if self.active_id else None

    def select(self, project_id: str) -> ScannerProject:
        if project_id not in self.projects:
            raise KeyError(project_id)
        self.active_id = project_id
        project = self.projects[project_id]
        project.resume_pending_analysis()
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {
                "id": pid,
                "name": project.name,
                "sourceName": project.template.source_name,
                "active": pid == self.active_id,
                "prepared": project.prepared,
                "running": project.running,
            }
            for pid, project in self.projects.items()
        ]


    def delete_project(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project is None:
            raise KeyError(project_id)
        if project.running or getattr(project, "_phase", "") == "prepare":
            raise RuntimeError("수집 또는 현재 그림 비교 중에는 프로젝트를 삭제할 수 없습니다.")
        if project.analysis_calculating:
            raise RuntimeError("대표 영역과 색상을 분석하는 동안에는 프로젝트를 삭제할 수 없습니다.")

        source_filename = Path(str(project.template.source_name).split(":", 1)[0]).name
        project_path = project.path
        self.deleted_project_ids.add(project_id)
        self._save_deleted_project_ids()

        if project_path.exists():
            shutil.rmtree(project_path)
        del self.projects[project_id]

        shared_source = any(
            Path(str(item.template.source_name).split(":", 1)[0]).name == source_filename
            for item in self.projects.values()
        )
        source_removed = False
        if source_filename and not shared_source:
            for base in (self.templates_root, self.inbox):
                source_path = base / source_filename
                if source_path.exists() and source_path.is_file():
                    source_path.unlink()
                    source_removed = True

        if self.active_id == project_id:
            self.active_id = next(iter(self.projects), None)
        if self.active:
            self.active.resume_pending_analysis()
        return {
            "projectId": project_id,
            "name": project.name,
            "sourceRemoved": source_removed,
            "activeId": self.active_id,
        }

    def _find_source_path(self, project: ScannerProject) -> Path:
        source_filename = Path(str(project.template.source_name).split(":", 1)[0]).name
        source_path = self.templates_root / source_filename
        if source_path.is_file():
            return source_path
        raise FileNotFoundError("원본 템플릿 파일을 찾지 못했습니다. 템플릿을 다시 가져온 뒤 시도하세요.")

    def export_collaboration_job(self, project: ScannerProject, *, rebalance_pending: bool = False) -> Path:
        if project.running:
            raise RuntimeError("수집을 일시정지한 뒤 협업 시작 파일을 내보내세요.")
        if not project.prepared:
            raise RuntimeError("먼저 현재 그림과 비교하여 후보 목록을 준비하세요.")
        if rebalance_pending:
            assignment = project.create_pending_assignment()
        else:
            assignment = project.assignment_info()
        source = self._find_source_path(project)
        project._save_users()
        candidate_hash = str(project.meta["candidateHash"])
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = "collab-rebalance-start" if assignment["mode"] == "pending" else "collab-start"
        out = project.path / f"{prefix}-{stamp}.zip"
        snapshot_path = project.build_canvas_snapshot()
        if snapshot_path is None or not snapshot_path.is_file():
            raise RuntimeError("협업 시작 파일에 포함할 현재 그림을 만들지 못했습니다.")
        snapshot_at = project.meta.get("snapshotAt") or project.meta.get("preparedAt")
        manifest = {
            "type": COLLAB_JOB_TYPE,
            "formatVersion": COLLABORATION_FORMAT_VERSION,
            "generatedAt": utc_now(),
            "projectId": project.template.project_id,
            "projectName": project.name,
            "sourceHash": project.template.source_hash,
            "sourceFile": source.name,
            "candidatePixels": int(project.meta.get("candidatePixels") or 0),
            "candidateHash": candidate_hash,
            "shardCount": int(project.meta["settings"].get("collaborationShardCount", 1)),
            "assignmentMode": assignment["mode"],
            "assignmentPixels": int(assignment["count"]),
            "assignmentHash": str(assignment.get("hash") or ""),
            "snapshotAt": snapshot_at,
        }
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.write(source, f"template/{source.name}")
            zf.write(project.candidates_path, "candidates.bin")
            zf.write(project.owners_path, "owners.bin")
            zf.write(project.users_path, "users.json")
            zf.write(snapshot_path, "canvas-snapshot.png")
            if assignment["mode"] == "pending":
                zf.write(project.assignment_path, "assignment.bin")
        verify_created_zip(out, COLLAB_JOB_TYPE)
        if rebalance_pending:
            project.log(
                f"남은 작업 분배 재시작 파일 생성 완료: 미확인 {int(assignment['count']):,}개, "
                f"{int(project.meta['settings'].get('collaborationShardCount', 1))}분할"
            )
        return out


    def find_project_for_collaboration_result(self, package_path: Path) -> ScannerProject | None:
        """Find the local project identified by a current-format result package."""
        with CollaborationPackage(package_path) as package:
            manifest = package.manifest
            package_type = package.package_type
            if package_type == COLLAB_JOB_TYPE:
                raise ValueError(
                    "선택한 파일은 협업 시작 파일입니다. 시작 파일은 '협업 시작 파일 가져오기'에 사용하세요."
                )
            if package_type != COLLAB_RESULT_TYPE:
                shown = package_type or "종류 정보 없음"
                raise ValueError(f"작업 결과 파일이 아닙니다. 감지된 패키지 종류: {shown}")
            project_id = str(manifest.get("projectId") or "")
            if not project_id:
                raise ValueError("작업 결과 파일에 프로젝트 ID가 없습니다.")
            return self.projects.get(project_id)

    def import_collaboration_job(self, package_path: Path) -> ScannerProject:
        with CollaborationPackage(package_path) as package:
            manifest = package.manifest
            package_type = package.package_type
            if package_type == COLLAB_RESULT_TYPE:
                raise ValueError(
                    "선택한 파일은 작업 결과 파일입니다. 결과 파일은 기준 노드의 '작업 결과 병합'에 사용하세요."
                )
            if package_type != COLLAB_JOB_TYPE:
                shown = package_type or "종류 정보 없음"
                raise ValueError(f"협업 시작 파일이 아닙니다. 감지된 패키지 종류: {shown}")
            _require_manifest_fields(
                manifest,
                (
                    "type", "formatVersion", "generatedAt", "projectId", "projectName",
                    "sourceHash", "sourceFile", "candidatePixels", "candidateHash",
                    "shardCount", "assignmentMode", "assignmentPixels", "assignmentHash", "snapshotAt",
                ),
                "협업 시작 파일",
            )
            source_file = Path(str(manifest["sourceFile"])).name
            if not source_file:
                raise ValueError("협업 시작 파일의 manifest.json에 원본 템플릿 파일명이 없습니다.")
            source_bytes = package.read(f"template/{source_file}")
            candidates = package.read("candidates.bin")
            owners = package.read("owners.bin")
            users = package.read_json("users.json")
            snapshot_bytes = package.read("canvas-snapshot.png")
            assignment_mode = str(manifest["assignmentMode"])
            if assignment_mode not in ("all", "pending"):
                raise ValueError(f"알 수 없는 협업 작업 순서 형식입니다: {assignment_mode}")
            expected_files = {
                "manifest.json", f"template/{source_file}", "candidates.bin", "owners.bin",
                "users.json", "canvas-snapshot.png",
            }
            if assignment_mode == "pending":
                expected_files.add("assignment.bin")
            if package.file_names != expected_files:
                unexpected = sorted(package.file_names - expected_files)
                missing = sorted(expected_files - package.file_names)
                details = []
                if missing:
                    details.append(f"누락: {', '.join(missing)}")
                if unexpected:
                    details.append(f"불필요: {', '.join(unexpected)}")
                raise ValueError("협업 시작 ZIP 구성이 올바르지 않습니다. " + " / ".join(details))
            assignment_bytes = package.read("assignment.bin") if assignment_mode == "pending" else b""

        if not isinstance(users, dict):
            raise ValueError("협업 시작 파일의 users.json 형식이 올바르지 않습니다.")
        project_id = str(manifest["projectId"])
        project_name = str(manifest["projectName"]).strip()
        source_hash = str(manifest["sourceHash"])
        candidate_hash = str(manifest["candidateHash"])
        if not project_id or not project_name or not source_hash or len(candidate_hash) != 64:
            raise ValueError("협업 시작 파일의 프로젝트 식별 정보가 올바르지 않습니다.")
        source_path = self.templates_root / source_file
        if source_path.exists() and source_path.read_bytes() != source_bytes:
            source_path = self.templates_root / f"collab-{project_id[:12]}-{source_file}"
        if not source_path.exists():
            # Preserve the exact bytes because the template project ID is content based.
            source_path.write_bytes(source_bytes)
        templates = load_blue_marble_templates(source_path)
        match = next((t for t in templates if t.project_id == project_id), None)
        if match is None:
            raise ValueError("협업 작업의 템플릿 ID를 원본 파일에서 찾지 못했습니다.")
        if match.source_hash != source_hash:
            raise ValueError("협업 시작 파일의 원본 템플릿 해시가 일치하지 않습니다.")
        try:
            with Image.open(io.BytesIO(snapshot_bytes)) as snapshot_image:
                if snapshot_image.format != "PNG":
                    raise ValueError("PNG 형식이 아닙니다.")
                if snapshot_image.size != (match.width, match.height):
                    raise ValueError(
                        f"그림 크기가 {snapshot_image.size[0]}x{snapshot_image.size[1]}이며 "
                        f"템플릿 크기 {match.width}x{match.height}와 다릅니다."
                    )
                snapshot_image.verify()
        except Exception as exc:
            raise ValueError(f"협업 시작 파일의 canvas-snapshot.png가 올바르지 않습니다: {exc}") from exc
        project = ScannerProject(self.projects_root, match)
        if project.running:
            raise RuntimeError("해당 프로젝트가 수집 중입니다.")
        expected_count = int(manifest["candidatePixels"])
        if len(candidates) != expected_count * 4 or len(owners) != expected_count * 8:
            raise ValueError("협업 작업의 후보 또는 결과 파일 크기가 올바르지 않습니다.")
        if hashlib.sha256(candidates).hexdigest() != candidate_hash:
            raise ValueError("협업 작업의 후보 목록 해시가 일치하지 않습니다.")
        if assignment_mode == "pending":
            assignment_count = int(manifest["assignmentPixels"])
            if assignment_count <= 0 or len(assignment_bytes) != assignment_count * UINT32.size:
                raise ValueError("남은 작업 재분배 목록의 크기가 올바르지 않습니다.")
            expected_assignment_hash = str(manifest["assignmentHash"])
            if not expected_assignment_hash or hashlib.sha256(assignment_bytes).hexdigest() != expected_assignment_hash:
                raise ValueError("남은 작업 재분배 목록의 해시가 일치하지 않습니다.")
            previous = -1
            for offset in range(0, len(assignment_bytes), UINT32.size):
                idx = UINT32.unpack_from(assignment_bytes, offset)[0]
                if idx >= expected_count:
                    raise ValueError("남은 작업 재분배 목록에 범위를 벗어난 후보 번호가 있습니다.")
                if idx <= previous:
                    raise ValueError("남은 작업 재분배 목록이 중복되었거나 정렬되지 않았습니다.")
                previous = idx
        else:
            assignment_count = int(manifest["assignmentPixels"])
            if assignment_count != expected_count or str(manifest["assignmentHash"]):
                raise ValueError("전체 작업 분배 정보가 후보 픽셀 수와 일치하지 않습니다.")

        shard_count = int(manifest["shardCount"])
        if not 1 <= shard_count <= 1024:
            raise ValueError("협업 시작 파일의 분할 수가 허용 범위를 벗어났습니다.")
        same_candidates = project.prepared and project._file_sha256(project.candidates_path) == candidate_hash
        if not same_candidates:
            project.candidates_path.write_bytes(candidates)
            project.owners_path.write_bytes(owners)
        else:
            # Keep local progress and fill only still-pending owner entries from the package.
            with project.owners_path.open("r+b") as f:
                local = mmap.mmap(f.fileno(), 0)
                try:
                    for idx in range(expected_count):
                        incoming = INT64.unpack_from(owners, idx * INT64.size)[0]
                        current = INT64.unpack_from(local, idx * INT64.size)[0]
                        if incoming != OWNER_PENDING and current == OWNER_PENDING:
                            INT64.pack_into(local, idx * INT64.size, incoming)
                    local.flush()
                    os.fsync(f.fileno())
                finally:
                    local.close()
        if assignment_mode == "pending":
            project.assignment_path.write_bytes(assignment_bytes)
            project.meta["assignment"] = {
                "mode": "pending",
                "count": assignment_count,
                "hash": str(manifest["assignmentHash"]),
                "generatedAt": manifest["generatedAt"],
            }
        else:
            project._reset_assignment()
        for key, value in users.items():
            from .project import UserMeta
            uid = int(key)
            incoming = UserMeta(**value)
            current = project._users.get(uid)
            if current is None or (not current.name and incoming.name):
                project._users[uid] = incoming
        project.meta["candidatePixels"] = expected_count
        project.meta["displayName"] = project_name[:120]
        project.meta["candidateHash"] = candidate_hash
        recommended_shards = shard_count
        project.meta["settings"]["collaborationShardCount"] = recommended_shards
        project.meta["settings"]["collaborationShardIndex"] = 0
        project.meta["preparedAt"] = str(manifest["generatedAt"])
        (project.path / "canvas-snapshot.png").write_bytes(snapshot_bytes)
        project.meta["snapshotAt"] = str(manifest["snapshotAt"] or project.meta["preparedAt"])
        project._save_users()
        project._progress_initialized = False
        project._stats_initialized = False
        project._ensure_progress_and_stats()
        project.meta["scan"]["completed"] = project._done_count >= expected_count
        project.meta["scan"]["pausedReason"] = ""
        project._save_meta()
        project._invalidate_analysis()
        if assignment_mode == "pending":
            project.log(f"남은 작업 재분배 패키지를 가져왔습니다: 재분배 대상 {assignment_count:,}개. 작업 번호를 설정하세요.")
        else:
            project.log("협업 시작 파일을 가져왔습니다. 다른 노드와 겹치지 않게 내 작업 번호를 설정하고 저장한 뒤 수집을 시작하세요.")
        # A collaboration start/rebalance package is normally followed by scanning.
        # Do not launch expensive representative analysis here; defer it until the
        # node pauses or finishes so analysis never races with owner writes.
        project._defer_analysis_for_scan()
        self.projects[project.template.project_id] = project
        self.active_id = project.template.project_id
        return project
