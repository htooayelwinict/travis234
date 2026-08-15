"""Bounded language-server contracts and runtime services."""

from travis.coding_agent.language_services.config import (
    SettingsValidationError,
    parse_language_servers,
    select_server_config,
)
from travis.coding_agent.language_services.types import (
    DocumentLocation,
    DocumentPosition,
    LanguageServerConfig,
    LanguageServiceLimits,
    NormalizedDiagnostic,
    NormalizedSymbol,
    NormalizedWorkspaceEdit,
)

__all__ = [
    "DocumentLocation",
    "DocumentPosition",
    "LanguageServerConfig",
    "LanguageServiceLimits",
    "NormalizedDiagnostic",
    "NormalizedSymbol",
    "NormalizedWorkspaceEdit",
    "SettingsValidationError",
    "parse_language_servers",
    "select_server_config",
]
