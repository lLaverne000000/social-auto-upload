"""Native smoke test for an installed combined macOS package."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Sequence


DEFAULT_APP = Path("/Applications/Social Auto Upload.app")
DEFAULT_CLI = Path("/usr/local/bin/sau")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_payload(app: Path, expected_arch: str) -> tuple[Path, Path]:
    actual_arch = {"aarch64": "arm64"}.get(platform.machine().casefold(), platform.machine().casefold())
    if actual_arch != expected_arch:
        raise RuntimeError(f"native architecture mismatch: expected {expected_arch}, found {actual_arch}")
    payload = app / "Contents" / "Resources" / "payloads" / expected_arch
    resource_root = payload / "_internal"
    manifest_path = resource_root / "browser-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"installed browser-manifest.json is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    browser = manifest.get("payloads", {}).get(f"darwin-{expected_arch}")
    if not isinstance(browser, dict):
        raise RuntimeError(f"installed browser manifest has no darwin-{expected_arch} payload")
    relative = browser.get("executable")
    expected_hash = browser.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_hash, str):
        raise RuntimeError("installed browser manifest is incomplete")
    executable = resource_root / relative
    try:
        executable.resolve(strict=True).relative_to(resource_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RuntimeError("installed browser executable escapes its payload") from exc
    if _sha256(executable) != expected_hash.casefold():
        raise RuntimeError("installed browser executable hash mismatch")
    return payload, executable


def _smoke_external_cli(cli: Path) -> None:
    mode = cli.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or not os.access(cli, os.X_OK):
        raise RuntimeError(f"installed external CLI is not a regular executable: {cli}")
    subprocess.run([str(cli), "--help"], check=True, timeout=30)


def _smoke_browser(executable: Path) -> None:
    from patchright.sync_api import sync_playwright

    with sync_playwright() as api:
        browser = api.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = browser.new_page()
            page.goto("data:text/html,<title>Installed SAU</title><p>installed-browser-ok</p>")
            if page.title() != "Installed SAU" or page.locator("p").inner_text() != "installed-browser-ok":
                raise RuntimeError("installed bundled Chromium returned unexpected content")
        finally:
            browser.close()


def _loopback_port(pid: int, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["lsof", "-Pan", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            text=True,
            capture_output=True,
            check=False,
        )
        match = re.search(r"127\.0\.0\.1:(\d+)", result.stdout)
        if match is not None:
            return int(match.group(1))
        time.sleep(0.1)
    raise RuntimeError("installed GUI did not expose a loopback listener")


def _smoke_gui(app: Path) -> None:
    launcher = app / "Contents" / "MacOS" / "launcher"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise RuntimeError(f"installed GUI launcher is missing: {launcher}")
    with tempfile.TemporaryFile(mode="w+b") as output:
        process = subprocess.Popen([str(launcher)], stdout=output, stderr=subprocess.STDOUT)
        try:
            port = _loopback_port(process.pid)
            base = f"http://127.0.0.1:{port}"
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )
            with opener.open(f"{base}/", timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError("installed GUI index health failed")
            with opener.open(f"{base}/api/v1/health", timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
            if health.get("ok") is not True or health.get("data", {}).get("status") != "ok":
                raise RuntimeError("installed GUI health response is invalid")
            request = urllib.request.Request(
                f"{base}/api/v1/app/quit",
                data=b"",
                headers={"Origin": base},
                method="POST",
            )
            with opener.open(request, timeout=5) as response:
                if response.status != 202:
                    raise RuntimeError("installed GUI protected quit was not accepted")
            if process.wait(timeout=20) != 0:
                raise RuntimeError("installed GUI exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def smoke_install(app: Path, cli: Path, expected_arch: str) -> None:
    _, browser_executable = _selected_payload(app, expected_arch)
    _smoke_external_cli(cli)
    _smoke_browser(browser_executable)
    _smoke_gui(app)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--expected-arch", choices=("x86_64", "arm64"), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    smoke_install(arguments.app, arguments.cli, arguments.expected_arch)
    print(f"installed macOS {arguments.expected_arch} CLI/GUI/browser smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
