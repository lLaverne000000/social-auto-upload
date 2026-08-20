from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def profile_dir_for(account_file: str | Path, platform: str) -> Path:
    account_path = Path(account_file).expanduser().resolve()
    account_key = hashlib.sha256(str(account_path).encode("utf-8")).hexdigest()[:16]
    return account_path.parent / ".browser_profiles" / f"{platform}-{account_key}"


async def save_secure_storage_state(context, account_file: str | Path) -> Path:
    account_path = Path(account_file)
    account_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{account_path.name}.",
        suffix=".tmp",
        dir=account_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.chmod(0o600)
        await context.storage_state(path=str(temporary_path))
        temporary_path.chmod(0o600)
        temporary_path.replace(account_path)
        account_path.chmod(0o600)
    finally:
        temporary_path.unlink(missing_ok=True)
    return account_path


async def launch_persistent_account_context(
    chromium,
    *,
    account_file: str | Path,
    platform: str,
    headless: bool,
    permissions: list[str] | None = None,
    executable_path: str | None = None,
):
    """Launch a stable account profile and import legacy cookie JSON once."""
    account_path = Path(account_file)
    if account_path.is_file():
        account_path.chmod(0o600)
    profile_dir = profile_dir_for(account_path, platform)
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.chmod(0o700)

    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "channel": "chromium",
    }
    if permissions:
        launch_kwargs["permissions"] = permissions
    if executable_path:
        launch_kwargs.pop("channel", None)
        launch_kwargs["executable_path"] = executable_path

    context = await chromium.launch_persistent_context(**launch_kwargs)
    marker = profile_dir / ".storage_state_imported"
    if account_path.is_file() and not marker.exists():
        try:
            state = json.loads(account_path.read_text(encoding="utf-8"))
            cookies = state.get("cookies", []) if isinstance(state, dict) else []
            if cookies:
                await context.add_cookies(cookies)
            marker.touch()
        except Exception as exc:
            try:
                await context.close()
            except Exception:
                pass
            raise RuntimeError(f"账号 cookie 无法导入持久化 profile: {account_path}") from exc
    return context
