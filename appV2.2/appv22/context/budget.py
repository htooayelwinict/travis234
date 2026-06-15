from __future__ import annotations

import json
from typing import Any


def estimate_chars(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, default=str))
