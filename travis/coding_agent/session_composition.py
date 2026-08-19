"""Typed dependencies for coding-session composition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_contracts import SessionFactory
from travis.coding_agent.settings_manager import SettingsManager


@dataclass(frozen=True, slots=True)
class SessionDependencies:
    """Immutable internal form of the legacy session-services mapping."""

    cwd: str
    agent_dir: str
    settings_manager: SettingsManager
    resource_loader: DefaultResourceLoader
    auth_storage: AuthStorage
    model_registry: ModelRegistry
    session_catalog: SessionCatalog
    session_path: str
    session_id: str
    operation_runtime: object | None
    diagnostics: tuple[Mapping[str, object], ...]
    session_factory: SessionFactory | None = None

    def __post_init__(self) -> None:
        if self.model_registry.auth_storage is not self.auth_storage:
            raise ValueError(
                "modelRegistry and authStorage must share the same AuthStorage"
            )

    def to_legacy_mapping(self) -> dict[str, object]:
        """Return the supported camelCase service dictionary shape."""

        services: dict[str, object] = {
            "cwd": self.cwd,
            "agentDir": self.agent_dir,
            "settingsManager": self.settings_manager,
            "resourceLoader": self.resource_loader,
            "authStorage": self.auth_storage,
            "modelRegistry": self.model_registry,
            "sessionCatalog": self.session_catalog,
            "sessionPath": self.session_path,
            "sessionId": self.session_id or None,
            "operationRuntime": self.operation_runtime,
            "diagnostics": [dict(item) for item in self.diagnostics],
        }
        if self.session_factory is not None:
            services["sessionFactory"] = self.session_factory
        return services

    @classmethod
    def from_legacy_mapping(
        cls,
        services: Mapping[str, object],
    ) -> SessionDependencies:
        """Normalize one legacy services mapping at the composition boundary."""

        settings_manager = services.get("settingsManager")
        resource_loader = services.get("resourceLoader")
        auth_storage = services.get("authStorage")
        model_registry = services.get("modelRegistry")
        session_catalog = services.get("sessionCatalog")
        if not isinstance(settings_manager, SettingsManager):
            raise TypeError("settingsManager must be a SettingsManager")
        if not isinstance(resource_loader, DefaultResourceLoader):
            raise TypeError("resourceLoader must be a DefaultResourceLoader")
        if not isinstance(auth_storage, AuthStorage):
            raise TypeError("authStorage must be an AuthStorage")
        if not isinstance(model_registry, ModelRegistry):
            raise TypeError("modelRegistry must be a ModelRegistry")
        if not isinstance(session_catalog, SessionCatalog):
            raise TypeError("sessionCatalog must be a SessionCatalog")
        raw_diagnostics = services.get("diagnostics") or ()
        if not isinstance(raw_diagnostics, (list, tuple)):
            raise TypeError("diagnostics must be a list or tuple of mappings")
        diagnostics: list[Mapping[str, object]] = []
        for item in raw_diagnostics:
            if not isinstance(item, Mapping):
                raise TypeError("diagnostics must contain mappings")
            diagnostics.append(dict(item))
        session_factory = services.get("sessionFactory") or services.get("session_factory")
        if session_factory is not None and not callable(session_factory):
            raise TypeError("sessionFactory must be callable")
        return cls(
            cwd=str(services["cwd"]),
            agent_dir=str(services["agentDir"]),
            settings_manager=settings_manager,
            resource_loader=resource_loader,
            auth_storage=auth_storage,
            model_registry=model_registry,
            session_catalog=session_catalog,
            session_path=str(services["sessionPath"]),
            session_id=str(services.get("sessionId") or ""),
            operation_runtime=services.get("operationRuntime"),
            diagnostics=tuple(diagnostics),
            session_factory=cast(SessionFactory | None, session_factory),
        )


__all__ = ["SessionDependencies"]
