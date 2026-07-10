"""Pi-style auth storage for coding-agent services."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import fcntl

from appv231.ai.env_config import find_env_keys, get_env_api_key
import appv231.ai.models as ai_models

AuthCredential = dict[str, object]
AuthStorageData = dict[str, AuthCredential]
LockResult = dict[str, object]


class AuthStorageError(RuntimeError):
    pass


def _agent_auth_path() -> Path:
    return Path.home() / ".pi" / "agent" / "auth.json"


class AuthStorageBackend:
    def withLock(self, fn: Callable[[str | None], LockResult]) -> object:
        raise NotImplementedError

    with_lock = withLock


class FileAuthStorageBackend(AuthStorageBackend):
    def __init__(self, auth_path: str | os.PathLike[str] | None = None) -> None:
        self.auth_path = Path(auth_path or _agent_auth_path()).expanduser().resolve()

    def _ensure_file(self) -> None:
        self.auth_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not self.auth_path.exists():
            self.auth_path.write_text("{}", encoding="utf-8")
            os.chmod(self.auth_path, 0o600)

    def withLock(self, fn: Callable[[str | None], LockResult]) -> object:
        self._ensure_file()
        lock_path = self.auth_path.with_name(f"{self.auth_path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                current = self.auth_path.read_text(encoding="utf-8")
                result = fn(current)
                if "next" in result:
                    self._atomic_write(str(result["next"]))
                return result.get("result")
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, content: str) -> None:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.auth_path.name}.",
            suffix=".tmp",
            dir=self.auth_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.auth_path)
            directory_fd = os.open(self.auth_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    with_lock = withLock


class InMemoryAuthStorageBackend(AuthStorageBackend):
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def withLock(self, fn: Callable[[str | None], LockResult]) -> object:
        result = fn(self.value)
        if "next" in result:
            self.value = str(result["next"])
        return result.get("result")

    with_lock = withLock


class AuthStorage:
    def __init__(self, storage: AuthStorageBackend) -> None:
        self._storage = storage
        self._data: AuthStorageData = {}
        self._runtime_overrides: dict[str, str] = {}
        self._fallback_resolver: Callable[[str], str | None] | None = None
        self._load_error: Exception | None = None
        self._errors: list[Exception] = []
        self._oauth_providers: dict[str, dict[str, object]] = {}
        self._oauth_locks: dict[str, threading.Lock] = {}
        self._oauth_locks_guard = threading.Lock()
        self.reload()

    @staticmethod
    def create(authPath: str | os.PathLike[str] | None = None) -> "AuthStorage":
        return AuthStorage(FileAuthStorageBackend(authPath))

    @staticmethod
    def fromStorage(storage: AuthStorageBackend) -> "AuthStorage":
        return AuthStorage(storage)

    @staticmethod
    def inMemory(data: AuthStorageData | None = None) -> "AuthStorage":
        storage = InMemoryAuthStorageBackend(json.dumps(data or {}, indent=2))
        return AuthStorage.fromStorage(storage)

    from_storage = fromStorage
    in_memory = inMemory

    def _record_error(self, error: object) -> None:
        self._errors.append(error if isinstance(error, Exception) else RuntimeError(str(error)))

    def _parse_storage_data(self, content: str | None) -> AuthStorageData:
        if not content:
            return {}
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {}
        return {str(provider): dict(credential) for provider, credential in parsed.items() if isinstance(credential, dict)}

    def reload(self) -> None:
        try:
            content_holder: dict[str, str | None] = {"content": None}

            def read(current: str | None) -> LockResult:
                content_holder["content"] = current
                return {"result": None}

            self._storage.withLock(read)
            self._data = self._parse_storage_data(content_holder["content"])
            self._load_error = None
        except Exception as error:  # noqa: BLE001 - Pi records storage errors and keeps running.
            self._load_error = error
            self._record_error(error)

    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> AuthStorageData:
        if self._load_error is not None:
            raise AuthStorageError(f"auth storage is malformed: {self._load_error}") from self._load_error
        try:
            def update(current: str | None) -> LockResult:
                merged = self._parse_storage_data(current)
                if credential is None:
                    merged.pop(provider, None)
                else:
                    merged[provider] = dict(credential)
                return {"result": merged, "next": json.dumps(merged, indent=2)}

            committed = self._storage.withLock(update)
            if not isinstance(committed, dict):
                raise AuthStorageError("auth storage transaction did not return committed data")
            return {str(key): dict(value) for key, value in committed.items() if isinstance(value, dict)}
        except Exception as error:
            self._record_error(error)
            if isinstance(error, AuthStorageError):
                raise
            raise AuthStorageError(f"auth storage update failed: {error}") from error

    def setRuntimeApiKey(self, provider: str, api_key: str) -> None:
        self._runtime_overrides[provider] = api_key

    def removeRuntimeApiKey(self, provider: str) -> None:
        self._runtime_overrides.pop(provider, None)

    def setFallbackResolver(self, resolver: Callable[[str], str | None]) -> None:
        self._fallback_resolver = resolver

    set_runtime_api_key = setRuntimeApiKey
    remove_runtime_api_key = removeRuntimeApiKey
    set_fallback_resolver = setFallbackResolver

    def get(self, provider: str) -> AuthCredential | None:
        credential = self._data.get(provider)
        return dict(credential) if credential is not None else None

    def set(self, provider: str, credential: AuthCredential) -> None:
        self._data = self._persist_provider_change(provider, credential)

    def remove(self, provider: str) -> None:
        self._data = self._persist_provider_change(provider, None)

    delete = remove

    def list(self) -> list[str]:
        return list(self._data.keys())

    def has(self, provider: str) -> bool:
        return provider in self._data

    def hasAuth(self, provider: str) -> bool:
        if provider in self._runtime_overrides:
            return True
        if provider in self._data:
            return True
        if get_env_api_key(provider):
            return True
        if self._fallback_resolver is not None and self._fallback_resolver(provider):
            return True
        return False

    has_auth = hasAuth

    def getAuthStatus(self, provider: str) -> dict[str, object]:
        if provider in self._data:
            return {"configured": True, "source": "stored"}
        if provider in self._runtime_overrides:
            return {"configured": True, "source": "runtime", "label": "--api-key"}
        env_keys = find_env_keys(provider)
        if env_keys:
            return {"configured": True, "source": "environment", "label": env_keys[0]}
        if self._fallback_resolver is not None and self._fallback_resolver(provider):
            return {"configured": True, "source": "fallback", "label": "custom provider config"}
        return {"configured": False}

    get_auth_status = getAuthStatus

    def getAll(self) -> AuthStorageData:
        return {provider: dict(credential) for provider, credential in self._data.items()}

    get_all = getAll

    def drainErrors(self) -> list[Exception]:
        errors = list(self._errors)
        self._errors.clear()
        return errors

    drain_errors = drainErrors

    def logout(self, provider: str) -> None:
        self.remove(provider)

    def login(self, provider: str, callbacks: dict[str, object]) -> None:
        ai_models.login_oauth_provider(provider, callbacks)
        credential = ai_models.get_auth_credential(provider)
        if credential is not None:
            self.set(provider, credential)

    def getApiKey(self, provider: str, options: dict[str, object] | None = None) -> str | None:
        if provider in self._runtime_overrides:
            return self._runtime_overrides[provider]

        credential = self._data.get(provider)
        if credential is not None:
            if credential.get("type") == "api_key":
                return ai_models._resolve_config_value(str(credential.get("key", "")))  # noqa: SLF001
            if credential.get("type") == "oauth":
                return self._get_oauth_api_key(provider)

        env_key = get_env_api_key(provider)
        if env_key:
            return env_key

        include_fallback = True
        if options is not None:
            include_fallback = bool(options.get("includeFallback", options.get("include_fallback", True)))
        if include_fallback and self._fallback_resolver is not None:
            return self._fallback_resolver(provider)
        return None

    get_api_key = getApiKey

    def register_oauth_provider(self, provider: str, implementation: dict[str, object]) -> None:
        self._oauth_providers[provider] = dict(implementation)

    def unregister_oauth_provider(self, provider: str) -> None:
        self._oauth_providers.pop(provider, None)

    def _get_oauth_api_key(self, provider: str) -> str | None:
        with self._oauth_locks_guard:
            lock = self._oauth_locks.setdefault(provider, threading.Lock())
        with lock:
            credential = self.get(provider)
            if credential is None or credential.get("type") != "oauth":
                return None
            implementation = self._oauth_providers.get(provider)
            if implementation is not None and _oauth_is_expired(credential):
                refresh = implementation.get("refreshToken") or implementation.get("refresh_token")
                if not callable(refresh):
                    return None
                refreshed = ai_models._settle_oauth_result(refresh(credential))  # noqa: SLF001
                if not isinstance(refreshed, dict):
                    raise AuthStorageError(f"OAuth provider {provider} returned invalid refreshed credentials")
                self.set(provider, {"type": "oauth", **refreshed})
                credential = self.get(provider) or {}
            if implementation is not None:
                get_api_key = implementation.get("getApiKey") or implementation.get("get_api_key")
                if callable(get_api_key):
                    return str(get_api_key(credential))
            access = credential.get("access") or credential.get("access_token")
            return str(access) if access is not None else None

    def getOAuthProviders(self) -> list[dict[str, object]]:
        return ai_models.get_oauth_providers()

    get_oauth_providers = getOAuthProviders


def _oauth_is_expired(credential: AuthCredential) -> bool:
    expires = credential.get("expires")
    if expires is None:
        return False
    try:
        return int(expires) <= int(time.time() * 1000)
    except (TypeError, ValueError):
        return False
