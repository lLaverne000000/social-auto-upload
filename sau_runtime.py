from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    resource_root: Path
    data_root: Path
    cookies_dir: Path
    profiles_dir: Path
    logs_dir: Path
    safety_dir: Path
    media_dir: Path
    database_file: Path


def resolve_resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    return Path(frozen_root).resolve() if frozen_root else Path(__file__).parent.resolve()


def resolve_data_root() -> Path:
    override = os.environ.get("SOCIAL_AUTO_UPLOAD_HOME")
    if override:
        return Path(override).expanduser().resolve()

    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "SocialAutoUpload"
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is required on Windows")
        return Path(local) / "SocialAutoUpload"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "SocialAutoUpload"


def get_runtime_paths(*, create: bool = True) -> RuntimePaths:
    data_root = resolve_data_root()
    paths = RuntimePaths(
        resource_root=resolve_resource_root(),
        data_root=data_root,
        cookies_dir=data_root / "cookies",
        profiles_dir=data_root / "profiles",
        logs_dir=data_root / "logs",
        safety_dir=data_root / ".sau_safety",
        media_dir=data_root / "media",
        database_file=data_root / "db" / "database.db",
    )
    if create:
        for directory in (
            paths.data_root,
            paths.cookies_dir,
            paths.profiles_dir,
            paths.logs_dir,
            paths.safety_dir,
            paths.media_dir,
            paths.database_file.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
    return paths
