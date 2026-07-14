"""Shared forwarding behavior for public composition facades."""

from __future__ import annotations


class RuntimeFacade:
    """Forward public access and overrides to a composed runtime object."""

    def __getattr__(self, name: str):
        return getattr(self._runtime, name)

    def __setattr__(self, name: str, value) -> None:
        runtime = self.__dict__.get("_runtime")
        if runtime is None or name == "_runtime":
            object.__setattr__(self, name, value)
            return
        setattr(runtime, name, value)

    def __dir__(self) -> list[str]:
        return sorted(set(object.__dir__(self)) | set(dir(self._runtime)))
