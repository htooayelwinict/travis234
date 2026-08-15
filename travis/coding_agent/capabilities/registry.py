"""Explainable, atomically reloaded capability registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock, RLock
from types import MappingProxyType

from .types import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityLoadContext,
    CapabilityProvider,
    CapabilityProviderResult,
    CapabilityRecord,
)


@dataclass(frozen=True)
class CapabilityResolution:
    winner: CapabilityRecord | None
    candidates: tuple[CapabilityRecord, ...]


class CapabilityReloadError(RuntimeError):
    def __init__(self, message: str, diagnostic: CapabilityDiagnostic) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class CapabilitySnapshot:
    def __init__(
        self,
        generation: int,
        diagnostics: tuple[CapabilityDiagnostic, ...],
        records_by_kind: Mapping[CapabilityKind, tuple[CapabilityRecord, ...]],
        resolutions: Mapping[
            tuple[CapabilityKind, str], CapabilityResolution
        ],
        provider_states: Mapping[str, object],
    ) -> None:
        self.generation = generation
        self.diagnostics = diagnostics
        self._records_by_kind = MappingProxyType(dict(records_by_kind))
        self._resolutions = MappingProxyType(dict(resolutions))
        self._provider_states = MappingProxyType(dict(provider_states))

    def records(self, kind: CapabilityKind) -> tuple[CapabilityRecord, ...]:
        return self._records_by_kind.get(kind, ())

    def resolve(self, kind: CapabilityKind, key: str) -> CapabilityResolution:
        return self._resolutions.get((kind, key), CapabilityResolution(None, ()))

    def provider_state(self, provider_name: str) -> object | None:
        return self._provider_states.get(provider_name)


def _empty_snapshot(generation: int = 0) -> CapabilitySnapshot:
    return CapabilitySnapshot(generation, (), {}, {}, {})


def _build_snapshot(
    generation: int,
    loaded: list[tuple[int, CapabilityProvider, CapabilityProviderResult]],
) -> CapabilitySnapshot:
    diagnostics: list[CapabilityDiagnostic] = []
    candidates: dict[
        tuple[CapabilityKind, str], list[CapabilityRecord]
    ] = {}
    provider_states: dict[str, object] = {}

    for order, provider, result in sorted(
        loaded, key=lambda item: (-item[1].priority, item[0])
    ):
        del order
        diagnostics.extend(result.diagnostics)
        if result.state is not None:
            provider_states[provider.name] = result.state
        records = sorted(
            (record for record in result.records if record.enabled),
            key=lambda record: -record.priority,
        )
        for record in records:
            candidates.setdefault((record.kind, record.key), []).append(record)

    resolutions: dict[tuple[CapabilityKind, str], CapabilityResolution] = {}
    records_by_kind: dict[CapabilityKind, list[CapabilityRecord]] = {}
    for identity, candidate_records in candidates.items():
        frozen_candidates = tuple(candidate_records)
        winner = frozen_candidates[0]
        resolutions[identity] = CapabilityResolution(winner, frozen_candidates)
        records_by_kind.setdefault(identity[0], []).append(winner)
        for shadowed in frozen_candidates[1:]:
            diagnostics.append(
                CapabilityDiagnostic(
                    "collision",
                    shadowed.source.provider,
                    "capability_collision",
                    f'{identity[0].value} "{identity[1]}" was shadowed',
                    shadowed.source,
                )
            )

    return CapabilitySnapshot(
        generation,
        tuple(diagnostics),
        {kind: tuple(records) for kind, records in records_by_kind.items()},
        resolutions,
        provider_states,
    )


def _dispose_results(
    results: Mapping[str, CapabilityProviderResult],
    *,
    retained: Mapping[str, CapabilityProviderResult] | None = None,
) -> None:
    retained_ids = {id(result) for result in (retained or {}).values()}
    seen: set[int] = set()
    for result in results.values():
        identity = id(result)
        if identity in seen or identity in retained_ids:
            continue
        seen.add(identity)
        if result.dispose is not None:
            try:
                result.dispose()
            except Exception:
                pass


class CapabilityRegistry:
    def __init__(self) -> None:
        self._providers: list[CapabilityProvider] = []
        self._provider_names: set[str] = set()
        self._enabled: dict[str, bool] = {}
        self._seeded: dict[str, CapabilityProviderResult] = {}
        self._results: dict[str, CapabilityProviderResult] = {}
        self._snapshot = _empty_snapshot()
        self._has_reloaded = False
        self._reload_lock = Lock()
        self._state_lock = RLock()

    def register(self, provider: CapabilityProvider) -> None:
        name = getattr(provider, "name", None)
        priority = getattr(provider, "priority", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("capability provider name must be a non-empty string")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("capability provider priority must be an integer")
        with self._reload_lock:
            if self._has_reloaded:
                raise ValueError("providers cannot be registered after the first reload")
            if name in self._provider_names:
                raise ValueError(f'capability provider "{name}" is already registered')
            self._provider_names.add(name)
            self._providers.append(provider)
            self._enabled[name] = True

    def seed(self, provider_name: str, result: CapabilityProviderResult) -> None:
        with self._reload_lock:
            if self._has_reloaded:
                raise ValueError("capabilities cannot be seeded after the first reload")
            provider = self._find_provider(provider_name)
            if provider_name in self._seeded:
                raise ValueError(
                    f'capability provider "{provider_name}" was already seeded'
                )
            self._seeded[provider_name] = result
            self._results[provider_name] = result
            loaded = [
                (order, item, self._seeded[item.name])
                for order, item in enumerate(self._providers)
                if item.name in self._seeded and self._enabled[item.name]
            ]
            with self._state_lock:
                self._snapshot = _build_snapshot(0, loaded)

    def set_enabled(self, provider_name: str, enabled: bool) -> None:
        with self._reload_lock:
            self._find_provider(provider_name)
            self._enabled[provider_name] = bool(enabled)

    @property
    def snapshot(self) -> CapabilitySnapshot:
        with self._state_lock:
            return self._snapshot

    def reload(
        self,
        context: CapabilityLoadContext,
        *,
        on_commit: Callable[[CapabilitySnapshot], None] | None = None,
    ) -> CapabilitySnapshot:
        with self._reload_lock:
            self._has_reloaded = True
            loaded: list[
                tuple[int, CapabilityProvider, CapabilityProviderResult]
            ] = []
            candidate_results: dict[str, CapabilityProviderResult] = {}
            for order, provider in enumerate(self._providers):
                if not self._enabled[provider.name]:
                    continue
                try:
                    result = provider.load(context)
                except Exception as error:
                    _dispose_results(candidate_results, retained=self._results)
                    diagnostic = CapabilityDiagnostic(
                        "error",
                        provider.name,
                        "provider_load_failed",
                        f'capability provider "{provider.name}" failed',
                    )
                    raise CapabilityReloadError(
                        diagnostic.message, diagnostic
                    ) from error
                candidate_results[provider.name] = result
                loaded.append((order, provider, result))

            candidate = _build_snapshot(context.generation, loaded)
            with self._state_lock:
                old_snapshot = self._snapshot
                old_results = self._results
                self._snapshot = candidate
                self._results = candidate_results
                try:
                    if on_commit is not None:
                        on_commit(candidate)
                except Exception as error:
                    self._snapshot = old_snapshot
                    self._results = old_results
                    commit_error = error
                else:
                    commit_error = None

            if commit_error is not None:
                _dispose_results(candidate_results, retained=old_results)
                diagnostic = CapabilityDiagnostic(
                    "error",
                    "registry",
                    "snapshot_commit_failed",
                    f"capability snapshot commit failed: {commit_error}",
                )
                raise CapabilityReloadError(
                    diagnostic.message, diagnostic
                ) from commit_error

            _dispose_results(old_results, retained=candidate_results)
            return candidate

    def close(self) -> None:
        with self._reload_lock:
            with self._state_lock:
                results = self._results
                self._results = {}
                self._snapshot = _empty_snapshot(self._snapshot.generation)
            _dispose_results(results)

    def _find_provider(self, provider_name: str) -> CapabilityProvider:
        for provider in self._providers:
            if provider.name == provider_name:
                return provider
        raise ValueError(f'unknown capability provider "{provider_name}"')


__all__ = [
    "CapabilityRegistry",
    "CapabilityReloadError",
    "CapabilityResolution",
    "CapabilitySnapshot",
]
