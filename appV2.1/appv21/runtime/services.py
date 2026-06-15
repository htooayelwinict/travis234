"""Composition root for AppV2.1 runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv21.context.budget import ContextBudgetManager
from appv21.context.manager import DualContextManager
from appv21.context.prompt_builder import PromptBuilder
from appv21.context.run_memory import RunMemoryBuilder
from appv21.context.selector import ContextSelector
from appv21.extensions.decomposer import DecomposerExtension
from appv21.extensions.observer import ObserverExtension
from appv21.extensions.planner import PlannerExtension
from appv21.extensions.runner import ExtensionRunner, TraceExtension
from appv21.extensions.skills import SkillRouter
from appv21.extensions.verifier import VerifierExtension
from appv21.providers.base import AgentProvider
from appv21.providers.deterministic import DeterministicWorkspaceProvider
from appv21.runtime.decision_validator import DecisionValidator
from appv21.runtime.event_bus import EventBus
from appv21.runtime.model_registry import ModelRegistry
from appv21.runtime.session_store import JsonlSessionStore
from appv21.runtime.state_machine import RuntimeStateMachine
from appv21.state.store import InMemoryEventStore
from appv21.tools.broker import ToolBroker
from appv21.tools.evidence_store import EvidenceStore
from appv21.validators.artifacts import ArtifactValidator


@dataclass
class AppV21RuntimeServices:
    root_path: Path
    broker: ToolBroker
    observer: ObserverExtension
    decomposer: DecomposerExtension
    planner: PlannerExtension
    skills: SkillRouter
    verifier: VerifierExtension
    context: DualContextManager
    context_budget: ContextBudgetManager
    context_selector: ContextSelector
    prompt_builder: PromptBuilder
    run_memory_builder: RunMemoryBuilder
    artifact_validator: ArtifactValidator
    decision_validator: DecisionValidator
    state_machine: RuntimeStateMachine
    event_store: InMemoryEventStore
    evidence_store: EvidenceStore
    event_bus: EventBus
    session_store: JsonlSessionStore
    extension_runner: ExtensionRunner
    model_registry: ModelRegistry
    provider: AgentProvider


def create_appv21_runtime_services(
    *,
    root_path: str | Path,
    session_path: str | Path | None = None,
    enable_trace_extension: bool = True,
    provider: AgentProvider | None = None,
) -> AppV21RuntimeServices:
    root = Path(root_path).resolve()
    path = Path(session_path) if session_path is not None else root / ".appv21" / "session.jsonl"
    return AppV21RuntimeServices(
        root_path=root,
        broker=ToolBroker(root_path=root),
        observer=ObserverExtension(),
        decomposer=DecomposerExtension(),
        planner=PlannerExtension(),
        skills=SkillRouter(),
        verifier=VerifierExtension(),
        context=DualContextManager(),
        context_budget=ContextBudgetManager(),
        context_selector=ContextSelector(),
        prompt_builder=PromptBuilder(),
        run_memory_builder=RunMemoryBuilder(),
        artifact_validator=ArtifactValidator(),
        decision_validator=DecisionValidator(),
        state_machine=RuntimeStateMachine(),
        event_store=InMemoryEventStore(),
        evidence_store=EvidenceStore(),
        event_bus=EventBus(),
        session_store=JsonlSessionStore(path),
        extension_runner=ExtensionRunner([TraceExtension()] if enable_trace_extension else []),
        model_registry=ModelRegistry(),
        provider=provider or DeterministicWorkspaceProvider(),
    )
