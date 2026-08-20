import configparser
import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import sau_cli
from sau_runtime import RuntimePaths


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeResourceConsumerTests(unittest.TestCase):
    def test_bilibili_login_example_runs_cli_from_resource_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            resource_root = Path(tmp) / "resources"
            data_root = Path(tmp) / "data"
            conf = types.ModuleType("conf")
            conf.BASE_DIR = data_root
            conf.RESOURCE_DIR = resource_root

            with (
                patch.dict(sys.modules, {"conf": conf}),
                patch("subprocess.run") as run,
            ):
                runpy.run_path(
                    PROJECT_ROOT / "examples" / "get_bilibili_cookie.py",
                    run_name="__main__",
                )

            self.assertEqual(run.call_args.args[0][1], str(resource_root / "sau_cli.py"))

    def test_xhs_example_reads_bundled_accounts_from_resource_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            resource_root = Path(tmp) / "resources"
            data_root = Path(tmp) / "data"
            conf = types.ModuleType("conf")
            conf.BASE_DIR = data_root
            conf.RESOURCE_DIR = resource_root
            xhs = types.ModuleType("xhs")
            xhs.XhsClient = object
            files_times = types.ModuleType("utils.files_times")
            files_times.generate_schedule_time_next_day = object
            files_times.get_title_and_hashtags = object
            xhs_main = types.ModuleType("uploader.xhs_uploader.main")
            xhs_main.sign_local = object
            xhs_main.beauty_print = object

            with (
                patch.dict(
                    sys.modules,
                    {
                        "conf": conf,
                        "xhs": xhs,
                        "utils.files_times": files_times,
                        "uploader.xhs_uploader.main": xhs_main,
                    },
                ),
                patch.object(configparser.RawConfigParser, "read") as read,
            ):
                runpy.run_path(PROJECT_ROOT / "examples" / "upload_video_to_xhs.py")

            self.assertEqual(
                read.call_args.args[0],
                resource_root / "uploader" / "xhs_uploader" / "accounts.ini",
            )

    def test_cli_account_file_stays_under_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            resource_root = Path(tmp) / "resources"
            data_root = Path(tmp) / "data"
            data_root.mkdir()
            paths = RuntimePaths(
                resource_root=resource_root,
                data_root=data_root,
                cookies_dir=data_root / "cookies",
                profiles_dir=data_root / "profiles",
                logs_dir=data_root / "logs",
                safety_dir=data_root / ".sau_safety",
                media_dir=data_root / "media",
                database_file=data_root / "db" / "database.db",
            )

            with patch("sau_cli.get_runtime_paths", return_value=paths):
                account_file = sau_cli.resolve_account_file("bilibili", "creator")

            self.assertEqual(account_file, data_root / "cookies" / "bilibili_creator.json")
            self.assertTrue(account_file.parent.exists())
            self.assertFalse((resource_root / "cookies").exists())
