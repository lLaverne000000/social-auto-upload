import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from sau_desktop_service import (
    JobManager,
    JobStatus,
    PublishRequest,
    build_publish_argv,
)
from utils.risk_control import RiskControlError


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


if __name__ == "__main__":
    unittest.main()
