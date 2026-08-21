import hashlib
import json
import os
import runpy
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release_tools.stage_browser import BrowserStagingError, stage_browser
from release_tools.verify_release import ReleaseVerificationError, verify_payload


def _write_macho(path: Path, cpu_type: int = 0x01000007) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, cpu_type, 3, 2, 0, 0, 0, 0))
    path.chmod(0o755)


def _make_release_payload(root: Path) -> Path:
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    executable = root / "browsers" / "chromium" / "Chrome.app" / "Contents" / "MacOS" / "Chrome"
    _write_macho(executable)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (root / "browser-manifest.json").write_text(
        json.dumps(
            {
                "revision": "1208",
                "payloads": {
                    "darwin-x86_64": {
                        "executable": executable.relative_to(root).as_posix(),
                        "sha256": digest,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return executable


class BrowserStagingTests(unittest.TestCase):
    def test_rejects_profile_state_at_any_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chromium-1208"
            _write_macho(source / "Chrome.app" / "Contents" / "MacOS" / "Chrome")
            (source / "nested" / "Default").mkdir(parents=True)
            with self.assertRaises(BrowserStagingError):
                stage_browser(source, root / "stage", "darwin", "x86_64", "1208")

    def test_rejects_source_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chromium-1208"
            _write_macho(source / "Chrome.app" / "Contents" / "MacOS" / "Chrome")
            (root / "outside").write_text("secret", encoding="utf-8")
            os.symlink(root / "outside", source / "escape")
            with self.assertRaises(BrowserStagingError):
                stage_browser(source, root / "stage", "darwin", "x86_64", "1208")

    def test_writes_runtime_compatible_manifest_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chromium-1208"
            executable = source / "Chrome.app" / "Contents" / "MacOS" / "Chrome"
            _write_macho(executable)
            (source / "framework").mkdir()
            (source / "framework" / "version-1").write_text("framework", encoding="utf-8")
            os.symlink("version-1", source / "framework" / "Current")
            manifest = stage_browser(source, root / "stage", "darwin", "x86_64", "1208")
            payload = manifest["payloads"]["darwin-x86_64"]
            staged_executable = root / "stage" / payload["executable"]
            self.assertTrue(staged_executable.is_file())
            self.assertEqual(hashlib.sha256(staged_executable.read_bytes()).hexdigest(), payload["sha256"])
            self.assertEqual(manifest, json.loads((root / "stage" / "browser-manifest.json").read_text()))
            self.assertTrue((root / "stage" / "browsers" / source.name / "framework" / "Current").is_symlink())


class ReleaseForbiddenPathTests(unittest.TestCase):
    def test_rejects_cookie_and_profile_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "cookies").mkdir()
            (root / "cookies" / "account.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")


class FrozenSpecificationTests(unittest.TestCase):
    def test_native_build_pins_the_locally_verified_opencv_release(self):
        metadata = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"opencv-python==4.12.0.88"', metadata)
        self.assertNotIn('"opencv-python>=', metadata)

    def test_dispatcher_selects_cli_or_gui_from_executable_name(self):
        import sau_frozen_entry

        calls = []

        def load(name):
            return SimpleNamespace(main=lambda: calls.append(name))

        with patch("sau_frozen_entry.importlib.import_module", side_effect=load), \
             patch.object(sys, "executable", "/payload/sau"):
            sau_frozen_entry.main()
        with patch("sau_frozen_entry.importlib.import_module", side_effect=load), \
             patch.object(sys, "executable", "/payload/SocialAutoUpload"):
            sau_frozen_entry.main()
        self.assertEqual(calls, ["sau_cli", "sau_desktop"])

    def test_spec_creates_console_and_windowed_executables_from_one_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frontend = root / "frontend"
            browser_stage = root / "browser-stage"
            frontend.mkdir()
            browser_stage.mkdir()
            (frontend / "index.html").write_text("ok", encoding="utf-8")
            (browser_stage / "browser-manifest.json").write_text("{}", encoding="utf-8")
            (browser_stage / "browsers").mkdir()

            calls = {"analysis": [], "pyz": [], "exe": [], "collect": []}

            def analysis(*args, **kwargs):
                result = SimpleNamespace(
                    scripts=[("sau_frozen_entry", "entry", "PYSOURCE")],
                    pure=[],
                    binaries=[
                        ("browsers/chromium/Chrome", "/source/Chrome", "BINARY"),
                        ("libpython.dylib", "/source/libpython.dylib", "BINARY"),
                    ],
                    datas=[],
                    zipped_data=[],
                )
                calls["analysis"].append((args, kwargs, result))
                return result

            def pyz(*args, **kwargs):
                result = object()
                calls["pyz"].append((args, kwargs, result))
                return result

            def exe(*args, **kwargs):
                result = SimpleNamespace(name=kwargs["name"])
                calls["exe"].append((args, kwargs, result))
                return result

            def collect(*args, **kwargs):
                calls["collect"].append((args, kwargs))
                return object()

            hooks = types.ModuleType("PyInstaller.utils.hooks")
            hooks.collect_all = lambda package: ([(package, package)], [], [f"{package}.hidden"])
            hooks.collect_submodules = lambda package: [f"{package}.submodule"]
            modules = {
                "PyInstaller": types.ModuleType("PyInstaller"),
                "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
                "PyInstaller.utils.hooks": hooks,
            }
            spec = Path(__file__).parents[1] / "packaging" / "pyinstaller" / "social_auto_upload.spec"
            environment = {
                "SAU_PROJECT_ROOT": str(Path(__file__).parents[1]),
                "SAU_FRONTEND_DIST": str(frontend),
                "SAU_BROWSER_STAGE": str(browser_stage),
            }
            with patch.dict(sys.modules, modules), patch.dict(os.environ, environment, clear=False):
                runpy.run_path(
                    str(spec),
                    init_globals={"Analysis": analysis, "PYZ": pyz, "EXE": exe, "COLLECT": collect},
                )

            self.assertEqual(len(calls["analysis"]), 1)
            self.assertEqual(len(calls["pyz"]), 1)
            self.assertEqual(len(calls["exe"]), 2)
            self.assertEqual(len(calls["collect"]), 1)
            first, second = calls["exe"]
            self.assertIs(first[0][0], second[0][0])
            self.assertEqual(
                [(call[1]["name"], call[1]["console"], call[1]["exclude_binaries"]) for call in calls["exe"]],
                [("sau", True, True), ("SocialAutoUpload", False, True)],
            )
            analysis_kwargs = calls["analysis"][0][1]
            destinations = {destination for _, destination in analysis_kwargs["datas"]}
            self.assertTrue({"frontend", ".", "browsers"}.issubset(destinations))
            hidden = set(analysis_kwargs["hiddenimports"])
            self.assertTrue(
                {"sau_cli", "sau_desktop", "sau_desktop_api", "sau_desktop_service"}.issubset(hidden)
            )
            analysis_result = calls["analysis"][0][2]
            self.assertIn(("browsers/chromium/Chrome", "/source/Chrome", "DATA"), analysis_result.datas)
            self.assertNotIn(("browsers/chromium/Chrome", "/source/Chrome", "BINARY"), analysis_result.binaries)
            self.assertIn(("libpython.dylib", "/source/libpython.dylib", "BINARY"), analysis_result.binaries)


class ReleaseVerificationTests(unittest.TestCase):
    def test_missing_frontend_fails_without_writing_release_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "frontend" / "index.html").unlink()
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            self.assertFalse((root / "release-manifest.json").exists())
            self.assertFalse((root / "SHA256SUMS").exists())

    def test_rejects_invalid_browser_manifest_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            manifest = json.loads((root / "browser-manifest.json").read_text())
            manifest["payloads"]["darwin-x86_64"]["sha256"] = "0" * 64
            (root / "browser-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_private_key_but_allows_public_ca_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "certificate.pem").write_text(
                "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
                encoding="utf-8",
            )
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_absolute_development_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "config.txt").write_text("/Users/laverneliu/private", encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            outside = root.parent / "outside-secret"
            outside.write_text("secret", encoding="utf-8")
            try:
                os.symlink(outside, root / "escape")
                with self.assertRaises(ReleaseVerificationError):
                    verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            finally:
                outside.unlink(missing_ok=True)

    def test_rejects_wrong_executable_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = _make_release_payload(root)
            _write_macho(executable, cpu_type=0x0100000C)
            manifest = json.loads((root / "browser-manifest.json").read_text())
            manifest["payloads"]["darwin-x86_64"]["sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
            (root / "browser-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_allows_safe_conf_and_writes_deterministic_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "certifi").mkdir()
            (root / "certifi" / "cacert.pem").write_text(
                "-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----\n",
                encoding="utf-8",
            )
            (root / "conf.py").write_text(
                'XHS_SERVER="http://127.0.0.1:11901"\nLOCAL_CHROME_PATH=""\nLOCAL_CHROME_HEADLESS=True\nDEBUG_MODE=True\nYT_PROXY=None\n',
                encoding="utf-8",
            )
            first = verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            sums_first = (root / "SHA256SUMS").read_bytes()
            manifest_first = (root / "release-manifest.json").read_bytes()
            second = verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            self.assertEqual(first, second)
            self.assertEqual(sums_first, (root / "SHA256SUMS").read_bytes())
            self.assertEqual(manifest_first, (root / "release-manifest.json").read_bytes())
            self.assertNotIn(b"SHA256SUMS", sums_first)
            self.assertNotIn(b"release-manifest.json", sums_first)

    def test_rejects_unsafe_conf_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "conf.py").write_text('LOCAL_CHROME_PATH="/Users/me/Chrome"\n', encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")


if __name__ == "__main__":
    unittest.main()
