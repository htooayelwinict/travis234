from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services


@dataclass(frozen=True)
class RuntimeAdapterConfig:
    workspace: Path
    dotenv_path: Path
    max_turns: int = 12
    extensions: tuple[str, ...] = field(default_factory=lambda: ("file_management",))


class RuntimeAdapter:
    """Thin UI-facing wrapper around AppV22 public runtime APIs."""

    def __init__(self, config: RuntimeAdapterConfig) -> None:
        self.config = config

    def run(
        self,
        prompt: str,
        *,
        active_user_request: str | None = None,
        ui_context: dict[str, Any] | None = None,
        previous_result: dict[str, Any] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtime(event_sink=event_sink)
        if previous_result:
            return runtime.continue_run(
                previous_result,
                prompt,
                active_user_request=active_user_request,
                ui_context=ui_context,
            )
        return runtime.run(prompt, active_user_request=active_user_request, ui_context=ui_context)

    def _runtime(self, *, event_sink: Callable[[dict[str, Any]], None] | None = None) -> AppV22AgentRuntime:
        provider = create_appv22_provider_from_appv2_env(self.config.dotenv_path)
        services = create_appv22_services(
            root_path=self.config.workspace,
            provider=provider,
            extensions=self._extensions(),
        )
        return AppV22AgentRuntime(
            root_path=self.config.workspace,
            services=services,
            max_turns=self.config.max_turns,
            event_sink=event_sink,
        )

    def _extensions(self) -> list[object]:
        extensions: list[object] = []
        for extension_id in self.config.extensions:
            if extension_id == "file_management":
                extensions.append(FileManagementExtension())
            else:
                raise ValueError(f"unsupported AppV22 UI extension: {extension_id}")
        return extensions
