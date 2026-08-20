import asyncio
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sau_desktop_service import (
    JobManager,
    JobStatus,
    PublishRequest,
    build_publish_argv,
)
from utils.risk_control import RiskControlError
from utils.risk_control import require_manual_publish_confirmation


class DesktopServiceTranslationTests(unittest.TestCase):
    def _media_file(self, root: str) -> Path:
        media = Path(root) / "demo.mp4"
        media.write_bytes(b"video")
        return media

    def test_xiaohongshu_defaults_to_headed_manual_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = PublishRequest(
                platform="xiaohongshu",
                account_name="creator",
                media_file=self._media_file(tmp),
                title="标题",
                tags=("旅行",),
                content_source="original",
            )
            argv = build_publish_argv(request)

        self.assertEqual(argv[:2], ["xiaohongshu", "upload-video"])
        self.assertIn("--headed", argv)
        self.assertNotIn("--automatic-publish", argv)

    def test_automatic_publish_is_added_only_after_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = PublishRequest(
                platform="xiaohongshu",
                account_name="creator",
                media_file=self._media_file(tmp),
                title="标题",
                content_source="original",
                automatic_publish=True,
            )
            argv = build_publish_argv(request)

        self.assertIn("--automatic-publish", argv)

    def test_automatic_publish_rejects_non_boolean_values(self):
        for invalid in ("false", 1):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                request = PublishRequest(
                    platform="xiaohongshu",
                    account_name="creator",
                    media_file=self._media_file(tmp),
                    title="标题",
                    content_source="original",
                    automatic_publish=invalid,
                )
                with self.assertRaisesRegex(ValueError, "automatic_publish"):
                    build_publish_argv(request)

    def test_douyin_requires_explicit_declaration_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = PublishRequest(
                platform="douyin",
                account_name="creator",
                media_file=self._media_file(tmp),
                title="标题",
            )
            with self.assertRaisesRegex(ValueError, "declaration"):
                build_publish_argv(request)

    def test_xiaohongshu_rejects_more_than_ten_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = PublishRequest(
                platform="xiaohongshu",
                account_name="creator",
                media_file=self._media_file(tmp),
                title="标题",
                tags=tuple(f"tag-{index}" for index in range(11)),
                content_source="original",
            )
            with self.assertRaisesRegex(ValueError, "10"):
                build_publish_argv(request)

    def test_relative_user_selected_media_is_rejected(self):
        request = PublishRequest(
            platform="kuaishou",
            account_name="creator",
            media_file=Path("demo.mp4"),
            title="标题",
        )
        with self.assertRaisesRegex(ValueError, "media"):
            build_publish_argv(request)


class DesktopServiceJobTests(unittest.TestCase):
    def _request(self, root: str) -> PublishRequest:
        media = Path(root) / "demo.mp4"
        media.write_bytes(b"video")
        return PublishRequest(
            platform="kuaishou",
            account_name="creator",
            media_file=media,
            title="标题",
        )

    def test_actual_parser_namespace_is_passed_to_dispatcher(self):
        seen = []

        async def dispatcher(args):
            seen.append((args.platform, args.action, args.account, args.headless))
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            job = manager.submit(self._request(tmp))
            result = manager.wait(job.id, timeout=2)

        self.assertEqual(result.status, JobStatus.SUCCEEDED)
        self.assertEqual(seen, [("kuaishou", "upload-video", "creator", False)])

    def test_windowed_job_waits_for_explicit_confirmation_without_tty(self):
        async def dispatcher(args):
            await require_manual_publish_confirmation(
                platform="小红书",
                content_type="视频",
                headless=args.headless,
                enabled=args.confirm_before_publish,
            )
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "demo.mp4"
            media.write_bytes(b"video")
            request = PublishRequest(
                platform="xiaohongshu",
                account_name="creator",
                media_file=media,
                title="标题",
                content_source="original",
            )
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            with patch("sys.stdin", io.StringIO("")):
                job = manager.submit(request)
                waiting = self._wait_for_status(
                    manager,
                    job.id,
                    JobStatus.WAITING_FOR_CONFIRMATION,
                )
                self.assertIn("小红书", waiting.message)
                manager.confirm(job.id)
                result = manager.wait(job.id, timeout=2)

        self.assertEqual(result.status, JobStatus.SUCCEEDED)

    def test_shutdown_releases_job_waiting_for_confirmation(self):
        async def dispatcher(args):
            await require_manual_publish_confirmation(
                platform="小红书",
                content_type="视频",
                headless=args.headless,
                enabled=args.confirm_before_publish,
            )
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "demo.mp4"
            media.write_bytes(b"video")
            manager = JobManager(dispatcher=dispatcher)
            job = manager.submit(PublishRequest(
                platform="xiaohongshu",
                account_name="creator",
                media_file=media,
                title="标题",
                content_source="original",
            ))
            self._wait_for_status(
                manager,
                job.id,
                JobStatus.WAITING_FOR_CONFIRMATION,
            )
            manager.shutdown()
            result = manager.get(job.id)

        self.assertEqual(result.status, JobStatus.BLOCKED)
        self.assertIn("stopped", result.message.lower())

    def test_returned_job_is_an_immutable_detached_snapshot(self):
        release = threading.Event()

        async def dispatcher(_args):
            await asyncio.to_thread(release.wait, 2)
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            snapshot = manager.submit(self._request(tmp))
            object.__setattr__(snapshot, "status", JobStatus.SUCCEEDED)
            internal = manager.get(snapshot.id)
            release.set()
            result = manager.wait(snapshot.id, timeout=2)

        self.assertIsNot(internal, snapshot)
        self.assertNotEqual(internal.status, JobStatus.SUCCEEDED)
        self.assertEqual(result.status, JobStatus.SUCCEEDED)

    def test_risk_control_error_is_blocked(self):
        async def dispatcher(_args):
            raise RiskControlError("cooldown active")

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            job = manager.submit(self._request(tmp))
            result = manager.wait(job.id, timeout=2)

        self.assertEqual(result.status, JobStatus.BLOCKED)
        self.assertIn("cooldown", result.message)

    def test_ordinary_validation_error_is_failed(self):
        async def dispatcher(_args):
            raise ValueError("invalid publish value")

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            job = manager.submit(self._request(tmp))
            result = manager.wait(job.id, timeout=2)

        self.assertEqual(result.status, JobStatus.FAILED)
        self.assertIn("invalid publish value", result.message)

    def test_nonzero_or_missing_dispatch_result_is_not_success(self):
        results = iter((1, None))

        async def dispatcher(_args):
            return next(results)

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            first = manager.submit(self._request(tmp))
            second = manager.submit(self._request(tmp))
            first_result = manager.wait(first.id, timeout=2)
            second_result = manager.wait(second.id, timeout=2)

        self.assertEqual(first_result.status, JobStatus.FAILED)
        self.assertEqual(second_result.status, JobStatus.FAILED)

    def test_executor_runs_at_most_two_dispatchers(self):
        lock = threading.Lock()
        release = threading.Event()
        two_started = threading.Event()
        active = 0
        maximum_active = 0

        async def dispatcher(_args):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    two_started.set()
            await asyncio.to_thread(release.wait, 2)
            with lock:
                active -= 1
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            jobs = [manager.submit(self._request(tmp)) for _ in range(3)]
            self.assertTrue(two_started.wait(2))
            self.assertEqual(maximum_active, 2)
            release.set()
            for job in jobs:
                manager.wait(job.id, timeout=2)

        self.assertEqual(maximum_active, 2)

    def test_only_two_hundred_completed_jobs_are_retained(self):
        async def dispatcher(_args):
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(dispatcher=dispatcher)
            self.addCleanup(manager.shutdown)
            jobs = []
            for _ in range(201):
                job = manager.submit(self._request(tmp))
                manager.wait(job.id, timeout=2)
                jobs.append(job)

            with self.assertRaises(KeyError):
                manager.get(jobs[0].id)
            self.assertEqual(manager.get(jobs[-1].id).status, JobStatus.SUCCEEDED)

    def _wait_for_status(
        self,
        manager: JobManager,
        job_id: str,
        expected: JobStatus,
        timeout: float = 2,
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job.status == expected:
                return job
            time.sleep(0.01)
        self.fail(f"job {job_id} did not reach {expected.value}")


if __name__ == "__main__":
    unittest.main()
