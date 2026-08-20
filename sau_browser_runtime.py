"""Resolve the Chromium payload staged beside a frozen application."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from sau_runtime import RuntimePaths


MANIFEST_NAME = "browser-manifest.json"
_BROWSER_DIRECTORY = "browsers"


@dataclass(frozen=True, slots=True)
class BrowserPayload:
    revision: str
    executable: Path
    browser_root: Path | None


def _platform_key() -> str:
    system = platform.system().strip().lower()
    machine = platform.machine().strip().lower()
    system_name = {
        "darwin": "darwin",
        "windows": "windows",
        "linux": "linux",
    }.get(system, system)
    architecture = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    return f"{system_name}-{architecture}"


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: expected an object")
    return manifest


def _manifest_executable(resource_root: Path, executable: object) -> tuple[Path, Path | None]:
    if not isinstance(executable, str) or not executable:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: executable is required")

    relative_path = Path(executable)
    windows_path = PureWindowsPath(executable)
    if relative_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: executable path must be relative")

    resolved_root = resource_root.resolve()
    executable_path = resource_root / relative_path
    resolved_executable = executable_path.resolve()
    try:
        resolved_executable.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: executable path escapes resource root") from exc

    # The packaging layout explicitly stages browser payloads under this directory.
    # Only then is it valid to configure Playwright's browser-directory variable.
    parts = relative_path.parts
    browser_root = resource_root / _BROWSER_DIRECTORY if parts and parts[0] == _BROWSER_DIRECTORY else None
    return executable_path, browser_root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as payload_file:
        for chunk in iter(lambda: payload_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_bundled_browser(paths: RuntimePaths, required: bool) -> BrowserPayload | None:
    """Validate and return the staged browser selected for this host platform.

    Non-frozen source checkouts may omit the manifest and use Patchright's normal
    installed-browser behavior. A packaged application always requires a complete,
    integrity-checked payload and never triggers a browser download here.
    """
    manifest_path = paths.resource_root / MANIFEST_NAME
    if not manifest_path.is_file():
        if required:
            raise RuntimeError(f"Missing required {MANIFEST_NAME}: {manifest_path}")
        return None

    manifest = _read_manifest(manifest_path)
    revision = manifest.get("revision")
    payloads = manifest.get("payloads")
    if not isinstance(revision, str) or not revision:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: revision is required")
    if not isinstance(payloads, dict):
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: payloads is required")

    target = _platform_key()
    payload = payloads.get(target)
    if not isinstance(payload, dict):
        raise RuntimeError(f"No browser payload for target {target} in {MANIFEST_NAME}")

    executable, browser_root = _manifest_executable(paths.resource_root, payload.get("executable"))
    expected_sha256 = payload.get("sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: SHA-256 is required for target {target}")
    try:
        int(expected_sha256, 16)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {MANIFEST_NAME}: SHA-256 is invalid for target {target}") from exc

    if not executable.is_file():
        raise RuntimeError(f"Bundled browser executable is missing: {executable}")
    if _sha256(executable) != expected_sha256.lower():
        raise RuntimeError(f"Bundled browser SHA-256 mismatch: {executable}")
    if browser_root is not None and not browser_root.is_dir():
        raise RuntimeError(f"Bundled browser root is missing: {browser_root}")

    return BrowserPayload(
        revision=revision,
        executable=executable,
        browser_root=browser_root,
    )


def configure_browser_environment(paths: RuntimePaths, required: bool) -> BrowserPayload | None:
    """Expose a verified staged Chromium to uploader launch helpers."""
    payload = resolve_bundled_browser(paths, required)
    if payload is None:
        return None

    os.environ["SAU_CHROMIUM_EXECUTABLE"] = str(payload.executable)
    if payload.browser_root is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(payload.browser_root)
    return payload
