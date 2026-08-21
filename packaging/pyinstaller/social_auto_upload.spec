# -*- mode: python ; coding: utf-8 -*-
"""Shared-analysis, one-folder frozen desktop payload."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


PROJECT_ROOT = Path(os.environ.get("SAU_PROJECT_ROOT", Path.cwd())).resolve()
FRONTEND_DIST = Path(
    os.environ.get("SAU_FRONTEND_DIST", PROJECT_ROOT / "sau_frontend" / "dist")
).resolve()
BROWSER_STAGE = Path(
    os.environ.get("SAU_BROWSER_STAGE", PROJECT_ROOT / "packaging" / "browser-stage")
).resolve()

if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit(f"Compiled frontend is missing: {FRONTEND_DIST}")
if not (BROWSER_STAGE / "browser-manifest.json").is_file():
    raise SystemExit(f"Browser manifest is missing: {BROWSER_STAGE}")
if not (BROWSER_STAGE / "browsers").is_dir():
    raise SystemExit(f"Staged browser directory is missing: {BROWSER_STAGE}")

patchright_datas, patchright_binaries, patchright_hidden = collect_all("patchright")
webview_datas, webview_binaries, webview_hidden = collect_all("webview")

hiddenimports = sorted(
    set(
        [
            "conf",
            "sau_backend",
            "sau_browser_runtime",
            "sau_cli",
            "sau_desktop",
            "sau_desktop_api",
            "sau_desktop_service",
            "sau_frozen_entry",
            "sau_media_validation",
            "sau_runtime",
        ]
        + patchright_hidden
        + webview_hidden
        + collect_submodules("uploader")
        + collect_submodules("utils")
        + collect_submodules("myUtils")
    )
)

datas = [
    (str(FRONTEND_DIST), "frontend"),
    (str(BROWSER_STAGE / "browser-manifest.json"), "."),
    (str(BROWSER_STAGE / "browsers"), "browsers"),
] + patchright_datas + webview_datas

a = Analysis(
    [str(PROJECT_ROOT / "sau_frozen_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=patchright_binaries + webview_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PyInstaller reclassifies Mach-O/PE files passed through ``datas`` as binaries.
# A staged Chromium app is already a signed, internally linked distribution;
# processing each nested executable independently breaks that bundle. Keep every
# browser entry as opaque data while PyInstaller processes application binaries
# normally.
browser_binaries = []
application_binaries = []
for destination, source, typecode in a.binaries:
    normalized_destination = destination.replace("\\", "/")
    if normalized_destination == "browsers" or normalized_destination.startswith("browsers/"):
        browser_binaries.append((destination, source, "DATA"))
    else:
        application_binaries.append((destination, source, typecode))
a.binaries = application_binaries
a.datas += browser_binaries

pyz = PYZ(a.pure)

sau = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sau",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

desktop = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SocialAutoUpload",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

payload = COLLECT(
    sau,
    desktop,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SocialAutoUpload",
)
