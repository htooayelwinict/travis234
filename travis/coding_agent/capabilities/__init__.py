"""Capability contracts for workspace access and resource discovery."""

from .types import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityLoadContext,
    CapabilityProvider,
    CapabilityProviderResult,
    CapabilityRecord,
    CapabilitySource,
)
from .registry import (
    CapabilityRegistry,
    CapabilityReloadError,
    CapabilityResolution,
    CapabilitySnapshot,
)
from .workspace import AccessMode, CapabilityViolation, WorkspaceCapability

__all__ = [
    "AccessMode",
    "CapabilityDiagnostic",
    "CapabilityKind",
    "CapabilityLoadContext",
    "CapabilityProvider",
    "CapabilityProviderResult",
    "CapabilityRegistry",
    "CapabilityReloadError",
    "CapabilityResolution",
    "CapabilityRecord",
    "CapabilitySource",
    "CapabilitySnapshot",
    "CapabilityViolation",
    "WorkspaceCapability",
]
