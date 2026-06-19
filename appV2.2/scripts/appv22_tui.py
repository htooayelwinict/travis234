#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


APPV22_ROOT = Path(__file__).resolve().parents[1]
if str(APPV22_ROOT) not in sys.path:
    sys.path.insert(0, str(APPV22_ROOT))

from appv22_ui.tui_app import main as legacy_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if os.getenv("APPV22_LEGACY_PY_TUI") == "1":
        return legacy_main(argv)
    frontend = APPV22_ROOT / "appv22_ui" / "pi_tui" / "app.mjs"
    python = os.getenv("APPV22_PYTHON") or sys.executable
    completed = subprocess.run(
        ["node", str(frontend), "--python", python, *(argv or sys.argv[1:])],
        check=False,
    )
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
