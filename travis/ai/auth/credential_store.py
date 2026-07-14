"""Credential stores used by the provider collection."""

from __future__ import annotations

import threading
from collections.abc import Callable

from travis.ai.auth.types import Credential


class CredentialStore:
    def read(self, provider_id: str) -> Credential | None:
        raise NotImplementedError

    def modify(
        self,
        provider_id: str,
        callback: Callable[[Credential | None], Credential | None],
    ) -> Credential | None:
        raise NotImplementedError

    def delete(self, provider_id: str) -> None:
        raise NotImplementedError


class InMemoryCredentialStore(CredentialStore):
    def __init__(self, credentials: dict[str, Credential] | None = None) -> None:
        self._credentials = {
            provider: dict(credential)
            for provider, credential in (credentials or {}).items()
        }
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, provider_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(provider_id, threading.RLock())

    def read(self, provider_id: str) -> Credential | None:
        with self._lock_for(provider_id):
            credential = self._credentials.get(provider_id)
            return dict(credential) if credential is not None else None

    def modify(
        self,
        provider_id: str,
        callback: Callable[[Credential | None], Credential | None],
    ) -> Credential | None:
        with self._lock_for(provider_id):
            current = self._credentials.get(provider_id)
            next_credential = callback(dict(current) if current is not None else None)
            if next_credential is not None:
                self._credentials[provider_id] = dict(next_credential)
            committed = self._credentials.get(provider_id)
            return dict(committed) if committed is not None else None

    def delete(self, provider_id: str) -> None:
        with self._lock_for(provider_id):
            self._credentials.pop(provider_id, None)


__all__ = ["CredentialStore", "InMemoryCredentialStore"]
