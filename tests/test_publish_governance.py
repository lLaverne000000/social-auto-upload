import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import sau_cli
import utils.browser_profile as browser_profile
from myUtils.postVideo import post_video_DouYin, post_video_xhs
from uploader.douyin_uploader.main import DouYinVideo
from uploader.xhs_uploader.main import sign_local
from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo
from utils.browser_profile import launch_persistent_account_context, profile_dir_for
from utils.risk_control import RiskControlError


class PublishEntryGovernanceTests(unittest.TestCase):
    def test_direct_douyin_publish_without_cli_permit_is_blocked(self):
        app = DouYinVideo("标题", "demo.mp4", [], 0, "account.json", declaration="none")
        with self.assertRaisesRegex(RiskControlError, "统一 CLI"):
            asyncio.run(app.douyin_upload_video())

    def test_direct_xiaohongshu_publish_without_cli_permit_is_blocked(self):
        app = XiaoHongShuVideo(
            "标题", "demo.mp4", [], 0, "account.json", content_source="original"
        )
        with self.assertRaisesRegex(RiskControlError, "统一 CLI"):
            asyncio.run(app.main())

    def test_xhs_publish_helper_without_cli_permit_stops_before_navigation(self):
        app = XiaoHongShuVideo(
            "标题", "demo.mp4", [], 0, "account.json", content_source="original"
        )
        page = MagicMock()
        page.goto = AsyncMock()
        with self.assertRaisesRegex(RiskControlError, "统一 CLI"):
            asyncio.run(app.upload_video_content(page))
        page.goto.assert_not_awaited()

    def test_legacy_batch_publishers_are_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "sau CLI"):
            post_video_DouYin("标题", [], [], [])
        with self.assertRaisesRegex(RuntimeError, "sau CLI"):
            post_video_xhs("标题", [], [], [])

    def test_legacy_xhs_signing_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "已禁用"):
            sign_local("/api/test")

    def test_missing_douyin_declaration_stops_before_cookie_validation(self):
        app = DouYinVideo("标题", "demo.mp4", [], 0, "account.json")
        with patch(
            "uploader.douyin_uploader.main.cookie_auth", new=AsyncMock(return_value=True)
        ) as cookie_auth:
            with self.assertRaisesRegex(ValueError, "显式选择"):
                asyncio.run(app.validate_upload_args())
        cookie_auth.assert_not_awaited()

    def test_cli_does_not_duplicate_douyin_cookie_setup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video_file = root / "demo.mp4"
            account_file = root / "account.json"
            video_file.write_bytes(b"video")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.DouyinVideoUploadRequest(
                account_name="creator",
                video_file=video_file,
                title="标题",
                description="",
                tags=[],
                publish_date=0,
                declaration="none",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch("sau_cli.douyin_setup", new=AsyncMock(return_value=True)) as setup,
                patch.object(
                    sau_cli.DouYinVideo, "douyin_upload_video", new=AsyncMock()
                ),
            ):
                asyncio.run(sau_cli.upload_video(request))
        setup.assert_not_awaited()

    def test_cli_does_not_duplicate_xhs_cookie_setup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video_file = root / "demo.mp4"
            account_file = root / "account.json"
            video_file.write_bytes(b"video")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.XiaohongshuVideoUploadRequest(
                account_name="creator",
                video_file=video_file,
                title="标题",
                description="",
                tags=[],
                publish_date=0,
                content_source="original",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch("sau_cli.xiaohongshu_setup", new=AsyncMock(return_value=True)) as setup,
                patch.object(sau_cli.XiaoHongShuVideo, "main", new=AsyncMock()),
            ):
                asyncio.run(sau_cli.upload_xiaohongshu_video(request))
        setup.assert_not_awaited()

    def test_success_is_recorded_when_douyin_cleanup_fails(self):
        async def publish_then_fail(app):
            app.publish_succeeded = True
            app.publish_success_url = (
                "https://creator.douyin.com/creator-micro/content/manage?aweme_id=receipt456"
            )
            app.publish_work_id = "receipt456"
            raise OSError("storage state failed")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video_file = root / "demo.mp4"
            account_file = root / "account.json"
            video_file.write_bytes(b"video")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.DouyinVideoUploadRequest(
                account_name="creator",
                video_file=video_file,
                title="标题",
                description="",
                tags=[],
                publish_date=0,
                declaration="none",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch.object(sau_cli.DouYinVideo, "douyin_upload_video", publish_then_fail),
                self.assertRaisesRegex(OSError, "storage state failed"),
            ):
                asyncio.run(sau_cli.upload_video(request))

            state_files = list((root / ".sau_safety").glob("*.json"))
            self.assertEqual(len(state_files), 1)
            state = json.loads(state_files[0].read_text(encoding="utf-8"))
            self.assertGreater(state["last_success_at"], 0)

    def test_success_is_recorded_when_xhs_cleanup_fails(self):
        async def publish_then_fail(app):
            app.publish_succeeded = True
            app.publish_success_url = (
                "https://creator.xiaohongshu.com/publish/success?note_id=receipt123"
            )
            app.publish_work_id = "receipt123"
            raise OSError("profile close failed")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video_file = root / "demo.mp4"
            account_file = root / "account.json"
            video_file.write_bytes(b"video")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.XiaohongshuVideoUploadRequest(
                account_name="creator",
                video_file=video_file,
                title="标题",
                description="",
                tags=[],
                publish_date=0,
                content_source="original",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch.object(sau_cli.XiaoHongShuVideo, "main", publish_then_fail),
                self.assertRaisesRegex(OSError, "profile close failed"),
            ):
                asyncio.run(sau_cli.upload_xiaohongshu_video(request))

            state_files = list((root / ".sau_safety").glob("*.json"))
            self.assertEqual(len(state_files), 1)
            state = json.loads(state_files[0].read_text(encoding="utf-8"))
            self.assertGreater(state["last_success_at"], 0)
            receipt_files = list((root / ".sau_safety" / "receipts").glob("*.json"))
            self.assertEqual(len(receipt_files), 1)
            receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
            self.assertEqual(receipt["work_id"], "receipt123")

    def test_douyin_failure_evidence_uses_operation_and_last_page_url(self):
        async def fail_with_page(app):
            app.publish_current_url = (
                "https://creator.douyin.com/creator-micro/content/post/video"
                "?token=secret"
            )
            raise RuntimeError("publish failed")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            video_file = root / "demo.mp4"
            account_file = root / "douyin_creator.json"
            video_file.write_bytes(b"video")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.DouyinVideoUploadRequest(
                account_name="creator",
                video_file=video_file,
                title="标题",
                description="",
                tags=[],
                publish_date=0,
                declaration="none",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch.object(
                    sau_cli.DouYinVideo,
                    "douyin_upload_video",
                    fail_with_page,
                ),
                self.assertRaisesRegex(RuntimeError, "publish failed"),
            ):
                asyncio.run(sau_cli.upload_video(request))

            evidence_path = next((root / ".sau_safety" / "evidence").rglob("*.json"))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["operation"], "upload-video")
            self.assertEqual(evidence["stage"], "upload-video")
            self.assertEqual(
                evidence["page_url"],
                "https://creator.douyin.com/creator-micro/content/post/video",
            )

    def test_xhs_note_failure_evidence_uses_operation_and_last_page_url(self):
        async def fail_with_page(app):
            app.publish_current_url = (
                "https://creator.xiaohongshu.com/publish/publish"
                "?access_token=secret"
            )
            raise RuntimeError("note failed")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            image_file = root / "demo.png"
            account_file = root / "xiaohongshu_creator.json"
            image_file.write_bytes(b"image")
            account_file.write_text("{}", encoding="utf-8")
            request = sau_cli.XiaohongshuNoteUploadRequest(
                account_name="creator",
                image_files=[image_file],
                title="标题",
                note="正文",
                tags=[],
                publish_date=0,
                content_source="original",
                confirm_before_publish=False,
                min_publish_interval_minutes=0,
            )
            with (
                patch("sau_cli.resolve_account_file", return_value=account_file),
                patch.object(
                    sau_cli.XiaoHongShuNote,
                    "main",
                    fail_with_page,
                ),
                self.assertRaisesRegex(RuntimeError, "note failed"),
            ):
                asyncio.run(sau_cli.upload_xiaohongshu_note(request))

            evidence_path = next((root / ".sau_safety" / "evidence").rglob("*.json"))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["operation"], "upload-note")
            self.assertEqual(evidence["stage"], "upload-note")
            self.assertEqual(
                evidence["page_url"],
                "https://creator.xiaohongshu.com/publish/publish",
            )

    def test_missing_xhs_repost_source_stops_before_cookie_validation(self):
        app = XiaoHongShuVideo(
            "标题", "demo.mp4", [], 0, "account.json", content_source="repost"
        )
        with patch(
            "uploader.xiaohongshu_uploader.main.cookie_auth", new=AsyncMock(return_value=True)
        ) as cookie_auth:
            with self.assertRaisesRegex(ValueError, "repost_source"):
                asyncio.run(app.validate_upload_args())
        cookie_auth.assert_not_awaited()


class PersistentProfileTests(unittest.TestCase):
    def test_profile_dir_is_stable_and_account_scoped(self):
        root = Path("/tmp/accounts/creator.json")
        self.assertEqual(profile_dir_for(root, "douyin"), profile_dir_for(root, "douyin"))
        self.assertNotEqual(profile_dir_for(root, "douyin"), profile_dir_for(root, "xiaohongshu"))

    def test_cookie_state_is_imported_only_once(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "account.json"
            account_file.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "x", "domain": ".example.com", "path": "/"}]}),
                encoding="utf-8",
            )
            context = MagicMock()
            context.add_cookies = AsyncMock()
            chromium = MagicMock()
            chromium.launch_persistent_context = AsyncMock(return_value=context)

            first = asyncio.run(
                launch_persistent_account_context(
                    chromium, account_file=account_file, platform="douyin", headless=True
                )
            )
            second = asyncio.run(
                launch_persistent_account_context(
                    chromium, account_file=account_file, platform="douyin", headless=True
                )
            )

        self.assertIs(first, context)
        self.assertIs(second, context)
        context.add_cookies.assert_awaited_once()

    def test_existing_cookie_is_hardened_before_profile_import(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "account.json"
            account_file.write_text('{"cookies": []}', encoding="utf-8")
            account_file.chmod(0o644)
            context = MagicMock()
            context.add_cookies = AsyncMock()
            chromium = MagicMock()
            chromium.launch_persistent_context = AsyncMock(return_value=context)

            asyncio.run(
                launch_persistent_account_context(
                    chromium,
                    account_file=account_file,
                    platform="douyin",
                    headless=True,
                )
            )

            self.assertEqual(os.stat(account_file).st_mode & 0o777, 0o600)

    def test_cookie_import_failure_closes_profile_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "account.json"
            account_file.write_text(
                json.dumps({"cookies": [{"name": "broken"}]}), encoding="utf-8"
            )
            context = MagicMock()
            context.add_cookies = AsyncMock(side_effect=RuntimeError("invalid cookie"))
            context.close = AsyncMock()
            chromium = MagicMock()
            chromium.launch_persistent_context = AsyncMock(return_value=context)

            with self.assertRaisesRegex(RuntimeError, "无法导入"):
                asyncio.run(
                    launch_persistent_account_context(
                        chromium,
                        account_file=account_file,
                        platform="douyin",
                        headless=True,
                    )
                )

        context.close.assert_awaited_once()

    def test_secure_storage_state_file_uses_owner_only_permissions(self):
        saver = getattr(browser_profile, "save_secure_storage_state", None)
        self.assertIsNotNone(saver, "secure storage-state helper is missing")

        class _Context:
            async def storage_state(self, *, path):
                Path(path).write_text('{"cookies": []}', encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "account.json"
            asyncio.run(saver(_Context(), account_file))
            mode = os.stat(account_file).st_mode & 0o777

        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
