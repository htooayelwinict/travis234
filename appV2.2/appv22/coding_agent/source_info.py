"""SourceInfo metadata. Port of pi/packages/coding-agent/src/core/source-info.ts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceInfo:
    path: str
    source: str
    scope: str = "temporary"
    origin: str = "top-level"
    base_dir: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {
            "path": self.path,
            "source": self.source,
            "scope": self.scope,
            "origin": self.origin,
        }
        if self.base_dir is not None:
            data["baseDir"] = self.base_dir
            data["base_dir"] = self.base_dir
        return data


def create_synthetic_source_info(
    path: str,
    *,
    source: str,
    scope: str = "temporary",
    origin: str = "top-level",
    base_dir: str | None = None,
) -> SourceInfo:
    return SourceInfo(path=path, source=source, scope=scope, origin=origin, base_dir=base_dir)
