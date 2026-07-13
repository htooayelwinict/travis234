#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys


APPV231_ROOT = Path(__file__).resolve().parents[1]
if str(APPV231_ROOT) not in sys.path:
    sys.path.insert(0, str(APPV231_ROOT))


def _maybe_reexec_project_python() -> None:
    if os.getenv("APPV231_NO_VENV_REEXEC") == "1":
        return
    venv_python = APPV231_ROOT.parent / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_maybe_reexec_project_python()

from appv231.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
