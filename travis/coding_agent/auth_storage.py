"""Serialized credential storage for the Travis provider runtime."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fcntl

from travis.ai.auth.types import Credential
from travis.ai.env_config import find_env_keys, get_env_api_key

AuthStorageData = dict[str, Credential]
LockResult = dict[str, object]


class AuthStorageError(RuntimeError):
    pass


class _CredentialCallbackError(RuntimeError):
    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def _agent_auth_path() -> Path:
    return Path.home() / ".travis234" / "agent" / "auth.json"


class AuthStorageBackend:
    def with_lock(self, callback: Callable[[str | None], LockResult]) -> object:
        raise NotImplementedError


class FileAuthStorageBackend(AuthStorageBackend):
    def __init__(self, auth_path: str | os.PathLike[str] | None = None) -> None:
        self.auth_path = Path(auth_path or _agent_auth_path()).expanduser().resolve()

    def _ensure_file(self) -> None:
        self.auth_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not self.auth_path.exists():
            self.auth_path.write_text("{}", encoding="utf-8")
            os.chmod(self.auth_path, 0o600)

    def with_lock(self, callback: Callable[[str | None], LockResult]) -> object:
        self._ensure_file()
        lock_path = self.auth_path.with_name(f"{self.auth_path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                result = callback(self.auth_path.read_text(encoding="utf-8"))
                if "next" in result:
                    self._atomic_write(str(result["next"]))
                return result.get("result")
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.auth_path.name}.",
            suffix=".tmp",
            dir=self.auth_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.auth_path)
            directory_descriptor = os.open(self.auth_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)


class InMemoryAuthStorageBackend(AuthStorageBackend):
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self._lock = threading.RLock()

    def with_lock(self, callback: Callable[[str | None], LockResult]) -> object:
        with self._lock:
            result = callback(self.value)
            if "next" in result:
                self.value = str(result["next"])
            return result.get("result")


class AuthStorage:
    """App-owned credential store with atomic per-provider modification."""

    def __init__(self, storage: AuthStorageBackend) -> None:
        self._storage = storage
        self._data: AuthStorageData = {}
        self._runtime_overrides: dict[str, str] = {}
        self._load_error: Exception | None = None
        self._errors: list[Exception] = []
        self.reload()

    @staticmethod
    def create(auth_path: str | os.PathLike[str] | None = None) -> "AuthStorage":
        return AuthStorage(FileAuthStorageBackend(auth_path))

    @staticmethod
    def from_storage(storage: AuthStorageBackend) -> "AuthStorage":
        return AuthStorage(storage)

    @staticmethod
    def in_memory(data: AuthStorageData | None = None) -> "AuthStorage":
        return AuthStorage(
            InMemoryAuthStorageBackend(json.dumps(data or {}, indent=2))
        )

    def reload(self) -> None:
        try:
            content = self._storage.with_lock(lambda current: {"result": current})
            self._data = self._parse(content if isinstance(content, str) else None)
            self._load_error = None
        except Exception as error:  # noqa: BLE001 - the in-memory view remains usable.
            self._load_error = error
            self._record_error(error)

    def read(self, provider_id: str) -> Credential | None:
        runtime = self._runtime_overrides.get(provider_id)
        if runtime is not None:
            return {"type": "api_key", "key": runtime}
        credential = self._data.get(provider_id)
        return dict(credential) if credential is not None else None

    def modify(
        self,
        provider_id: str,
        callback: Callable[[Credential | None], Credential | None],
    ) -> Credential | None:
        if self._load_error is not None:
            self.reload()
        if self._load_error is not None:
            raise AuthStorageError(f"auth storage is malformed: {self._load_error}")

        def transaction(current_text: str | None) -> LockResult:
            current_data = self._parse(current_text)
            current = current_data.get(provider_id)
            try:
                next_credential = callback(dict(current) if current is not None else None)
            except Exception as error:  # Provider auth failures keep their type.
                raise _CredentialCallbackError(error) from error
            if next_credential is None:
                result = dict(current) if current is not None else None
                return {"result": result}
            current_data[provider_id] = dict(next_credential)
            return {
                "result": dict(next_credential),
                "next": json.dumps(current_data, indent=2),
            }

        try:
            committed = self._storage.with_lock(transaction)
            self._data = self._read_all_locked()
            return dict(committed) if isinstance(committed, dict) else None
        except Exception as error:  # noqa: BLE001
            if isinstance(error, _CredentialCallbackError):
                self._record_error(error.error)
                raise error.error
            self._record_error(error)
            if isinstance(error, AuthStorageError):
                raise
            raise AuthStorageError(f"auth storage update failed: {error}") from error

    def delete(self, provider_id: str) -> None:
        self._persist(provider_id, None)

    def get(self, provider: str) -> Credential | None:
        credential = self._data.get(provider)
        return dict(credential) if credential is not None else None

    def set(self, provider: str, credential: Credential) -> None:
        self._persist(provider, credential)

    def remove(self, provider: str) -> None:
        self.delete(provider)

    def list(self) -> list[str]:
        return list(self._data)

    def has(self, provider: str) -> bool:
        return provider in self._data

    def get_all(self) -> AuthStorageData:
        return {provider: dict(credential) for provider, credential in self._data.items()}

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        self._runtime_overrides.pop(provider, None)

    def has_auth(self, provider: str) -> bool:
        return bool(
            provider in self._runtime_overrides
            or provider in self._data
            or get_env_api_key(provider)
        )

    def get_api_key(self, provider: str, options: dict[str, object] | None = None) -> str | None:
        if provider in self._runtime_overrides:
            return self._runtime_overrides[provider]
        credential = self._data.get(provider)
        if credential and credential.get("type") == "api_key" and credential.get("key"):
            return str(credential["key"])
        if options and options.get("includeFallback", options.get("include_fallback", True)) is False:
            return None
        return get_env_api_key(provider)

    def get_provider_env(self, provider: str) -> dict[str, str] | None:
        credential = self._data.get(provider)
        value = credential.get("env") if credential and credential.get("type") == "api_key" else None
        if not isinstance(value, dict):
            return None
        resolved = {str(key): str(item) for key, item in value.items()}
        return resolved or None

    def get_auth_status(self, provider: str) -> dict[str, object]:
        if provider in self._data:
            return {"configured": True, "source": "stored"}
        if provider in self._runtime_overrides:
            return {"configured": True, "source": "runtime", "label": "--api-key"}
        env_keys = find_env_keys(provider) or []
        if env_keys:
            return {"configured": True, "source": "environment", "label": env_keys[0]}
        if get_env_api_key(provider):
            return {"configured": True, "source": "environment", "label": "ambient credentials"}
        return {"configured": False}

    def drain_errors(self) -> list[Exception]:
        errors = list(self._errors)
        self._errors.clear()
        return errors

    def _persist(self, provider: str, credential: Credential | None) -> None:
        if self._load_error is not None:
            self.reload()
        if self._load_error is not None:
            raise AuthStorageError(f"auth storage is malformed: {self._load_error}")

        def transaction(current_text: str | None) -> LockResult:
            current = self._parse(current_text)
            if credential is None:
                current.pop(provider, None)
            else:
                current[provider] = dict(credential)
            return {"result": current, "next": json.dumps(current, indent=2)}

        try:
            committed = self._storage.with_lock(transaction)
            if not isinstance(committed, dict):
                raise AuthStorageError("auth storage transaction returned invalid data")
            self._data = {
                str(key): dict(value)
                for key, value in committed.items()
                if isinstance(value, dict)
            }
            self._load_error = None
        except Exception as error:  # noqa: BLE001
            self._record_error(error)
            if isinstance(error, AuthStorageError):
                raise
            raise AuthStorageError(f"auth storage update failed: {error}") from error

    def _read_all_locked(self) -> AuthStorageData:
        value = self._storage.with_lock(lambda current: {"result": current})
        return self._parse(value if isinstance(value, str) else None)

    @staticmethod
    def _parse(content: str | None) -> AuthStorageData:
        if not content:
            return {}
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise AuthStorageError("auth storage root must be an object")
        return {
            str(provider): dict(credential)
            for provider, credential in parsed.items()
            if isinstance(credential, dict)
        }

    def _record_error(self, error: object) -> None:
        self._errors.append(error if isinstance(error, Exception) else RuntimeError(str(error)))


__all__ = [
    "AuthStorage",
    "AuthStorageBackend",
    "AuthStorageData",
    "AuthStorageError",
    "FileAuthStorageBackend",
    "InMemoryAuthStorageBackend",
]
