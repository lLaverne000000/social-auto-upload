"""Douyin publishing examples through the governed unified CLI."""

import subprocess
import sys
from pathlib import Path

from conf import RESOURCE_DIR


def _run_sau(*arguments: str) -> None:
    subprocess.run([sys.executable, "-m", "sau_cli", *arguments], check=True)


def upload_video_to_douyin() -> None:
    _run_sau(
        "douyin",
        "upload-video",
        "--account",
        "creator",
        "--file",
        str(Path(RESOURCE_DIR) / "videos" / "demo.mp4"),
        "--title",
        "抖音视频示例",
        "--desc",
        "统一 CLI 安全发布示例",
        "--tags",
        "视频示例,安全发布",
        "--declaration",
        "none",
        "--headed",
    )


def upload_note_to_douyin() -> None:
    _run_sau(
        "douyin",
        "upload-note",
        "--account",
        "creator",
        "--images",
        str(Path(RESOURCE_DIR) / "videos" / "demo1.png"),
        str(Path(RESOURCE_DIR) / "videos" / "demo2.png"),
        "--title",
        "抖音图文示例",
        "--note",
        "统一 CLI 安全发布示例",
        "--tags",
        "图文示例,安全发布",
        "--headed",
    )


if __name__ == "__main__":
    upload_note_to_douyin()
