"""Pi-style interface layer for AppV2.2.

This package is intentionally outside the AppV2.2 runtime.  It adapts
runtime results into CLI/TUI views without owning planning, compaction, or
tool execution.
"""

from appv22_ui.runtime_adapter import RuntimeAdapter, RuntimeAdapterConfig

__all__ = ["RuntimeAdapter", "RuntimeAdapterConfig"]
