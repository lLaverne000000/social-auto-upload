import hashlib
import json
import os
import plistlib
import runpy
import shutil
import socket
import struct
import subprocess
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


def _make_release_payload(
    root: Path,
    *,
    cpu_type: int = 0x01000007,
    arch: str = "x86_64",
) -> Path:
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    executable = root / "browsers" / "chromium" / "Chrome.app" / "Contents" / "MacOS" / "Chrome"
    _write_macho(executable, cpu_type=cpu_type)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (root / "browser-manifest.json").write_text(
        json.dumps(
            {
                "revision": "1208",
                "payloads": {
                    f"darwin-{arch}": {
                        "executable": executable.relative_to(root).as_posix(),
                        "sha256": digest,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _write_macho(root / "sau", cpu_type=cpu_type)
    _write_macho(root / "SocialAutoUpload", cpu_type=cpu_type)
    return executable


def _make_browser_source(root: Path) -> Path:
    source = root / "chromium-1208"
    _write_macho(source / "Chrome.app" / "Contents" / "MacOS" / "Chrome")
    return source


def _safe_conf_text() -> str:
    return (
        "from sau_runtime import get_runtime_paths\n\n"
        "_RUNTIME_PATHS = get_runtime_paths()\n"
        "BASE_DIR = _RUNTIME_PATHS.data_root\n"
        "RESOURCE_DIR = _RUNTIME_PATHS.resource_root\n\n"
        'XHS_SERVER = "http://127.0.0.1:11901"\n'
        'LOCAL_CHROME_PATH = ""\n'
        "LOCAL_CHROME_HEADLESS = True\n"
        "DEBUG_MODE = True\n"
        "YT_PROXY = None\n"
    )


def _bind_unix_socket(path: Path) -> socket.socket:
    handle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    handle.bind(str(path))
    return handle


class BrowserStagingTests(unittest.TestCase):
    def test_rejects_secret_names_and_private_key_material(self):
        names_and_content = {
            ".env": "TOKEN=secret",
            "id_rsa": "secret",
            "id_ed25519": "secret",
            "credentials.json": "{}",
            "secrets.json": "{}",
            "client.key": "secret",
            "certificate.pem": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
        }
        for name, content in names_and_content.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = _make_browser_source(root)
                (source / name).write_text(content, encoding="utf-8")
                with self.assertRaises(BrowserStagingError):
                    stage_browser(source, root / "stage", "darwin", "x86_64", "1208")

    def test_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _make_browser_source(root)
            original = source / "original.dat"
            original.write_text("same inode", encoding="utf-8")
            os.link(original, source / "duplicate.dat")
            with self.assertRaises(BrowserStagingError):
                stage_browser(source, root / "stage", "darwin", "x86_64", "1208")

    def test_rejects_fifo_socket_and_other_special_entries(self):
        factories = {
            "fifo": lambda path: os.mkfifo(path),
            "socket": lambda path: _bind_unix_socket(path),
        }
        for kind, factory in factories.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = _make_browser_source(root)
                handle = factory(source / f"special-{kind}")
                try:
                    with self.assertRaises(BrowserStagingError):
                        stage_browser(source, root / "stage", "darwin", "x86_64", "1208")
                finally:
                    if handle is not None:
                        handle.close()

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
    def test_requires_both_regular_non_symlink_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "SocialAutoUpload").unlink()
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "SocialAutoUpload").unlink()
            os.symlink("sau", root / "SocialAutoUpload")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_post_stage_browser_profile_state(self):
        for name in ("Default", "Local State", "History", "Preferences"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_release_payload(root)
                state = root / "browsers" / "chromium" / name
                state.parent.mkdir(parents=True, exist_ok=True)
                state.write_text("state", encoding="utf-8")
                with self.assertRaises(ReleaseVerificationError):
                    verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            original = root / "first.txt"
            original.write_text("same inode", encoding="utf-8")
            os.link(original, root / "second.txt")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_special_entry_without_emitting_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            os.mkfifo(root / "named-pipe")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            self.assertFalse((root / "release-manifest.json").exists())
            self.assertFalse((root / "SHA256SUMS").exists())

    def test_conf_rejects_unknown_assignments_imports_calls_and_statements(self):
        unsafe_lines = (
            'API_TOKEN = "secret"',
            "import os",
            "print('side effect')",
            "if True:\n    DEBUG_MODE = True",
        )
        for unsafe in unsafe_lines:
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_release_payload(root)
                (root / "conf.py").write_text(_safe_conf_text() + unsafe + "\n", encoding="utf-8")
                with self.assertRaises(ReleaseVerificationError):
                    verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_checksum_unsafe_paths(self):
        for name in ("line\nbreak.txt", "carriage\rreturn.txt", "escape\\name.txt", "control\x01name.txt"):
            with self.subTest(name=repr(name)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _make_release_payload(root)
                (root / name).write_text("unsafe", encoding="utf-8")
                with self.assertRaises(ReleaseVerificationError):
                    verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_manifest_covers_all_contained_symlinks_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_release_payload(root)
            (root / "library").mkdir()
            (root / "library" / "version-1").write_text("payload", encoding="utf-8")
            (root / "target-dir").mkdir()
            os.symlink("version-1", root / "library" / "Current")
            os.symlink("target-dir", root / "CurrentDir")
            first = verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            first_bytes = (root / "release-manifest.json").read_bytes()
            second = verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, (root / "release-manifest.json").read_bytes())
            self.assertEqual(first["symlink_count"], 2)
            self.assertEqual(
                [(item["path"], item["target"]) for item in first["symlinks"]],
                [("CurrentDir", "target-dir"), ("library/Current", "version-1")],
            )
            self.assertTrue(all(len(item["sha256"]) == 64 for item in first["symlinks"]))

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
                _safe_conf_text(),
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


class MacOSPackagingTests(unittest.TestCase):
    launcher_source = Path(__file__).parents[1] / "packaging" / "macos" / "launcher"
    build_script = Path(__file__).parents[1] / "packaging" / "macos" / "build_pkg.sh"

    def _make_launcher_fixture(self, root: Path) -> Path:
        self.assertTrue(self.launcher_source.is_file(), "macOS launcher must exist")
        macos = root / "App With Spaces.app" / "Contents" / "MacOS"
        payloads = root / "App With Spaces.app" / "Contents" / "Resources" / "payloads"
        macos.mkdir(parents=True)
        for arch in ("x86_64", "arm64"):
            payload = payloads / arch
            payload.mkdir(parents=True)
            for name, label in (("SocialAutoUpload", "gui"), ("sau", "cli")):
                executable = payload / name
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' '{label}-{arch}'\n"
                    "printf '%s\\n' \"$@\"\n"
                    "if [ \"${SAU_TEST_EXIT_CODE:-0}\" -ne 0 ]; then\n"
                    "    exit \"$SAU_TEST_EXIT_CODE\"\n"
                    "fi\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
        shutil.copy2(self.launcher_source, macos / "launcher")
        shutil.copy2(self.launcher_source, macos / "sau")
        return macos

    def _launcher_environment(self, root: Path, machine: str) -> dict[str, str]:
        tools = root / f"tools-{machine}"
        tools.mkdir(exist_ok=True)
        uname = tools / "uname"
        uname.write_text(f"#!/bin/sh\nprintf '%s\\n' '{machine}'\n", encoding="utf-8")
        uname.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{tools}:/usr/bin:/bin"
        return environment

    def _make_native_payloads(self, root: Path) -> tuple[Path, Path]:
        x86 = root / "payload x86_64"
        arm = root / "payload arm64"
        _make_release_payload(x86)
        _make_release_payload(arm, cpu_type=0x0100000C, arch="arm64")
        return x86, arm

    def _build_and_expand_native_fixture(self, root: Path) -> tuple[Path, Path]:
        x86, arm = self._make_native_payloads(root)
        output = root / "release"
        build = subprocess.run(
            ["bash", str(self.build_script), str(x86), str(arm), str(output)],
            text=True,
            capture_output=True,
            env={**os.environ, "SAU_PYTHON": sys.executable},
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        package = output / "SocialAutoUpload-macOS-Universal.pkg"
        expanded = root / "expanded"
        inspect = subprocess.run(
            ["pkgutil", "--expand-full", str(package), str(expanded)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        postinstalls = list(expanded.rglob("postinstall"))
        self.assertEqual(len(postinstalls), 1)
        return package, postinstalls[0]

    def test_macos_launcher_selects_exact_gui_and_cli_payload_for_runtime_arch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macos = self._make_launcher_fixture(root)
            for machine in ("x86_64", "arm64"):
                for entrypoint, label in (("launcher", "gui"), ("sau", "cli")):
                    with self.subTest(machine=machine, entrypoint=entrypoint):
                        result = subprocess.run(
                            [str(macos / entrypoint), "argument with spaces"],
                            text=True,
                            capture_output=True,
                            env=self._launcher_environment(root, machine),
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.splitlines(), [f"{label}-{machine}", "argument with spaces"])

    def test_macos_launcher_rejects_unknown_architecture_without_running_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macos = self._make_launcher_fixture(root)
            result = subprocess.run(
                [str(macos / "launcher")],
                text=True,
                capture_output=True,
                env=self._launcher_environment(root, "riscv64"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported", result.stderr.casefold())
            self.assertEqual(result.stdout, "")

    def test_macos_cli_symlink_invocation_resolves_app_launcher_and_preserves_argv_and_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            macos = self._make_launcher_fixture(root)
            installed_bin = root / "usr/local/bin"
            installed_bin.mkdir(parents=True)
            installed_cli = installed_bin / "sau"
            installed_cli.symlink_to(macos / "sau")
            environment = self._launcher_environment(root, "x86_64")
            environment["SAU_TEST_EXIT_CODE"] = "37"
            result = subprocess.run(
                [str(installed_cli), "first argument", "second"],
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 37, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["cli-x86_64", "first argument", "second"],
            )

    def test_macos_launcher_does_not_use_eval_or_arch_translation(self):
        self.assertTrue(self.launcher_source.is_file(), "macOS launcher must exist")
        source = self.launcher_source.read_text(encoding="utf-8")
        self.assertNotIn("eval ", source)
        self.assertNotIn("arch -x86_64", source)
        self.assertNotIn("arch -arm64", source)

    def test_macos_package_requires_both_payloads(self):
        self.assertTrue(self.build_script.is_file(), "macOS package builder must exist")
        result = subprocess.run(
            ["bash", str(self.build_script), "--check-inputs", "/missing/x86_64", "/missing/arm64"],
            text=True,
            capture_output=True,
            env={**os.environ, "SAU_PYTHON": sys.executable},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both", result.stderr.casefold())

    def test_macos_package_reverifies_both_declared_native_architectures(self):
        self.assertTrue(self.build_script.is_file(), "macOS package builder must exist")
        with tempfile.TemporaryDirectory() as tmp:
            x86, arm = self._make_native_payloads(Path(tmp))
            result = subprocess.run(
                ["bash", str(self.build_script), "--check-inputs", str(x86), str(arm)],
                text=True,
                capture_output=True,
                env={**os.environ, "SAU_PYTHON": sys.executable},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("both", result.stdout.casefold())
            self.assertEqual(json.loads((x86 / "release-manifest.json").read_text())["arch"], "x86_64")
            self.assertEqual(json.loads((arm / "release-manifest.json").read_text())["arch"], "arm64")

    def test_macos_package_rejects_payloads_in_swapped_architecture_slots(self):
        self.assertTrue(self.build_script.is_file(), "macOS package builder must exist")
        with tempfile.TemporaryDirectory() as tmp:
            x86, arm = self._make_native_payloads(Path(tmp))
            result = subprocess.run(
                ["bash", str(self.build_script), "--check-inputs", str(arm), str(x86)],
                text=True,
                capture_output=True,
                env={**os.environ, "SAU_PYTHON": sys.executable},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("architecture", result.stderr.casefold())

    @unittest.skipUnless(
        sys.platform == "darwin"
        and shutil.which("pkgbuild")
        and shutil.which("productbuild")
        and shutil.which("pkgutil"),
        "native macOS package inspection requires pkgbuild/productbuild/pkgutil",
    )
    def test_macos_package_contains_fixed_app_layout_and_conditional_cli_install(self):
        self.assertTrue(self.build_script.is_file(), "macOS package builder must exist")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            x86, arm = self._make_native_payloads(root)
            output = root / "release with spaces"
            result = subprocess.run(
                ["bash", str(self.build_script), str(x86), str(arm), str(output)],
                text=True,
                capture_output=True,
                env={**os.environ, "SAU_PYTHON": sys.executable},
                preexec_fn=lambda: os.umask(0o077),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            package = output / "SocialAutoUpload-macOS-Universal.pkg"
            self.assertTrue(package.is_file())
            self.assertEqual([item.name for item in output.iterdir()], [package.name])

            expanded = root / "expanded"
            inspect_result = subprocess.run(
                ["pkgutil", "--expand-full", str(package), str(expanded)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(inspect_result.returncode, 0, inspect_result.stderr)
            apps = list(expanded.rglob("Social Auto Upload.app"))
            self.assertEqual(len(apps), 1)
            app = apps[0]
            self.assertEqual(app.stat().st_mode & 0o777, 0o755)
            self.assertTrue((app / "Contents/MacOS/launcher").is_file())
            self.assertTrue((app / "Contents/MacOS/sau").is_file())
            self.assertTrue((app / "Contents/Resources/payloads/x86_64/SocialAutoUpload").is_file())
            self.assertTrue((app / "Contents/Resources/payloads/arm64/SocialAutoUpload").is_file())
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual((app / "Contents/Info.plist").stat().st_mode & 0o777, 0o644)
            self.assertEqual(info["CFBundleExecutable"], "launcher")
            self.assertEqual(info["CFBundleIdentifier"], "com.socialautoupload.desktop")

            postinstalls = list(expanded.rglob("postinstall"))
            self.assertEqual(len(postinstalls), 1)
            postinstall = postinstalls[0]
            without_bin = root / "target without bin"
            without_bin.mkdir()
            no_bin_result = subprocess.run(
                [str(postinstall), "package", "1.0", str(without_bin)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(no_bin_result.returncode, 0, no_bin_result.stderr)
            self.assertFalse((without_bin / "usr").exists())

            with_bin = root / "target with bin"
            (with_bin / "usr/local/bin").mkdir(parents=True)
            cli_result = subprocess.run(
                [str(postinstall), "package", "1.0", str(with_bin)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            installed_cli = with_bin / "usr/local/bin/sau"
            self.assertTrue(installed_cli.is_file())
            self.assertFalse(installed_cli.is_symlink())
            self.assertTrue(os.access(installed_cli, os.X_OK))
            self.assertEqual(
                installed_cli.read_text(encoding="utf-8"),
                "#!/bin/sh\n"
                'exec "/Applications/Social Auto Upload.app/Contents/MacOS/sau" "$@"\n',
            )

    @unittest.skipUnless(
        sys.platform == "darwin"
        and shutil.which("pkgbuild")
        and shutil.which("productbuild")
        and shutil.which("pkgutil"),
        "native macOS package inspection requires pkgbuild/productbuild/pkgutil",
    )
    def test_macos_postinstall_never_follows_target_components_or_replaces_foreign_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, postinstall = self._build_and_expand_native_fixture(root)

            for component in ("usr", "usr/local", "usr/local/bin"):
                with self.subTest(component=component):
                    suffix = component.replace("/", "-")
                    target = root / f"target-{suffix}"
                    outside = root / f"outside-{suffix}"
                    target.mkdir()
                    if component == "usr":
                        (outside / "local/bin").mkdir(parents=True)
                        (target / "usr").symlink_to(outside, target_is_directory=True)
                        escaped_cli = outside / "local/bin/sau"
                    elif component == "usr/local":
                        (target / "usr").mkdir()
                        (outside / "bin").mkdir(parents=True)
                        (target / "usr/local").symlink_to(outside, target_is_directory=True)
                        escaped_cli = outside / "bin/sau"
                    else:
                        (target / "usr/local").mkdir(parents=True)
                        outside.mkdir()
                        (target / "usr/local/bin").symlink_to(outside, target_is_directory=True)
                        escaped_cli = outside / "sau"
                    result = subprocess.run(
                        [str(postinstall), "package", "1.0", str(target)],
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(escaped_cli.exists())
                    self.assertFalse(escaped_cli.is_symlink())

            expected_self_target = "/Applications/Social Auto Upload.app/Contents/MacOS/sau"
            link_cases = (
                ("foreign-existing", str(root / "foreign-target")),
                ("foreign-broken", "/not/a/social-auto-upload/target"),
                ("expected-self", expected_self_target),
            )
            (root / "foreign-target").write_text("foreign-content", encoding="utf-8")
            for name, link_target in link_cases:
                with self.subTest(existing_link=name):
                    target = root / f"target-link-{name}"
                    cli_directory = target / "usr/local/bin"
                    cli_directory.mkdir(parents=True)
                    cli = cli_directory / "sau"
                    cli.symlink_to(link_target)
                    result = subprocess.run(
                        [str(postinstall), "package", "1.0", str(target)],
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(cli.is_symlink())
                    self.assertEqual(os.readlink(cli), link_target)
            self.assertEqual((root / "foreign-target").read_text(encoding="utf-8"), "foreign-content")

            target = root / "target-regular"
            cli_directory = target / "usr/local/bin"
            cli_directory.mkdir(parents=True)
            cli = cli_directory / "sau"
            cli.write_text("existing-command", encoding="utf-8")
            result = subprocess.run(
                [str(postinstall), "package", "1.0", str(target)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cli.read_text(encoding="utf-8"), "existing-command")

    @unittest.skipUnless(
        sys.platform == "darwin"
        and shutil.which("ditto")
        and shutil.which("pkgbuild")
        and shutil.which("productbuild"),
        "native macOS package assembly requires ditto/pkgbuild/productbuild",
    )
    def test_macos_package_output_is_atomic_and_failed_build_preserves_existing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            x86, arm = self._make_native_payloads(root)
            output = root / "release"
            output.mkdir()
            package = output / "SocialAutoUpload-macOS-Universal.pkg"
            sentinel = b"previous-good-package"
            package.write_bytes(sentinel)

            fake_tools = root / "fake-tools"
            fake_tools.mkdir()
            fake_productbuild = fake_tools / "productbuild"
            fake_productbuild.write_text(
                "#!/bin/sh\n"
                "for output do :; done\n"
                "printf '%s' 'partial-broken-package' >\"$output\"\n"
                "exit 23\n",
                encoding="utf-8",
            )
            fake_productbuild.chmod(0o755)
            failed = subprocess.run(
                ["bash", str(self.build_script), str(x86), str(arm), str(output)],
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{fake_tools}:{os.environ['PATH']}",
                    "SAU_PYTHON": sys.executable,
                },
            )
            self.assertEqual(failed.returncode, 23)
            self.assertEqual(package.read_bytes(), sentinel)
            self.assertEqual(list(output.glob(".SocialAutoUpload-macOS-Universal.*")), [])

            fake_productbuild.write_text(
                "#!/bin/sh\n"
                "for output do :; done\n"
                "ln -s '/tmp/not-a-package' \"$output\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_productbuild.chmod(0o755)
            symlink_result = subprocess.run(
                ["bash", str(self.build_script), str(x86), str(arm), str(output)],
                text=True,
                capture_output=True,
                env={
                    **os.environ,
                    "PATH": f"{fake_tools}:{os.environ['PATH']}",
                    "SAU_PYTHON": sys.executable,
                },
            )
            self.assertNotEqual(symlink_result.returncode, 0)
            self.assertIn("regular package", symlink_result.stderr)
            self.assertEqual(package.read_bytes(), sentinel)
            self.assertEqual(list(output.glob(".SocialAutoUpload-macOS-Universal.*")), [])

            succeeded = subprocess.run(
                ["bash", str(self.build_script), str(x86), str(arm), str(output)],
                text=True,
                capture_output=True,
                env={**os.environ, "SAU_PYTHON": sys.executable},
            )
            self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
            self.assertTrue(package.is_file())
            self.assertFalse(package.is_symlink())
            self.assertNotEqual(package.read_bytes(), sentinel)
            self.assertEqual(list(output.glob(".SocialAutoUpload-macOS-Universal.*")), [])


if __name__ == "__main__":
    unittest.main()
