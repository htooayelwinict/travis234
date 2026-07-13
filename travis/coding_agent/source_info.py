"""SourceInfo metadata."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping


@dataclass(frozen=True)
class SourceInfo:
    path: str
    source: str
    scope: str = "temporary"
    origin: str = "top-level"
    base_dir: str | None = None

    @property
    def baseDir(self) -> str | None:
        return self.base_dir

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


def create_source_info(path: str, metadata: Mapping[str, object]) -> SourceInfo:
    return SourceInfo(
        path=path,
        source=str(metadata.get("source") or ""),
        scope=str(metadata.get("scope") or "temporary"),
        origin=str(metadata.get("origin") or "top-level"),
        base_dir=_optional_string(metadata.get("baseDir", metadata.get("base_dir"))),
    )


def createSyntheticSourceInfo(path: str, options: Mapping[str, object]) -> SourceInfo:
    return SourceInfo(
        path=path,
        source=str(options.get("source") or ""),
        scope=str(options.get("scope") or "temporary"),
        origin=str(options.get("origin") or "top-level"),
        base_dir=_optional_string(options.get("baseDir", options.get("base_dir"))),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


createSourceInfo = create_source_info
