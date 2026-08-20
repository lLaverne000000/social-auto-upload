import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from utils.risk_control import PublishGuard
from utils.risk_control import RiskControlError
from utils.risk_control import _pid_is_alive


def _hold_publish_lock(account_file: str, ready, release) -> None:
    guard = PublishGuard(
        platform="douyin",
        account_file=account_file,
        fingerprint="child",
        min_interval_minutes=0,
    )
    with guard:
        ready.set()
        if not release.wait(15):
            raise RuntimeError("parent did not release child lock holder")


def _append_audit_records(root: str, platform: str, offset: int) -> None:
    guard = PublishGuard(
        platform=platform,
        account_file=Path(root) / f"{platform}_creator.json",
        fingerprint=str(offset),
        min_interval_minutes=0,
        audit_max_bytes=1024,
        audit_backup_count=30,
    )
    guard.state_dir.mkdir(parents=True, exist_ok=True)
    guard.state_dir.chmod(0o700)
    for index in range(30):
        guard._audit("cross-platform-stress", reason=f"record-{offset + index}")


class CrossPlatformLockIntegrationTests(unittest.TestCase):
    def test_live_process_lock_blocks_then_releases_without_unlinking(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "douyin_creator.json"
            ready = context.Event()
            release = context.Event()
            process = context.Process(
                target=_hold_publish_lock,
                args=(str(account_file), ready, release),
            )
            process.start()
            self.assertTrue(ready.wait(15), "child did not acquire publish lock")

            probe = PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="parent",
                min_interval_minutes=0,
            )
            lock_identity = probe.lock_path.stat()
            with self.assertRaisesRegex(RiskControlError, "拒绝并发"):
                with probe:
                    pass

            release.set()
            process.join(15)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(probe.lock_path.exists())
            self.assertEqual(probe.lock_path.read_text(encoding="ascii"), "pid=0\n")
            released_identity = probe.lock_path.stat()
            self.assertEqual(
                (lock_identity.st_dev, lock_identity.st_ino),
                (released_identity.st_dev, released_identity.st_ino),
            )

            with PublishGuard(
                platform="douyin",
                account_file=account_file,
                fingerprint="after-release",
                min_interval_minutes=0,
            ):
                pass

    def test_audit_rotation_is_serialized_across_processes(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp_dir:
            processes = [
                context.Process(
                    target=_append_audit_records,
                    args=(
                        tmp_dir,
                        "douyin" if index % 2 == 0 else "xiaohongshu",
                        index * 30,
                    ),
                )
                for index in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)

            state_dir = Path(tmp_dir) / ".sau_safety"
            audit_files = [
                path
                for path in state_dir.glob("audit.jsonl*")
                if path.name == "audit.jsonl"
                or path.name.removeprefix("audit.jsonl.").isdigit()
            ]
            records = [
                json.loads(line)
                for path in audit_files
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 60)
            self.assertEqual({record["event"] for record in records}, {"cross-platform-stress"})

    def test_pid_liveness_probe_keeps_current_process_running(self):
        self.assertTrue(_pid_is_alive(os.getpid()))


if __name__ == "__main__":
    unittest.main()
