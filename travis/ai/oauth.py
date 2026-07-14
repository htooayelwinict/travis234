"""Shared OAuth credential helpers."""

from __future__ import annotations

import time
from collections.abc import Mapping


def oauth_credential_is_expired(credential: Mapping[str, object]) -> bool:
    expires = credential.get("expires")
    if expires is None:
        return False
    try:
        return int(expires) <= int(time.time() * 1000)
    except (TypeError, ValueError):
        return False
