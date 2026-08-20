from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Callable
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, make_response, request, send_file

from sau_cli import build_parser, dispatch
from sau_desktop_service import JobStatus, PublishRequest
from sau_runtime import RuntimePaths, use_runtime_paths
from utils.risk_control import read_publish_safety_status


_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_RETIRED_LEGACY_PATHS = frozenset({"/postVideo", "/postVideoBatch"})
_GUI_PLATFORMS = frozenset({"douyin", "kuaishou", "tencent", "xiaohongshu"})
_ACCOUNT_NAME = re.compile(r"^[\w .@+-]{1,80}$")
_MATERIAL_ID = re.compile(r"^[0-9a-f]{32}$")
_TERMINAL_LOGIN_STATES = frozenset({"succeeded", "failed", "blocked"})
_SAFE_MEDIA_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".webm": "video/webm",
    ".webp": "image/webp",
}
_PREVIEW_MEDIA_TYPES = frozenset(_SAFE_MEDIA_TYPES.values())


def _success(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data, "error": None}), status


def _failure(code: str, message: str, status: int):
    return jsonify({
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
    }), status


def _loopback_request_origin() -> tuple[str, str, int] | None:
    match = re.fullmatch(r"127\.0\.0\.1(?::([0-9]{1,5}))?", request.host)
    if match is None:
        return None
    port_text = match.group(1)
    if port_text is None:
        port = 443 if request.scheme == "https" else 80
    else:
        port = int(port_text)
        if not 1 <= port <= 65535:
            return None
    return request.scheme, "127.0.0.1", port


def _origin_matches(expected: tuple[str, str, int], raw_origin: str) -> bool:
    try:
        parsed = urlsplit(raw_origin)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        port = parsed.port
    except ValueError:
        return False
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return (
        isinstance(parsed.hostname, str)
        and secrets.compare_digest(parsed.scheme, expected[0])
        and secrets.compare_digest(parsed.hostname, expected[1])
        and port == expected[2]
    )


def _valid_session(session_token: str) -> bool:
    supplied = request.cookies.get("sau_session", "")
    return bool(supplied) and secrets.compare_digest(supplied, session_token)


def _status_value(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value)


def _public_job_message(status: str, message: str) -> str:
    if status == JobStatus.BLOCKED.value:
        return "Publishing was blocked by local safety controls."
    if status == JobStatus.FAILED.value:
        return "Publishing failed. Check local application logs for details."
    if status == JobStatus.WAITING_FOR_CONFIRMATION.value:
        return "Waiting for explicit publish confirmation."
    if status == JobStatus.WAITING_FOR_LOGIN.value:
        return "Waiting for account login."
    if status == JobStatus.SUCCEEDED.value:
        return "completed"
    return "" if not message else "Job is in progress."


def _job_payload(job: Any) -> dict[str, Any]:
    status = _status_value(job.status)
    message = getattr(job, "message", "")
    if not isinstance(message, str):
        message = ""
    result_code = getattr(job, "result_code", None)
    if type(result_code) is not int:
        result_code = None
    created_at = getattr(job, "created_at", None)
    if type(created_at) not in {int, float}:
        created_at = None
    updated_at = getattr(job, "updated_at", None)
    if type(updated_at) not in {int, float}:
        updated_at = None
    return {
        "id": str(job.id),
        "status": status,
        "message": _public_job_message(status, message),
        "resultCode": result_code,
        "createdAt": created_at,
        "updatedAt": updated_at,
    }


def _contained_path(root: Path, candidate: Path, *, label: str) -> Path:
    root_resolved = Path(root).expanduser().resolve()
    candidate_resolved = Path(candidate).expanduser().resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"{label} must stay inside its runtime directory")
    return candidate_resolved


def _prepare_runtime_paths(paths: RuntimePaths) -> None:
    data_root = Path(paths.data_root).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    for label, directory in (
        ("cookies", paths.cookies_dir),
        ("profiles", paths.profiles_dir),
        ("logs", paths.logs_dir),
        ("safety", paths.safety_dir),
        ("media", paths.media_dir),
    ):
        resolved = _contained_path(data_root, directory, label=label)
        resolved.mkdir(parents=True, exist_ok=True)
    database = _contained_path(
        data_root,
        paths.database_file,
        label="database",
    )
    database.parent.mkdir(parents=True, exist_ok=True)


def _initialize_database(paths: RuntimePaths) -> None:
    with sqlite3.connect(paths.database_file) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_info (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type INTEGER NOT NULL,
                filePath TEXT NOT NULL,
                userName TEXT NOT NULL,
                status INTEGER DEFAULT 0
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS file_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filesize REAL,
                upload_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                file_path TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS desktop_materials (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL,
                media_type TEXT
            )
        """)
        material_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(desktop_materials)")
        }
        if "media_type" not in material_columns:
            connection.execute(
                "ALTER TABLE desktop_materials ADD COLUMN media_type TEXT"
            )


def _account_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("accountName is required")
    cleaned = value.strip()
    if cleaned in {"", ".", ".."} or not _ACCOUNT_NAME.fullmatch(cleaned):
        raise ValueError("accountName contains unsupported path characters")
    return cleaned


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip() or None


def _material_record(paths: RuntimePaths, material_id: Any) -> dict[str, Any]:
    if not isinstance(material_id, str) or not _MATERIAL_ID.fullmatch(material_id):
        raise KeyError("invalid material id")
    with sqlite3.connect(paths.database_file) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, original_name, stored_name, size_bytes, created_at, media_type
            FROM desktop_materials WHERE id = ?
            """,
            (material_id,),
        ).fetchone()
    if row is None:
        raise KeyError(material_id)
    return dict(row)


def _material_storage_path(paths: RuntimePaths, record: dict[str, Any]) -> Path:
    stored_name = record.get("stored_name")
    if not isinstance(stored_name, str) or not stored_name:
        raise KeyError("invalid stored material")
    try:
        material_file = _contained_path(
            paths.media_dir,
            Path(paths.media_dir) / stored_name,
            label="material file",
        )
    except ValueError as exc:
        raise KeyError("invalid stored material") from exc
    return material_file


def _material_file(paths: RuntimePaths, record: dict[str, Any]) -> Path:
    material_file = _material_storage_path(paths, record)
    if not material_file.is_file():
        raise KeyError("material file missing")
    return material_file


def _material_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["original_name"],
        "sizeBytes": record["size_bytes"],
        "createdAt": record["created_at"],
    }


def _original_filename(raw_name: str) -> str:
    normalized = raw_name.replace("\\", "/")
    name = Path(normalized).name.strip()
    name = "".join(character for character in name if ord(character) >= 32)
    return name[:200] or "material"


def _detected_media_type(filename: str, prefix: bytes) -> str | None:
    suffix = Path(filename).suffix.lower()
    expected = _SAFE_MEDIA_TYPES.get(suffix)
    if expected is None:
        return None
    if suffix == ".png":
        valid = prefix.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = prefix.startswith(b"\xff\xd8\xff")
    elif suffix == ".gif":
        valid = prefix.startswith((b"GIF87a", b"GIF89a"))
    elif suffix == ".webp":
        valid = (
            len(prefix) >= 12
            and prefix.startswith(b"RIFF")
            and prefix[8:12] == b"WEBP"
        )
    elif suffix == ".webm":
        valid = prefix.startswith(b"\x1a\x45\xdf\xa3")
    else:
        valid = len(prefix) >= 12 and prefix[4:8] == b"ftyp"
    return expected if valid else None


def _media_path(paths: RuntimePaths, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mediaFile is required")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(paths.media_dir) / candidate
    return _contained_path(
        paths.media_dir,
        candidate,
        label="media file",
    )


def _publish_request(paths: RuntimePaths, payload: Any) -> PublishRequest:
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    platform = payload.get("platform")
    if platform not in _GUI_PLATFORMS:
        raise ValueError("unsupported platform")
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    automatic_publish = payload.get("automaticPublish", False)
    if type(automatic_publish) is not bool:
        raise ValueError("automaticPublish must be a boolean")
    material_id = payload.get("materialId")
    media_file = payload.get("mediaFile")
    if material_id is not None and media_file is not None:
        raise ValueError("choose materialId or mediaFile")
    if material_id is not None:
        try:
            media_path = _material_file(paths, _material_record(paths, material_id))
        except KeyError as exc:
            raise ValueError("materialId is invalid") from exc
    else:
        media_path = _media_path(paths, media_file)
    return PublishRequest(
        platform=platform,
        account_name=_account_name(payload.get("accountName")),
        media_file=media_path,
        title=_required_string(payload, "title"),
        tags=tuple(tags),
        description=_optional_string(payload, "description") or "",
        schedule=_optional_string(payload, "schedule"),
        declaration=_optional_string(payload, "declaration"),
        content_source=_optional_string(payload, "contentSource"),
        automatic_publish=automatic_publish,
    )


def _public_safety_status(status: dict[str, Any]) -> dict[str, Any]:
    lock = status.get("lock") if isinstance(status.get("lock"), dict) else {}
    audit = status.get("audit") if isinstance(status.get("audit"), dict) else {}
    latest_failure = status.get("latest_failure")
    if isinstance(latest_failure, dict):
        latest_failure = {
            key: latest_failure.get(key)
            for key in (
                "stage",
                "error_type",
                "task_id",
                "manual_reconciliation_required",
            )
            if key in latest_failure
        }
    else:
        latest_failure = None
    public = {
        "platform": status.get("platform"),
        "account": status.get("account"),
        "stateStatus": status.get("state_status"),
        "lastSuccessAt": status.get("last_success_at"),
        "cooldownRemainingSeconds": status.get("cooldown_remaining_seconds", 0),
        "recentCount": status.get("recent_count", 0),
        "lock": {
            "exists": bool(lock.get("exists", False)),
            "pid": lock.get("pid"),
            "pidAlive": bool(lock.get("pid_alive", False)),
        },
        "audit": {
            "sizeBytes": audit.get("size_bytes", 0),
            "backupCount": audit.get("backup_count", 0),
        },
        "latestFailure": latest_failure,
    }
    if status.get("state_status") == "corrupt":
        public["error"] = "Local safety state is corrupt."
    return public


@dataclass(frozen=True, slots=True)
class _LoginJob:
    id: str
    platform: str
    account_name: str
    status: str = JobStatus.QUEUED.value
    message: str = ""
    result_code: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class _LoginJobManager:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        parser_factory: Callable,
        dispatcher: Callable,
    ) -> None:
        self._paths = paths
        self._parser_factory = parser_factory
        self._dispatcher = dispatcher
        self._lock = threading.RLock()
        self._jobs: dict[str, _LoginJob] = {}
        self._active: dict[tuple[str, str], str] = {}
        self._completed: deque[str] = deque()
        self._queue: Queue[str | None] = Queue(maxsize=20)
        self._running: set[str] = set()
        self._shutting_down = False
        self._workers = tuple(
            threading.Thread(
                target=self._worker,
                name=f"sau-login-{index + 1}",
                daemon=True,
            )
            for index in range(2)
        )
        for worker in self._workers:
            worker.start()

    def submit(self, platform: str, account_name: str) -> _LoginJob:
        key = platform, account_name
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("login manager is shutting down")
            existing_id = self._active.get(key)
            if existing_id is not None:
                return replace(self._jobs[existing_id])
            if len(self._active) >= 20:
                raise RuntimeError("login queue is full")
            now = time.time()
            job = _LoginJob(
                id=uuid.uuid4().hex,
                platform=platform,
                account_name=account_name,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.id] = job
            self._active[key] = job.id
            try:
                self._queue.put_nowait(job.id)
            except Full as exc:
                self._jobs.pop(job.id, None)
                self._active.pop(key, None)
                raise RuntimeError("login queue is full") from exc
        return job

    def get(self, job_id: str) -> _LoginJob:
        with self._lock:
            try:
                return replace(self._jobs[job_id])
            except KeyError as exc:
                raise KeyError(job_id) from exc

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                with self._lock:
                    shutting_down = self._shutting_down
                    if not shutting_down:
                        self._running.add(job_id)
                if shutting_down:
                    self._cancel(job_id)
                else:
                    self._run(job_id)
            finally:
                if job_id is not None:
                    with self._lock:
                        self._running.discard(job_id)
                self._queue.task_done()

    def _run(self, job_id: str) -> None:
        self._set(job_id, status=JobStatus.WAITING_FOR_LOGIN.value)
        try:
            with use_runtime_paths(self._paths):
                current = self.get(job_id)
                args = self._parser_factory().parse_args([
                    current.platform,
                    "login",
                    "--account",
                    current.account_name,
                    "--headed",
                ])
                result = self._dispatcher(args)
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
            if type(result) is int and result == 0:
                self._finish(
                    job_id,
                    status=JobStatus.SUCCEEDED.value,
                    message="completed",
                    result_code=0,
                )
            else:
                self._finish(
                    job_id,
                    status=JobStatus.FAILED.value,
                    message="Login did not complete successfully.",
                    result_code=result if type(result) is int else None,
                )
        except Exception:
            self._finish(
                job_id,
                status=JobStatus.FAILED.value,
                message="Login failed. Check local application logs for details.",
            )
        except SystemExit:
            self._finish(
                job_id,
                status=JobStatus.FAILED.value,
                message="Login request validation failed.",
            )
        finally:
            self._retain_completed(job_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            for job_id in tuple(self._running):
                self._set(
                    job_id,
                    status=JobStatus.BLOCKED.value,
                    message="Login was cancelled during application shutdown.",
                )
        while True:
            try:
                job_id = self._queue.get_nowait()
            except Empty:
                break
            try:
                if job_id is not None:
                    self._cancel(job_id)
            finally:
                self._queue.task_done()
        for _worker in self._workers:
            try:
                self._queue.put_nowait(None)
            except Full:
                break

    def _cancel(self, job_id: str) -> None:
        self._set(
            job_id,
            status=JobStatus.BLOCKED.value,
            message="Login was cancelled during application shutdown.",
        )
        self._retain_completed(job_id)

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        message: str = "",
        result_code: int | None = None,
    ) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._set(
                job_id,
                status=status,
                message=message,
                result_code=result_code,
            )

    def _set(
        self,
        job_id: str,
        *,
        status: str,
        message: str = "",
        result_code: int | None = None,
    ) -> None:
        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = replace(
                current,
                status=status,
                message=message,
                result_code=result_code,
                updated_at=time.time(),
            )

    def _retain_completed(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                key = job.platform, job.account_name
                if self._active.get(key) == job_id:
                    self._active.pop(key, None)
            self._completed.append(job_id)
            while len(self._completed) > 200:
                expired_id = self._completed.popleft()
                self._jobs.pop(expired_id, None)


def _login_payload(job: _LoginJob) -> dict[str, Any]:
    message = _public_job_message(job.status, job.message)
    if job.status == JobStatus.FAILED.value:
        message = "Login failed. Check local application logs for details."
    elif job.status == JobStatus.WAITING_FOR_LOGIN.value:
        message = "Waiting for account login."
    return {
        "id": job.id,
        "platform": job.platform,
        "accountName": job.account_name,
        "status": job.status,
        "message": message,
        "resultCode": job.result_code,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
    }


def create_desktop_app(
    paths: RuntimePaths,
    session_token: str,
    jobs: Any,
    *,
    login_dispatcher: Callable = dispatch,
    parser_factory: Callable = build_parser,
    safety_reader: Callable = read_publish_safety_status,
) -> Flask:
    if not isinstance(session_token, str) or not session_token:
        raise ValueError("session_token must be non-empty")
    _prepare_runtime_paths(paths)
    _initialize_database(paths)
    resource_root = Path(paths.resource_root).expanduser().resolve()
    frontend_dir = (resource_root / "frontend").resolve()
    try:
        frontend_dir.relative_to(resource_root)
    except ValueError as exc:
        raise ValueError("frontend must stay inside resource root") from exc
    login_jobs = _LoginJobManager(
        paths=paths,
        parser_factory=parser_factory,
        dispatcher=login_dispatcher,
    )
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024**3
    app.extensions["sau_login_jobs"] = login_jobs
    app.extensions["sau_desktop_shutdown"] = login_jobs.shutdown

    @app.before_request
    def require_private_mutation():
        expected_origin = _loopback_request_origin()
        if expected_origin is None:
            return _failure("forbidden", "A literal loopback host is required.", 403)
        if request.path.startswith("/api/v1/materials") and request.method in {"GET", "HEAD"}:
            if not _valid_session(session_token):
                return _failure("forbidden", "A valid desktop session is required.", 403)
        if request.method not in _MUTATING_METHODS:
            return None
        if request.path in _RETIRED_LEGACY_PATHS:
            return None
        if not _valid_session(session_token):
            return _failure("forbidden", "A valid desktop session is required.", 403)
        origin = request.headers.get("Origin", "")
        if not origin or not _origin_matches(expected_origin, origin):
            return _failure("forbidden", "A same-origin desktop request is required.", 403)
        return None

    @app.after_request
    def add_private_api_headers(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def safe_frontend_file(relative_path: str) -> Path | None:
        candidate = (frontend_dir / relative_path).resolve()
        try:
            candidate.relative_to(frontend_dir)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def serve_index():
        index_file = safe_frontend_file("index.html")
        if index_file is not None:
            response = make_response(send_file(index_file))
        else:
            response = make_response(
                "<!doctype html><title>Social Auto Upload</title>"
                "<p>Desktop frontend assets are not built.</p>",
                200,
            )
        response.set_cookie(
            "sau_session",
            session_token,
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response

    @app.get("/")
    def index():
        return serve_index()

    @app.get("/<path:asset_path>")
    def frontend_asset(asset_path: str):
        if asset_path.startswith("api/"):
            return _failure("not_found", "API endpoint not found.", 404)
        candidate = safe_frontend_file(asset_path)
        if candidate is not None:
            return send_file(candidate)
        if asset_path.startswith("assets/") or "." in Path(asset_path).name:
            return "Not found", 404
        return serve_index()

    @app.get("/api/v1/health")
    def health():
        return _success({"status": "ok"})

    @app.post("/api/v1/materials")
    def upload_material():
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return _failure("invalid_request", "A material file is required.", 400)
        original_name = _original_filename(uploaded.filename)
        suffix = Path(original_name).suffix.lower()
        try:
            prefix = uploaded.stream.read(32)
            uploaded.stream.seek(0)
        except (OSError, ValueError):
            return _failure("unsupported_media", "Material type is not supported.", 415)
        media_type = _detected_media_type(original_name, prefix)
        if media_type is None:
            return _failure("unsupported_media", "Material type is not supported.", 415)
        material_id = uuid.uuid4().hex
        stored_name = f"{material_id}{suffix}"
        destination: Path | None = None
        try:
            destination = _contained_path(
                paths.media_dir,
                Path(paths.media_dir) / stored_name,
                label="material file",
            )
            uploaded.save(destination)
            size_bytes = destination.stat().st_size
            try:
                destination.chmod(0o600)
            except OSError:
                pass
            created_at = time.time()
            with sqlite3.connect(paths.database_file) as connection:
                connection.execute(
                    """
                    INSERT INTO desktop_materials
                        (id, original_name, stored_name, size_bytes, created_at, media_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        material_id,
                        original_name,
                        stored_name,
                        size_bytes,
                        created_at,
                        media_type,
                    ),
                )
        except Exception:
            if destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            return _failure("internal_error", "Unable to store material.", 500)
        return _success({
            "id": material_id,
            "name": original_name,
            "sizeBytes": size_bytes,
            "createdAt": created_at,
        }, 201)

    @app.get("/api/v1/materials")
    def list_materials():
        try:
            with sqlite3.connect(paths.database_file) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT id, original_name, stored_name, size_bytes, created_at, media_type
                    FROM desktop_materials ORDER BY created_at, id
                    """
                ).fetchall()
        except Exception:
            return _failure("internal_error", "Unable to list materials.", 500)
        return _success({"materials": [_material_payload(dict(row)) for row in rows]})

    def material_response(material_id: str, *, download: bool):
        try:
            record = _material_record(paths, material_id)
            material_file = _material_file(paths, record)
        except KeyError:
            return _failure("not_found", "Material not found.", 404)
        media_type = record.get("media_type")
        if not download and media_type not in _PREVIEW_MEDIA_TYPES:
            return _failure("unsupported_media", "Material preview is not supported.", 415)
        response = make_response(send_file(
            material_file,
            as_attachment=download,
            download_name=record["original_name"],
            mimetype="application/octet-stream" if download else media_type,
            max_age=0,
        ))
        response.headers["X-Content-Type-Options"] = "nosniff"
        if not download:
            response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response

    @app.get("/api/v1/materials/<material_id>/preview")
    def preview_material(material_id: str):
        return material_response(material_id, download=False)

    @app.get("/api/v1/materials/<material_id>/download")
    def download_material(material_id: str):
        return material_response(material_id, download=True)

    @app.delete("/api/v1/materials/<material_id>")
    def delete_material(material_id: str):
        tombstone: Path | None = None
        material_file: Path | None = None
        try:
            record = _material_record(paths, material_id)
            material_file = _material_storage_path(paths, record)
            if material_file.exists():
                if not material_file.is_file():
                    raise KeyError("material file is invalid")
                tombstone = material_file.with_name(
                    f".{material_file.name}.{uuid.uuid4().hex}.deleting"
                )
                material_file.rename(tombstone)
        except KeyError:
            return _failure("not_found", "Material not found.", 404)
        except Exception:
            return _failure("internal_error", "Unable to delete material.", 500)

        connection = sqlite3.connect(paths.database_file)
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM desktop_materials WHERE id = ?",
                (material_id,),
            )
            if deleted.rowcount != 1:
                raise KeyError(material_id)
            connection.commit()
        except Exception:
            connection.rollback()
            if tombstone is not None and material_file is not None:
                try:
                    tombstone.replace(material_file)
                except OSError:
                    pass
            return _failure("internal_error", "Unable to delete material.", 500)
        finally:
            connection.close()
        if tombstone is not None:
            try:
                tombstone.unlink(missing_ok=True)
            except OSError:
                pass
        return _success({"id": material_id, "deleted": True})

    @app.get("/api/v1/jobs/<job_id>")
    def get_job(job_id: str):
        try:
            job = jobs.get(job_id)
        except KeyError:
            return _failure("not_found", "Job not found.", 404)
        except Exception:
            return _failure("internal_error", "Unable to read job state.", 500)
        return _success(_job_payload(job))

    @app.post("/api/v1/publish")
    def publish():
        try:
            publish_request = _publish_request(paths, request.get_json(silent=True))
            job = jobs.submit(publish_request)
        except ValueError:
            return _failure("invalid_request", "Publish request is invalid.", 400)
        except Exception:
            return _failure("internal_error", "Unable to submit publish job.", 500)
        return _success(_job_payload(job), 202)

    @app.post("/api/v1/jobs/<job_id>/confirm")
    def confirm(job_id: str):
        try:
            job = jobs.confirm(job_id)
        except KeyError:
            return _failure("not_found", "Job not found.", 404)
        except ValueError:
            return _failure(
                "invalid_state",
                "Job is not waiting for confirmation.",
                409,
            )
        except Exception:
            return _failure("internal_error", "Unable to confirm job.", 500)
        return _success(_job_payload(job))

    @app.post("/api/v1/login")
    def login():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _failure("invalid_request", "Login request is invalid.", 400)
        platform = payload.get("platform")
        if platform not in _GUI_PLATFORMS:
            return _failure(
                "unsupported",
                "This platform is not supported by the desktop login flow.",
                400,
            )
        try:
            account_name = _account_name(payload.get("accountName"))
            cookie_file = Path(paths.cookies_dir) / f"{platform}_{account_name}.json"
            _contained_path(paths.cookies_dir, cookie_file, label="cookie file")
        except ValueError:
            return _failure("invalid_request", "Login request is invalid.", 400)
        try:
            job = login_jobs.submit(platform, account_name)
        except RuntimeError:
            return _failure("unavailable", "Login service is unavailable.", 503)
        return _success(_login_payload(job), 202)

    @app.get("/api/v1/login/<job_id>/events")
    def login_events(job_id: str):
        try:
            login_jobs.get(job_id)
        except KeyError:
            return _failure("not_found", "Login job not found.", 404)

        def stream():
            previous = None
            while True:
                current = login_jobs.get(job_id)
                signature = (current.status, current.updated_at)
                if signature != previous:
                    envelope = {"ok": True, "data": _login_payload(current), "error": None}
                    yield f"event: status\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
                    previous = signature
                if current.status in _TERMINAL_LOGIN_STATES:
                    return
                time.sleep(0.1)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.get("/api/v1/safety/status")
    def safety_status():
        platform = request.args.get("platform", "")
        if platform not in {"douyin", "xiaohongshu"}:
            return _failure("invalid_request", "Safety platform is invalid.", 400)
        try:
            account_name = _account_name(request.args.get("account"))
            account_file = Path(paths.cookies_dir) / f"{platform}_{account_name}.json"
            account_file = _contained_path(
                paths.cookies_dir,
                account_file,
                label="cookie file",
            )
            status = safety_reader(platform=platform, account_file=account_file)
        except ValueError:
            return _failure("invalid_request", "Safety request is invalid.", 400)
        except Exception:
            return _failure("internal_error", "Unable to read safety state.", 500)
        return _success(_public_safety_status(status))

    @app.post("/postVideo")
    @app.post("/postVideoBatch")
    def retired_legacy_publish():
        return jsonify({
            "code": 410,
            "msg": "Legacy direct publishing is disabled; use the governed desktop API or CLI.",
            "data": None,
        }), 410

    @app.errorhandler(413)
    def request_too_large(_error):
        return _failure("too_large", "Request exceeds the local size limit.", 413)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return _failure("not_found", "API endpoint not found.", 404)
        return "Not found", 404

    @app.errorhandler(405)
    def method_not_allowed(_error):
        if request.path.startswith("/api/"):
            return _failure("method_not_allowed", "API method not allowed.", 405)
        return "Method not allowed", 405

    @app.errorhandler(500)
    def internal_server_error(_error):
        if request.path.startswith("/api/"):
            return _failure("internal_error", "Desktop API request failed.", 500)
        return "Internal server error", 500

    return app


__all__ = ["create_desktop_app"]
