"""Isolated extension-runtime candidate loading and ownership."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
import sys
from threading import RLock
from types import ModuleType

from travis.agent.async_utils import resolve, run_sync
from travis.coding_agent.event_bus import EventBusController
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.resource_discovery import collect_resource_files


@dataclass(frozen=True)
class ExtensionLoadRequest:
    cwd: str
    event_bus: EventBusController
    discovered_paths: tuple[str, ...]
    additional_paths: tuple[str, ...]
    factories: tuple[Callable[[ExtensionRunner], object], ...]
    no_extensions: bool
    generation: int
    apply_override: bool
    override: Callable[[dict[str, object]], dict[str, object]] | None


class ExtensionRuntimeLease:
    def __init__(
        self,
        result: dict[str, object],
        runtime: ExtensionRunner,
        module_names: tuple[str, ...] = (),
    ) -> None:
        self._result = result
        self._runtime = runtime
        self._module_names = list(module_names)
        self._references = 1
        self._released = False
        self._lock = RLock()

    @property
    def result(self) -> dict[str, object]:
        return self._result

    @property
    def runtime(self) -> ExtensionRunner:
        return self._runtime

    @property
    def module_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._module_names)

    def retain(self) -> ExtensionRuntimeLease:
        with self._lock:
            if self._released:
                raise RuntimeError("extension runtime lease is released")
            self._references += 1
            return self

    def release(self) -> None:
        with self._lock:
            if self._released:
                raise RuntimeError("extension runtime lease is released")
            self._references -= 1
            if self._references:
                return
            self._released = True
            runtime = self._runtime
            module_names = tuple(self._module_names)
        try:
            runtime.dispose()
        finally:
            for module_name in module_names:
                sys.modules.pop(module_name, None)

    def _replace_result(self, result: dict[str, object]) -> None:
        with self._lock:
            if self._released:
                raise RuntimeError("extension runtime lease is released")
            self._result = result

    def _add_module(self, module_name: str) -> None:
        with self._lock:
            if self._released:
                raise RuntimeError("extension runtime lease is released")
            self._module_names.append(module_name)


def create_empty_extension_runtime(
    cwd: str,
    event_bus: EventBusController,
) -> ExtensionRuntimeLease:
    runtime = ExtensionRunner(cwd=cwd, event_bus=event_bus)
    result: dict[str, object] = {
        "extensions": [],
        "errors": [],
        "runtime": runtime,
    }
    return ExtensionRuntimeLease(result, runtime)


def load_extension_runtime(
    request: ExtensionLoadRequest,
    *,
    preloaded: ExtensionRuntimeLease | None = None,
) -> ExtensionRuntimeLease:
    lease = preloaded or create_empty_extension_runtime(request.cwd, request.event_bus)
    runtime = lease.runtime
    existing = lease.result if preloaded is not None else {}
    errors = [
        dict(error)
        for error in _result_entries(existing, "errors")
        if isinstance(error, dict)
    ]
    preloaded_entries = [
        entry
        for entry in _result_entries(existing, "extensions")
        if isinstance(entry, dict)
    ]
    loaded_by_path = {
        str(entry["path"]): dict(entry)
        for entry in preloaded_entries
        if isinstance(entry.get("path"), str)
        and not str(entry["path"]).startswith("<inline:")
    }
    inline_loaded = [
        dict(entry)
        for entry in preloaded_entries
        if isinstance(entry.get("path"), str)
        and str(entry["path"]).startswith("<inline:")
    ]
    failed_paths = {
        str(error["path"])
        for error in errors
        if isinstance(error.get("path"), str)
    }

    extension_files: list[Path] = []
    if not request.no_extensions:
        seen: set[str] = set()
        for path_text in (*request.discovered_paths, *request.additional_paths):
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = Path(request.cwd) / path
            path = path.resolve()
            if not path.exists():
                if str(path) not in failed_paths:
                    errors.append(
                        {
                            "path": str(path),
                            "error": f"Extension path does not exist: {path}",
                        }
                    )
                    failed_paths.add(str(path))
                continue
            for extension_file in collect_resource_files(path, "extensions"):
                resolved = str(extension_file.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    extension_files.append(extension_file.resolve())

    try:
        for extension_file in extension_files:
            extension_path = str(extension_file)
            if extension_path in loaded_by_path or extension_path in failed_paths:
                continue
            try:
                module = _load_extension_module(extension_file, request.generation, lease)
                factory = getattr(module, "extension", None)
                if not callable(factory):
                    raise RuntimeError(
                        "Extension module must export callable extension(travis)"
                    )
                _run_extension_factory(runtime, factory, extension_path)
                loaded_by_path[extension_path] = {"path": extension_path}
            except Exception as error:  # noqa: BLE001 - extension failures are diagnostics.
                errors.append({"path": extension_path, "error": str(error)})
                failed_paths.add(extension_path)

        if preloaded is None:
            factories = () if request.no_extensions else request.factories
            for index, factory in enumerate(factories, start=1):
                extension_path = f"<inline:{index}>"
                try:
                    _run_extension_factory(runtime, factory, extension_path)
                    inline_loaded.append({"path": extension_path})
                except Exception as error:  # noqa: BLE001 - extension failures are diagnostics.
                    errors.append({"path": extension_path, "error": str(error)})

        loaded = [
            loaded_by_path[str(path)]
            for path in extension_files
            if str(path) in loaded_by_path
        ]
        loaded.extend(inline_loaded)
        result: dict[str, object] = {
            "extensions": loaded,
            "errors": errors,
            "runtime": runtime,
        }
        if request.apply_override and request.override is not None:
            result = request.override(result)
            _validate_override_result(result, runtime)
        lease._replace_result(result)
        return lease
    except Exception:
        if preloaded is None:
            lease.release()
        raise


def _result_entries(result: dict[str, object], name: str) -> list[object]:
    value = result.get(name)
    return list(value) if isinstance(value, list) else []


def _validate_override_result(
    result: object,
    runtime: ExtensionRunner,
) -> None:
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("extensions"), list)
        or not isinstance(result.get("errors"), list)
        or result.get("runtime") is not runtime
    ):
        raise TypeError(
            "extension override must return extensions, errors, and the active runtime"
        )


def _load_extension_module(
    path: Path,
    generation: int,
    lease: ExtensionRuntimeLease,
) -> ModuleType:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    module_name = f"_travis234_extension_{digest}_{generation}"
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = module_name
    module.__path__ = [str(path.parent)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    lease._add_module(module_name)
    exec(  # noqa: S102 - trusted extension execution.
        compile(path.read_text(encoding="utf-8"), str(path), "exec"),
        module.__dict__,
    )
    return module


def _run_extension_factory(
    runtime: ExtensionRunner,
    factory: Callable[[ExtensionRunner], object],
    extension_path: str,
) -> None:
    pending_start = len(runtime.pending_provider_registrations)
    owner_scope = getattr(runtime.events, "owner", None)
    scope = (
        owner_scope(runtime._event_bus_owner)  # noqa: SLF001
        if callable(owner_scope)
        else nullcontext()
    )
    api = runtime.create_extension_api(extension_path)
    with scope, runtime.source_scope(extension_path):
        result = factory(api)  # type: ignore[arg-type]
        if inspect.isawaitable(result):
            run_sync(resolve(result))
    for pending_index in range(
        pending_start, len(runtime._pending_provider_registrations)  # noqa: SLF001
    ):
        name, config, _old_path = runtime._pending_provider_registrations[  # noqa: SLF001
            pending_index
        ]
        runtime._pending_provider_registrations[pending_index] = (  # noqa: SLF001
            name,
            config,
            extension_path,
        )


__all__ = [
    "ExtensionLoadRequest",
    "ExtensionRuntimeLease",
    "create_empty_extension_runtime",
    "load_extension_runtime",
]
