# Language: 中文
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.douyin_uploader import main as douyin_main
from uploader.douyin_uploader.main import DouYinVideo
from uploader.douyin_uploader.main import DouYinNote
from utils.risk_control import _issue_cli_publish_permit
from utils.risk_control import RiskControlError


class DouyinDeclarationTests(unittest.TestCase):
    def test_note_upload_wait_loop_obeys_hard_deadline(self):
        note = DouYinNote(
            image_paths=["a.png"],
            note="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="标题",
            publish_permit=_issue_cli_publish_permit(),
        )
        page = MagicMock()
        page.get_by_text.return_value.click = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.locator.return_value.set_input_files = AsyncMock()
        page.wait_for_url = AsyncMock(side_effect=TimeoutError("not ready"))

        deadline = MagicMock()
        deadline.raise_if_expired.side_effect = RiskControlError("hard deadline")
        with (
            patch.object(douyin_main, "StageDeadline", return_value=deadline, create=True),
            patch.object(douyin_main.douyin_logger, "debug"),
            self.assertRaisesRegex(RiskControlError, "hard deadline"),
        ):
            asyncio.run(asyncio.wait_for(note.upload_note_content(page), timeout=0.1))

    def test_note_upload_checks_risk_after_each_unsuccessful_poll(self):
        note = DouYinNote(
            image_paths=["a.png"],
            note="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="标题",
            publish_permit=_issue_cli_publish_permit(),
        )
        page = MagicMock()
        page.get_by_text.return_value.click = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.locator.return_value.set_input_files = AsyncMock()
        page.wait_for_url = AsyncMock(side_effect=TimeoutError("not ready"))

        async def risk_check(_page, _platform, stage="当前阶段"):
            if stage == "等待图文编辑页时":
                raise RiskControlError("risk prompt")

        with (
            patch.object(douyin_main, "assert_no_risk_prompt", side_effect=risk_check),
            patch.object(douyin_main.douyin_logger, "debug"),
            self.assertRaisesRegex(RiskControlError, "risk prompt"),
        ):
            asyncio.run(asyncio.wait_for(note.upload_note_content(page), timeout=0.1))

    def test_upload_only_sets_explicit_declaration(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
            publish_permit=_issue_cli_publish_permit(),
        )
        self.assertEqual(video.declaration, "已确认声明原文")

    def test_missing_declaration_does_not_fall_back_to_personal_opinion(self):
        video = DouYinVideo("标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json")
        self.assertIsNone(video.declaration)

    def test_legacy_positional_runtime_flags_keep_their_meaning(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            None, "", "", None, "",
            "scheduled", False, False,
        )
        self.assertEqual(video.publish_strategy, "scheduled")
        self.assertFalse(video.debug)
        self.assertFalse(video.headless)
        self.assertIsNone(video.declaration)

    def test_apply_declaration_skips_when_unspecified(self):
        video = DouYinVideo("标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json")
        video.set_self_declaration = AsyncMock()
        asyncio.run(video.apply_self_declaration(object()))
        video.set_self_declaration.assert_not_awaited()

    def test_apply_declaration_uses_exact_explicit_text(self):
        page = object()
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        video.set_self_declaration = AsyncMock(return_value=True)
        asyncio.run(video.apply_self_declaration(page))
        video.set_self_declaration.assert_awaited_once_with(page, "已确认声明原文")

    def test_explicit_declaration_failure_blocks_publish(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
        )
        video.set_self_declaration = AsyncMock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "自主声明"):
            asyncio.run(video.apply_self_declaration(object()))

    def test_declaration_failure_closes_browser_resources(self):
        video = DouYinVideo(
            "标题", "/tmp/demo.mp4", [], 0, "/tmp/cookie.json",
            declaration="已确认声明原文",
            publish_permit=_issue_cli_publish_permit(),
        )
        video.validate_upload_args = AsyncMock()
        video.fill_title_and_description = AsyncMock()
        video.set_thumbnail = AsyncMock()
        video.apply_self_declaration = AsyncMock(side_effect=RuntimeError("抖音自主声明设置失败"))

        locator = MagicMock()
        locator.set_input_files = AsyncMock()
        locator.count = AsyncMock(return_value=1)
        page = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_url = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.locator.return_value = locator

        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock(side_effect=OSError("close failed"))
        playwright = MagicMock()

        with (
            patch.object(douyin_main, "launch_persistent_account_context", AsyncMock(return_value=context)),
            patch.object(douyin_main, "set_init_script", AsyncMock(return_value=context)),
            patch.object(douyin_main, "assert_no_risk_prompt", AsyncMock()),
            patch.object(douyin_main.asyncio, "sleep", AsyncMock()),
            self.assertRaisesRegex(RuntimeError, "自主声明"),
        ):
            asyncio.run(video.upload(playwright))

        context.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
