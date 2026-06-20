"""CLI entrypoint for the pi+hermes-compliant appv22 stack."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import sys

from appv22.ai.env_config import get_default_model_for_provider, load_model_config
from appv22.ai.model_resolver import ScopedModel, resolve_cli_model, resolve_model_scope
from appv22.ai.models import get_model, get_models, get_providers, has_configured_auth
from appv22.ai.register_builtins import register_builtin_providers
from appv22.ai.types import Model
from appv22.app import CodingApp
from appv22.coding_agent.export_html import export_from_file
from appv22.tui.interactive_mode import InteractiveMode


class _CliModelRegistry:
    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def get_all(self) -> list[Model]:
        return list(self._models)

    getAll = get_all

    def get_available(self) -> list[Model]:
        return list(self._models)

    getAvailable = get_available

    def find(self, provider: str, model_id: str) -> Model | None:
        registered = get_model(provider, model_id)
        if registered is not None:
            return registered
        return next((model for model in self._models if model.provider == provider and model.id == model_id), None)

    def has_configured_auth(self, model: Model) -> bool:
        return has_configured_auth(model)

    hasConfiguredAuth = has_configured_auth


_VALID_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class _StartupModelSelection:
    model: Model
    thinking_level: str | None = None
    scoped_models: list[ScopedModel] = field(default_factory=list)


def _model_from_env(
    dotenv_path: str | Path,
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> Model:
    return _startup_model_from_env(
        dotenv_path,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_thinking=cli_thinking,
        cli_models=cli_models,
    ).model


def _startup_model_from_env(
    dotenv_path: str | Path,
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> _StartupModelSelection:
    config = load_model_config("APPV2_WORKER_LLM", dotenv_path)
    model_id = config.model or get_default_model_for_provider("openrouter") or "moonshotai/kimi-k2.6"
    env_model = Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openrouter",
        base_url=config.base_url,
        reasoning=False,
        context_window=128000,
        max_tokens=config.max_tokens or 8192,
    )
    registry = _CliModelRegistry(_registered_models_with_env_fallback(env_model))
    scoped_models = resolve_model_scope(cli_models or [], registry) if cli_models else []
    if not cli_model:
        if scoped_models:
            scoped = scoped_models[0]
            return _StartupModelSelection(
                model=scoped.model,
                thinking_level=cli_thinking or scoped.thinking_level,
                scoped_models=scoped_models,
            )
        return _StartupModelSelection(model=env_model, thinking_level=cli_thinking, scoped_models=scoped_models)

    resolved = resolve_cli_model(
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_thinking=cli_thinking,
        model_registry=registry,
    )
    if resolved.warning:
        print(f"Warning: {resolved.warning}", file=sys.stderr)
    if resolved.error:
        raise ValueError(resolved.error)
    if resolved.model is not None:
        return _StartupModelSelection(
            model=resolved.model,
            thinking_level=cli_thinking or resolved.thinking_level,
            scoped_models=scoped_models,
        )
    return _StartupModelSelection(model=env_model, thinking_level=cli_thinking, scoped_models=scoped_models)


def _registered_models_with_env_fallback(env_model: Model) -> list[Model]:
    models = [model for provider in get_providers() for model in get_models(provider)]
    if not any(model.provider == env_model.provider and model.id == env_model.id for model in models):
        models.append(env_model)
    return models


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the appv22 pi+hermes coding app")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    parser.add_argument("--cwd", default=".", help="Working directory for tools")
    parser.add_argument("--dotenv", default=".env", help="Dotenv file for APPV2_WORKER_LLM/OpenRouter settings")
    parser.add_argument("--provider", help="Provider name for --model resolution")
    parser.add_argument("--model", help='Model pattern or ID, including optional "provider/id" form')
    parser.add_argument("--models", help="Comma-separated model patterns for scoped cycling")
    parser.add_argument("--thinking", help="Set thinking level: off, minimal, low, medium, high, xhigh")
    parser.add_argument("--tui", action="store_true", help="Render live agent events with the ported differential TUI")
    parser.add_argument("--plain", action="store_true", help="Use the plain stdin loop instead of the interactive TUI")
    parser.add_argument("--export", help="Export a session JSONL file to standalone HTML and exit")
    args = parser.parse_args(argv)

    if args.export:
        output_path = args.prompt[0] if args.prompt else None
        try:
            exported_path = export_from_file(args.export, output_path)
        except Exception as error:  # noqa: BLE001 - CLI should convert export failures to an exit code.
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(f"Exported to: {exported_path}")
        return 0

    if args.thinking and args.thinking not in _VALID_THINKING_LEVELS:
        print(
            f'Warning: Invalid thinking level "{args.thinking}". '
            f"Valid values: {', '.join(_VALID_THINKING_LEVELS)}",
            file=sys.stderr,
        )
        args.thinking = None

    register_builtin_providers(dotenv_path=args.dotenv)
    try:
        startup = _startup_model_from_env(
            args.dotenv,
            cli_provider=args.provider,
            cli_model=args.model,
            cli_thinking=args.thinking,
            cli_models=_split_models_arg(args.models),
        )
    except ValueError as error:
        parser.error(str(error))
    app = CodingApp(
        cwd=str(Path(args.cwd).resolve()),
        model=startup.model,
        thinking_level=startup.thinking_level or "off",
        scoped_models=startup.scoped_models,
        enable_tui=args.tui or not args.prompt and not args.plain,
    )

    prompt = " ".join(args.prompt).strip()
    if prompt:
        app.run_turn(prompt)
        _print_last_assistant(app)
        return 0

    if not args.plain:
        return InteractiveMode(app).run()

    while True:
        try:
            prompt = input("appv22> ").strip()
        except EOFError:
            return 0
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if not prompt:
            continue
        app.run_turn(prompt)
        _print_last_assistant(app)


def _split_models_arg(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _print_last_assistant(app: CodingApp) -> None:
    for message in reversed(app.messages):
        if getattr(message, "role", None) != "assistant":
            continue
        texts = [block.text for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"]
        if texts:
            print("".join(texts))
        return


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
