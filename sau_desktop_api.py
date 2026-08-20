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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from flask import Flask, Response, jsonify, make_response, request, send_from_directory

from sau_cli import build_parser, dispatch
from sau_desktop_service import JobStatus, PublishRequest
from sau_runtime import RuntimePaths
from utils.risk_control import read_publish_safety_status


_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_RETIRED_LEGACY_PATHS = frozenset({"/postVideo", "/postVideoBatch"})
_GUI_PLATFORMS = frozenset({"douyin", "kuaishou", "tencent", "xiaohongshu"})
_ACCOUNT_NAME = re.compile(r"^[\w .@+-]{1,80}$")
_TERMINAL_LOGIN_STATES = frozenset({"succeeded", "failed", "blocked"})


def _success(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data, "error": None}), status


def _failure(code: str, message: str, status: int):
    return jsonify({
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
    }), status


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


def _media_path(paths: RuntimePaths, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mediaFile is required")
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return _contained_path(
        paths.media_dir,
        Path(paths.media_dir) / candidate,
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
    return PublishRequest(
        platform=platform,
        account_name=_account_name(payload.get("accountName")),
        media_file=_media_path(paths, payload.get("mediaFile")),
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
    def __init__(self, *, parser_factory: Callable, dispatcher: Callable) -> None:
        self._parser_factory = parser_factory
        self._dispatcher = dispatcher
        self._lock = threading.RLock()
        self._jobs: dict[str, _LoginJob] = {}

    def submit(self, platform: str, account_name: str) -> _LoginJob:
        now = time.time()
        job = _LoginJob(
            id=uuid.uuid4().hex,
            platform=platform,
            account_name=account_name,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(
            target=self._run,
            args=(job.id,),
            name=f"sau-login-{job.id[:8]}",
            daemon=True,
        ).start()
        return job

    def get(self, job_id: str) -> _LoginJob:
        with self._lock:
            try:
                return replace(self._jobs[job_id])
            except KeyError as exc:
                raise KeyError(job_id) from exc

    def _run(self, job_id: str) -> None:
        self._set(job_id, status=JobStatus.WAITING_FOR_LOGIN.value)
        try:
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
                self._set(
                    job_id,
                    status=JobStatus.SUCCEEDED.value,
                    message="completed",
                    result_code=0,
                )
            else:
                self._set(
                    job_id,
                    status=JobStatus.FAILED.value,
                    message="Login did not complete successfully.",
                    result_code=result if type(result) is int else None,
                )
        except Exception:
            self._set(
                job_id,
                status=JobStatus.FAILED.value,
                message="Login failed. Check local application logs for details.",
            )
        except SystemExit:
            self._set(
                job_id,
                status=JobStatus.FAILED.value,
                message="Login request validation failed.",
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
    frontend_dir = Path(paths.resource_root) / "frontend"
    login_jobs = _LoginJobManager(
        parser_factory=parser_factory,
        dispatcher=login_dispatcher,
    )
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024**3

    @app.before_request
    def require_private_mutation():
        if request.method not in _MUTATING_METHODS:
            return None
        if request.path in _RETIRED_LEGACY_PATHS:
            return None
        supplied = request.cookies.get("sau_session", "")
        if not supplied or not secrets.compare_digest(supplied, session_token):
            return _failure("forbidden", "A valid desktop session is required.", 403)
        origin = request.headers.get("Origin", "")
        expected = request.host_url.rstrip("/")
        if not origin or not secrets.compare_digest(origin.rstrip("/"), expected):
            return _failure("forbidden", "A same-origin desktop request is required.", 403)
        return None

    @app.after_request
    def add_private_api_headers(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def serve_index():
        index_file = frontend_dir / "index.html"
        if index_file.is_file():
            response = make_response(send_from_directory(frontend_dir, "index.html"))
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
        candidate = frontend_dir / asset_path
        if candidate.is_file():
            return send_from_directory(frontend_dir, asset_path)
        return serve_index()

    @app.get("/api/v1/health")
    def health():
        return _success({"status": "ok"})

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
        job = login_jobs.submit(platform, account_name)
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
