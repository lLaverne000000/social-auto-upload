"""Fail-closed verifier for frozen Social Auto Upload payloads."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Sequence

from release_tools.stage_browser import BrowserStagingError, executable_architecture


class ReleaseVerificationError(RuntimeError):
    pass


_OUTPUT_FILES = {"release-manifest.json", "SHA256SUMS"}
_FORBIDDEN_COMPONENTS = {
    "default",
    "local state",
    "history",
    "preferences",
    "cookies",
    "cookiesfile",
    "profiles",
    ".sau_safety",
    "videofile",
    "logs",
    "media",
    "db",
}
_SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
_SECRET_SUFFIXES = {".key", ".p12", ".pfx"}
_TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".html", ".ini", ".js", ".json", ".map",
    ".md", ".pem", ".plist", ".py", ".toml", ".txt", ".yaml", ".yml",
}
_DEVELOPMENT_PATHS = (
    re.compile(rb"/Users/(?P<user>[A-Za-z0-9._-]+)/"),
    re.compile(rb"/home/(?P<user>[A-Za-z0-9._-]+)/"),
    re.compile(rb"[A-Za-z]:[\\/]Users[\\/](?P<user>[^\\/\x00]+)[\\/]"),
)
_DOCUMENTATION_USERS = {b"example", b"me", b"test", b"user", b"username"}
_SAFE_CONF_SOURCE = """from sau_runtime import get_runtime_paths

_RUNTIME_PATHS = get_runtime_paths()
BASE_DIR = _RUNTIME_PATHS.data_root
RESOURCE_DIR = _RUNTIME_PATHS.resource_root

XHS_SERVER = "http://127.0.0.1:11901"
LOCAL_CHROME_PATH = ""
LOCAL_CHROME_HEADLESS = True
DEBUG_MODE = True
YT_PROXY = None
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as payload:
        for chunk in iter(lambda: payload.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_key(platform_name: str, arch: str) -> tuple[str, str, str]:
    system = {"macos": "darwin", "win32": "windows"}.get(platform_name.casefold(), platform_name.casefold())
    machine = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(arch.casefold(), arch.casefold())
    if system not in {"darwin", "windows", "linux"} or machine not in {"x86_64", "arm64"}:
        raise ReleaseVerificationError(f"Unsupported release target: {platform_name}-{arch}")
    return system, machine, f"{system}-{machine}"


def _checksum_safe_text(value: str) -> bool:
    return "\\" not in value and all(
        unicodedata.category(character) not in {"Cc", "Cs"}
        for character in value
    )


def _symlink_record(path: Path, root: Path) -> dict[str, str]:
    relative = path.relative_to(root).as_posix()
    target = os.readlink(path)
    if not _checksum_safe_text(target):
        raise ReleaseVerificationError(f"Checksum-unsafe symlink target: {relative}")
    digest = hashlib.sha256()
    digest.update(b"SYMLINK\0")
    digest.update(os.fsencode(relative))
    digest.update(b"\0")
    digest.update(os.fsencode(target))
    return {"path": relative, "target": target, "sha256": digest.hexdigest()}


def _walk_payload(root: Path) -> tuple[list[Path], list[dict[str, str]]]:
    resolved_root = root.resolve(strict=True)
    files: list[Path] = []
    symlinks: list[dict[str, str]] = []
    for current, directories, names in os.walk(root, followlinks=False):
        for name in [*directories, *names]:
            path = Path(current) / name
            relative = path.relative_to(root)
            relative_text = relative.as_posix()
            if not _checksum_safe_text(relative_text):
                raise ReleaseVerificationError(f"Checksum-unsafe release path: {relative!s}")
            if any(part.casefold() in _FORBIDDEN_COMPONENTS for part in relative.parts):
                raise ReleaseVerificationError(f"Forbidden runtime/user-data path: {relative}")
            lowered = name.casefold()
            if lowered in _SECRET_NAMES or Path(lowered).suffix in _SECRET_SUFFIXES:
                raise ReleaseVerificationError(f"Forbidden secret file: {relative}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                try:
                    path.resolve(strict=True).relative_to(resolved_root)
                except (OSError, ValueError) as exc:
                    raise ReleaseVerificationError(f"Symlink escapes release payload: {relative}") from exc
                symlinks.append(_symlink_record(path, root))
            elif stat.S_ISDIR(mode):
                continue
            elif stat.S_ISREG(mode):
                if path.lstat().st_nlink != 1:
                    raise ReleaseVerificationError(f"Hard-linked file is forbidden: {relative}")
                files.append(path)
            else:
                raise ReleaseVerificationError(f"Special filesystem entry is forbidden: {relative}")
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    symlinks.sort(key=lambda item: item["path"])
    return files, symlinks


def _verify_conf(path: Path) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ReleaseVerificationError(f"Invalid conf.py: {path}") from exc
    safe_tree = ast.parse(_SAFE_CONF_SOURCE, filename="safe-conf.py")
    if ast.dump(tree, include_attributes=False) != ast.dump(safe_tree, include_attributes=False):
        raise ReleaseVerificationError(f"Unsafe or unrecognized conf.py statement: {path}")


def _scan_text_files(files: Iterable[Path], root: Path) -> None:
    for path in files:
        if path.name == "conf.py":
            _verify_conf(path)
        if path.suffix.casefold() not in _TEXT_SUFFIXES or path.stat().st_size > 16 * 1024 * 1024:
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ReleaseVerificationError(f"Cannot inspect release file: {path.relative_to(root)}") from exc
        if any(
            match.group("user").lower() not in _DOCUMENTATION_USERS
            for pattern in _DEVELOPMENT_PATHS
            for match in pattern.finditer(content)
        ):
            raise ReleaseVerificationError(f"Absolute development path leaked: {path.relative_to(root)}")
        if b"-----BEGIN " in content and b"PRIVATE KEY-----" in content:
            raise ReleaseVerificationError(f"Private key leaked: {path.relative_to(root)}")


def _resource_root(root: Path) -> Path:
    direct = root / "browser-manifest.json"
    internal = root / "_internal" / "browser-manifest.json"
    if direct.is_file():
        return root
    if internal.is_file():
        return root / "_internal"
    raise ReleaseVerificationError("Missing browser-manifest.json")


def _safe_relative_path(resource_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ReleaseVerificationError("Browser manifest executable is required")
    relative = Path(value)
    windows = PureWindowsPath(value)
    if relative.is_absolute() or windows.is_absolute() or windows.drive or ".." in relative.parts:
        raise ReleaseVerificationError("Browser manifest executable must be a contained relative path")
    candidate = resource_root / relative
    try:
        candidate.resolve(strict=True).relative_to(resource_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReleaseVerificationError("Browser manifest executable escapes payload") from exc
    return candidate


def _verify_browser_manifest(resource_root: Path, platform_name: str, arch: str, key: str) -> dict[str, Any]:
    manifest_path = resource_root / "browser-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError("Invalid browser-manifest.json") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("revision"), str) or not manifest["revision"]:
        raise ReleaseVerificationError("Browser manifest revision is required")
    payloads = manifest.get("payloads")
    payload = payloads.get(key) if isinstance(payloads, dict) else None
    if not isinstance(payload, dict):
        raise ReleaseVerificationError(f"Browser manifest has no payload for {key}")
    executable = _safe_relative_path(resource_root, payload.get("executable"))
    if not executable.is_file():
        raise ReleaseVerificationError("Bundled browser executable is missing")
    expected_hash = payload.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ReleaseVerificationError("Browser manifest SHA-256 is invalid")
    if _sha256(executable) != expected_hash.casefold():
        raise ReleaseVerificationError("Bundled browser SHA-256 mismatch")
    try:
        actual_arch = executable_architecture(executable, platform_name)
    except BrowserStagingError as exc:
        raise ReleaseVerificationError(str(exc)) from exc
    if actual_arch != arch:
        raise ReleaseVerificationError(f"Browser architecture mismatch: expected {arch}, found {actual_arch}")
    return manifest


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def verify_payload(
    root: Path | str,
    *,
    expected_platform: str,
    expected_arch: str,
) -> dict[str, Any]:
    root_path = Path(root).expanduser()
    if not root_path.is_dir() or root_path.is_symlink():
        raise ReleaseVerificationError(f"Release payload root is not a directory: {root_path}")
    platform_name, arch, target_key = _target_key(expected_platform, expected_arch)
    files, symlinks = _walk_payload(root_path)
    _scan_text_files(files, root_path)
    resource_root = _resource_root(root_path)
    if not (resource_root / "frontend" / "index.html").is_file():
        raise ReleaseVerificationError("Compiled frontend/index.html is missing")
    browser_manifest = _verify_browser_manifest(resource_root, platform_name, arch, target_key)

    for executable_name in ("sau.exe", "SocialAutoUpload.exe") if platform_name == "windows" else ("sau", "SocialAutoUpload"):
        executable = root_path / executable_name
        try:
            mode = executable.lstat().st_mode
        except OSError as exc:
            raise ReleaseVerificationError(f"Required entry point is missing: {executable_name}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or executable.lstat().st_nlink != 1:
            raise ReleaseVerificationError(f"Entry point must be a regular non-linked file: {executable_name}")
        if platform_name != "windows" and not mode & 0o111:
            raise ReleaseVerificationError(f"Entry point is not executable: {executable_name}")
        try:
            actual = executable_architecture(executable, platform_name)
        except BrowserStagingError as exc:
            raise ReleaseVerificationError(str(exc)) from exc
        if actual != arch:
            raise ReleaseVerificationError(f"Executable architecture mismatch: {executable_name}")

    checksum_files = [
        path for path in files if path.relative_to(root_path).as_posix() not in _OUTPUT_FILES
    ]
    checksum_lines = [f"{_sha256(path)}  {path.relative_to(root_path).as_posix()}" for path in checksum_files]
    checksum_content = (("\n".join(checksum_lines) + "\n") if checksum_lines else "").encode("utf-8")
    release_manifest = {
        "arch": arch,
        "browser_revision": browser_manifest["revision"],
        "file_count": len(checksum_files),
        "platform": platform_name,
        "schema_version": 2,
        "symlink_count": len(symlinks),
        "symlinks": symlinks,
        "total_bytes": sum(path.stat().st_size for path in checksum_files),
    }
    manifest_content = (json.dumps(release_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(root_path / "release-manifest.json", manifest_content)
    _atomic_write(root_path / "SHA256SUMS", checksum_content)
    return release_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--platform", dest="expected_platform", required=True)
    parser.add_argument("--arch", dest="expected_arch", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    verify_payload(
        arguments.root,
        expected_platform=arguments.expected_platform,
        expected_arch=arguments.expected_arch,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
