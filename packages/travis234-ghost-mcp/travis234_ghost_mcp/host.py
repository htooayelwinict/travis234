from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


class UnsupportedHostError(RuntimeError):
    """Raised when the bundled executable cannot run on the current host."""


class PackageIntegrityError(RuntimeError):
    """Raised when an installed add-on payload is absent or unsafe."""


def package_root() -> Path:
    return Path(__file__).resolve().parent


def require_supported_host() -> None:
    raw_version = platform.mac_ver()[0]
    try:
        version = tuple(int(part) for part in raw_version.split(".")[:2])
    except ValueError:
        version = (0,)
    if (
        sys.platform != "darwin"
        or platform.machine() != "arm64"
        or version < (14, 0)
    ):
        raise UnsupportedHostError(
            "travis234-ghost-mcp requires macOS 14 or newer on Apple Silicon"
        )


def ghost_binary() -> Path:
    require_supported_host()
    root = package_root().resolve()
    binary = (root / "bin" / "ghost").resolve()
    try:
        binary.relative_to(root)
    except ValueError as error:
        raise PackageIntegrityError(
            "travis234-ghost-mcp is missing its embedded Ghost executable"
        ) from error
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise PackageIntegrityError(
            "travis234-ghost-mcp is missing its embedded Ghost executable"
        )
    return binary
