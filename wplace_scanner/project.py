from __future__ import annotations

import csv
import hashlib
import json
import mmap
import os
import struct
import threading
import time
import zipfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from PIL import Image, ImageChops

from .analytics import AnalysisCancelled, analysis_method, compute_contributor_analysis
from .constants import (
    ANALYSIS_FORMAT_VERSION, COLLABORATION_FORMAT_VERSION, DEFAULT_PIXEL_URL, DEFAULT_TILE_URL,
    PROJECT_FORMAT_VERSION, TILE_SIZE, TRANSPARENT_TEMPLATE_RGB,
)
from .collaboration import CollaborationPackage, COLLAB_JOB_TYPE, COLLAB_RESULT_TYPE, verify_created_zip
from .coords import global_to_tile_pixel, tile_pixel_to_global, tile_pixel_to_latlng, wplace_link
from .network import ProtectiveResponse, StopScanning, WplaceClient, sleep_with_jitter
from .pdf_report import build_pdf_report
from .template import BlueMarbleTemplate, decode_tile

UINT32 = struct.Struct("<I")
INT64 = struct.Struct("<q")
OWNER_PENDING = 0
OWNER_NO_AUTHOR = -1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UserMeta:
    id: int
    name: str = ""
    allianceName: str = ""


class ScannerProject:
    def __init__(self, root: Path, template: BlueMarbleTemplate):
        self.root = root
        self.template = template
        self.path = root / template.project_id
        self.path.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.path / "tiles"
        self.candidates_path = self.path / "candidates.bin"
        self.owners_path = self.path / "owners.bin"
        self.assignment_path = self.path / "assignment.bin"
        self.meta_path = self.path / "project.json"
        self.users_path = self.path / "users.json"
        self.log_path = self.path / "scanner.log"
        self.analysis_path = self.path / "contributor-analysis.json"
        self.target_colors_path = self.path / "target-colors.rgb"
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.worker_threads: list[threading.Thread] = []
        self.lock = threading.RLock()
        self.prepare_lock = threading.Lock()
        self.claim_lock = threading.Lock()
        self.checkpoint_lock = threading.Lock()
        self.protection_lock = threading.Lock()
        self._status_message = "현재 그림과 비교를 실행하거나 수집 시작을 누르세요."
        self._phase = "idle"
        self._prepare_done = 0
        self._prepare_total = len(self.template.tiles)
        self._prepare_matches = 0
        self._errors = 0
        self._consecutive_errors = 0
        self._protection_retry_count = 0
        self._protection_wait_until = 0.0
        self._protection_last_status: int | None = None
        self._users: dict[int, UserMeta] = {}
        self._live_stats: dict[int, dict[str, Any]] = {}
        self._stats_initialized = False
        self._progress_initialized = False
        self._done_count = 0
        self._no_author_count = 0
        self._assigned_done_count = 0
        self._assigned_total_count = 0
        self._rate_times: deque[float] = deque(maxlen=300)
        self.analysis_lock = threading.RLock()
        self._analysis_compute_lock = threading.Lock()
        self._analysis_thread: threading.Thread | None = None
        self._analysis_trigger_thread: threading.Thread | None = None
        self._analysis_trigger_lock = threading.Lock()
        self._analysis_cache: dict[int, dict[str, Any]] = {}
        self._analysis_cache_signature: dict[str, Any] | None = None
        self._analysis_cache_format_version = 0
        self._analysis_cache_method = ""
        self._analysis_progress_done = 0
        self._analysis_progress_total = 0
        self._analysis_error = ""
        self._analysis_cancel_event = threading.Event()
        self._analysis_deferred_until_stop = False
        self.merge_lock = threading.Lock()
        self.merge_status_lock = threading.Lock()
        self._merge_status: dict[str, Any] = {
            "running": False,
            "fileName": "",
            "phase": "idle",
            "processed": 0,
            "total": 0,
            "added": 0,
            "same": 0,
            "conflicts": 0,
            "error": "",
        }
        self._load_or_initialize()
        self._load_analysis_cache()

    def _default_meta(self) -> dict[str, Any]:
        return {
            "formatVersion": PROJECT_FORMAT_VERSION,
            "projectId": self.template.project_id,
            "displayName": self.template.name,
            "sourceHash": self.template.source_hash,
            "candidatePixels": None,
            "candidateHash": None,
            "assignment": {
                "mode": "all",
                "count": None,
                "hash": None,
                "generatedAt": None,
            },
            "preparedAt": None,
            "snapshotAt": None,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "settings": {
                "requestIntervalSeconds": 0.5,
                "jitterRatio": 0.20,
                "timeoutSeconds": 30.0,
                "tileUrl": DEFAULT_TILE_URL,
                "pixelUrl": DEFAULT_PIXEL_URL,
                "autoRetryProtectiveResponses": True,
                "protectiveRetryBaseSeconds": 60.0,
                "protectiveRetryMaxSeconds": 1800.0,
                "protectiveRetryMaxAttempts": 0,
                "checkpointEvery": 100,
                "parallelWorkers": 1,
                "collaborationShardCount": 1,
                "collaborationShardIndex": 0,
            },
            "scan": {
                "completed": False,
                "pausedReason": "",
            },
        }

    def _validate_current_meta(self, meta: dict[str, Any]) -> None:
        if not isinstance(meta, dict):
            raise ValueError("project.json 형식이 올바르지 않습니다.")
        if int(meta.get("formatVersion", -1)) != PROJECT_FORMAT_VERSION:
            raise ValueError(
                "지원하지 않는 프로젝트 데이터 형식입니다. "
                "Wplace Contributor Scanner 1.5.1에서 새로 만든 프로젝트만 사용할 수 있습니다."
            )
        if str(meta.get("projectId") or "") != self.template.project_id:
            raise ValueError("프로젝트 ID가 원본 템플릿과 일치하지 않습니다.")
        if str(meta.get("sourceHash") or "") != self.template.source_hash:
            raise ValueError("프로젝트와 원본 템플릿의 해시가 일치하지 않습니다.")
        display_name = meta.get("displayName")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("프로젝트 이름이 비어 있거나 올바르지 않습니다.")

        default_meta = self._default_meta()
        if set(meta) != set(default_meta):
            raise ValueError("project.json 항목 구성이 올바르지 않습니다.")
        settings = meta.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("프로젝트 조회 설정이 올바르지 않습니다.")
        if set(settings) != set(default_meta["settings"]):
            raise ValueError("프로젝트 조회 설정 항목 구성이 올바르지 않습니다.")
        request_interval = float(settings["requestIntervalSeconds"])
        jitter = float(settings["jitterRatio"])
        timeout = float(settings["timeoutSeconds"])
        checkpoint = int(settings["checkpointEvery"])
        workers = int(settings["parallelWorkers"])
        if not 0.1 <= request_interval <= 3600:
            raise ValueError("프로젝트 요청 간격이 허용 범위를 벗어났습니다.")
        if not 0 <= jitter <= 0.5:
            raise ValueError("프로젝트 지터 비율이 허용 범위를 벗어났습니다.")
        if not 5 <= timeout <= 180:
            raise ValueError("프로젝트 네트워크 타임아웃이 허용 범위를 벗어났습니다.")
        if not 10 <= checkpoint <= 100000:
            raise ValueError("프로젝트 체크포인트 설정이 올바르지 않습니다.")
        if not 1 <= workers <= 32:
            raise ValueError("프로젝트 병렬 워커 수가 허용 범위를 벗어났습니다.")
        if float(settings["protectiveRetryMaxSeconds"]) < float(settings["protectiveRetryBaseSeconds"]):
            raise ValueError("보호 응답 최대 대기는 기본 대기보다 작을 수 없습니다.")
        self._validate_collaboration_settings(settings)

        scan = meta.get("scan")
        if not isinstance(scan, dict) or set(scan) != {"completed", "pausedReason"}:
            raise ValueError("프로젝트 수집 상태가 올바르지 않습니다.")
        assignment = meta.get("assignment")
        if not isinstance(assignment, dict) or set(assignment) != {"mode", "count", "hash", "generatedAt"}:
            raise ValueError("프로젝트 작업 분배 정보가 올바르지 않습니다.")
        if str(assignment.get("mode") or "") not in {"all", "pending"}:
            raise ValueError("프로젝트 작업 분배 방식이 올바르지 않습니다.")

        candidate_value = meta.get("candidatePixels")
        if candidate_value is None:
            return
        candidate_count = int(candidate_value)
        if candidate_count < 0:
            raise ValueError("후보 픽셀 수가 올바르지 않습니다.")
        if not self.candidates_path.is_file() or self.candidates_path.stat().st_size != candidate_count * UINT32.size:
            raise ValueError("candidates.bin 크기가 후보 픽셀 수와 일치하지 않습니다.")
        if not self.owners_path.is_file() or self.owners_path.stat().st_size != candidate_count * INT64.size:
            raise ValueError("owners.bin 크기가 후보 픽셀 수와 일치하지 않습니다.")
        candidate_hash = str(meta.get("candidateHash") or "")
        if not candidate_hash or candidate_hash != self._file_sha256(self.candidates_path):
            raise ValueError("후보 픽셀 목록 해시가 일치하지 않습니다.")
        mode = str(assignment["mode"])
        if mode == "all":
            if int(assignment.get("count") or 0) != candidate_count:
                raise ValueError("전체 작업 분배 수가 후보 픽셀 수와 일치하지 않습니다.")
        else:
            assignment_count = int(assignment.get("count") or 0)
            assignment_hash = str(assignment.get("hash") or "")
            if assignment_count <= 0:
                raise ValueError("남은 작업 재분배 수가 올바르지 않습니다.")
            if not self.assignment_path.is_file() or self.assignment_path.stat().st_size != assignment_count * UINT32.size:
                raise ValueError("assignment.bin 크기가 작업 분배 수와 일치하지 않습니다.")
            if not assignment_hash or assignment_hash != self._file_sha256(self.assignment_path):
                raise ValueError("작업 분배 목록 해시가 일치하지 않습니다.")

    def _load_or_initialize(self) -> None:
        if self.meta_path.exists():
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self._validate_current_meta(meta)
            self.meta = meta
        else:
            self.meta = self._default_meta()
            self._save_meta()
        if self.users_path.exists():
            raw = json.loads(self.users_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("users.json 형식이 올바르지 않습니다.")
            users: dict[int, UserMeta] = {}
            for key, value in raw.items():
                if not isinstance(value, dict) or set(value) != {"id", "name", "allianceName"}:
                    raise ValueError("users.json 항목 구성이 올바르지 않습니다.")
                user = UserMeta(**value)
                if int(key) != user.id:
                    raise ValueError("users.json 작업자 ID가 일치하지 않습니다.")
                users[user.id] = user
            self._users = users

    def _atomic_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _save_meta(self) -> None:
        with self.lock:
            self.meta["updatedAt"] = utc_now()
            self._atomic_json(self.meta_path, self.meta)

    def _save_users(self) -> None:
        with self.lock:
            self._atomic_json(self.users_path, {str(k): asdict(v) for k, v in self._users.items()})

    def _owners_signature(self) -> dict[str, Any] | None:
        if not self.prepared:
            return None
        try:
            stat = self.owners_path.stat()
        except FileNotFoundError:
            return None
        return {
            "candidateHash": str(self.meta.get("candidateHash") or ""),
            "candidatePixels": int(self.meta.get("candidatePixels") or 0),
            "ownersSize": int(stat.st_size),
            "ownersMtimeNs": int(stat.st_mtime_ns),
        }

    def _reset_assignment(self) -> None:
        self.meta["assignment"] = {
            "mode": "all",
            "count": int(self.meta.get("candidatePixels") or 0),
            "hash": None,
            "generatedAt": None,
        }
        try:
            self.assignment_path.unlink(missing_ok=True)
        except Exception:
            pass

    def assignment_info(self) -> dict[str, Any]:
        total = int(self.meta.get("candidatePixels") or 0)
        assignment = self.meta["assignment"]
        mode = str(assignment["mode"])
        if mode == "pending":
            return {
                "mode": "pending",
                "count": int(assignment["count"]),
                "hash": str(assignment["hash"]),
                "generatedAt": assignment.get("generatedAt"),
            }
        return {"mode": "all", "count": total, "hash": "", "generatedAt": None}

    def create_pending_assignment(self) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("수집을 일시정지한 뒤 남은 작업을 재분배하세요.")
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        tmp = self.assignment_path.with_suffix(".bin.tmp")
        count = 0
        with self.owners_path.open("rb") as owner_file, tmp.open("wb") as assignment_file:
            total = int(self.meta.get("candidatePixels") or 0)
            for idx in range(total):
                raw = owner_file.read(INT64.size)
                if len(raw) != INT64.size:
                    raise RuntimeError("owners.bin 크기가 후보 픽셀 수와 일치하지 않습니다.")
                if INT64.unpack(raw)[0] == OWNER_PENDING:
                    assignment_file.write(UINT32.pack(idx))
                    count += 1
            assignment_file.flush()
            os.fsync(assignment_file.fileno())
        if count <= 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("재분배할 미확인 픽셀이 없습니다.")
        tmp.replace(self.assignment_path)
        info = {
            "mode": "pending",
            "count": count,
            "hash": self._file_sha256(self.assignment_path),
            "generatedAt": utc_now(),
        }
        self.meta["assignment"] = info
        self.meta["settings"]["collaborationShardIndex"] = 0
        self._progress_initialized = False
        self._stats_initialized = False
        self._save_meta()
        self._ensure_progress_and_stats()
        return dict(info)

    def _load_analysis_cache(self) -> None:
        if not self.analysis_path.exists():
            return
        try:
            payload = json.loads(self.analysis_path.read_text(encoding="utf-8"))
            format_version = int(payload.get("formatVersion") or 0)
            method = str(payload.get("method") or "")
            contributors = payload.get("contributors")
            if (
                format_version != ANALYSIS_FORMAT_VERSION
                or method != analysis_method()
                or payload.get("signature") != self._owners_signature()
                or not isinstance(contributors, dict)
            ):
                raise ValueError("분석 캐시가 현재 프로젝트 상태와 일치하지 않습니다.")
            self._analysis_cache = {int(uid): dict(value) for uid, value in contributors.items()}
            self._analysis_cache_signature = payload.get("signature")
            self._analysis_cache_format_version = format_version
            self._analysis_cache_method = method
        except Exception:
            self._analysis_cache = {}
            self._analysis_cache_signature = None
            self._analysis_cache_format_version = 0
            self._analysis_cache_method = ""
            try:
                self.analysis_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _invalidate_analysis(self) -> None:
        with self.analysis_lock:
            self._analysis_cache = {}
            self._analysis_cache_signature = None
            self._analysis_cache_format_version = 0
            self._analysis_cache_method = ""
            self._analysis_error = ""
            self._analysis_progress_done = 0
            self._analysis_progress_total = 0
        try:
            self.analysis_path.unlink(missing_ok=True)
        except Exception:
            pass

    @property
    def analysis_calculating(self) -> bool:
        return bool(self._analysis_thread and self._analysis_thread.is_alive())

    def _analysis_cache_is_current(self, expected_user_ids: Iterable[int] | None = None) -> bool:
        signature = self._owners_signature()
        if not (
            signature
            and self._analysis_cache_signature == signature
            and self._analysis_cache_format_version == ANALYSIS_FORMAT_VERSION
            and self._analysis_cache_method == analysis_method()
        ):
            return False
        if expected_user_ids is not None:
            expected = {int(uid) for uid in expected_user_ids}
            if not expected.issubset(self._analysis_cache):
                return False
        return True

    def _defer_analysis_for_scan(self) -> None:
        """Cancel/hold expensive analysis while owner data is changing."""
        self._analysis_deferred_until_stop = True
        self._analysis_cancel_event.set()
        with self.analysis_lock:
            self._analysis_error = ""
            self._analysis_progress_done = 0
            self._analysis_progress_total = 0

    def _calculate_and_store_analysis(self) -> None:
        # A status request and the post-scan trigger can request analysis at nearly
        # the same time. Serialize the expensive calculation and let the second
        # caller reuse the cache produced by the first one.
        with self._analysis_compute_lock:
            try:
                if self._analysis_cancel_event.is_set() or self._analysis_deferred_until_stop or self.running:
                    return
                self._ensure_progress_and_stats()
                if self._analysis_cache_is_current(self._live_stats.keys()):
                    return
                signature = self._owners_signature()
                if not signature:
                    raise RuntimeError("준비된 프로젝트가 아닙니다.")

                def progress(done: int, total: int) -> None:
                    with self.analysis_lock:
                        self._analysis_progress_done = done
                        self._analysis_progress_total = total

                result = compute_contributor_analysis(
                    self.template,
                    self.candidates_path,
                    self.owners_path,
                    self.target_colors_path,
                    int(self.meta.get("candidatePixels") or 0),
                    progress,
                    should_cancel=lambda: (
                        self._analysis_cancel_event.is_set()
                        or self._analysis_deferred_until_stop
                        or self.running
                    ),
                )
                ending_signature = self._owners_signature()
                if ending_signature != signature:
                    raise RuntimeError("분석 중 작업자 데이터가 변경되었습니다. 잠시 후 다시 계산합니다.")
                payload = {
                    "formatVersion": ANALYSIS_FORMAT_VERSION,
                    "generatedAt": utc_now(),
                    "method": analysis_method(),
                    "signature": ending_signature,
                    "contributors": {str(uid): value for uid, value in result.items()},
                }
                self._atomic_json(self.analysis_path, payload)
                with self.analysis_lock:
                    self._analysis_cache = result
                    self._analysis_cache_signature = ending_signature
                    self._analysis_cache_format_version = ANALYSIS_FORMAT_VERSION
                    self._analysis_cache_method = analysis_method()
                    self._analysis_error = ""
            except AnalysisCancelled:
                with self.analysis_lock:
                    self._analysis_error = ""
                    self._analysis_progress_done = 0
                    self._analysis_progress_total = 0
            except Exception as exc:
                with self.analysis_lock:
                    self._analysis_error = str(exc)
            finally:
                with self.analysis_lock:
                    self._analysis_progress_done = self._analysis_progress_total

    def recalculate_analysis(self, blocking: bool = False, force: bool = False) -> None:
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        if self.running or self._analysis_deferred_until_stop:
            raise RuntimeError("수집 중에는 대표 영역과 색상 비율을 계산할 수 없습니다. 먼저 일시정지하세요.")
        self._analysis_cancel_event.clear()
        self._ensure_progress_and_stats()
        expected_user_ids = tuple(self._live_stats)
        if not force and self._analysis_cache_is_current(expected_user_ids):
            return
        if blocking:
            if self.analysis_calculating and self._analysis_thread:
                self._analysis_thread.join()
            if force or not self._analysis_cache_is_current(expected_user_ids):
                self._calculate_and_store_analysis()
            if self._analysis_error:
                raise RuntimeError(self._analysis_error)
            return
        if self.analysis_calculating:
            return
        with self.analysis_lock:
            self._analysis_error = ""
            self._analysis_progress_done = 0
            self._analysis_progress_total = max(1, int(self.meta.get("candidatePixels") or 0) * 2)
        self._analysis_thread = threading.Thread(
            target=self._calculate_and_store_analysis,
            name="contributor-analysis",
            daemon=True,
        )
        self._analysis_thread.start()

    def schedule_analysis_after_stop(self, force: bool = False) -> None:
        """Wait for the scan controller to exit, then calculate the current merged result."""
        if not self.prepared:
            return
        with self._analysis_trigger_lock:
            if self._analysis_trigger_thread and self._analysis_trigger_thread.is_alive():
                return
            worker = self.worker

            def wait_and_calculate() -> None:
                try:
                    if worker and worker is not threading.current_thread():
                        worker.join()
                    # The worker can finish between capture and join; wait until the public state agrees.
                    while self.running:
                        time.sleep(0.05)
                    done, _, _ = self._owner_progress()
                    if done and not self.running:
                        # A scan can begin while an import/startup analysis is still
                        # unwinding. Wait for that cancelled pass before launching the
                        # one authoritative post-stop analysis.
                        prior_analysis = self._analysis_thread
                        if prior_analysis and prior_analysis.is_alive() and prior_analysis is not threading.current_thread():
                            prior_analysis.join()
                        self._analysis_deferred_until_stop = False
                        self._analysis_cancel_event.clear()
                        self.recalculate_analysis(blocking=False, force=force)
                except Exception as exc:
                    with self.analysis_lock:
                        self._analysis_error = str(exc)

            self._analysis_trigger_thread = threading.Thread(
                target=wait_and_calculate,
                name="analysis-after-stop",
                daemon=True,
            )
            self._analysis_trigger_thread.start()

    def resume_pending_analysis(self) -> None:
        """Start missing or stale analysis for a prepared, stopped project."""
        if not self.prepared or self.running:
            return
        self._analysis_deferred_until_stop = False
        self._analysis_cancel_event.clear()
        try:
            self._ensure_progress_and_stats()
            if self._done_count and not self._analysis_cache_is_current(self._live_stats.keys()):
                self.recalculate_analysis(blocking=False)
        except Exception as exc:
            with self.analysis_lock:
                self._analysis_error = str(exc)

    def log(self, message: str) -> None:
        line = f"[{utc_now()}] {message}\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        with self.lock:
            self._status_message = message

    @property
    def prepared(self) -> bool:
        return bool(
            self.candidates_path.exists()
            and self.owners_path.exists()
            and self.meta.get("candidatePixels") is not None
        )

    @property
    def name(self) -> str:
        return str(self.meta["displayName"])

    def rename(self, display_name: str) -> str:
        name = str(display_name or "").strip()
        if not name:
            raise ValueError("프로젝트 이름을 입력하세요.")
        if len(name) > 120:
            raise ValueError("프로젝트 이름은 120자 이하여야 합니다.")
        if any(ord(ch) < 32 for ch in name):
            raise ValueError("프로젝트 이름에는 제어 문자를 사용할 수 없습니다.")
        self.meta["displayName"] = name
        self._save_meta()
        return name

    @property
    def running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    @staticmethod
    def _validate_collaboration_settings(settings: dict[str, Any]) -> None:
        count = int(settings.get("collaborationShardCount", 1))
        index = int(settings.get("collaborationShardIndex", 0))
        if not 1 <= count <= 1024:
            raise ValueError("협업 분할 수는 1~1024 범위여야 합니다.")
        if not 0 <= index < count:
            raise ValueError("내 작업 번호는 협업 분할 수보다 작아야 합니다.")

    def update_settings(self, patch: dict[str, Any]) -> None:
        if self.running:
            raise RuntimeError("수집 중에는 설정을 변경할 수 없습니다.")
        with self.lock:
            s = self.meta["settings"]
            if "requestIntervalSeconds" in patch:
                value = float(patch["requestIntervalSeconds"])
                if value < 0.1 or value > 3600:
                    raise ValueError("요청 간격은 0.1초 이상 3600초 이하여야 합니다.")
                s["requestIntervalSeconds"] = value
            if "jitterRatio" in patch:
                value = float(patch["jitterRatio"])
                if not 0 <= value <= 0.5:
                    raise ValueError("지터 비율은 0~0.5 범위여야 합니다.")
                s["jitterRatio"] = value
            if "timeoutSeconds" in patch:
                value = float(patch["timeoutSeconds"])
                if not 5 <= value <= 180:
                    raise ValueError("타임아웃은 5~180초 범위여야 합니다.")
                s["timeoutSeconds"] = value
            if "checkpointEvery" in patch:
                value = int(patch["checkpointEvery"])
                if not 10 <= value <= 100000:
                    raise ValueError("체크포인트 간격은 10~100000픽셀 범위여야 합니다.")
                s["checkpointEvery"] = value
            if "parallelWorkers" in patch:
                value = int(patch["parallelWorkers"])
                if not 1 <= value <= 32:
                    raise ValueError("병렬 워커 수는 1~32 범위여야 합니다.")
                s["parallelWorkers"] = value
            if "autoRetryProtectiveResponses" in patch:
                s["autoRetryProtectiveResponses"] = bool(patch["autoRetryProtectiveResponses"])
            if "protectiveRetryBaseSeconds" in patch:
                value = float(patch["protectiveRetryBaseSeconds"])
                if not 1 <= value <= 86400:
                    raise ValueError("보호 응답 기본 재시도 대기는 1~86400초 범위여야 합니다.")
                s["protectiveRetryBaseSeconds"] = value
            if "protectiveRetryMaxSeconds" in patch:
                value = float(patch["protectiveRetryMaxSeconds"])
                if not 1 <= value <= 604800:
                    raise ValueError("보호 응답 최대 재시도 대기는 1~604800초 범위여야 합니다.")
                s["protectiveRetryMaxSeconds"] = value
            if "protectiveRetryMaxAttempts" in patch:
                value = int(patch["protectiveRetryMaxAttempts"])
                if not 0 <= value <= 100000:
                    raise ValueError("보호 응답 최대 재시도 횟수는 0~100000 범위여야 합니다.")
                s["protectiveRetryMaxAttempts"] = value
            if float(s.get("protectiveRetryMaxSeconds", 1800.0)) < float(s.get("protectiveRetryBaseSeconds", 60.0)):
                raise ValueError("보호 응답 최대 대기는 기본 대기보다 작을 수 없습니다.")
            if "collaborationShardCount" in patch:
                s["collaborationShardCount"] = int(patch["collaborationShardCount"])
            if "collaborationShardIndex" in patch:
                # UI uses 1-based numbering; API stores 0-based.
                s["collaborationShardIndex"] = int(patch["collaborationShardIndex"])
            if "tileUrl" in patch:
                s["tileUrl"] = str(patch["tileUrl"])
            if "pixelUrl" in patch:
                s["pixelUrl"] = str(patch["pixelUrl"])
            self._validate_collaboration_settings(s)
            if "collaborationShardCount" in patch or "collaborationShardIndex" in patch:
                self._progress_initialized = False
                self._stats_initialized = False
            self._assigned_total_count = 0
            self._assigned_done_count = 0
            self._save_meta()

    def _client(self) -> WplaceClient:
        settings = self.meta["settings"]
        return WplaceClient(settings["tileUrl"], settings["pixelUrl"], float(settings["timeoutSeconds"]))

    def prepare(self, refresh_tiles: bool = True) -> None:
        if self.running:
            raise RuntimeError("수집 중에는 준비 작업을 실행할 수 없습니다.")
        if not self.prepare_lock.acquire(blocking=False):
            raise RuntimeError("이미 현재 그림 비교 작업이 진행 중입니다.")
        self._phase = "prepare"
        self._prepare_done = 0
        self._prepare_total = len(self.template.tiles)
        self._prepare_matches = 0
        self.log("현재 캔버스 타일을 확인하고 일치 픽셀 목록을 만드는 중입니다.")
        client = self._client()
        origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
        origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
        tmp_candidates = self.candidates_path.with_suffix(".bin.tmp")
        count = 0
        tile_count = len(self.template.tiles)

        try:
            with tmp_candidates.open("wb") as candidate_file:
                for tile_number, template_tile in enumerate(self.template.tiles, start=1):
                    live_path = self.cache_dir / f"{template_tile.tx}_{template_tile.ty}.png"
                    if refresh_tiles or not live_path.exists():
                        client.download_tile(template_tile.tx, template_tile.ty, live_path)
                        if tile_number < tile_count:
                            time.sleep(max(1.0, min(5.0, float(self.meta["settings"]["requestIntervalSeconds"]))))
                    live = Image.open(live_path).convert("RGBA")
                    if live.size != (TILE_SIZE, TILE_SIZE):
                        raise RuntimeError(f"현재 타일 {template_tile.tx},{template_tile.ty} 크기가 {live.size}입니다; 1000x1000이 필요합니다.")
                    target = decode_tile(template_tile)
                    target_pixels = target.load()
                    live_pixels = live.load()
                    scale = template_tile.image_scale
                    logical_w, logical_h = target.width // scale, target.height // scale
                    gx0 = template_tile.tx * TILE_SIZE + template_tile.start_px
                    gy0 = template_tile.ty * TILE_SIZE + template_tile.start_py
                    local_x0, local_y0 = gx0 - origin_gx, gy0 - origin_gy

                    for y in range(logical_h):
                        py = template_tile.start_py + y
                        for x in range(logical_w):
                            target_rgba = target_pixels[x * scale + scale // 2, y * scale + scale // 2]
                            if target_rgba[3] == 0:
                                continue
                            px = template_tile.start_px + x
                            current = live_pixels[px, py]
                            target_rgb = target_rgba[:3]
                            if self.template.match_mode == "region":
                                matches = True
                            elif target_rgb == TRANSPARENT_TEMPLATE_RGB:
                                matches = current[3] == 0
                            else:
                                matches = current[3] > 0 and current[:3] == target_rgb
                            if matches:
                                local_x, local_y = local_x0 + x, local_y0 + y
                                linear = local_y * self.template.width + local_x
                                candidate_file.write(UINT32.pack(linear))
                                count += 1
                    target.close()
                    live.close()
                    self._prepare_done = tile_number
                    self._prepare_matches = count
                    tile_label = "영역 대상" if self.template.match_mode == "region" else "일치"
                    self.log(f"타일 비교 {tile_number}/{tile_count}: {tile_label} 픽셀 {count:,}개")

            tmp_candidates.replace(self.candidates_path)
            with self.owners_path.open("wb") as f:
                if count:
                    f.seek(count * INT64.size - 1)
                    f.write(b"\0")
            self.meta["candidatePixels"] = count
            self.meta["candidateHash"] = self._file_sha256(self.candidates_path)
            self._reset_assignment()
            self.meta["preparedAt"] = utc_now()
            self.meta["scan"] = {
                "completed": count == 0,
                "pausedReason": "",
            }
            self._users.clear()
            self._save_users()
            self._save_meta()
            self._phase = "idle"
            label = "영역 대상" if self.template.match_mode == "region" else "템플릿 일치"
            self.log(f"준비 완료: {label} 픽셀 {count:,}개")
            self._live_stats = {}
            self._stats_initialized = True
            self._progress_initialized = True
            self._done_count = 0
            self._no_author_count = 0
            self._assigned_done_count = 0
            shard_count = max(1, int(self.meta["settings"].get("collaborationShardCount", 1)))
            shard_index = int(self.meta["settings"].get("collaborationShardIndex", 0))
            self._assigned_total_count = (
                ((count - 1 - shard_index) // shard_count) + 1
                if count > shard_index
                else 0
            )
            self._invalidate_analysis()
            snapshot = self.build_canvas_snapshot()
            if snapshot is not None:
                self.meta["snapshotAt"] = self.meta.get("preparedAt") or utc_now()
                self._save_meta()
        finally:
            if self.prepare_lock.locked():
                self.prepare_lock.release()

    def preparation_failed(self, exc: Exception) -> None:
        self._phase = "idle"
        if self.prepare_lock.locked():
            self.prepare_lock.release()
        self.log(f"준비 실패: {exc}")

    def _assignment_counts_from_file(self) -> tuple[int, int]:
        if not self.prepared:
            return 0, 0
        settings = self.meta["settings"]
        shard_count = int(settings.get("collaborationShardCount", 1))
        shard_index = int(settings.get("collaborationShardIndex", 0))
        total = int(self.meta.get("candidatePixels") or 0)
        if total <= 0:
            return 0, 0
        assignment = self.assignment_info()
        assigned_total = assigned_done = 0
        with self.owners_path.open("rb") as owner_file:
            owners = mmap.mmap(owner_file.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                if assignment["mode"] == "pending":
                    with self.assignment_path.open("rb") as assignment_file:
                        for order_pos in range(int(assignment["count"])):
                            raw = assignment_file.read(UINT32.size)
                            if len(raw) != UINT32.size:
                                break
                            if order_pos % shard_count != shard_index:
                                continue
                            idx = UINT32.unpack(raw)[0]
                            if idx >= total:
                                continue
                            assigned_total += 1
                            if INT64.unpack_from(owners, idx * INT64.size)[0] != OWNER_PENDING:
                                assigned_done += 1
                else:
                    for idx in range(total):
                        if idx % shard_count != shard_index:
                            continue
                        assigned_total += 1
                        if INT64.unpack_from(owners, idx * INT64.size)[0] != OWNER_PENDING:
                            assigned_done += 1
            finally:
                owners.close()
        return assigned_done, assigned_total





    def start(self) -> None:
        if not self.prepared:
            raise RuntimeError("먼저 현재 타일 비교를 실행해야 합니다.")
        if self.running:
            return
        self._ensure_progress_and_stats()
        assigned_done, assigned_total = self._assignment_counts_from_file()
        self._assigned_done_count = assigned_done
        self._assigned_total_count = assigned_total
        if assigned_done >= assigned_total:
            raise RuntimeError("현재 협업 작업 번호에 남은 픽셀이 없습니다.")
        self._defer_analysis_for_scan()
        self.stop_event.clear()
        self._rate_times.clear()
        with self.protection_lock:
            self._protection_retry_count = 0
            self._protection_wait_until = 0.0
            self._protection_last_status = None
        self.worker = threading.Thread(target=self._scan_loop, name=f"scan-controller-{self.template.project_id}", daemon=True)
        self.worker.start()

    def pause(self, reason: str = "사용자가 일시정지했습니다.") -> None:
        self.stop_event.set()
        with self.lock:
            self.meta["scan"]["pausedReason"] = reason
            self._save_meta()
        self.log(reason)
        self.schedule_analysis_after_stop()

    def _linear_to_coords(self, linear: int) -> tuple[int, int, int, int]:
        local_y, local_x = divmod(linear, self.template.width)
        origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
        origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
        return global_to_tile_pixel(origin_gx + local_x, origin_gy + local_y)

    def _update_user(self, user_id: int, payload: dict) -> None:
        painted = payload.get("paintedBy") or {}
        old = self._users.get(user_id)
        self._users[user_id] = UserMeta(
            id=user_id,
            name=str(painted.get("name") or (old.name if old else "")),
            allianceName=str(painted.get("allianceName") or (old.allianceName if old else "")),
        )

    def _record_success(self, linear: int, user_id: int | None, payload: dict) -> None:
        with self.lock:
            if user_id is not None:
                self._update_user(user_id, payload)
            self._done_count += 1
            self._assigned_done_count += 1
            if user_id is None:
                self._no_author_count += 1
            else:
                local_y, local_x = divmod(linear, self.template.width)
                origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
                origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
                gx, gy = origin_gx + local_x, origin_gy + local_y
                item = self._live_stats.get(user_id)
                if item is None:
                    item = self._live_stats[user_id] = {
                        "count": 0,
                        "firstGx": gx,
                        "firstGy": gy,
                        "representativeGx": gx,
                        "representativeGy": gy,
                    }
                item["count"] += 1
            self._rate_times.append(time.monotonic())

    def _scan_loop(self) -> None:
        self._phase = "scan"
        settings = dict(self.meta["settings"])
        workers = int(settings.get("parallelWorkers", 1))
        shard_count = int(settings.get("collaborationShardCount", 1))
        shard_index = int(settings.get("collaborationShardIndex", 0))
        total = int(self.meta["candidatePixels"] or 0)
        assignment = self.assignment_info()
        assignment_total = int(assignment["count"])
        checkpoint_every = max(10, int(settings.get("checkpointEvery", 100)))
        user_checkpoint_every = checkpoint_every * 5
        max_errors = 8
        auto_retry_protection = bool(settings.get("autoRetryProtectiveResponses", True))
        protection_base = max(1.0, float(settings.get("protectiveRetryBaseSeconds", 60.0)))
        protection_max = max(protection_base, float(settings.get("protectiveRetryMaxSeconds", 1800.0)))
        protection_attempt_limit = max(0, int(settings.get("protectiveRetryMaxAttempts", 0)))
        self.meta["scan"]["pausedReason"] = ""
        self._save_meta()
        order_label = "남은 작업 재분배" if assignment["mode"] == "pending" else "전체 후보 고정 분배"
        self.log(f"수집 시작: 병렬 {workers}개, 협업 작업 {shard_index + 1}/{shard_count}, {order_label}")

        claim_cursor = 0
        success_since_checkpoint = 0
        success_since_user_checkpoint = 0
        fatal_reason: list[str] = []

        try:
            with self.candidates_path.open("rb") as candidate_file, self.owners_path.open("r+b") as owner_file:
                candidates = mmap.mmap(candidate_file.fileno(), 0, access=mmap.ACCESS_READ)
                owners = mmap.mmap(owner_file.fileno(), 0)
                assignment_file = None
                assignment_map = None
                if assignment["mode"] == "pending":
                    assignment_file = self.assignment_path.open("rb")
                    assignment_map = mmap.mmap(assignment_file.fileno(), 0, access=mmap.ACCESS_READ)

                def claim_next() -> tuple[int, int] | None:
                    nonlocal claim_cursor
                    with self.claim_lock:
                        while claim_cursor < assignment_total:
                            order_pos = claim_cursor
                            claim_cursor += 1
                            if order_pos % shard_count != shard_index:
                                continue
                            if assignment_map is not None:
                                idx = UINT32.unpack_from(assignment_map, order_pos * UINT32.size)[0]
                            else:
                                idx = order_pos
                            if idx >= total:
                                continue
                            owner = INT64.unpack_from(owners, idx * INT64.size)[0]
                            if owner != OWNER_PENDING:
                                continue
                            linear = UINT32.unpack_from(candidates, idx * UINT32.size)[0]
                            return idx, linear
                    return None

                def checkpoint(force: bool = False) -> None:
                    nonlocal success_since_checkpoint, success_since_user_checkpoint
                    with self.checkpoint_lock:
                        with self.lock:
                            save_owners = force or success_since_checkpoint >= checkpoint_every
                            save_users = force or success_since_user_checkpoint >= user_checkpoint_every
                            if save_owners:
                                success_since_checkpoint = 0
                                owners.flush()
                                os.fsync(owner_file.fileno())
                                self._save_meta()
                            if save_users:
                                success_since_user_checkpoint = 0
                                self._save_users()

                def wait_for_protection_cooldown() -> bool:
                    while not self.stop_event.is_set():
                        with self.protection_lock:
                            remaining = self._protection_wait_until - time.monotonic()
                        if remaining <= 0:
                            return True
                        self.stop_event.wait(min(1.0, remaining))
                    return False

                def scan_worker(worker_number: int) -> None:
                    nonlocal success_since_checkpoint, success_since_user_checkpoint
                    client = self._client()
                    consecutive_errors = 0
                    while not self.stop_event.is_set():
                        job = claim_next()
                        if job is None:
                            return
                        idx, linear = job
                        tx, ty, px, py = self._linear_to_coords(linear)
                        while not self.stop_event.is_set():
                            if not wait_for_protection_cooldown():
                                return
                            try:
                                user_id, payload = client.get_pixel(tx, ty, px, py)
                                owner_value = user_id if user_id is not None else OWNER_NO_AUTHOR
                                with self.lock:
                                    # A collaboration merge is blocked while running, so no other writer can conflict.
                                    if INT64.unpack_from(owners, idx * INT64.size)[0] == OWNER_PENDING:
                                        INT64.pack_into(owners, idx * INT64.size, owner_value)
                                        self._record_success(linear, user_id, payload)
                                        success_since_checkpoint += 1
                                        success_since_user_checkpoint += 1
                                consecutive_errors = 0
                                self._consecutive_errors = 0
                                with self.protection_lock:
                                    if time.monotonic() >= self._protection_wait_until:
                                        self._protection_retry_count = 0
                                        self._protection_last_status = None
                                checkpoint(False)
                                with self.lock:
                                    self._status_message = (
                                        f"수집 중: 내 작업 {self._assigned_done_count:,}/{self._assigned_total_count:,} · "
                                        f"전체 확인 {self._done_count:,}/{total:,}"
                                    )
                                sleep_with_jitter(
                                    float(settings["requestIntervalSeconds"]),
                                    float(settings["jitterRatio"]),
                                    self.stop_event,
                                )
                                break
                            except ProtectiveResponse as exc:
                                # 401 usually means the endpoint now requires authorization; retrying it automatically is not useful.
                                if exc.status == 401 or not auto_retry_protection:
                                    reason = f"보호 응답으로 자동 일시정지: {exc}"
                                    with self.lock:
                                        self.meta["scan"]["pausedReason"] = reason
                                        if not fatal_reason:
                                            fatal_reason.append(reason)
                                    self.stop_event.set()
                                    return

                                created_cooldown = False
                                exhausted_reason = ""
                                now = time.monotonic()
                                with self.protection_lock:
                                    remaining = self._protection_wait_until - now
                                    # Multiple parallel requests can receive the same response at once.
                                    # Only the first response in a wave increments the retry counter.
                                    if remaining <= 0:
                                        next_attempt = self._protection_retry_count + 1
                                        if protection_attempt_limit and next_attempt > protection_attempt_limit:
                                            exhausted_reason = (
                                                f"보호 응답 자동 재시도 한도 {protection_attempt_limit}회를 초과해 일시정지했습니다: {exc}"
                                            )
                                        else:
                                            exponential = protection_base * (2 ** min(20, next_attempt - 1))
                                            requested = float(exc.retry_after_seconds or 0.0)
                                            delay = max(requested, min(protection_max, max(protection_base, exponential)))
                                            self._protection_retry_count = next_attempt
                                            self._protection_wait_until = now + delay
                                            self._protection_last_status = exc.status
                                            remaining = delay
                                            created_cooldown = True

                                if exhausted_reason:
                                    with self.lock:
                                        self.meta["scan"]["pausedReason"] = exhausted_reason
                                        if not fatal_reason:
                                            fatal_reason.append(exhausted_reason)
                                    self.stop_event.set()
                                    return

                                with self.lock:
                                    self._status_message = (
                                        f"HTTP {exc.status} 보호 응답 · 전체 워커 {max(1, int(remaining))}초 대기 후 "
                                        f"자동 재시도 {self._protection_retry_count}회차"
                                    )
                                if created_cooldown:
                                    self.log(
                                        f"HTTP {exc.status} 보호 응답: 전체 워커가 {remaining:.1f}초 대기한 뒤 "
                                        f"같은 픽셀부터 자동 재시도합니다. (재시도 {self._protection_retry_count}회차)"
                                    )
                                continue
                            except StopScanning as exc:
                                reason = f"자동 일시정지: {exc}"
                                with self.lock:
                                    self.meta["scan"]["pausedReason"] = reason
                                    if not fatal_reason:
                                        fatal_reason.append(reason)
                                self.stop_event.set()
                                return
                            except Exception as exc:
                                with self.lock:
                                    self._errors += 1
                                    consecutive_errors += 1
                                    self._consecutive_errors = max(self._consecutive_errors, consecutive_errors)
                                    self._status_message = f"워커 {worker_number} 조회 오류 {consecutive_errors}/{max_errors}: {exc}"
                                if consecutive_errors >= max_errors:
                                    reason = f"워커 {worker_number}에서 오류가 {max_errors}회 연속 발생해 자동 일시정지했습니다."
                                    with self.lock:
                                        self.meta["scan"]["pausedReason"] = reason
                                        if not fatal_reason:
                                            fatal_reason.append(reason)
                                    self.stop_event.set()
                                    return
                                backoff = min(300.0, float(settings["requestIntervalSeconds"]) * (2 ** consecutive_errors))
                                self.stop_event.wait(max(0.1, backoff))

                self.worker_threads = [
                    threading.Thread(target=scan_worker, args=(i + 1,), name=f"scan-worker-{i + 1}", daemon=True)
                    for i in range(workers)
                ]
                for thread in self.worker_threads:
                    thread.start()
                for thread in self.worker_threads:
                    thread.join()
                checkpoint(True)
                if assignment_map is not None:
                    assignment_map.close()
                if assignment_file is not None:
                    assignment_file.close()
                candidates.close()
                owners.close()

            self._progress_initialized = False
            self._stats_initialized = False
            self._ensure_progress_and_stats()
            assigned_done, assigned_total = self._assignment_counts_from_file()
            self._assigned_done_count = assigned_done
            self._assigned_total_count = assigned_total
            global_completed = self._done_count >= total
            self.meta["scan"]["completed"] = global_completed
            if global_completed:
                self.meta["scan"]["pausedReason"] = ""
                self.log("모든 후보 픽셀 작업자 확인을 완료했습니다.")
            elif assigned_done >= assigned_total and not fatal_reason:
                self.meta["scan"]["pausedReason"] = ""
                self.log(f"협업 작업 {shard_index + 1}/{shard_count}의 모든 픽셀을 완료했습니다. 참여 노드라면 작업 결과 파일을 내보내 기준 노드에서 병합하세요.")
            elif fatal_reason:
                self.log(fatal_reason[0])
            elif not self.meta["scan"].get("pausedReason"):
                self.meta["scan"]["pausedReason"] = "일시정지됨"
            self._save_users()
            self._save_meta()
        except Exception as exc:
            self.meta["scan"]["pausedReason"] = f"수집기 오류: {exc}"
            self._save_meta()
            self.log(f"수집기 오류로 중단: {exc}")
        finally:
            self.worker_threads = []
            self._phase = "idle"
            self.stop_event.set()
            self.schedule_analysis_after_stop()

    def _iter_records(self) -> Iterable[tuple[int, int]]:
        if not self.prepared:
            return
        total = int(self.meta["candidatePixels"] or 0)
        with self.candidates_path.open("rb") as cf, self.owners_path.open("rb") as of:
            for _ in range(total):
                linear_raw = cf.read(UINT32.size)
                owner_raw = of.read(INT64.size)
                if len(linear_raw) < UINT32.size or len(owner_raw) < INT64.size:
                    break
                owner = INT64.unpack(owner_raw)[0]
                if owner > 0:
                    yield UINT32.unpack(linear_raw)[0], owner

    def _ensure_progress_and_stats(self) -> None:
        if self._progress_initialized and self._stats_initialized:
            return
        with self.lock:
            if self._progress_initialized and self._stats_initialized:
                return
            stats: dict[int, dict[str, Any]] = {}
            done = no_author = 0
            settings = self.meta["settings"]
            shard_count = int(settings.get("collaborationShardCount", 1))
            shard_index = int(settings.get("collaborationShardIndex", 0))
            assigned_done = assigned_total = 0
            assignment_mode = self.assignment_info()["mode"]
            origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
            origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
            if self.prepared:
                total = int(self.meta["candidatePixels"] or 0)
                with self.candidates_path.open("rb") as cf, self.owners_path.open("rb") as of:
                    for idx in range(total):
                        linear_raw = cf.read(UINT32.size)
                        owner_raw = of.read(INT64.size)
                        if len(linear_raw) < UINT32.size or len(owner_raw) < INT64.size:
                            break
                        owner = INT64.unpack(owner_raw)[0]
                        if assignment_mode == "all" and idx % shard_count == shard_index:
                            assigned_total += 1
                            if owner != OWNER_PENDING:
                                assigned_done += 1
                        if owner == OWNER_PENDING:
                            continue
                        done += 1
                        if owner == OWNER_NO_AUTHOR:
                            no_author += 1
                            continue
                        if owner < 1:
                            continue
                        linear = UINT32.unpack(linear_raw)[0]
                        local_y, local_x = divmod(linear, self.template.width)
                        gx, gy = origin_gx + local_x, origin_gy + local_y
                        item = stats.get(owner)
                        if item is None:
                            item = stats[owner] = {
                                "count": 0,
                                "firstGx": gx,
                                "firstGy": gy,
                                "representativeGx": gx,
                                "representativeGy": gy,
                            }
                        item["count"] += 1
            if assignment_mode == "pending":
                assigned_done, assigned_total = self._assignment_counts_from_file()
            self._done_count = done
            self._no_author_count = no_author
            self._assigned_done_count = assigned_done
            self._assigned_total_count = assigned_total
            self._live_stats = stats
            self._progress_initialized = True
            self._stats_initialized = True

    def compute_stats(self, exact_representative: bool = False) -> dict[int, dict[str, Any]]:
        self._ensure_progress_and_stats()
        with self.lock:
            stats = {uid: dict(item) for uid, item in self._live_stats.items()}
        if not exact_representative or not stats:
            return stats
        self.recalculate_analysis(blocking=True)
        with self.analysis_lock:
            analysis = {uid: dict(value) for uid, value in self._analysis_cache.items()}
        for uid, item in stats.items():
            data = analysis.get(uid, {})
            item["representativeGx"] = int(data.get("gx", item["firstGx"]))
            item["representativeGy"] = int(data.get("gy", item["firstGy"]))
            item["representativeRegionPixels"] = int(data.get("regionPixels", item["count"]))
            item["representativeRegionShare"] = float(data.get("regionShare", 100.0))
            item["overallColors"] = list(data.get("overallColors", []))
            item["regionColors"] = list(data.get("regionColors", []))
        return stats

    def _owner_progress(self) -> tuple[int, int, int]:
        self._ensure_progress_and_stats()
        with self.lock:
            return self._done_count, int(self.meta.get("candidatePixels") or 0), self._no_author_count

    def prepare_reset_info(self) -> dict[str, Any]:
        """Return the destructive impact of running tile comparison again."""
        if not self.prepared:
            return {
                "confirmationRequired": False,
                "checkedPixels": 0,
                "candidatePixels": 0,
                "pendingPixels": 0,
                "assignmentMode": "all",
            }
        done, total, _ = self._owner_progress()
        assignment_mode = self.assignment_info().get("mode", "all")
        return {
            "confirmationRequired": True,
            "checkedPixels": int(done),
            "candidatePixels": int(total),
            "pendingPixels": max(0, int(total) - int(done)),
            "assignmentMode": assignment_mode,
        }


    def _existing_canvas_snapshot(self) -> Path | None:
        """Return the persisted snapshot only when it is a complete native-size PNG."""
        out = self.path / "canvas-snapshot.png"
        if not out.exists():
            return None
        try:
            with Image.open(out) as snapshot:
                if snapshot.format != "PNG" or snapshot.size != (self.template.width, self.template.height):
                    return None
                # Converting loads and validates the PNG stream. Do not call verify()
                # afterwards: Pillow requires verify() immediately after open(), and
                # calling it after convert() incorrectly rejected every valid persisted
                # collaboration snapshot.
                rgba = snapshot.convert("RGBA")
                try:
                    rgba.load()
                    # Reject a native-size but fully transparent snapshot because the
                    # Blue Marble 3x center-dot alpha mask may have sampled the
                    # wrong source pixel. Treat that file as missing so PDF export
                    # recaptures the live canvas instead of reusing a blank image.
                    if self.template.valid_pixel_count > 0 and rgba.getchannel("A").getbbox() is None:
                        return None
                finally:
                    rgba.close()
            return out
        except Exception:
            return None

    def _template_alpha_mask(self) -> Image.Image | None:
        mask_tiles = [tile for tile in self.template.tiles if tile.image_b64]
        if not mask_tiles:
            return None
        origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
        origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
        mask = Image.new("L", (self.template.width, self.template.height), 0)
        for template_tile in mask_tiles:
            target = decode_tile(template_tile)
            try:
                scale = template_tile.image_scale
                logical_size = (target.width // scale, target.height // scale)
                source_alpha = target.getchannel("A")
                try:
                    # Blue Marble imageScale=3 tiles store each logical pixel at
                    # the centre of a 3x3 cell. Pillow's resize mapping already
                    # samples that centre. Adding scale//2 to an affine transform
                    # shifted sampling onto the transparent separator pixels and
                    # produced an entirely empty mask.
                    alpha = source_alpha.resize(logical_size, Image.Resampling.NEAREST)
                finally:
                    source_alpha.close()
                try:
                    gx0 = template_tile.tx * TILE_SIZE + template_tile.start_px
                    gy0 = template_tile.ty * TILE_SIZE + template_tile.start_py
                    mask.paste(alpha, (gx0 - origin_gx, gy0 - origin_gy))
                finally:
                    alpha.close()
            finally:
                target.close()
        return mask

    def _save_masked_snapshot(self, image: Image.Image, out: Path) -> Path:
        rgba = image.convert("RGBA")
        try:
            mask = self._template_alpha_mask()
            if mask is not None:
                try:
                    combined_alpha = ImageChops.multiply(rgba.getchannel("A"), mask)
                    try:
                        rgba.putalpha(combined_alpha)
                    finally:
                        combined_alpha.close()
                finally:
                    mask.close()
            # Keep pixels outside the template mask transparent while preserving the
            # exact Wplace colors and native image dimensions.
            tmp = out.with_name(f"{out.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                rgba.save(tmp, "PNG", optimize=True)
                tmp.replace(out)
            finally:
                tmp.unlink(missing_ok=True)
            return out
        finally:
            rgba.close()

    def build_canvas_snapshot(self) -> Path | None:
        """Stitch a complete cached canvas image, or normalize the persisted snapshot."""
        if not self.prepared:
            return None
        out = self.path / "canvas-snapshot.png"
        required_tiles = sorted({(tile.tx, tile.ty) for tile in self.template.tiles})
        if not required_tiles or any(
            not (self.cache_dir / f"{tx}_{ty}.png").exists() for tx, ty in required_tiles
        ):
            existing = self._existing_canvas_snapshot()
            if existing is None:
                return None
            # Apply the template alpha mask before reusing a saved snapshot.
            with Image.open(existing) as persisted:
                normalized = persisted.convert("RGBA")
            try:
                return self._save_masked_snapshot(normalized, out)
            finally:
                normalized.close()

        origin_gx = self.template.coords[0] * TILE_SIZE + self.template.coords[2]
        origin_gy = self.template.coords[1] * TILE_SIZE + self.template.coords[3]
        canvas = Image.new("RGBA", (self.template.width, self.template.height), (255, 255, 255, 0))
        try:
            for tx, ty in required_tiles:
                path = self.cache_dir / f"{tx}_{ty}.png"
                with Image.open(path) as source:
                    if source.size != (TILE_SIZE, TILE_SIZE):
                        raise RuntimeError(
                            f"현재 타일 {tx},{ty} 크기가 {source.size}입니다; {TILE_SIZE}x{TILE_SIZE}이 필요합니다."
                        )
                    live = source.convert("RGBA")
                tile_gx = tx * TILE_SIZE
                tile_gy = ty * TILE_SIZE
                left = max(origin_gx, tile_gx)
                top = max(origin_gy, tile_gy)
                right = min(origin_gx + self.template.width, tile_gx + TILE_SIZE)
                bottom = min(origin_gy + self.template.height, tile_gy + TILE_SIZE)
                if right > left and bottom > top:
                    crop = live.crop((left - tile_gx, top - tile_gy, right - tile_gx, bottom - tile_gy))
                    canvas.alpha_composite(crop, (left - origin_gx, top - origin_gy))
                    crop.close()
                live.close()
            return self._save_masked_snapshot(canvas, out)
        except Exception:
            persisted = self._existing_canvas_snapshot()
            if persisted is not None:
                with Image.open(persisted) as existing:
                    normalized = existing.convert("RGBA")
                try:
                    return self._save_masked_snapshot(normalized, out)
                finally:
                    normalized.close()
            raise
        finally:
            canvas.close()

    def refresh_canvas_snapshot(self) -> Path:
        """Download current tiles and rebuild the PDF image without resetting scan data."""
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        required_tiles = sorted({(tile.tx, tile.ty) for tile in self.template.tiles})
        if not required_tiles:
            raise RuntimeError("그림을 만들 타일 정보가 없습니다.")
        client = self._client()
        interval = max(0.0, float(self.meta["settings"].get("requestIntervalSeconds", 1.0)))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="wpcs-pdf-tiles-", dir=self.path) as td:
            temp_root = Path(td)
            for number, (tx, ty) in enumerate(required_tiles, start=1):
                temp_path = temp_root / f"{tx}_{ty}.png"
                client.download_tile(tx, ty, temp_path)
                if number < len(required_tiles):
                    time.sleep(max(0.1, min(5.0, interval)))
            for tx, ty in required_tiles:
                (temp_root / f"{tx}_{ty}.png").replace(self.cache_dir / f"{tx}_{ty}.png")
        snapshot = self.build_canvas_snapshot()
        if snapshot is None:
            raise RuntimeError("현재 캔버스 그림을 만들지 못했습니다.")
        self.meta["snapshotAt"] = utc_now()
        self._save_meta()
        return snapshot

    def _current_rate(self) -> float:
        with self.lock:
            if len(self._rate_times) >= 2:
                elapsed = self._rate_times[-1] - self._rate_times[0]
                if elapsed > 0:
                    return (len(self._rate_times) - 1) / elapsed
        settings = self.meta["settings"]
        workers = max(1, int(settings.get("parallelWorkers", 1)))
        interval = float(settings.get("requestIntervalSeconds", 0.5))
        return workers / max(0.05, interval + 0.25)

    def _set_merge_status(self, **patch: Any) -> None:
        with self.merge_status_lock:
            self._merge_status.update(patch)

    def merge_status(self) -> dict[str, Any]:
        with self.merge_status_lock:
            return dict(self._merge_status)

    def finish_collaboration_merge_batch(self) -> None:
        """Release the analysis hold after sequential result merges."""
        if self.prepared:
            self.schedule_analysis_after_stop(force=True)

    def status(self) -> dict[str, Any]:
        done, total, no_author = self._owner_progress()
        stats = self.compute_stats(False) if done else {}
        assigned_done = self._assigned_done_count
        assigned_total = self._assigned_total_count
        rate = self._current_rate()
        remaining = max(0, assigned_total - assigned_done)
        eta_seconds = remaining / rate if rate > 0 else None
        merge_status = self.merge_status()
        analysis_current = self._analysis_cache_is_current(stats.keys())
        if (
            self.prepared
            and done
            and not self.running
            and not merge_status.get("running")
            and not self._analysis_deferred_until_stop
            and not analysis_current
            and not self.analysis_calculating
        ):
            # Small projects finish inline; large projects stay asynchronous so the UI remains responsive.
            self.recalculate_analysis(blocking=total <= 100000)
            analysis_current = self._analysis_cache_is_current(stats.keys())
        exact_analysis = self._analysis_cache if analysis_current else {}
        top = []
        for uid, item in sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:100]:
            meta = self._users.get(uid, UserMeta(uid))
            data = exact_analysis.get(uid, {})
            exact = uid in exact_analysis
            gx = int(data.get("gx", item["firstGx"]))
            gy = int(data.get("gy", item["firstGy"]))
            tx, ty, px, py = global_to_tile_pixel(gx, gy)
            top.append({
                "id": uid,
                "name": meta.name or f"User {uid}",
                "allianceName": meta.allianceName,
                "count": item["count"],
                "shareOfChecked": (item["count"] / max(1, done - no_author)) * 100,
                "representative": {"tx": tx, "ty": ty, "px": px, "py": py},
                "representativeExact": exact,
                "representativeRegionPixels": int(data.get("regionPixels", item["count"])),
                "representativeRegionShare": float(data.get("regionShare", 100.0 if not exact else 0.0)),
                "overallColors": list(data.get("overallColors", [])),
                "regionColors": list(data.get("regionColors", [])),
                "link": wplace_link(tx, ty, px, py),
            })
        assignment_completed = self.prepared and assigned_done >= assigned_total
        reset_info = {
            "confirmationRequired": bool(self.prepared),
            "checkedPixels": int(done),
            "candidatePixels": int(total),
            "pendingPixels": max(0, int(total) - int(done)),
            "assignmentMode": self.assignment_info().get("mode", "all"),
        }
        with self.protection_lock:
            protection_wait_seconds = max(0.0, self._protection_wait_until - time.monotonic())
            protection_retry_count = self._protection_retry_count
            protection_last_status = self._protection_last_status
        with self.analysis_lock:
            analysis_done = self._analysis_progress_done
            analysis_total = self._analysis_progress_total
            analysis_error = self._analysis_error
        return {
            "projectId": self.template.project_id,
            "name": self.name,
            "sourceName": self.template.source_name,
            "coords": list(self.template.coords),
            "width": self.template.width,
            "height": self.template.height,
            "validPixels": self.template.valid_pixel_count,
            "matchMode": self.template.match_mode,
            "templateFormat": self.template.template_format,
            "candidatePixels": self.meta.get("candidatePixels"),
            "pendingPixels": max(0, total - done),
            "assignmentMode": self.assignment_info()["mode"],
            "assignmentPixels": int(self.assignment_info()["count"]),
            "prepared": self.prepared,
            "prepareReset": reset_info,
            "running": self.running,
            "phase": self._phase,
            "prepareDone": self._prepare_done,
            "prepareTotal": self._prepare_total,
            "prepareMatches": self._prepare_matches,
            "message": self._status_message,
            "done": done,
            "total": total,
            "assignedDone": assigned_done,
            "assignedTotal": assigned_total,
            "noAuthor": no_author,
            "progressPercent": (done / total * 100) if total else 0.0,
            "assignmentProgressPercent": (assigned_done / assigned_total * 100) if assigned_total else 0.0,
            "etaSeconds": eta_seconds,
            "requestsPerSecond": rate,
            "errorCount": self._errors,
            "protectiveRetryCount": protection_retry_count,
            "protectiveWaitSeconds": protection_wait_seconds,
            "protectiveLastStatus": protection_last_status,
            "pausedReason": self.meta["scan"].get("pausedReason", ""),
            "completed": bool(self.meta["scan"].get("completed")),
            "assignmentCompleted": assignment_completed,
            "settings": self.meta["settings"],
            "checkpointEvery": int(self.meta["settings"].get("checkpointEvery", 100)),
            "analysisExact": self._analysis_cache_is_current(stats.keys()),
            "analysisCalculating": self.analysis_calculating,
            "analysisQueued": bool(self._analysis_trigger_thread and self._analysis_trigger_thread.is_alive()),
            "analysisDeferredUntilStop": bool(self._analysis_deferred_until_stop),
            "analysisProgressDone": analysis_done,
            "analysisProgressTotal": analysis_total,
            "analysisError": analysis_error,
            "merge": merge_status,
            "topUsers": top,
        }

    def export_csv(self) -> Path:
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        if self.running:
            raise RuntimeError("정확한 대표 영역과 색상 비율을 내보내려면 먼저 일시정지하세요.")
        stats = self.compute_stats(exact_representative=True)
        done, total, no_author = self._owner_progress()
        denominator = max(1, done - no_author)
        out = self.path / f"contributors-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "rank", "user_id", "name", "pixels", "share_of_identified_percent",
                "representative_region_pixels", "representative_region_percent",
                "overall_color_usage", "representative_region_color_usage", "alliance_name",
                "tile_x", "tile_y", "pixel_x", "pixel_y", "latitude", "longitude", "wplace_url",
            ])
            for rank, (uid, item) in enumerate(sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0])), 1):
                meta = self._users.get(uid, UserMeta(uid))
                tx, ty, px, py = global_to_tile_pixel(item["representativeGx"], item["representativeGy"])
                lat, lng = tile_pixel_to_latlng(tx, ty, px, py)
                overall_colors = "; ".join(
                    f"{c['hex']} {c['percent']:.4f}% ({c['count']})" for c in item.get("overallColors", [])
                )
                region_colors = "; ".join(
                    f"{c['hex']} {c['percent']:.4f}% ({c['count']})" for c in item.get("regionColors", [])
                )
                writer.writerow([
                    rank, uid, meta.name, item["count"], f"{item['count'] / denominator * 100:.8f}",
                    item.get("representativeRegionPixels", item["count"]),
                    f"{item.get('representativeRegionShare', 100.0):.8f}",
                    overall_colors, region_colors, meta.allianceName,
                    tx, ty, px, py, f"{lat:.10f}", f"{lng:.10f}", wplace_link(tx, ty, px, py),
                ])
        return out

    def export_pdf(
        self, language: str = "ko", timezone_name: str | None = None,
        timezone_offset_minutes: int | None = None,
        manual_work_start: str | None = None, manual_work_end: str | None = None,
        report_note: str | None = None,
    ) -> Path:
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        if self.running:
            raise RuntimeError("정확한 대표 영역과 색상 비율을 PDF로 내보내려면 먼저 일시정지하세요.")
        stats = self.compute_stats(exact_representative=True)
        done, total, no_author = self._owner_progress()
        denominator = max(1, done - no_author)
        rows: list[dict[str, Any]] = []
        for rank, (uid, item) in enumerate(sorted(stats.items(), key=lambda kv: (-kv[1]["count"], kv[0])), 1):
            meta = self._users.get(uid, UserMeta(uid))
            tx, ty, px, py = global_to_tile_pixel(item["representativeGx"], item["representativeGy"])
            rows.append({
                "rank": rank,
                "userId": uid,
                "name": meta.name or f"User {uid}",
                "allianceName": meta.allianceName,
                "pixels": item["count"],
                "share": item["count"] / denominator * 100.0,
                "regionPixels": item.get("representativeRegionPixels", item["count"]),
                "regionShare": item.get("representativeRegionShare", 100.0),
                "coordinate": f"Tl {tx},{ty} / Px {px},{py}",
                "link": wplace_link(tx, ty, px, py),
                "overallColors": item.get("overallColors", []),
                "regionColors": item.get("regionColors", []),
            })
        out = self.path / f"contributors-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        snapshot_path = self.build_canvas_snapshot()
        if snapshot_path is None:
            self.log("PDF 그림 파일이 없어 현재 캔버스 타일을 다시 내려받는 중입니다.")
            snapshot_path = self.refresh_canvas_snapshot()
        snapshot_at = self.meta.get("snapshotAt") or self.meta.get("preparedAt")
        return build_pdf_report(
            out,
            self.name,
            self.template.source_name,
            utc_now(),
            done,
            total,
            no_author,
            analysis_method(),
            rows,
            language=language,
            match_mode=self.template.match_mode,
            snapshot_path=snapshot_path,
            snapshot_at=snapshot_at,
            timezone_name=timezone_name,
            timezone_offset_minutes=timezone_offset_minutes,
            manual_work_start=manual_work_start,
            manual_work_end=manual_work_end,
            report_note=report_note,
        )

    def export_collaboration_result(self) -> Path:
        if self.running:
            raise RuntimeError("수집을 일시정지한 뒤 결과를 내보내세요.")
        if not self.prepared:
            raise RuntimeError("준비된 프로젝트가 아닙니다.")
        self._save_users()
        candidate_hash = str(self.meta["candidateHash"])
        settings = self.meta["settings"]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = self.path / f"collab-node-result-{settings['collaborationShardIndex'] + 1}of{settings['collaborationShardCount']}-{stamp}.zip"
        manifest = {
            "type": COLLAB_RESULT_TYPE,
            "formatVersion": COLLABORATION_FORMAT_VERSION,
            "generatedAt": utc_now(),
            "projectId": self.template.project_id,
            "sourceHash": self.template.source_hash,
            "candidatePixels": int(self.meta.get("candidatePixels") or 0),
            "candidateHash": candidate_hash,
            "shardCount": int(settings.get("collaborationShardCount", 1)),
            "shardIndex": int(settings.get("collaborationShardIndex", 0)),
        }
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.write(self.owners_path, "owners.bin")
            zf.write(self.users_path, "users.json")
        verify_created_zip(out, COLLAB_RESULT_TYPE)
        return out

    def merge_collaboration_result(self, package_path: Path, *, defer_analysis: bool = False) -> dict[str, int]:
        if self.running:
            raise RuntimeError("수집을 일시정지한 뒤 결과를 병합하세요.")
        if not self.prepared:
            raise RuntimeError("먼저 동일한 협업 시작 파일을 가져오거나 프로젝트를 준비해야 합니다.")
        if not self.merge_lock.acquire(blocking=False):
            raise RuntimeError("이미 다른 작업 결과를 병합하고 있습니다.")

        total_candidates = int(self.meta.get("candidatePixels") or 0)
        self._set_merge_status(
            running=True,
            fileName=package_path.name,
            phase="validating",
            processed=0,
            total=total_candidates,
            added=0,
            same=0,
            conflicts=0,
            error="",
        )
        # Stop queued or running analysis before owners.bin is modified.
        self._defer_analysis_for_scan()
        prior_trigger = self._analysis_trigger_thread
        if prior_trigger and prior_trigger.is_alive() and prior_trigger is not threading.current_thread():
            prior_trigger.join()
        prior_analysis = self._analysis_thread
        if prior_analysis and prior_analysis.is_alive() and prior_analysis is not threading.current_thread():
            prior_analysis.join()
        self._defer_analysis_for_scan()
        try:
            with CollaborationPackage(package_path) as package:
                manifest = package.manifest
                package_type = package.package_type
                if package_type == COLLAB_JOB_TYPE:
                    raise ValueError(
                        "선택한 파일은 협업 시작 파일입니다. 시작 파일은 '협업 시작 파일 가져오기'에 사용하고, "
                        "병합에는 참여 노드가 내보낸 collab-node-result-*.zip을 선택하세요."
                    )
                if package_type != COLLAB_RESULT_TYPE:
                    shown = package_type or "종류 정보 없음"
                    raise ValueError(f"작업 결과 파일이 아닙니다. 감지된 패키지 종류: {shown}")
                required_fields = {
                    "type", "formatVersion", "generatedAt", "projectId", "sourceHash",
                    "candidatePixels", "candidateHash", "shardCount", "shardIndex",
                }
                if set(manifest) != required_fields:
                    details: list[str] = []
                    missing_fields = sorted(required_fields - set(manifest))
                    unexpected_fields = sorted(set(manifest) - required_fields)
                    if missing_fields:
                        details.append("누락: " + ", ".join(missing_fields))
                    if unexpected_fields:
                        details.append("불필요: " + ", ".join(unexpected_fields))
                    raise ValueError("작업 결과 manifest.json 항목 구성이 올바르지 않습니다. " + " / ".join(details))
                expected_files = {"manifest.json", "owners.bin", "users.json"}
                if package.file_names != expected_files:
                    unexpected = sorted(package.file_names - expected_files)
                    missing_files = sorted(expected_files - package.file_names)
                    details = []
                    if missing_files:
                        details.append(f"누락: {', '.join(missing_files)}")
                    if unexpected:
                        details.append(f"불필요: {', '.join(unexpected)}")
                    raise ValueError("작업 결과 ZIP 구성이 올바르지 않습니다. " + " / ".join(details))
                if str(manifest.get("projectId") or "") != self.template.project_id:
                    raise ValueError("다른 프로젝트의 작업 결과입니다. 같은 협업 시작 파일에서 만든 프로젝트를 선택하세요.")
                if manifest.get("sourceHash") != self.template.source_hash:
                    raise ValueError("템플릿 해시가 다른 작업 결과입니다.")
                if int(manifest.get("candidatePixels", -1)) != total_candidates:
                    raise ValueError("후보 픽셀 수가 다른 작업 결과입니다.")
                local_hash = str(self.meta["candidateHash"])
                if manifest.get("candidateHash") != local_hash:
                    raise ValueError("후보 목록이 다른 작업 결과입니다. 같은 협업 시작 파일을 사용해야 합니다.")
                remote_owners = package.read("owners.bin")
                expected = total_candidates * INT64.size
                if len(remote_owners) != expected:
                    raise ValueError(
                        f"작업 결과 owners.bin 크기가 올바르지 않습니다. "
                        f"예상 {expected:,}바이트, 실제 {len(remote_owners):,}바이트"
                    )
                remote_users = package.read_json("users.json")

            if not isinstance(remote_users, dict):
                raise ValueError("작업 결과의 users.json 형식이 올바르지 않습니다.")
            shard_count = int(manifest["shardCount"])
            shard_index = int(manifest["shardIndex"])
            if not 1 <= shard_count <= 1024 or not 0 <= shard_index < shard_count:
                raise ValueError("작업 결과의 협업 작업 번호가 올바르지 않습니다.")

            self._set_merge_status(phase="merging")
            added = same = conflicts = 0
            update_every = max(1, min(20000, total_candidates // 200 if total_candidates else 1))
            with self.owners_path.open("r+b") as f:
                local = mmap.mmap(f.fileno(), 0)
                try:
                    for idx in range(total_candidates):
                        remote = INT64.unpack_from(remote_owners, idx * INT64.size)[0]
                        if remote != OWNER_PENDING:
                            current = INT64.unpack_from(local, idx * INT64.size)[0]
                            if current == OWNER_PENDING:
                                INT64.pack_into(local, idx * INT64.size, remote)
                                added += 1
                            elif current == remote:
                                same += 1
                            else:
                                conflicts += 1
                        processed = idx + 1
                        if processed == total_candidates or processed % update_every == 0:
                            self._set_merge_status(
                                processed=processed,
                                added=added,
                                same=same,
                                conflicts=conflicts,
                            )
                    self._set_merge_status(phase="saving", processed=total_candidates)
                    local.flush()
                    os.fsync(f.fileno())
                finally:
                    local.close()
            for key, value in remote_users.items():
                uid = int(key)
                incoming = UserMeta(**value)
                current = self._users.get(uid)
                if current is None or (not current.name and incoming.name):
                    self._users[uid] = incoming
            self._save_users()
            self._progress_initialized = False
            self._stats_initialized = False
            self._ensure_progress_and_stats()
            self.meta["scan"]["completed"] = self._done_count >= total_candidates
            self.meta["scan"]["pausedReason"] = ""
            self._save_meta()
            self._invalidate_analysis()
            self.log(f"작업 결과 병합 완료: 신규 {added:,}, 중복 {same:,}, 충돌 {conflicts:,}")
            self._set_merge_status(
                running=False,
                phase="completed",
                processed=total_candidates,
                added=added,
                same=same,
                conflicts=conflicts,
            )
            if not defer_analysis:
                self.schedule_analysis_after_stop(force=True)
            return {"added": added, "same": same, "conflicts": conflicts}
        except Exception as exc:
            self._set_merge_status(running=False, phase="error", error=str(exc))
            if not defer_analysis:
                self.schedule_analysis_after_stop(force=True)
            raise
        finally:
            self.merge_lock.release()
