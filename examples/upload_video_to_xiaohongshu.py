"""Xiaohongshu publishing examples through the governed unified CLI."""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from conf import BASE_DIR


def _run_sau(*arguments: str) -> None:
    subprocess.run([sys.executable, "-m", "sau_cli", *arguments], check=True)


def upload_video_to_xiaohongshu(schedule: str | None = None) -> None:
    arguments = [
        "xiaohongshu",
        "upload-video",
        "--account",
        "creator",
        "--file",
        str(Path(BASE_DIR) / "videos" / "demo.mp4"),
        "--title",
        "小红书视频示例",
        "--desc",
        "统一 CLI 安全发布示例",
        "--tags",
        "小红书,视频示例",
        "--content-source",
        "original",
        "--headed",
    ]
    if schedule:
        arguments.extend(("--schedule", schedule))
    _run_sau(*arguments)


def upload_note_to_xiaohongshu(schedule: str | None = None) -> None:
    arguments = [
        "xiaohongshu",
        "upload-note",
        "--account",
        "creator",
        "--images",
        str(Path(BASE_DIR) / "videos" / "demo1.png"),
        str(Path(BASE_DIR) / "videos" / "demo2.png"),
        "--title",
        "小红书图文示例",
        "--note",
        "统一 CLI 安全发布示例",
        "--tags",
        "小红书,图文示例",
        "--content-source",
        "original",
        "--headed",
    ]
    if schedule:
        arguments.extend(("--schedule", schedule))
    _run_sau(*arguments)


if __name__ == "__main__":
    publish_time = (datetime.now() + timedelta(hours=3)).replace(
        second=0,
        microsecond=0,
    )
    upload_note_to_xiaohongshu(publish_time.strftime("%Y-%m-%d %H:%M"))
