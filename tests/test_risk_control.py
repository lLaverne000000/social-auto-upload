import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import utils.risk_control as risk_control
from utils.base_social_media import set_init_script
from utils.risk_control import PublishGuard
from utils.risk_control import RiskControlError
from utils.risk_control import assert_no_risk_prompt
from utils.risk_control import content_fingerprint
from utils.risk_control import require_manual_publish_confirmation


class _BodyLocator:
    def __init__(self, text: str):
        self.text = text

    async def inner_text(self, timeout: int):
        return self.text


class _Page:
    def __init__(self, text: str, url: str = "https://creator.example.com/publish"):
        self.text = text
        self.url = url

    def locator(self, selector: str):
        if selector != "body":
            raise AssertionError(selector)
        return _BodyLocator(self.text)


class _Response:
    def __init__(self, status: int, url: str = "https://creator.example.com/publish"):
        self.status = status
        self.url = url


class _InteractiveInput(io.StringIO):
    def isatty(self):
        return True


class RiskPromptTests(unittest.TestCase):
    def test_blocks_visible_security_challenge(self):
        with self.assertRaisesRegex(RiskControlError, "安全验证"):
            asyncio.run(assert_no_risk_prompt(_Page("请完成安全验证后继续"), "抖音"))

    def test_allows_normal_publish_page(self):
        asyncio.run(assert_no_risk_prompt(_Page("填写标题 添加话题 发布"), "小红书"))

    def test_blocks_login_redirect_url(self):
        with self.assertRaisesRegex(RiskControlError, "登录或验证页面"):
            asyncio.run(
                assert_no_risk_prompt(
                    _Page("扫码登录", "https://creator.xiaohongshu.com/login"),
                    "小红书",
                    "进入上传页后",
                )
            )

    def test_blocks_host_compound_path_and_query_value_risk_urls(self):
        urls = (
            "https://verify.example.com/publish",
            "https://creator.example.com/account-login",
            "https://creator.example.com/publish?redirect=https%3A%2F%2Fpassport.example.com",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(RiskControlError, "登录或验证页面"):
                    asyncio.run(assert_no_risk_prompt(_Page("正常页面", url), "抖音"))

    def test_blocks_upload_failure_text(self):
        with self.assertRaisesRegex(RiskControlError, "上传失败"):
            asyncio.run(assert_no_risk_prompt(_Page("视频上传失败，请重试"), "抖音"))

    def test_navigation_rejects_rate_limit_and_server_errors(self):
        checker = getattr(risk_control, "assert_healthy_navigation_response", None)
        self.assertIsNotNone(checker, "navigation response checker is missing")
        for status in (429, 503):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RiskControlError, str(status)):
                    checker(_Response(status), "抖音", "进入上传页")

    def test_navigation_allows_success_and_missing_response(self):
        checker = getattr(risk_control, "assert_healthy_navigation_response", None)
        self.assertIsNotNone(checker, "navigation response checker is missing")
        checker(_Response(200), "抖音", "进入上传页")
        checker(None, "抖音", "进入上传页")

    def test_stage_deadline_stops_expired_operation(self):
        deadline_cls = getattr(risk_control, "StageDeadline", None)
        self.assertIsNotNone(deadline_cls, "stage deadline is missing")
        ticks = iter((0.0, 901.0))
        deadline = deadline_cls("抖音", "视频上传", 900, clock=lambda: next(ticks))
        with self.assertRaisesRegex(RiskControlError, "超时"):
            deadline.raise_if_expired()

    def test_manual_confirmation_rejects_headless_mode(self):
        with self.assertRaisesRegex(RiskControlError, "--headed"):
            asyncio.run(
                require_manual_publish_confirmation(
                    platform="抖音",
                    content_type="视频",
                    headless=True,
                    enabled=True,
                )
            )

    def test_manual_confirmation_without_provider_still_requires_tty(self):
        with (
            patch.object(risk_control.sys, "stdin", io.StringIO("")),
            self.assertRaisesRegex(RiskControlError, "不可交互"),
        ):
            asyncio.run(
                require_manual_publish_confirmation(
                    platform="抖音",
                    content_type="视频",
                    headless=False,
                    enabled=True,
                )
            )

    def test_terminal_confirmation_keeps_publish_phrase_flow(self):
        with (
            patch.object(risk_control.sys, "stdin", _InteractiveInput()),
            patch("builtins.input", return_value="PUBLISH") as input_prompt,
        ):
            asyncio.run(
                require_manual_publish_confirmation(
                    platform="抖音",
                    content_type="视频",
                    headless=False,
                    enabled=True,
                )
            )

        input_prompt.assert_called_once()

    def test_context_provider_handles_confirmation_without_terminal_input(self):
        calls = []

        async def provider(*, platform, content_type):
            calls.append((platform, content_type))
            return True

        provider_context = getattr(
            risk_control,
            "use_manual_confirmation_provider",
            None,
        )
        self.assertIsNotNone(provider_context)
        with (
            provider_context(provider),
            patch.object(risk_control.sys, "stdin", io.StringIO("")),
        ):
            asyncio.run(
                require_manual_publish_confirmation(
                    platform="小红书",
                    content_type="视频",
                    headless=False,
                    enabled=True,
                )
            )

        self.assertEqual(calls, [("小红书", "视频")])

    def test_compatibility_hook_does_not_inject_javascript(self):
        context = AsyncMock()
        result = asyncio.run(set_init_script(context))
        self.assertIs(result, context)
        context.add_init_script.assert_not_awaited()


class PublishGuardTests(unittest.TestCase):
    def test_windows_pid_liveness_never_calls_os_kill(self):
        with (
            patch.object(risk_control.os, "name", "nt"),
            patch.object(
                risk_control,
                "_windows_pid_is_alive",
                return_value=True,
                create=True,
            ) as windows_probe,
            patch.object(
                risk_control.os,
                "kill",
                side_effect=AssertionError("os.kill must not run on Windows"),
            ),
        ):
            self.assertTrue(risk_control._pid_is_alive(1234))

        windows_probe.assert_called_once_with(1234)

    def test_rejects_concurrent_platform_publish(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            first = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            )
            with first:
                with self.assertRaisesRegex(RiskControlError, "拒绝并发"):
                    with PublishGuard(
                        platform="douyin",
                        account_file=account_file,
                        fingerprint="second",
                        min_interval_minutes=0,
                    ):
                        pass

    def test_rejects_recent_exact_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            with PublishGuard(
                platform="xiaohongshu",
                account_file=account_file,
                fingerprint="same",
                min_interval_minutes=0,
            ) as guard:
                guard.mark_success(success_url="https://example.test/success?work_id=same")

            with self.assertRaisesRegex(RiskControlError, "相同内容"):
                with PublishGuard(
                    platform="xiaohongshu",
                    account_file=account_file,
                    fingerprint="same",
                    min_interval_minutes=0,
                ):
                    pass

    def test_enforces_account_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            with PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=30,
            ) as guard:
                guard.mark_success(success_url="https://example.test/success?work_id=first")

            with self.assertRaisesRegex(RiskControlError, "冷却期"):
                with PublishGuard(
                    platform="douyin",
                    account_file=account_file,
                    fingerprint="different",
                    min_interval_minutes=30,
                ):
                    pass

    def test_fingerprint_changes_with_media(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            media = Path(tmp_dir) / "demo.mp4"
            media.write_bytes(b"one")
            first = content_fingerprint("title", "text", [media])
            media.write_bytes(b"two")
            second = content_fingerprint("title", "text", [media])
            self.assertNotEqual(first, second)

    def test_fresh_lock_owned_by_dead_pid_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            guard = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            )
            guard.state_dir.mkdir(parents=True)
            guard.lock_path.write_text("pid=999999\n", encoding="ascii")

            with patch("utils.risk_control._pid_is_alive", return_value=False, create=True):
                with guard:
                    self.assertTrue(guard.lock_path.exists())

    def test_dead_pid_recovery_never_unlinks_shared_lock_inode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            guard = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            )
            guard.state_dir.mkdir(parents=True)
            guard.lock_path.write_text("pid=999999\n", encoding="ascii")

            with (
                patch("utils.risk_control._pid_is_alive", return_value=False),
                patch.object(
                    Path,
                    "unlink",
                    side_effect=AssertionError("shared lock inode must stay stable"),
                ),
            ):
                with guard:
                    self.assertTrue(guard.lock_path.exists())

            self.assertEqual(guard.lock_path.read_text(encoding="ascii"), "pid=0\n")

    def test_old_lock_owned_by_live_pid_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            guard = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            )
            guard.state_dir.mkdir(parents=True)
            guard.lock_path.write_text("pid=123\n", encoding="ascii")
            os.utime(guard.lock_path, (0, 0))

            with patch("utils.risk_control._pid_is_alive", return_value=True, create=True):
                with self.assertRaisesRegex(RiskControlError, "拒绝并发"):
                    with guard:
                        pass

    def test_audit_records_share_unique_task_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            with PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            ) as guard:
                guard.mark_success(success_url="https://example.test/success?work_id=audit")

            records = [
                json.loads(line)
                for line in guard.audit_path.read_text(encoding="utf-8").splitlines()
            ]
            task_ids = {record.get("task_id") for record in records}
            self.assertEqual(len(task_ids), 1)
            self.assertNotIn(None, task_ids)

    def test_success_writes_reconciliation_receipt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "creator.json"
            with PublishGuard(
                platform="xiaohongshu",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            ) as guard:
                guard.mark_success(
                    success_url="https://creator.xiaohongshu.com/publish/success?note_id=abc123"
                )

            receipt_files = list((guard.state_dir / "receipts").glob("*.json"))
            self.assertEqual(len(receipt_files), 1)
            receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
            self.assertEqual(receipt["task_id"], guard.task_id)
            self.assertEqual(receipt["status"], "success_url_confirmed")
            self.assertEqual(receipt["work_id"], "abc123")
            self.assertFalse(receipt["manual_reconciliation_required"])
            self.assertEqual(os.stat(guard.state_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(guard.audit_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(receipt_files[0]).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(guard.state_dir).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(guard.receipt_dir).st_mode & 0o777, 0o700)

    def test_success_requires_confirmed_url(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            guard = PublishGuard(
                platform="douyin",
                account_file=Path(tmp_dir) / "creator.json",
                fingerprint="first",
                min_interval_minutes=0,
            )
            with guard:
                with self.assertRaisesRegex(RiskControlError, "成功 URL"):
                    guard.mark_success()

            self.assertFalse(guard.state_path.exists())

    def test_receipt_failure_does_not_commit_success_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            guard = PublishGuard(
                platform="douyin",
                account_file=Path(tmp_dir) / "creator.json",
                fingerprint="first",
                min_interval_minutes=0,
            )
            with (
                self.assertRaisesRegex(OSError, "receipt failed"),
                patch.object(guard, "_write_receipt", side_effect=OSError("receipt failed")),
            ):
                with guard:
                    guard.mark_success(success_url="https://example.test/success?work_id=42")

            self.assertFalse(guard.state_path.exists())

    def test_extract_work_id_uses_known_query_fields_only(self):
        extractor = getattr(risk_control, "extract_work_id", None)
        self.assertIsNotNone(extractor, "work-id extractor is missing")
        self.assertEqual(extractor("https://example.test/success?item_id=42"), "42")
        self.assertIsNone(extractor("https://example.test/success?from=publish"))


if __name__ == "__main__":
    unittest.main()
