from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid
from argparse import ArgumentParser, Namespace
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Literal

from sau_cli import build_parser, dispatch
from utils.risk_control import RiskControlError


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_LOGIN = "waiting-for-login"
    WAITING_FOR_CONFIRMATION = "waiting-for-confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PublishRequest:
    platform: Literal["douyin", "kuaishou", "tencent", "xiaohongshu"]
    account_name: str
    media_file: Path
    title: str
    tags: tuple[str, ...] = ()
    description: str = ""
    schedule: str | None = None
    declaration: str | None = None
    content_source: str | None = None
    automatic_publish: bool = False


@dataclass(slots=True)
class PublishJob:
    id: str
    request: PublishRequest
    status: JobStatus = JobStatus.QUEUED
    message: str = ""
    result_code: int | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _validated_media_file(media_file: Path) -> Path:
    path = Path(media_file).expanduser()
    if not path.is_absolute():
        raise ValueError("media_file must be an absolute user-selected path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"media_file does not exist: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"media_file is not a file: {path}")
    return resolved


def _normalized_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        cleaned
        for tag in tags
        if (cleaned := str(tag).strip().lstrip("#"))
    )


def build_publish_argv(request: PublishRequest) -> list[str]:
    if request.platform not in {"douyin", "kuaishou", "tencent", "xiaohongshu"}:
        raise ValueError(f"unsupported platform: {request.platform}")
    if not request.account_name.strip():
        raise ValueError("account_name is required")
    if not request.title.strip():
        raise ValueError("title is required")

    media_file = _validated_media_file(request.media_file)
    tags = _normalized_tags(request.tags)
    if request.platform == "xiaohongshu" and len(tags) > 10:
        raise ValueError("Xiaohongshu accepts at most 10 tags")

    argv = [
        request.platform,
        "upload-video",
        "--account",
        request.account_name.strip(),
        "--file",
        str(media_file),
        "--title",
        request.title.strip(),
        "--headed",
    ]
    if request.description:
        argv.extend(("--desc", request.description))
    if tags:
        argv.extend(("--tags", ",".join(tags)))
    if request.schedule:
        argv.extend(("--schedule", request.schedule))

    if request.platform == "douyin":
        declaration = (request.declaration or "").strip()
        if not declaration:
            raise ValueError("declaration is required for Douyin")
        argv.extend(("--declaration", declaration))

    if request.platform == "xiaohongshu":
        content_source = (request.content_source or "").strip()
        if content_source not in {"original", "repost"}:
            raise ValueError("content_source must be 'original' or 'repost'")
        argv.extend(("--content-source", content_source))

    if request.automatic_publish and request.platform in {"douyin", "xiaohongshu"}:
        argv.append("--automatic-publish")
    return argv


Dispatcher = Callable[[Namespace], int | Awaitable[int]]


class JobManager:
    def __init__(
        self,
        *,
        dispatcher: Dispatcher = dispatch,
        parser_factory: Callable[[], ArgumentParser] = build_parser,
    ) -> None:
        self._dispatcher = dispatcher
        self._parser_factory = parser_factory
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="sau-publish",
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, PublishJob] = {}
        self._futures: dict[str, Future[None]] = {}
        self._completed: deque[str] = deque()

    def submit(self, request: PublishRequest) -> PublishJob:
        argv = build_publish_argv(request)
        job = PublishJob(id=uuid.uuid4().hex, request=request)
        with self._lock:
            self._jobs[job.id] = job
            self._futures[job.id] = self._executor.submit(self._run, job.id, argv)
        return job

    def get(self, job_id: str) -> PublishJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise KeyError(f"unknown job: {job_id}") from exc

    def wait(self, job_id: str, timeout: float | None = None) -> PublishJob:
        with self._lock:
            future = self._futures.get(job_id)
            if future is None:
                return self.get(job_id)
        future.result(timeout=timeout)
        return self.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, job_id: str, argv: list[str]) -> None:
        self._set_job(job_id, status=JobStatus.RUNNING, message="")
        try:
            args = self._parser_factory().parse_args(argv)
            result = self._dispatcher(args)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            if type(result) is int and result == 0:
                self._set_job(
                    job_id,
                    status=JobStatus.SUCCEEDED,
                    message="completed",
                    result_code=0,
                )
            else:
                code = result if type(result) is int else None
                self._set_job(
                    job_id,
                    status=JobStatus.FAILED,
                    message=f"dispatcher returned non-zero result: {result!r}",
                    result_code=code,
                )
        except RiskControlError as exc:
            self._set_job(job_id, status=JobStatus.BLOCKED, message=str(exc))
        except SystemExit as exc:
            self._set_job(
                job_id,
                status=JobStatus.FAILED,
                message=f"CLI validation failed with exit code {exc.code}",
            )
        except Exception as exc:
            self._set_job(job_id, status=JobStatus.FAILED, message=str(exc))
        finally:
            self._retain_completed(job_id)

    def _set_job(
        self,
        job_id: str,
        *,
        status: JobStatus,
        message: str,
        result_code: int | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.message = message
            job.result_code = result_code
            job.updated_at = time.time()

    def _retain_completed(self, job_id: str) -> None:
        with self._lock:
            self._completed.append(job_id)
            while len(self._completed) > 200:
                expired_id = self._completed.popleft()
                self._jobs.pop(expired_id, None)
                self._futures.pop(expired_id, None)
