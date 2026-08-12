from __future__ import annotations

from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
VENDOR_ROOT = PACKAGE_ROOT / "vendor" / "ghost-os"
ASSET_ROOT = PACKAGE_ROOT / "travis234_ghost_mcp" / "assets"
