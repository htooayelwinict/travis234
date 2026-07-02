"""CLI entrypoint for the pi+hermes-compliant appv231 stack."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
import json
import os
from pathlib import Path
import sys

from appv231.ai.env_config import get_default_model_for_provider, load_model_config
from appv231.ai.model_resolver import ScopedModel, resolve_cli_model, resolve_model_scope
from appv231.ai.models import get_model, get_models, get_providers, has_configured_auth, set_auth_credential
from appv231.ai.register_builtins import register_builtin_providers
from appv231.ai.types import Model
from appv231.app import CodingApp
from appv231.coding_agent.config import get_agent_dir, get_auth_path
from appv231.coding_agent.agent_session_services import _new_session_path
from appv231.coding_agent.export_html import export_from_file
from appv231.tui.interactive_mode import InteractiveMode


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


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _resolve_dotenv_path(dotenv_arg: str | None, *, search_start: Path | None = None) -> Path:
    if dotenv_arg is not None:
        dotenv_path = Path(dotenv_arg).expanduser()
        if dotenv_path.is_absolute():
            return dotenv_path
        base = _npm_initial_cwd() or Path.cwd()
        return (base / dotenv_path).resolve()
    current = (search_start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return Path(".env")


def _resolve_cwd_path(cwd_arg: str) -> Path:
    cwd_path = Path(cwd_arg).expanduser()
    if cwd_path.is_absolute():
        return cwd_path.resolve()
    npm_initial_cwd = _npm_initial_cwd()
    if npm_initial_cwd is not None:
        return (npm_initial_cwd / cwd_path).resolve()
    return cwd_path.resolve()


def _npm_initial_cwd() -> Path | None:
    initial_cwd = os.environ.get("INIT_CWD")
    if not initial_cwd or not os.environ.get("npm_lifecycle_event"):
        return None
    return Path(initial_cwd).expanduser().resolve()


def _load_persisted_auth_credentials() -> None:
    auth_path = Path(get_auth_path()).expanduser()
    try:
        parsed = json.loads(auth_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(parsed, dict):
        return
    for provider, credential in parsed.items():
        if isinstance(provider, str) and isinstance(credential, dict):
            set_auth_credential(provider, dict(credential))


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
    parser = argparse.ArgumentParser(description="Run the appv231 pi+hermes coding app")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    parser.add_argument("--cwd", default=".", help="Working directory for tools")
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Dotenv file for APPV2_WORKER_LLM/OpenRouter settings; defaults to nearest .env in --cwd or parents",
    )
    parser.add_argument("--provider", help="Provider name for --model resolution")
    parser.add_argument("--model", help='Model pattern or ID, including optional "provider/id" form')
    parser.add_argument("--models", help="Comma-separated model patterns for scoped cycling")
    parser.add_argument("--thinking", help="Set thinking level: off, minimal, low, medium, high, xhigh")
    parser.add_argument("--tui", action="store_true", help="Render live agent events with the ported differential TUI")
    parser.add_argument("--plain", action="store_true", help="Use the plain stdin loop instead of the interactive TUI")
    parser.add_argument(
        "--max-iterations",
        type=_positive_int_arg,
        help="Maximum tool-calling model iterations per turn (Hermes default: 90)",
    )
    parser.add_argument(
        "--tool-loop-hard-stop",
        action="store_true",
        help="Enable Hermes hard-stop thresholds for repeated failed/non-progressing tool calls",
    )
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

    cwd_path = _resolve_cwd_path(args.cwd)
    if not cwd_path.exists():
        print(f"Error: working directory does not exist: {cwd_path}", file=sys.stderr)
        return 1
    if not cwd_path.is_dir():
        print(f"Error: working directory is not a directory: {cwd_path}", file=sys.stderr)
        return 1

    if args.thinking and args.thinking not in _VALID_THINKING_LEVELS:
        print(
            f'Warning: Invalid thinking level "{args.thinking}". '
            f"Valid values: {', '.join(_VALID_THINKING_LEVELS)}",
            file=sys.stderr,
        )
        args.thinking = None

    dotenv_path = _resolve_dotenv_path(args.dotenv, search_start=cwd_path)
    register_builtin_providers(dotenv_path=dotenv_path)
    _load_persisted_auth_credentials()
    try:
        startup = _startup_model_from_env(
            dotenv_path,
            cli_provider=args.provider,
            cli_model=args.model,
            cli_thinking=args.thinking,
            cli_models=_split_models_arg(args.models),
        )
    except ValueError as error:
        parser.error(str(error))
    runtime_options: dict[str, object] = {}
    if args.max_iterations is not None:
        runtime_options["max_iterations"] = args.max_iterations
    if args.tool_loop_hard_stop:
        runtime_options["tool_loop_guardrails"] = {"hard_stop_enabled": True}
    agent_dir = get_agent_dir()
    session_path, session_id = _new_session_path(str(cwd_path), agent_dir)

    app = CodingApp(
        cwd=str(cwd_path),
        model=startup.model,
        thinking_level=startup.thinking_level or "off",
        scoped_models=startup.scoped_models,
        enable_tui=args.tui or not args.prompt and not args.plain,
        session_path=session_path,
        session_id=session_id,
        agent_dir=agent_dir,
        **runtime_options,
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
            prompt = input("appv231> ").strip()
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
