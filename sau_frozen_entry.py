"""Shared PyInstaller entry point for the console and desktop executables."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> None:
    executable_name = Path(sys.executable).stem.casefold()
    module_name = "sau_desktop" if executable_name == "socialautoupload" else "sau_cli"
    importlib.import_module(module_name).main()


if __name__ == "__main__":
    main()

