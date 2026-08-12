from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = PACKAGE_ROOT / "vendor" / "ghost-os"
ASSET_ROOT = PACKAGE_ROOT / "travis234_ghost_mcp" / "assets"
