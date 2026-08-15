from dataclasses import FrozenInstanceError, dataclass
from threading import Event, Thread
from types import MappingProxyType

import pytest

from travis.coding_agent.capabilities.types import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityLoadContext,
    CapabilityProviderResult,
    CapabilityRecord,
    CapabilitySource,
)
from travis.coding_agent.capabilities.registry import (
    CapabilityRegistry,
    CapabilityReloadError,
)


def test_capability_kinds_cover_phase_one_and_reserved_followups() -> None:
    assert {kind.value for kind in CapabilityKind} == {
        "context_file",
        "skill",
        "prompt_template",
        "theme",
        "extension",
        "tool",
        "agent_role",
    }


def test_capability_records_and_context_are_immutable() -> None:
    source = CapabilitySource("test", "/tmp/a")
    record = CapabilityRecord(CapabilityKind.SKILL, "audit", object(), source)
    context = CapabilityLoadContext(
        "/tmp/repo",
        "/tmp/agent",
        False,
        True,
        1,
        MappingProxyType({"reason": "test"}),
    )

    with pytest.raises(FrozenInstanceError):
        record.key = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        context.data["reason"] = "changed"  # type: ignore[index]


def test_diagnostic_attribution_is_stable() -> None:
    source = CapabilitySource("skills", "/repo/SKILL.md")
    item = CapabilityDiagnostic(
        "collision",
        "skills",
        "capability_collision",
        'skill "audit" was shadowed',
        source,
    )

    assert item.source is source
    assert item.code == "capability_collision"


@dataclass
class StaticProvider:
    name: str
    priority: int
    result: CapabilityProviderResult

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        del context
        return self.result


def _record(
    provider: str,
    key: str,
    value: str,
    *,
    priority: int = 0,
    enabled: bool = True,
) -> CapabilityRecord:
    return CapabilityRecord(
        CapabilityKind.SKILL,
        key,
        value,
        CapabilitySource(provider, f"/{provider}/{key}"),
        priority=priority,
        enabled=enabled,
    )


def _context(generation: int) -> CapabilityLoadContext:
    return CapabilityLoadContext("/repo", "/agent", False, False, generation)


def test_registry_explains_precedence_and_filters_before_dedupe() -> None:
    registry = CapabilityRegistry()
    registry.register(
        StaticProvider(
            "low",
            10,
            CapabilityProviderResult(records=(_record("low", "audit", "low"),)),
        )
    )
    registry.register(
        StaticProvider(
            "high",
            20,
            CapabilityProviderResult(
                records=(
                    _record("high", "audit", "lower-priority", priority=1),
                    _record("high", "audit", "winner", priority=2),
                    _record("high", "disabled", "ignored", enabled=False),
                )
            ),
        )
    )

    snapshot = registry.reload(_context(1))
    resolution = snapshot.resolve(CapabilityKind.SKILL, "audit")

    assert resolution.winner is not None
    assert resolution.winner.value == "winner"
    assert [record.value for record in resolution.candidates] == [
        "winner",
        "lower-priority",
        "low",
    ]
    assert snapshot.resolve(CapabilityKind.SKILL, "disabled").winner is None
    assert [item.code for item in snapshot.diagnostics] == [
        "capability_collision",
        "capability_collision",
    ]


def test_disabled_provider_contributes_neither_records_nor_state() -> None:
    provider = StaticProvider(
        "optional",
        5,
        CapabilityProviderResult(
            records=(_record("optional", "audit", "hidden"),),
            state={"connected": True},
        ),
    )
    registry = CapabilityRegistry()
    registry.register(provider)
    registry.set_enabled("optional", False)

    snapshot = registry.reload(_context(1))

    assert snapshot.records(CapabilityKind.SKILL) == ()
    assert snapshot.provider_state("optional") is None


class MutableProvider:
    priority = 0

    def __init__(self, name: str) -> None:
        self.name = name
        self.result = CapabilityProviderResult()
        self.error: Exception | None = None

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        del context
        if self.error is not None:
            raise self.error
        return self.result


def test_failed_reload_keeps_snapshot_and_disposes_candidate() -> None:
    disposed: list[str] = []
    stable = MutableProvider("stable")
    failing = MutableProvider("failing")
    registry = CapabilityRegistry()
    registry.register(stable)
    registry.register(failing)
    stable.result = CapabilityProviderResult(
        records=(_record("stable", "audit", "v1"),)
    )
    first = registry.reload(_context(1))
    stable.result = CapabilityProviderResult(
        records=(_record("stable", "audit", "v2"),),
        dispose=lambda: disposed.append("v2"),
    )
    failing.error = RuntimeError("candidate failed")

    with pytest.raises(CapabilityReloadError, match="failing") as caught:
        registry.reload(_context(2))

    assert registry.snapshot is first
    assert disposed == ["v2"]
    assert caught.value.diagnostic.code == "provider_load_failed"


def test_failed_commit_keeps_snapshot_and_disposes_candidate() -> None:
    disposed: list[str] = []
    provider = MutableProvider("resources")
    registry = CapabilityRegistry()
    registry.register(provider)
    provider.result = CapabilityProviderResult(
        records=(_record("resources", "audit", "v1"),)
    )
    first = registry.reload(_context(1))
    provider.result = CapabilityProviderResult(
        records=(_record("resources", "audit", "v2"),),
        dispose=lambda: disposed.append("v2"),
    )

    with pytest.raises(CapabilityReloadError, match="commit rejected") as caught:
        registry.reload(
            _context(2),
            on_commit=lambda _snapshot: (_ for _ in ()).throw(
                RuntimeError("commit rejected")
            ),
        )

    assert registry.snapshot is first
    assert disposed == ["v2"]
    assert caught.value.diagnostic.code == "snapshot_commit_failed"


class BlockingProvider(MutableProvider):
    def __init__(self, entered: Event, release: Event) -> None:
        super().__init__("blocking")
        self._entered = entered
        self._release = release

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        if context.generation == 2:
            self._entered.set()
            if not self._release.wait(2):
                raise TimeoutError("reload was not released")
        return super().load(context)


def test_readers_see_only_complete_snapshots_during_reload() -> None:
    entered = Event()
    release = Event()
    provider = BlockingProvider(entered, release)
    registry = CapabilityRegistry()
    registry.register(provider)
    provider.result = CapabilityProviderResult(
        records=(_record("blocking", "audit", "v1"),)
    )
    first = registry.reload(_context(1))
    provider.result = CapabilityProviderResult(
        records=(_record("blocking", "audit", "v2"),)
    )
    failures: list[BaseException] = []

    def reload() -> None:
        try:
            registry.reload(_context(2))
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    worker = Thread(target=reload)
    worker.start()
    assert entered.wait(2)
    assert registry.snapshot is first
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert failures == []
    assert registry.snapshot.resolve(CapabilityKind.SKILL, "audit").winner.value == "v2"
