import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import uploader.xiaohongshu_uploader.main as xhs_main
from utils.risk_control import _issue_cli_publish_permit
from utils.risk_control import RiskControlError


class FakeLocator:
    def __init__(self, name, count=0, src=None, children=None):
        self.name = name
        self._count = count
        self._src = src
        self._children = children or {}

    @property
    def first(self):
        return self

    def locator(self, selector):
        return self._children.get(selector, FakeLocator(selector))

    def get_by_text(self, text, exact=False):
        return self._children.get(f"text:{text}", FakeLocator(text))

    def filter(self, **kwargs):
        return self

    def nth(self, index):
        return self

    async def count(self):
        return self._count

    async def wait_for(self, **kwargs):
        return None

    async def get_attribute(self, name):
        if name == "src":
            return self._src
        return None

    async def fill(self, value):
        return None

    async def click(self):
        return None


class RecordingKeyboard:
    def __init__(self):
        self.actions = []

    async def press(self, key):
        self.actions.append(("press", key))

    async def type(self, text, delay=None):
        self.actions.append(("type", text, delay))


class RecordingLocator(FakeLocator):
    def __init__(self, name):
        super().__init__(name, count=1)
        self.actions = []

    async def fill(self, value):
        self.actions.append(("fill", value))

    async def click(self):
        self.actions.append(("click",))

    async def wait_for(self, **kwargs):
        self.actions.append(("wait_for", kwargs))


class RecordingPage:
    def __init__(self):
        self.keyboard = RecordingKeyboard()
        self.locators = {
            'input[placeholder*="填写标题"]': RecordingLocator("title"),
            'p[data-placeholder*="输入正文描述"]': RecordingLocator("desc"),
            '#creator-editor-topic-container': RecordingLocator("topic-container"),
            '#creator-editor-topic-container .item': RecordingLocator("topic-item"),
        }

    def locator(self, selector):
        return self.locators[selector]


class XiaohongshuUploaderTests(unittest.TestCase):
    @staticmethod
    def _video_polling_page(preview_text: str):
        response = MagicMock(status=200, url="https://creator.xiaohongshu.com/publish")
        page = MagicMock(url="https://creator.xiaohongshu.com/publish")
        page.goto = AsyncMock(return_value=response)
        page.wait_for_url = AsyncMock()
        page.locator.return_value.set_input_files = AsyncMock()
        preview = MagicMock()
        preview.inner_text = AsyncMock(return_value=preview_text)
        preview.query_selector_all = AsyncMock(return_value=[])
        upload_input = MagicMock()
        upload_input.query_selector = AsyncMock(return_value=preview)
        page.wait_for_selector = AsyncMock(return_value=upload_input)
        return page

    def test_video_upload_rejects_unhealthy_navigation_response(self):
        app = xhs_main.XiaoHongShuVideo(
            title="demo",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            content_source="original",
            publish_permit=_issue_cli_publish_permit(),
        )
        response = MagicMock(status=503, url="https://creator.xiaohongshu.com/publish")
        page = MagicMock(url="https://creator.xiaohongshu.com/publish")
        page.goto = AsyncMock(return_value=response)
        page.wait_for_url = AsyncMock()
        page.locator.return_value.set_input_files = AsyncMock()
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError("not ready"))

        with (
            patch.object(xhs_main, "assert_no_risk_prompt", new=AsyncMock()),
            patch.object(xhs_main.xiaohongshu_logger, "debug"),
            self.assertRaisesRegex(RiskControlError, "HTTP 503"),
        ):
            asyncio.run(asyncio.wait_for(app.upload_video_content(page), timeout=0.1))

    def test_video_upload_checks_risk_after_each_unsuccessful_poll(self):
        app = xhs_main.XiaoHongShuVideo(
            title="demo",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            content_source="original",
            publish_permit=_issue_cli_publish_permit(),
        )
        page = self._video_polling_page("正在上传 50%")

        async def risk_check(_page, _platform, stage="当前阶段"):
            if stage == "素材上传过程中":
                raise RiskControlError("risk prompt")

        with (
            patch.object(xhs_main, "assert_no_risk_prompt", side_effect=risk_check),
            patch.object(xhs_main.xiaohongshu_logger, "debug"),
            self.assertRaisesRegex(RiskControlError, "risk prompt"),
        ):
            asyncio.run(asyncio.wait_for(app.upload_video_content(page), timeout=0.1))

    def test_success_page_risk_does_not_set_publish_succeeded(self):
        app = xhs_main.XiaoHongShuVideo(
            title="demo",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            content_source="original",
            publish_permit=_issue_cli_publish_permit(),
        )
        page = self._video_polling_page("上传成功")
        publish_button = MagicMock()
        publish_button.count = AsyncMock(return_value=1)
        publish_button.click = AsyncMock()
        page.get_by_role.return_value = publish_button
        app.fill_meta = AsyncMock()
        app.set_thumbnail = AsyncMock()
        app.apply_content_source_declaration = AsyncMock()

        async def risk_check(_page, _platform, stage="当前阶段"):
            if stage == "发布成功页":
                raise RiskControlError("success page risk")

        with (
            patch.object(xhs_main, "assert_no_risk_prompt", side_effect=risk_check),
            self.assertRaisesRegex(RuntimeError, "未确认发布成功"),
        ):
            asyncio.run(app.upload_video_content(page))

        self.assertFalse(app.publish_succeeded)
        self.assertEqual(app.publish_success_url, "")

    def test_creator_urls_keep_xiaohongshu_domain_by_default(self):
        with patch.dict(os.environ, {"SAU_XHS_CREATOR_BASE_URL": ""}):
            self.assertEqual(
                xhs_main._build_xhs_creator_url("/login"),
                "https://creator.xiaohongshu.com/login",
            )

    def test_creator_urls_use_configured_rednote_domain(self):
        with patch.dict(
            os.environ,
            {"SAU_XHS_CREATOR_BASE_URL": "https://creator.rednote.com/"},
        ):
            self.assertEqual(
                xhs_main._build_xhs_creator_url("/login"),
                "https://creator.rednote.com/login",
            )
            self.assertEqual(
                xhs_main._build_xhs_creator_url(
                    "/publish/publish?from=homepage&target=video"
                ),
                "https://creator.rednote.com/publish/publish?from=homepage&target=video",
            )

    def test_find_xhs_qrcode_locator_prefers_scan_sibling_inside_login_box(self):
        qrcode_locator = FakeLocator("qrcode", count=1, src="data:image/png;base64,abc")
        scan_text_locator = FakeLocator(
            "scan-text",
            count=1,
            children={
                "xpath=..//following-sibling::div//img": qrcode_locator,
            },
        )
        login_box_locator = FakeLocator(
            "login-box",
            count=1,
            children={
                "div:has-text('扫一扫')": scan_text_locator,
                "text:APP扫一扫登录": scan_text_locator,
            },
        )
        page = FakeLocator(
            "page",
            children={
                "div[class*='login-box']": login_box_locator,
                ".login-box-container": login_box_locator,
            },
        )

        locator = asyncio.run(xhs_main._find_xhs_qrcode_locator(page))
        self.assertIs(locator, qrcode_locator)

    def test_setup_returns_detail_when_cookie_invalid_without_handle(self):
        with patch("uploader.xiaohongshu_uploader.main.os.path.exists", return_value=False):
            result = asyncio.run(
                xhs_main.xiaohongshu_setup(
                    "missing.json",
                    handle=False,
                    return_detail=True,
                )
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "cookie_invalid")

    def test_setup_uses_login_flow_when_handle_is_true(self):
        login_result = {
            "success": True,
            "status": "success",
            "message": "ok",
            "account_file": "account.json",
            "qrcode": {"image_path": "qrcode.png"},
            "current_url": "https://creator.xiaohongshu.com/",
        }
        with patch("uploader.xiaohongshu_uploader.main.os.path.exists", return_value=False):
            with patch(
                "uploader.xiaohongshu_uploader.main.xiaohongshu_cookie_gen",
                new=AsyncMock(return_value=login_result),
            ) as mock_login:
                result = asyncio.run(
                    xhs_main.xiaohongshu_setup(
                        "account.json",
                        handle=True,
                        return_detail=True,
                    )
                )
        self.assertTrue(result["success"])
        mock_login.assert_awaited_once()

    def test_video_validate_upload_args_normalizes_video_and_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            thumbnail_path = Path(tmp_dir) / "demo.png"
            cookie_path = Path(tmp_dir) / "account.json"
            video_path.write_bytes(b"video")
            thumbnail_path.write_bytes(b"image")
            cookie_path.write_text("{}")

            app = xhs_main.XiaoHongShuVideo(
                title="demo",
                file_path=str(video_path),
                tags=["xhs"],
                publish_date=0,
                account_file=str(cookie_path),
                thumbnail_path=str(thumbnail_path),
                content_source="original",
            )

            with patch(
                "uploader.xiaohongshu_uploader.main.cookie_auth",
                new=AsyncMock(return_value=True),
            ):
                asyncio.run(app.validate_upload_args())

        self.assertTrue(app.file_path.endswith("demo.mp4"))
        self.assertTrue(app.thumbnail_path.endswith("demo.png"))

    def test_note_uploader_exists_and_validates_required_fields(self):
        note_cls = getattr(xhs_main, "XiaoHongShuNote")
        app = note_cls(
            image_paths=[],
            note="",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )

        with patch.object(app, "validate_base_args", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                asyncio.run(app.validate_upload_args())

    def test_video_fill_meta_uses_desc_then_first_tag(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=["话题1"],
            publish_date=0,
            account_file="account.json",
            desc="描述内容",
        )
        page = RecordingPage()

        asyncio.run(app.fill_meta(page))

        self.assertEqual(
            page.locators['input[placeholder*="填写标题"]'].actions,
            [("fill", "标题内容")],
        )
        self.assertEqual(
            page.locators['p[data-placeholder*="输入正文描述"]'].actions,
            [("click",)],
        )
        self.assertIn(("type", "描述内容", None), page.keyboard.actions)
        self.assertIn(("type", "#话题1", 30), page.keyboard.actions)
        self.assertEqual(
            page.locators['#creator-editor-topic-container .item'].actions,
            [("wait_for", {"state": "visible", "timeout": 2000}), ("click",)],
        )

    def test_video_fill_meta_can_fill_first_tag_without_desc(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=["话题1"],
            publish_date=0,
            account_file="account.json",
        )
        page = RecordingPage()

        asyncio.run(app.fill_meta(page))

        self.assertEqual(
            page.locators['p[data-placeholder*="输入正文描述"]'].actions,
            [("click",)],
        )
        self.assertNotIn(("type", "", None), page.keyboard.actions)
        self.assertIn(("type", "#话题1", 30), page.keyboard.actions)

    def test_note_title_defaults_do_not_override_explicit_title(self):
        app = xhs_main.XiaoHongShuNote(
            image_paths=["a.png"],
            note="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="显式标题",
            desc="图文正文",
        )

        self.assertEqual(app.title, "显式标题")
        self.assertEqual(app.desc, "图文正文")


if __name__ == "__main__":
    unittest.main()
