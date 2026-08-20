import os
import tempfile
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
