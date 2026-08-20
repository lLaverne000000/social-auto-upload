import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import sau_cli
import utils.risk_control as risk_control
from utils.risk_control import PublishGuard


class AuditRetentionTests(unittest.TestCase):
    def test_audit_rotates_with_bounded_backups_and_valid_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            guard = PublishGuard(
                platform="douyin",
                account_file=Path(tmp_dir) / "douyin_creator.json",
                fingerprint="first",
                min_interval_minutes=0,
                audit_max_bytes=420,
                audit_backup_count=2,
            )
            guard.state_dir.mkdir(parents=True)

            for index in range(20):
                guard._audit("failed", reason=f"failure-{index}-" + ("x" * 80))

            audit_files = sorted(guard.state_dir.glob("audit.jsonl*"))
            data_files = [path for path in audit_files if path.name != "audit.jsonl.lock"]
            self.assertEqual(
                {path.name for path in data_files},
                {"audit.jsonl", "audit.jsonl.1", "audit.jsonl.2"},
            )
            self.assertFalse((guard.state_dir / "audit.jsonl.3").exists())
            for path in data_files:
                self.assertLessEqual(path.stat().st_size, 420)
                records = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertTrue(records)
                self.assertTrue(all(record["platform"] == "douyin" for record in records))
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

            self.assertEqual(os.stat(guard.audit_lock_path).st_mode & 0o777, 0o600)


class FailureEvidenceTests(unittest.TestCase):
    def test_failed_publish_writes_sanitized_metadata_only_evidence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            guard = PublishGuard(
                platform="xiaohongshu",
                account_file=Path(tmp_dir) / "xiaohongshu_creator.json",
                fingerprint="first",
                min_interval_minutes=0,
                operation="upload-video",
            )

            with self.assertRaisesRegex(RuntimeError, "request failed"):
                with guard:
                    guard.set_failure_context(
                        stage="publish-confirmation",
                        page_url=(
                            "https://creator.example.com/publish/failure"
                            "?access_token=top-secret#private-fragment"
                        ),
                    )
                    raise RuntimeError(
                        "request failed at https://api.example.com/result?token=top-secret"
                    )

            evidence_files = list(guard.evidence_dir.glob("*.json"))
            self.assertEqual(len(evidence_files), 1)
            evidence_path = evidence_files[0]
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["task_id"], guard.task_id)
            self.assertEqual(evidence["platform"], "xiaohongshu")
            self.assertEqual(evidence["account"], "xiaohongshu_creator")
            self.assertEqual(evidence["operation"], "upload-video")
            self.assertEqual(evidence["stage"], "publish-confirmation")
            self.assertEqual(evidence["error_type"], "RuntimeError")
            self.assertEqual(
                evidence["page_url"],
                "https://creator.example.com/publish/failure",
            )
            serialized = json.dumps(evidence, ensure_ascii=False)
            self.assertNotIn("top-secret", serialized)
            self.assertNotIn("private-fragment", serialized)
            self.assertNotIn("cookies", evidence)
            self.assertNotIn("page_text", evidence)
            self.assertNotIn("screenshot", evidence)
            self.assertEqual(os.stat(guard.evidence_dir).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(evidence_path).st_mode & 0o777, 0o600)

    def test_evidence_write_failure_does_not_mask_publish_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            guard = PublishGuard(
                platform="douyin",
                account_file=Path(tmp_dir) / "douyin_creator.json",
                fingerprint="first",
                min_interval_minutes=0,
                operation="upload-video",
            )
            with (
                patch.object(
                    guard,
                    "_write_failure_evidence",
                    side_effect=OSError("evidence disk failure"),
                ),
                patch("sys.stderr", new=io.StringIO()) as stderr,
                self.assertRaisesRegex(RuntimeError, "original publish failure"),
            ):
                with guard:
                    raise RuntimeError("original publish failure")

            self.assertIn("evidence disk failure", stderr.getvalue())
            records = [
                json.loads(line)
                for line in guard.audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[-1]["event"], "failed")


class SafetyStatusTests(unittest.TestCase):
    def _reader(self):
        reader = getattr(risk_control, "read_publish_safety_status", None)
        self.assertIsNotNone(reader, "read-only safety status reader is missing")
        return reader

    def test_missing_status_is_healthy_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "cookies" / "douyin_creator.json"
            status = self._reader()(
                platform="douyin",
                account_file=account_file,
                min_interval_minutes=30,
            )

            self.assertEqual(status["state_status"], "missing")
            self.assertIsNone(status["last_success_at"])
            self.assertEqual(status["cooldown_remaining_seconds"], 0)
            self.assertFalse(account_file.parent.exists())

    def test_status_reports_success_lock_audit_and_latest_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "douyin_creator.json"
            with PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="success",
                min_interval_minutes=0,
            ) as success_guard:
                success_guard.mark_success(
                    success_url="https://creator.example.com/success?work_id=42"
                )

            failed_guard = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="failed",
                min_interval_minutes=0,
                operation="upload-note",
            )
            with self.assertRaises(RuntimeError):
                with failed_guard:
                    failed_guard.set_failure_context(
                        stage="upload",
                        page_url="https://creator.example.com/publish?token=secret",
                    )
                    raise RuntimeError("failure")

            failed_guard.lock_path.write_text(f"pid={os.getpid()}\n", encoding="ascii")
            status = self._reader()(
                platform="douyin",
                account_file=account_file,
                min_interval_minutes=30,
            )

            self.assertEqual(status["state_status"], "ok")
            self.assertIsNotNone(status["last_success_at"])
            self.assertGreater(status["cooldown_remaining_seconds"], 0)
            self.assertEqual(status["recent_count"], 1)
            self.assertEqual(status["lock"]["pid"], os.getpid())
            self.assertTrue(status["lock"]["pid_alive"])
            self.assertGreater(status["audit"]["size_bytes"], 0)
            self.assertEqual(
                status["latest_failure"]["task_id"],
                failed_guard.task_id,
            )

    def test_corrupt_state_is_reported_without_modification(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "douyin_creator.json"
            guard = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="first",
                min_interval_minutes=0,
            )
            guard.state_dir.mkdir(parents=True)
            guard.state_path.write_text("{not-json", encoding="utf-8")
            before = guard.state_path.read_bytes()

            status = self._reader()(
                platform="douyin",
                account_file=account_file,
                min_interval_minutes=30,
            )

            self.assertEqual(status["state_status"], "corrupt")
            self.assertIn("error", status)
            self.assertEqual(guard.state_path.read_bytes(), before)


class SafetyStatusCliTests(unittest.TestCase):
    def test_parser_accepts_safety_status_json(self):
        args = sau_cli.build_parser().parse_args(
            [
                "safety",
                "status",
                "--platform",
                "douyin",
                "--account",
                "creator",
                "--json",
            ]
        )
        self.assertEqual(args.platform, "safety")
        self.assertEqual(args.action, "status")
        self.assertEqual(args.target_platform, "douyin")
        self.assertTrue(args.json_output)

    def test_json_status_dispatch_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = io.StringIO()
            args = sau_cli.build_parser().parse_args(
                [
                    "safety",
                    "status",
                    "--platform",
                    "xiaohongshu",
                    "--account",
                    "creator",
                    "--json",
                ]
            )
            with (
                patch("sau_cli.resolve_runtime_home", return_value=Path(tmp_dir)),
                redirect_stdout(output),
            ):
                code = asyncio.run(sau_cli.dispatch(args))

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["platform"], "xiaohongshu")
            self.assertEqual(payload["state_status"], "missing")
            self.assertFalse((Path(tmp_dir) / "cookies").exists())

    def test_human_status_dispatch_explains_missing_history(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = io.StringIO()
            args = sau_cli.build_parser().parse_args(
                [
                    "safety",
                    "status",
                    "--platform",
                    "douyin",
                    "--account",
                    "creator",
                ]
            )
            with (
                patch("sau_cli.resolve_runtime_home", return_value=Path(tmp_dir)),
                redirect_stdout(output),
            ):
                code = asyncio.run(sau_cli.dispatch(args))

            self.assertEqual(code, 0)
            self.assertIn("无发布记录", output.getvalue())


if __name__ == "__main__":
    unittest.main()
