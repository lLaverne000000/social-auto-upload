from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Iterator
from urllib.parse import parse_qs, urlparse

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


class RiskControlError(RuntimeError):
    """Raised when a publish attempt should stop instead of retrying."""


ManualConfirmationProvider = Callable[..., Awaitable[bool]]
_MANUAL_CONFIRMATION_PROVIDER: ContextVar[ManualConfirmationProvider | None] = (
    ContextVar("sau_manual_confirmation_provider", default=None)
)


@contextmanager
def use_manual_confirmation_provider(
    provider: ManualConfirmationProvider,
) -> Iterator[None]:
    token = _MANUAL_CONFIRMATION_PROVIDER.set(provider)
    try:
        yield
    finally:
        _MANUAL_CONFIRMATION_PROVIDER.reset(token)


class _CliPublishPermit:
    pass


_CLI_PUBLISH_PERMIT = _CliPublishPermit()


def _issue_cli_publish_permit() -> object:
    """Issue the in-process permit used by the unified CLI publishing path."""
    return _CLI_PUBLISH_PERMIT


def require_cli_publish_permit(permit: object | None, platform: str) -> None:
    if permit is not _CLI_PUBLISH_PERMIT:
        raise RiskControlError(
            f"{platform}: 直接发布入口已禁用；请通过统一 CLI `sau {platform} ...` 发起任务"
        )


RISK_PHRASES = (
    "账号存在风险",
    "账号异常",
    "操作异常",
    "操作过于频繁",
    "操作频繁",
    "安全验证",
    "请完成验证",
    "短信验证码",
    "获取验证码",
    "拖动滑块",
    "滑块验证",
    "上传失败",
    "网络异常",
    "系统繁忙",
    "服务繁忙",
    "账号受限",
    "发布受限",
    "禁止发布",
)

RISK_URL_SEGMENTS = {
    "login",
    "passport",
    "captcha",
    "verify",
    "verification",
    "challenge",
    "security-check",
}

WORK_ID_QUERY_FIELDS = ("item_id", "note_id", "aweme_id", "work_id")
DEFAULT_AUDIT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_AUDIT_BACKUP_COUNT = 5
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(access_token|authorization|cookie|sessionid|token|secret)=([^\s&]+)"
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def extract_work_id(url: str) -> str | None:
    query = parse_qs(urlparse(url or "").query)
    for field in WORK_ID_QUERY_FIELDS:
        values = query.get(field, [])
        if values and values[0].strip():
            return values[0].strip()
    return None


def _account_key(account_file: Path) -> str:
    return hashlib.sha256(account_file.stem.encode("utf-8")).hexdigest()[:16]


def _redact_url(raw_url: str) -> str:
    raw_url = str(raw_url or "").strip()
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    return parsed._replace(
        netloc=f"{hostname}{port}",
        params="",
        query="",
        fragment="",
    ).geturl()


def _sanitize_reason(reason: str, *, limit: int = 500) -> str:
    def replace_url(match: re.Match) -> str:
        return _redact_url(match.group(0)) or "<redacted-url>"

    sanitized = _URL_PATTERN.sub(replace_url, str(reason or ""))
    sanitized = _SENSITIVE_VALUE_PATTERN.sub(r"\1=<redacted>", sanitized)
    return " ".join(sanitized.split())[:limit]


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_alive(pid: int) -> bool:  # pragma: no cover - Windows
    """Query process state without using os.kill, which terminates on Windows."""
    if pid <= 0 or pid > 0xFFFFFFFF:
        return False

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_invalid_parameter = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, wintypes.LPDWORD)
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        return error != error_invalid_parameter

    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        content = lock_path.read_text(encoding="ascii").strip()
        if not content.startswith("pid="):
            return None
        return int(content.removeprefix("pid="))
    except (OSError, ValueError):
        return None


def _try_acquire_os_file_lock(file_descriptor: int) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        if msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
            return True
    except (BlockingIOError, OSError):
        return False
    raise RuntimeError("当前平台不支持进程文件锁")


def _release_os_file_lock(file_descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError("当前平台不支持进程文件锁")


class StageDeadline:
    """Monotonic deadline for a single upload stage."""

    def __init__(
        self,
        platform: str,
        stage: str,
        timeout_seconds: int,
        *,
        clock=None,
    ) -> None:
        self.platform = platform
        self.stage = stage
        self.timeout_seconds = timeout_seconds
        self._clock = clock or time.monotonic
        self._started_at = self._clock()

    def raise_if_expired(self) -> None:
        if self._clock() - self._started_at >= self.timeout_seconds:
            raise RiskControlError(
                f"{self.platform}: {self.stage}超过 {self.timeout_seconds} 秒硬超时，已熔断且不会发布"
            )


def assert_healthy_navigation_response(response, platform: str, stage: str) -> None:
    if response is None:
        return
    status = int(getattr(response, "status", 0) or 0)
    if status >= 400:
        url = getattr(response, "url", "") or ""
        raise RiskControlError(
            f"{platform}: {stage}收到 HTTP {status}，已熔断且不会重试；URL: {url}"
        )


async def assert_no_risk_prompt(page, platform: str, stage: str = "当前阶段") -> None:
    """Fail closed when the visible page asks for security verification."""
    current_url = str(getattr(page, "url", "") or "")
    parsed_url = urlparse(current_url)
    parsed_query = parse_qs(parsed_url.query)
    url_components = [
        parsed_url.hostname or "",
        parsed_url.path,
        *parsed_query.keys(),
        *(value for values in parsed_query.values() for value in values),
    ]
    normalized_components = [component.lower() for component in url_components]
    url_tokens = {
        token
        for component in normalized_components
        for token in re.findall(r"[a-z0-9]+", component)
    }
    matched_url_token = next(
        (
            token
            for token in RISK_URL_SEGMENTS
            if token in url_tokens
            or (
                "-" in token
                and any(token in component for component in normalized_components)
            )
        ),
        None,
    )
    if matched_url_token:
        raise RiskControlError(
            f"{platform}: {stage}跳转到登录或验证页面，已熔断；URL: {current_url}"
        )
    try:
        body_text = await page.locator("body").inner_text(timeout=5000)
    except Exception as exc:
        raise RiskControlError(f"{platform}: {stage}无法完成风险页面检查，已停止发布") from exc

    matched = next((phrase for phrase in RISK_PHRASES if phrase in body_text), None)
    if matched:
        raise RiskControlError(
            f"{platform}: {stage}检测到平台风险提示“{matched}”，已熔断；请在官方页面人工处理，程序不会重试"
        )


async def require_manual_publish_confirmation(
    *, platform: str, content_type: str, headless: bool, enabled: bool
) -> None:
    if not enabled:
        return
    if headless:
        raise RiskControlError(f"{platform}: 人工确认发布要求使用 --headed")

    provider = _MANUAL_CONFIRMATION_PROVIDER.get()
    if provider is not None:
        try:
            confirmed = await provider(
                platform=platform,
                content_type=content_type,
            )
        except RiskControlError:
            raise
        except Exception as exc:
            raise RiskControlError(f"{platform}: GUI 人工确认失败，已停止发布") from exc
        if not confirmed:
            raise RiskControlError(f"{platform}: GUI 人工确认已停止，拒绝发布")
        return

    if not sys.stdin or not sys.stdin.isatty():
        raise RiskControlError(f"{platform}: 当前终端不可交互，拒绝跳过人工发布确认")

    prompt = f"\n请检查浏览器中的{platform}{content_type}。确认标题、素材、声明和账号无误后输入 PUBLISH: "
    try:
        answer = await asyncio.to_thread(input, prompt)
    except (EOFError, OSError) as exc:
        raise RiskControlError(f"{platform}: 无法读取人工确认，已停止发布") from exc
    if answer.strip() != "PUBLISH":
        raise RiskControlError(f"{platform}: 未收到 PUBLISH 确认，已停止发布")


def content_fingerprint(title: str, text: str, media_paths: Iterable[str | Path]) -> str:
    """Fingerprint metadata plus bounded samples of local media files."""
    digest = hashlib.sha256()
    digest.update(" ".join((title or "").split()).encode("utf-8"))
    digest.update(b"\0")
    digest.update(" ".join((text or "").split()).encode("utf-8"))

    for raw_path in media_paths:
        path = Path(raw_path)
        digest.update(b"\0")
        digest.update(path.name.encode("utf-8", errors="replace"))
        if not path.is_file():
            digest.update(str(path).encode("utf-8", errors="replace"))
            continue
        size = path.stat().st_size
        digest.update(str(size).encode("ascii"))
        with path.open("rb") as file_obj:
            digest.update(file_obj.read(1024 * 1024))
            if size > 1024 * 1024:
                file_obj.seek(max(0, size - 1024 * 1024))
                digest.update(file_obj.read(1024 * 1024))
    return digest.hexdigest()


class PublishGuard:
    """Per-platform serialization, per-account cooldown, dedup and audit log."""

    def __init__(
        self,
        *,
        platform: str,
        account_file: str | Path,
        fingerprint: str,
        min_interval_minutes: int = 30,
        duplicate_window_days: int = 7,
        operation: str = "publish",
        audit_max_bytes: int = DEFAULT_AUDIT_MAX_BYTES,
        audit_backup_count: int = DEFAULT_AUDIT_BACKUP_COUNT,
    ) -> None:
        self.platform = platform
        self.account_file = Path(account_file)
        self.fingerprint = fingerprint
        self.operation = operation
        self.min_interval_seconds = max(0, min_interval_minutes) * 60
        self.duplicate_window_seconds = max(1, duplicate_window_days) * 86400
        self.audit_max_bytes = max(1, audit_max_bytes)
        self.audit_backup_count = max(0, audit_backup_count)
        self.state_dir = self.account_file.parent / ".sau_safety"
        self.account_key = _account_key(self.account_file)
        self.state_path = self.state_dir / f"{self.account_key}.json"
        self.lock_path = self.state_dir / f"{platform}.lock"
        self.audit_path = self.state_dir / "audit.jsonl"
        self.audit_lock_path = self.state_dir / "audit.jsonl.lock"
        self.receipt_dir = self.state_dir / "receipts"
        self.evidence_dir = self.state_dir / "evidence" / self.account_key
        self.task_id = uuid.uuid4().hex
        self._lock_fd: int | None = None
        self._completed = False
        self._failure_stage = operation
        self._failure_page_url = ""

    def __enter__(self) -> "PublishGuard":
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        self._acquire_lock()
        try:
            self._check_state()
            self._audit("started")
        except Exception:
            self._release_lock()
            raise
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        try:
            if self._completed:
                return
            if exc is not None:
                try:
                    self._write_failure_evidence(exc)
                except Exception as evidence_exc:
                    print(
                        f"本地失败证据写入失败: {_sanitize_reason(str(evidence_exc))}",
                        file=sys.stderr,
                    )
                try:
                    self._audit("failed", reason=f"{type(exc).__name__}: {exc}")
                except Exception as audit_exc:
                    print(
                        f"本地审计日志写入失败: {_sanitize_reason(str(audit_exc))}",
                        file=sys.stderr,
                    )
            elif not self._completed:
                self._audit("stopped", reason="publish did not report success")
        finally:
            self._release_lock()

    def set_failure_context(self, *, stage: str = "", page_url: str = "") -> None:
        if stage:
            self._failure_stage = stage
        if page_url:
            self._failure_page_url = page_url

    def mark_success(self, *, success_url: str = "", work_id: str | None = None) -> None:
        success_url = success_url.strip()
        if not success_url:
            raise RiskControlError(f"{self.platform}: 缺少已确认的发布成功 URL，拒绝记录成功")

        now = time.time()
        state = self._load_state()
        recent = [
            item
            for item in state.get("recent", [])
            if now - float(item.get("timestamp", 0)) <= self.duplicate_window_seconds
        ]
        recent.append({"fingerprint": self.fingerprint, "timestamp": now})
        self._write_receipt(success_url, work_id or extract_work_id(success_url))
        self._write_json(
            self.state_path,
            {"last_success_at": now, "recent": recent[-50:]},
        )
        self._audit("succeeded")
        self._completed = True

    def _acquire_lock(self) -> None:
        created = False
        try:
            self._lock_fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
            created = True
        except FileExistsError:
            self._lock_fd = os.open(self.lock_path, os.O_RDWR)

        self.lock_path.chmod(0o600)
        if created:
            os.write(self._lock_fd, b"pid=0\n")
            os.fsync(self._lock_fd)
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
        if not _try_acquire_os_file_lock(self._lock_fd):
            owner_pid = _read_lock_pid(self.lock_path)
            os.close(self._lock_fd)
            self._lock_fd = None
            owner = f"；PID: {owner_pid}" if owner_pid is not None else ""
            raise RiskControlError(f"{self.platform}: 已有发布任务运行，拒绝并发{owner}")

        if not created:
            owner_pid = _read_lock_pid(self.lock_path)
            if owner_pid is not None and _pid_is_alive(owner_pid):
                self._close_locked_file()
                raise RiskControlError(
                    f"{self.platform}: 已有发布任务运行，拒绝并发；PID: {owner_pid}"
                )
            if owner_pid is None:
                age = time.time() - self.lock_path.stat().st_mtime
                if age <= 6 * 3600:
                    self._close_locked_file()
                    raise RiskControlError(
                        f"{self.platform}: 发布锁无法确认归属，拒绝并发；锁文件: {self.lock_path}"
                    )

        os.ftruncate(self._lock_fd, 0)
        os.lseek(self._lock_fd, 0, os.SEEK_SET)
        os.write(self._lock_fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(self._lock_fd)

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.ftruncate(self._lock_fd, 0)
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
            os.write(self._lock_fd, b"pid=0\n")
            os.fsync(self._lock_fd)
            self._close_locked_file()

    def _close_locked_file(self) -> None:
        if self._lock_fd is None:
            return
        try:
            _release_os_file_lock(self._lock_fd)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _check_state(self) -> None:
        now = time.time()
        state = self._load_state()
        last_success_at = float(state.get("last_success_at", 0))
        remaining = self.min_interval_seconds - (now - last_success_at)
        if remaining > 0:
            raise RiskControlError(
                f"{self.platform}: 账号仍在发布冷却期，还需等待约 {int(remaining // 60) + 1} 分钟"
            )

        for item in state.get("recent", []):
            timestamp = float(item.get("timestamp", 0))
            if (
                item.get("fingerprint") == self.fingerprint
                and now - timestamp <= self.duplicate_window_seconds
            ):
                raise RiskControlError(
                    f"{self.platform}: 7 天内检测到相同内容，拒绝重复发布"
                )

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RiskControlError(
                f"{self.platform}: 风控状态文件损坏，拒绝在未知状态下发布: {self.state_path}"
            ) from exc

    def _audit(self, event: str, reason: str = "") -> None:
        record = {
            "timestamp": int(time.time()),
            "platform": self.platform,
            "account": self.account_file.stem,
            "event": event,
            "fingerprint": self.fingerprint[:16],
            "task_id": self.task_id,
        }
        if reason:
            record["reason"] = _sanitize_reason(reason)
        encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        lock_fd = self._acquire_audit_lock()
        try:
            current_size = self.audit_path.stat().st_size if self.audit_path.exists() else 0
            if current_size and current_size + len(encoded) > self.audit_max_bytes:
                self._rotate_audit_files()
            descriptor = os.open(
                self.audit_path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                self.audit_path.chmod(0o600)
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("audit append made no progress")
                    view = view[written:]
            finally:
                os.close(descriptor)
        finally:
            _release_os_file_lock(lock_fd)
            os.close(lock_fd)

    def _acquire_audit_lock(self) -> int:
        descriptor = os.open(
            self.audit_lock_path,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        self.audit_lock_path.chmod(0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        deadline = time.monotonic() + 5
        while not _try_acquire_os_file_lock(descriptor):
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise RiskControlError("本地审计日志正忙，已停止以避免审计记录丢失")
            time.sleep(0.01)
        return descriptor

    def _rotate_audit_files(self) -> None:
        if self.audit_backup_count == 0:
            self.audit_path.unlink(missing_ok=True)
            return
        oldest = self.audit_path.with_name(
            f"{self.audit_path.name}.{self.audit_backup_count}"
        )
        oldest.unlink(missing_ok=True)
        for index in range(self.audit_backup_count - 1, 0, -1):
            source = self.audit_path.with_name(f"{self.audit_path.name}.{index}")
            if source.exists():
                destination = self.audit_path.with_name(
                    f"{self.audit_path.name}.{index + 1}"
                )
                source.replace(destination)
                destination.chmod(0o600)
        if self.audit_path.exists():
            first_backup = self.audit_path.with_name(f"{self.audit_path.name}.1")
            self.audit_path.replace(first_backup)
            first_backup.chmod(0o600)

    def _write_failure_evidence(self, exc: BaseException) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.parent.chmod(0o700)
        self.evidence_dir.chmod(0o700)
        self._write_json(
            self.evidence_dir / f"{self.task_id}.json",
            {
                "timestamp": int(time.time()),
                "task_id": self.task_id,
                "platform": self.platform,
                "account": self.account_file.stem,
                "status": "failed",
                "operation": self.operation,
                "stage": self._failure_stage,
                "error_type": type(exc).__name__,
                "reason": _sanitize_reason(str(exc)),
                "page_url": _redact_url(self._failure_page_url),
            },
        )

    def _write_receipt(self, success_url: str, work_id: str | None) -> None:
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        self.receipt_dir.chmod(0o700)
        self._write_json(
            self.receipt_dir / f"{self.task_id}.json",
            {
                "timestamp": int(time.time()),
                "task_id": self.task_id,
                "platform": self.platform,
                "account": self.account_file.stem,
                "status": "success_url_confirmed",
                "success_url": success_url,
                "work_id": work_id,
                "manual_reconciliation_required": work_id is None,
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
            0o600,
        )
        temporary.chmod(0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        temporary.replace(path)
        path.chmod(0o600)


def read_publish_safety_status(
    *,
    platform: str,
    account_file: str | Path,
    min_interval_minutes: int = 30,
) -> dict:
    """Read local publish safety state without creating or modifying files."""
    account_path = Path(account_file)
    state_dir = account_path.parent / ".sau_safety"
    account_key = _account_key(account_path)
    state_path = state_dir / f"{account_key}.json"
    lock_path = state_dir / f"{platform}.lock"
    audit_path = state_dir / "audit.jsonl"
    evidence_dir = state_dir / "evidence" / account_key
    now = time.time()
    status = {
        "platform": platform,
        "account": account_path.stem,
        "account_file": str(account_path),
        "state_path": str(state_path),
        "state_status": "missing",
        "last_success_at": None,
        "cooldown_remaining_seconds": 0,
        "recent_count": 0,
        "lock": {
            "path": str(lock_path),
            "exists": lock_path.exists(),
            "pid": None,
            "pid_alive": False,
        },
        "audit": {
            "path": str(audit_path),
            "size_bytes": 0,
            "backup_count": 0,
        },
        "latest_failure": None,
    }

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state root is not an object")
            last_success_at = float(state.get("last_success_at", 0) or 0)
            recent = state.get("recent", [])
            if not isinstance(recent, list):
                raise ValueError("recent is not a list")
            status["state_status"] = "ok"
            status["last_success_at"] = last_success_at or None
            status["recent_count"] = sum(
                1
                for item in recent
                if isinstance(item, dict)
                and now - float(item.get("timestamp", 0) or 0) <= 7 * 86400
            )
            if last_success_at:
                interval_seconds = max(0, min_interval_minutes) * 60
                status["cooldown_remaining_seconds"] = max(
                    0,
                    int(interval_seconds - (now - last_success_at)),
                )
        except (OSError, ValueError, TypeError) as exc:
            status["state_status"] = "corrupt"
            status["error"] = _sanitize_reason(str(exc)) or type(exc).__name__

    if lock_path.exists():
        pid = _read_lock_pid(lock_path)
        status["lock"]["pid"] = pid
        status["lock"]["pid_alive"] = _pid_is_alive(pid) if pid is not None else False

    if audit_path.exists():
        try:
            status["audit"]["size_bytes"] = audit_path.stat().st_size
        except OSError:
            pass
    if state_dir.exists():
        status["audit"]["backup_count"] = len(
            [
                path
                for path in state_dir.glob("audit.jsonl.*")
                if path.name.removeprefix("audit.jsonl.").isdigit()
            ]
        )

    if evidence_dir.exists():
        try:
            evidence_paths = sorted(
                evidence_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            evidence_paths = []
        for evidence_path in evidence_paths:
            try:
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                if isinstance(evidence, dict):
                    status["latest_failure"] = evidence
                    break
            except (OSError, ValueError, TypeError):
                continue

    return status
