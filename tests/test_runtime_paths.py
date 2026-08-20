import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import sau_runtime


class RuntimePathTests(unittest.TestCase):
    def test_macos_data_root_is_application_support(self):
        with patch.object(sau_runtime.platform, "system", return_value="Darwin"), \
             patch.dict(os.environ, {"HOME": "/Users/tester"}, clear=True):
            self.assertEqual(
                sau_runtime.resolve_data_root(),
                Path("/Users/tester/Library/Application Support/SocialAutoUpload"),
            )

    def test_windows_data_root_uses_localappdata(self):
        with patch.object(sau_runtime.platform, "system", return_value="Windows"), \
             patch.dict(os.environ, {"LOCALAPPDATA": r"C:\\Users\\tester\\AppData\\Local"}, clear=True):
            self.assertEqual(
                sau_runtime.resolve_data_root(),
                Path(r"C:\\Users\\tester\\AppData\\Local") / "SocialAutoUpload",
            )

    def test_explicit_home_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": tmp}, clear=False):
                self.assertEqual(sau_runtime.resolve_data_root(), Path(tmp).resolve())

    def test_get_runtime_paths_does_not_create_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": str(root)}, clear=False):
                paths = sau_runtime.get_runtime_paths(create=False)
            self.assertEqual(paths.data_root, root.resolve())
            self.assertFalse(root.exists())

    def test_scoped_runtime_paths_are_context_local_and_restore_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = sau_runtime.RuntimePaths(
                root / "resources",
                root / "first",
                root / "first/cookies",
                root / "first/profiles",
                root / "first/logs",
                root / "first/.sau_safety",
                root / "first/media",
                root / "first/db/database.db",
            )
            second = sau_runtime.RuntimePaths(
                root / "resources",
                root / "second",
                root / "second/cookies",
                root / "second/profiles",
                root / "second/logs",
                root / "second/.sau_safety",
                root / "second/media",
                root / "second/db/database.db",
            )
            barrier = threading.Barrier(2)
            seen = []

            def resolve(scoped):
                with sau_runtime.use_runtime_paths(scoped):
                    barrier.wait(timeout=2)
                    seen.append(sau_runtime.get_runtime_paths(create=False))

            threads = [
                threading.Thread(target=resolve, args=(first,)),
                threading.Thread(target=resolve, args=(second,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            self.assertCountEqual(seen, [first, second])
            self.assertNotIn(sau_runtime.get_runtime_paths(create=False), (first, second))
