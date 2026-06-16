#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


APPV22_ROOT = Path(__file__).resolve().parents[1]
if str(APPV22_ROOT) not in sys.path:
    sys.path.insert(0, str(APPV22_ROOT))

from appv22_ui.textual_app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
