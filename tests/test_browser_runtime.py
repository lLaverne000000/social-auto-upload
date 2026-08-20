import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sau_runtime import RuntimePaths
import sau_browser_runtime


class BrowserRuntimeTests(unittest.TestCase):
    def test_packaging_metadata_assertion_is_python_310_compatible(self):
        test_source = Path(__file__).read_text(encoding="utf-8")

        self.assertNotIn("import " + "toml" + "lib", test_source)

    def test_packaging_metadata_uses_sau_cli_and_not_stale_cli_main(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        match = re.search(
            r"^py-modules\s*=\s*\[([^]]*)\]",
            pyproject.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        modules = re.findall(r'"([^"]+)"', match.group(1))

        self.assertIn("sau_cli", modules)
        self.assertNotIn("cli_main", modules)

    def make_paths(self, root: Path) -> RuntimePaths:
        return RuntimePaths(
            root, root / "data", root / "data/cookies", root / "data/profiles",
            root / "data/logs", root / "data/.sau_safety", root / "data/media",
            root / "data/db/database.db",
        )

    @staticmethod
    def write_payload(root: Path, *, executable: str = "browsers/darwin-x86_64/chrome", contents: bytes = b"browser") -> Path:
        executable_path = root / executable
        executable_path.parent.mkdir(parents=True, exist_ok=True)
        executable_path.write_bytes(contents)
        (root / "browser-manifest.json").write_text(json.dumps({
            "revision": "1208",
            "payloads": {
                "darwin-x86_64": {
                    "executable": executable,
                    "sha256": hashlib.sha256(contents).hexdigest(),
                }
            },
        }), encoding="utf-8")
        return executable_path

    def test_resolves_matching_payload_and_sets_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = self.write_payload(root)
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"), \
                 patch.dict(os.environ, {}, clear=True):
                payload = sau_browser_runtime.configure_browser_environment(
                    self.make_paths(root), required=True
                )
                self.assertEqual(os.environ["SAU_CHROMIUM_EXECUTABLE"], str(executable))
                self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], str(root / "browsers"))
            self.assertEqual(payload.executable, executable)

    def test_frozen_mode_fails_closed_when_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "browser-manifest.json"):
                sau_browser_runtime.resolve_bundled_browser(
                    self.make_paths(Path(tmp)), required=True
                )

    def test_source_mode_allows_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(sau_browser_runtime.resolve_bundled_browser(
                self.make_paths(Path(tmp)), required=False
            ))

    def test_required_mode_rejects_missing_target_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_payload(root)
            with patch.object(sau_browser_runtime.platform, "system", return_value="Windows"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="AMD64"):
                with self.assertRaisesRegex(RuntimeError, "windows-x86_64"):
                    sau_browser_runtime.resolve_bundled_browser(self.make_paths(root), required=True)

    def test_rejects_executable_path_that_escapes_resource_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_payload(root, executable="browsers/darwin-x86_64/chrome")
            manifest_path = root / "browser-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["payloads"]["darwin-x86_64"]["executable"] = "../chrome"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(RuntimeError, "escape"):
                    sau_browser_runtime.resolve_bundled_browser(self.make_paths(root), required=True)

    def test_rejects_windows_drive_relative_executable_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_payload(root, executable="C:chrome")
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(RuntimeError, "relative"):
                    sau_browser_runtime.resolve_bundled_browser(self.make_paths(root), required=True)

    def test_rejects_missing_executable_and_sha256_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = self.write_payload(root)
            executable.unlink()
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(RuntimeError, "executable"):
                    sau_browser_runtime.resolve_bundled_browser(self.make_paths(root), required=True)

            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"tampered")
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    sau_browser_runtime.resolve_bundled_browser(self.make_paths(root), required=True)
