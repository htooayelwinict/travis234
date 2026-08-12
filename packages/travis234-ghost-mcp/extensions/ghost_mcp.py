from pathlib import Path
import sys


_PAYLOAD_ROOT = str(Path(__file__).resolve().parents[1])
if _PAYLOAD_ROOT not in sys.path:
    sys.path.insert(0, _PAYLOAD_ROOT)

from travis234_ghost_mcp.extension import extension


__all__ = ["extension"]
