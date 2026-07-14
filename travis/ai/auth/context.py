"""Default environment and filesystem access for provider authentication."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuthContext:
    def env(self, name: str) -> str | None:
        value = os.environ.get(name)
        return value if value and value.strip() else None

    def file_exists(self, path: str) -> bool:
        return Path(path).expanduser().exists()


def default_auth_context() -> AuthContext:
    return AuthContext()


__all__ = ["AuthContext", "default_auth_context"]
