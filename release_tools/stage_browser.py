"""Stage a clean Chromium distribution and emit the frozen-runtime manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence


class BrowserStagingError(RuntimeError):
    pass


_FORBIDDEN_STATE = {
    "default",
    "local state",
    "cookies",
    "history",
    "preferences",
    "cookiesfile",
    "profiles",
    ".sau_safety",
    "logs",
    "videofile",
}


def _target_key(platform_name: str, arch: str) -> str:
    system = platform_name.strip().lower()
    machine = arch.strip().lower()
    system = {"macos": "darwin", "win32": "windows"}.get(system, system)
    machine = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system not in {"darwin", "windows", "linux"}:
        raise BrowserStagingError(f"Unsupported browser platform: {platform_name}")
    if machine not in {"x86_64", "arm64"}:
        raise BrowserStagingError(f"Unsupported browser architecture: {arch}")
    return f"{system}-{machine}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def executable_architecture(path: Path, platform_name: str) -> str:
    with path.open("rb") as executable:
        header = executable.read(4096)
    if platform_name == "darwin":
        if len(header) < 8:
            raise BrowserStagingError(f"Browser executable is not a Mach-O file: {path}")
        magic = header[:4]
        endian = "<" if magic in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"} else ">"
        if magic not in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"}:
            raise BrowserStagingError(f"Browser executable is not a thin Mach-O file: {path}")
        cpu_type = struct.unpack(f"{endian}I", header[4:8])[0]
        result = {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cpu_type)
    elif platform_name == "windows":
        if len(header) < 64 or header[:2] != b"MZ":
            raise BrowserStagingError(f"Browser executable is not a PE file: {path}")
        pe_offset = struct.unpack("<I", header[60:64])[0]
        if pe_offset + 6 > len(header) or header[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise BrowserStagingError(f"Browser executable has an invalid PE header: {path}")
        result = {0x8664: "x86_64", 0xAA64: "arm64"}.get(struct.unpack("<H", header[pe_offset + 4 : pe_offset + 6])[0])
    else:
        if len(header) < 20 or header[:4] != b"\x7fELF":
            raise BrowserStagingError(f"Browser executable is not an ELF file: {path}")
        result = {62: "x86_64", 183: "arm64"}.get(struct.unpack("<H", header[18:20])[0])
    if result is None:
        raise BrowserStagingError(f"Unsupported browser executable architecture: {path}")
    return result


def _validate_source(source: Path) -> None:
    resolved_source = source.resolve(strict=True)
    if not resolved_source.is_dir():
        raise BrowserStagingError(f"Browser source is not a directory: {source}")
    for current, directories, files in os.walk(source, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            if name.casefold() in _FORBIDDEN_STATE:
                raise BrowserStagingError(f"Browser source contains runtime/profile state: {path}")
            mode = path.lstat().st_mode
            if stat.S_ISSOCK(mode):
                raise BrowserStagingError(f"Browser source contains a socket: {path}")
            if path.is_symlink():
                try:
                    path.resolve(strict=True).relative_to(resolved_source)
                except (OSError, ValueError) as exc:
                    raise BrowserStagingError(f"Browser source symlink escapes distribution: {path}") from exc


def _find_executable(root: Path, platform_name: str) -> Path:
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    if platform_name == "darwin":
        candidates = [
            path for path in files
            if "Contents" in path.parts and "MacOS" in path.parts
            and "chrome" in path.name.casefold() and "helper" not in path.name.casefold()
        ]
    elif platform_name == "windows":
        candidates = [path for path in files if path.name.casefold() == "chrome.exe"]
    else:
        candidates = [path for path in files if path.name.casefold() in {"chrome", "chromium"}]
    if len(candidates) != 1:
        rendered = ", ".join(str(path.relative_to(root)) for path in candidates[:5])
        raise BrowserStagingError(f"Expected one Chromium executable, found {len(candidates)}: {rendered}")
    return candidates[0]


def stage_browser(
    source: Path | str,
    target: Path | str,
    platform_name: str,
    arch: str,
    revision: str,
) -> dict[str, Any]:
    source_path = Path(source).expanduser()
    target_path = Path(target).expanduser()
    target_key = _target_key(platform_name, arch)
    platform_normalized, arch_normalized = target_key.split("-", 1)
    if not revision or not str(revision).strip():
        raise BrowserStagingError("Browser revision is required")
    _validate_source(source_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.parent / f".{target_path.name}.tmp-{uuid.uuid4().hex}"
    distribution_name = source_path.name
    if distribution_name in {"", ".", ".."}:
        raise BrowserStagingError("Browser source must have a stable directory name")
    staged_distribution = temporary / "browsers" / distribution_name
    try:
        shutil.copytree(source_path, staged_distribution, symlinks=True)
        executable = _find_executable(staged_distribution, platform_normalized)
        actual_arch = executable_architecture(executable, platform_normalized)
        if actual_arch != arch_normalized:
            raise BrowserStagingError(
                f"Browser architecture mismatch: expected {arch_normalized}, found {actual_arch}"
            )
        relative_executable = executable.relative_to(temporary).as_posix()
        manifest = {
            "revision": str(revision),
            "payloads": {
                target_key: {
                    "executable": relative_executable,
                    "sha256": _sha256(executable),
                }
            },
        }
        (temporary / "browser-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if target_path.exists():
            if target_path.is_symlink() or not target_path.is_dir():
                raise BrowserStagingError(f"Browser stage target must be a directory: {target_path}")
            shutil.rmtree(target_path)
        temporary.replace(target_path)
        return manifest
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--platform", dest="platform_name", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--revision", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    stage_browser(
        arguments.source,
        arguments.target,
        arguments.platform_name,
        arguments.arch,
        arguments.revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

