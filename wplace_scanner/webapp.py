from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .constants import APP_VERSION
from .manager import ProjectManager
from .localization import normalize_language, translate_error, translate_status_payload
from .console_i18n import console_text, detect_console_language


def _query_value(parsed_query: str, name: str, default: str) -> str:
    query = urllib.parse.parse_qs(parsed_query)
    return str(query.get(name, [default])[0]).strip()


def _query_int(parsed_query: str, name: str, default: int) -> int:
    query = urllib.parse.parse_qs(parsed_query)
    raw = str(query.get(name, [default])[0]).strip()
    return int(raw)


def _query_optional_int(parsed_query: str, name: str) -> int | None:
    query = urllib.parse.parse_qs(parsed_query)
    if name not in query:
        return None
    raw = str(query[name][0]).strip()
    return int(raw)


class AppHandler(BaseHTTPRequestHandler):
    server_version = f"WplaceContributorScanner/{APP_VERSION}"

    @property
    def manager(self) -> ProjectManager:
        return self.server.manager  # type: ignore[attr-defined]

    @property
    def app_root(self) -> Path:
        return self.server.app_root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def _json(self, payload, status=200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._response_started = True
        self.end_headers()
        self.wfile.write(body)

    def _request_language(self) -> str:
        parsed = urllib.parse.urlparse(self.path)
        return normalize_language(
            self.headers.get("X-WPCS-Language") or _query_value(parsed.query, "lang", "ko")
        )

    def _error(self, exc: Exception, status=400) -> None:
        if getattr(self, "_response_started", False):
            print(f"[web] response already started; suppressed secondary error: {exc}")
            return
        self._json({"ok": False, "error": translate_error(str(exc), self._request_language())}, status)

    def _raw_body(self, max_bytes: int = 300 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise ValueError(f"요청 크기는 1바이트~{max_bytes // 1024 // 1024}MB 범위여야 합니다.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise IOError("요청 본문이 중간에 끊겼습니다.")
        return raw

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 10 * 1024 * 1024:
            raise ValueError("JSON 요청이 너무 큽니다.")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _read_upload(self, max_bytes: int = 300 * 1024 * 1024) -> Path:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            raise ValueError(f"업로드 크기는 1바이트~{max_bytes // 1024 // 1024}MB 범위여야 합니다.")
        filename = Path(urllib.parse.unquote(self.headers.get("X-Filename", "upload.zip"))).name
        destination = self.manager.inbox / filename
        tmp = destination.with_suffix(destination.suffix + ".upload")
        remaining = length
        try:
            with tmp.open("wb") as f:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise IOError("업로드가 중간에 끊겼습니다.")
                    f.write(chunk)
                    remaining -= len(chunk)
            tmp.replace(destination)
            return destination
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _remove_processed_upload(self, path: Path) -> None:
        """Remove a successfully processed temporary upload from data/inbox only."""
        try:
            if path.parent.resolve() == self.manager.inbox.resolve():
                path.unlink(missing_ok=True)
        except OSError as exc:
            # Processing has already succeeded. A cleanup failure should not turn
            # the completed import/merge into a user-visible failure.
            print(f"[web] could not remove processed inbox upload {path.name}: {exc}")

    def _send_file(self, output: Path, content_type: str) -> None:
        size = output.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{output.name}"')
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._response_started = True
        self.end_headers()
        try:
            with output.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            # The browser, antivirus, or proxy cancelled a large download. This is
            # not an application error and the generated ZIP remains on disk.
            print(f"[web] download cancelled by client: {output.name} ({exc})")
        finally:
            self.close_connection = True

    def do_GET(self) -> None:
        self._response_started = False
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/projects":
                self._json({"ok": True, "projects": self.manager.list_projects(), "activeId": self.manager.active_id})
                return
            if path == "/api/status":
                active = self.manager.active
                self._json({"ok": True, "status": translate_status_payload(active.status(), self._request_language()) if active else None})
                return
            if path == "/api/snapshot-template/image":
                capture_id = _query_value(parsed.query, "id", "")
                kind = _query_value(parsed.query, "kind", "original")
                image = self.manager.snapshot_templates.image_path(capture_id, kind)
                data = image.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self._response_started = True
                self.end_headers()
                self.wfile.write(data)
                return
            active = self.manager.active
            if path == "/api/export":
                if not active:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                self._send_file(active.export_csv(), "text/csv; charset=utf-8")
                return
            if path == "/api/export/pdf":
                if not active:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                language = _query_value(parsed.query, "lang", "ko")
                timezone_name = _query_value(parsed.query, "tz", "")
                timezone_offset_minutes = _query_optional_int(parsed.query, "tzOffsetMinutes")
                manual_work_start = _query_value(parsed.query, "workStart", "")
                manual_work_end = _query_value(parsed.query, "workEnd", "")
                report_note = _query_value(parsed.query, "reportNote", "")
                self._send_file(
                    active.export_pdf(
                        language=language,
                        timezone_name=timezone_name or None,
                        timezone_offset_minutes=timezone_offset_minutes,
                        manual_work_start=manual_work_start or None,
                        manual_work_end=manual_work_end or None,
                        report_note=report_note or None,
                    ),
                    "application/pdf",
                )
                return
            if path == "/api/collaboration/job":
                if not active:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                self._send_file(self.manager.export_collaboration_job(active), "application/zip")
                return
            if path == "/api/collaboration/rebalance-job":
                if not active:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                shards = _query_int(
                    parsed.query,
                    "shards",
                    int(active.meta["settings"].get("collaborationShardCount", 1)),
                )
                if shards < 1 or shards > 1024:
                    raise ValueError("협업 분할 수는 1~1024 범위여야 합니다.")
                active.update_settings({"collaborationShardCount": shards, "collaborationShardIndex": 0})
                self._send_file(
                    self.manager.export_collaboration_job(active, rebalance_pending=True),
                    "application/zip",
                )
                return
            if path == "/api/collaboration/result":
                if not active:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                self._send_file(active.export_collaboration_result(), "application/zip")
                return
            if path == "/" or path == "/index.html":
                file_path = self.app_root / "static" / "index.html"
            else:
                file_path = self.app_root / "static" / path.lstrip("/")
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._response_started = True
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            print(f"[web] client disconnected: {exc}")
        except Exception as exc:
            try:
                self._error(exc)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as disconnect:
                print(f"[web] client disconnected while reporting error: {disconnect}")

    def do_POST(self) -> None:
        self._response_started = False
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/snapshot-template/capture":
                body = self._body_json()
                result = self.manager.capture_snapshot_region(body)
                self._json({"ok": True, "capture": result})
                return
            if path == "/api/snapshot-template/edit-existing":
                result = self.manager.reopen_active_snapshot_template()
                self._json({"ok": True, "capture": result})
                return
            if path == "/api/snapshot-template/create":
                capture_id = urllib.parse.unquote(self.headers.get("X-Capture-Id", "")).strip()
                name = urllib.parse.unquote(self.headers.get("X-Template-Name", "snapshot-template")).strip()
                match_mode = urllib.parse.unquote(self.headers.get("X-Match-Mode", "region")).strip()
                edited_png = self._raw_body()
                project = self.manager.create_snapshot_template_project(
                    capture_id, edited_png, name=name, match_mode=match_mode
                )
                def run_snapshot_prepare():
                    try:
                        project.prepare(False)
                    except Exception as exc:
                        project.preparation_failed(exc)
                threading.Thread(target=run_snapshot_prepare, daemon=True).start()
                self._json({
                    "ok": True,
                    "projectId": project.template.project_id,
                    "status": translate_status_payload(project.status(), self._request_language()),
                })
                return
            if path == "/api/import":
                destination = self._read_upload()
                if destination.suffix.lower() not in (".json", ".zip"):
                    raise ValueError("JSON 또는 ZIP 파일만 지원합니다.")
                imported = self.manager.import_path(destination, copy_source=True)
                self.manager.select(imported[0].template.project_id)
                self._remove_processed_upload(destination)
                self._json({"ok": True, "projects": [p.template.project_id for p in imported]})
                return
            if path == "/api/collaboration/import-job":
                destination = self._read_upload()
                project = self.manager.import_collaboration_job(destination)
                self._remove_processed_upload(destination)
                self._json({"ok": True, "status": translate_status_payload(project.status(), self._request_language())})
                return
            if path == "/api/collaboration/merge-result":
                destination = self._read_upload()
                active = self.manager.find_project_for_collaboration_result(destination)
                if not active:
                    raise RuntimeError(
                        "이 작업 결과 파일과 일치하는 로컬 프로젝트를 찾지 못했습니다. "
                        "먼저 같은 협업 시작 파일을 가져온 뒤 다시 병합하세요."
                    )
                self.manager.select(active.template.project_id)
                batch_merge = str(self.headers.get("X-WPCS-Batch-Merge") or "").strip().lower() in {"1", "true", "yes"}
                result = active.merge_collaboration_result(destination, defer_analysis=batch_merge)
                self._remove_processed_upload(destination)
                self._json({
                    "ok": True,
                    "merge": result,
                    "status": translate_status_payload(active.status(), self._request_language()),
                    "projectId": active.template.project_id,
                })
                return

            body = self._body_json()
            if path == "/api/collaboration/finish-merge-batch":
                project_ids = body.get("projectIds") or []
                if not isinstance(project_ids, list):
                    raise ValueError("projectIds는 배열이어야 합니다.")
                requested = [str(value) for value in project_ids if value]
                if body.get("allDeferred"):
                    requested.extend(
                        project_id
                        for project_id, project in self.manager.projects.items()
                        if bool(getattr(project, "_analysis_deferred_until_stop", False))
                    )
                finished = []
                for project_id in dict.fromkeys(requested):
                    project = self.manager.projects.get(project_id)
                    if project is None:
                        continue
                    project.finish_collaboration_merge_batch()
                    finished.append(project_id)
                self._json({"ok": True, "projectIds": finished})
                return
            if path == "/api/select":
                project = self.manager.select(str(body["projectId"]))
                self._json({"ok": True, "status": translate_status_payload(project.status(), self._request_language())})
                return
            if path == "/api/project/delete":
                project_id = str(body.get("projectId") or self.manager.active_id or "")
                if not project_id:
                    raise RuntimeError("선택된 프로젝트가 없습니다.")
                deleted = self.manager.delete_project(project_id)
                active_after_delete = self.manager.active
                self._json({
                    "ok": True,
                    "deleted": deleted,
                    "activeId": self.manager.active_id,
                    "projects": self.manager.list_projects(),
                    "status": translate_status_payload(
                        active_after_delete.status(), self._request_language()
                    ) if active_after_delete else None,
                })
                return
            active = self.manager.active
            if not active:
                raise RuntimeError("선택된 프로젝트가 없습니다.")
            if path == "/api/project/rename":
                name = active.rename(str(body.get("name") or ""))
                self._json({
                    "ok": True,
                    "name": name,
                    "status": translate_status_payload(active.status(), self._request_language()),
                })
                return
            if path == "/api/prepare":
                if active.running:
                    raise RuntimeError("수집 중에는 준비할 수 없습니다.")
                reset_info = active.prepare_reset_info()
                if reset_info["confirmationRequired"]:
                    expected = f"RESET:{active.template.project_id}"
                    if body.get("resetConfirmation") != expected:
                        raise RuntimeError(
                            "현재 그림과 비교를 다시 실행하면 기존 작업자 확인 결과, 진행률, "
                            "협업 작업 순서와 분석 결과가 초기화됩니다. GUI의 2단계 초기화 확인을 거쳐 실행하세요."
                        )

                def run_prepare():
                    try:
                        active.prepare(bool(body.get("refreshTiles", True)))
                    except Exception as exc:
                        active.preparation_failed(exc)

                threading.Thread(target=run_prepare, daemon=True).start()
                self._json({"ok": True})
                return
            if path == "/api/start":
                if active.prepared:
                    active.start()
                    self._json({"ok": True, "preparing": False})
                    return
                if active._phase == "prepare":
                    raise RuntimeError("현재 그림 비교가 진행 중입니다. 완료 후 자동으로 활성화됩니다.")

                def run_prepare_and_start():
                    try:
                        active.prepare(True)
                        if not active.meta["scan"].get("completed"):
                            active.start()
                    except Exception as exc:
                        active.preparation_failed(exc)

                threading.Thread(target=run_prepare_and_start, daemon=True).start()
                self._json({"ok": True, "preparing": True})
                return
            if path == "/api/pause":
                active.pause()
                self._json({"ok": True})
                return
            if path == "/api/settings":
                active.update_settings(body)
                self._json({"ok": True, "settings": active.meta["settings"]})
                return
            self.send_error(404)
        except KeyError as exc:
            try:
                self._error(ValueError(f"필수 값이 없습니다: {exc}"))
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as disconnect:
                print(f"[web] client disconnected while reporting error: {disconnect}")
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            print(f"[web] client disconnected: {exc}")
        except Exception as exc:
            try:
                self._error(exc)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as disconnect:
                print(f"[web] client disconnected while reporting error: {disconnect}")


class ScannerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def serve(
    app_root: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False,
    console_language: str | None = None,
) -> None:
    manager = ProjectManager(app_root)
    server = ScannerHTTPServer((host, port), AppHandler)
    server.manager = manager  # type: ignore[attr-defined]
    server.app_root = app_root  # type: ignore[attr-defined]
    listen_url = f"http://{host}:{port}/"
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    browser_url = f"http://{browser_host}:{port}/"
    language = detect_console_language(console_language)
    print(console_text("listening", language, url=listen_url))
    if host in {"0.0.0.0", "::"}:
        print(console_text("access", language, port=port, local=browser_url))
        print(console_text("warning", language))
    print(console_text("stop", language))
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(browser_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for project in manager.projects.values():
            if project.running:
                project.pause(console_text("shutdown_pause", language))
        server.server_close()
