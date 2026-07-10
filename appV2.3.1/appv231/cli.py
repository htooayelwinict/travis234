"""CLI entrypoint for the pi+hermes-compliant appv231 stack."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys

from appv231.ai.env_config import ModelConfig, get_default_model_for_provider, load_model_config
from appv231.ai.model_resolver import ScopedModel, resolve_cli_model, resolve_model_scope
from appv231.ai.models import get_model, get_models, get_providers, has_configured_auth, register_model, set_auth_credential
from appv231.ai.providers.capabilities import ProviderParamWarning, build_generation_payload
from appv231.ai.providers.catalog import determine_api_mode, normalize_provider
from appv231.ai.providers.model_catalog import get_live_openrouter_models
from appv231.ai.providers.params import GenerationParams, merge_generation_params, params_from_mapping
from appv231.ai.register_builtins import register_builtin_providers
from appv231.ai.types import Model
from appv231.app import CodingApp
from appv231.coding_agent.config import get_agent_dir, get_auth_path
from appv231.coding_agent.agent_session_services import _new_session_path
from appv231.coding_agent.export_html import export_from_file
from appv231.coding_agent.eval_trace import ConversationLogWriter, EvalTraceWriter
from appv231.coding_agent.provider_control_plane import ProviderControlPlane
from appv231.tui.interactive_mode import InteractiveMode


logger = logging.getLogger(__name__)


_VALID_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


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
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> Model:
    return _startup_model_from_env(
        dotenv_path,
        config=config,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_thinking=cli_thinking,
        cli_models=cli_models,
    ).model


def _startup_model_from_env(
    dotenv_path: str | Path,
    *,
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
    model_registry=None,
) -> _StartupModelSelection:
    config = config or load_model_config("APPV231_WORKER_LLM", dotenv_path)
    env_model = _env_model_from_config(config)
    registered_models = _registered_models_with_env_fallback(env_model)
    live_models = _load_live_startup_models(
        env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
    )
    if model_registry is None:
        model_registry = ProviderControlPlane.in_memory().models
    model_registry.replace_models(_dedupe_startup_models([*registered_models, *live_models]))
    registry = model_registry
    scoped_models = resolve_model_scope(cli_models or [], registry) if cli_models else []
    if not cli_model:
        if scoped_models:
            scoped = scoped_models[0]
            return _StartupModelSelection(
                model=scoped.model,
                thinking_level=cli_thinking or scoped.thinking_level,
                scoped_models=scoped_models,
            )
        return _StartupModelSelection(
            model=_matching_live_model(env_model, live_models) or env_model,
            thinking_level=cli_thinking,
            scoped_models=scoped_models,
        )

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


def _env_model_from_config(config: ModelConfig) -> Model:
    model_id = config.model or get_default_model_for_provider("openrouter") or "moonshotai/kimi-k2.6"
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openrouter",
        base_url=config.base_url,
        reasoning=False,
        context_window=128000,
        max_tokens=config.max_tokens or 8192,
    )


def _load_live_startup_models(
    env_model: Model,
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool = False,
) -> list[Model]:
    should_fetch = _should_fetch_live_startup_models(
        env_model=env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
        list_models=list_models,
    ) or _implicit_default_openrouter_startup(
        env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
        list_models=list_models,
    )
    if not should_fetch or not _startup_live_catalog_enabled():
        return []
    try:
        return get_live_openrouter_models(base_model=env_model, force_refresh=True)
    except Exception as exc:  # noqa: BLE001 - startup must preserve custom fallback when live catalog fails.
        logger.warning("OpenRouter live model catalog unavailable during startup: %s", exc, exc_info=True)
        return []


def _should_fetch_live_startup_models(
    *,
    env_model: Model,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool,
) -> bool:
    if cli_provider:
        return normalize_provider(cli_provider) == "openrouter"
    if list_models:
        return False
    if cli_model and _model_reference_points_to_openrouter(cli_model):
        return True
    if cli_model and "/" in cli_model and normalize_provider(env_model.provider) == "openrouter":
        return True
    return any(
        _model_reference_points_to_openrouter(pattern) or _model_pattern_requests_live_catalog(pattern)
        for pattern in cli_models or []
    )


def _startup_live_catalog_enabled() -> bool:
    raw = os.environ.get("APPV231_MODEL_CATALOG_STARTUP_FETCH", "true").strip().lower()
    return raw not in _FALSE_ENV_VALUES


def _model_reference_points_to_openrouter(reference: str) -> bool:
    text = reference.strip()
    if "/" not in text:
        return False
    prefix = text.split("/", 1)[0].strip()
    return bool(prefix) and normalize_provider(prefix) == "openrouter"


def _model_pattern_requests_live_catalog(pattern: str) -> bool:
    text = pattern.strip()
    if "*" not in text and "?" not in text and "[" not in text:
        return False
    if "/" not in text:
        return False
    prefix = text.split("/", 1)[0].strip()
    return bool(prefix) and normalize_provider(prefix) == "openrouter"


def _implicit_default_openrouter_startup(
    env_model: Model,
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool,
) -> bool:
    return (
        not list_models
        and not cli_provider
        and not cli_model
        and not cli_models
        and normalize_provider(env_model.provider) == "openrouter"
    )


def _dedupe_startup_models(models: list[Model]) -> list[Model]:
    deduped: dict[tuple[str, str], Model] = {}
    for model in models:
        deduped[(model.provider, model.id)] = model
    return list(deduped.values())


def _matching_live_model(env_model: Model, live_models: list[Model]) -> Model | None:
    wanted = (normalize_provider(env_model.provider), env_model.id.lower())
    for model in live_models:
        if (normalize_provider(model.provider), model.id.lower()) == wanted:
            return model
    return None


def _hydrate_live_models_for_list(config: ModelConfig, args: argparse.Namespace) -> None:
    env_model = _env_model_from_config(config)
    cli_models = _split_models_arg(args.models)
    live_models = _load_live_startup_models(
        env_model,
        cli_provider=args.provider,
        cli_model=args.model,
        cli_models=cli_models,
        list_models=True,
    )
    if _startup_live_catalog_enabled() and not live_models and _should_fetch_live_startup_models(
        env_model=env_model,
        cli_provider=args.provider,
        cli_model=args.model,
        cli_models=cli_models,
        list_models=True,
    ):
        print("OpenRouter live model catalog unavailable; showing registered models only.", file=sys.stderr)
    for model in live_models:
        register_model(model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the appv231 pi+hermes coding app")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    parser.add_argument("--cwd", default=".", help="Working directory for tools")
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Dotenv file for APPV231_WORKER_LLM/OpenRouter settings; defaults to nearest .env in --cwd or parents",
    )
    parser.add_argument("--provider", help="Provider name for --model resolution")
    parser.add_argument("--model", help='Model pattern or ID, including optional "provider/id" form')
    parser.add_argument("--models", help="Comma-separated model patterns for scoped cycling")
    parser.add_argument("--thinking", help="Set thinking level: off, minimal, low, medium, high, xhigh")
    parser.add_argument("--list-models", action="store_true", help="List available provider/model IDs and exit")
    parser.add_argument("--verbose-models", action="store_true", help="Show model metadata with --list-models")
    parser.add_argument("--list-providers", action="store_true", help="List available providers and exit")
    parser.add_argument("--temperature", help="Override generation temperature")
    parser.add_argument("--top-p", help="Override nucleus sampling top_p")
    parser.add_argument("--max-tokens", type=_positive_int_arg, help="Override generation max tokens")
    parser.add_argument("--timeout-seconds", help="Override provider request timeout")
    parser.add_argument("--provider-sort", help="Override provider routing sort preference where supported")
    parser.add_argument("--stop", help="Comma-separated or JSON-array stop sequences")
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
    parser.add_argument(
        "--allow-package-install",
        action="store_true",
        help="Grant one package/dependency mutation for the initial turn",
    )
    parser.add_argument("--export", help="Export a session JSONL file to standalone HTML and exit")
    parser.add_argument("--event-trace", help="Write a sanitized evaluation lifecycle JSONL trace")
    parser.add_argument("--conversation-log", help="Write an authorized, secret-redacted turn transcript")
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
    try:
        config = _config_with_cli_generation_params(load_model_config("APPV231_WORKER_LLM", dotenv_path), args)
    except ValueError as error:
        parser.error(str(error))
    register_builtin_providers(dotenv_path=dotenv_path, config=config)
    _load_persisted_auth_credentials()
    provider_control_plane = ProviderControlPlane.create_default()
    if args.list_providers:
        _print_provider_list()
        return 0
    if args.list_models:
        _hydrate_live_models_for_list(config, args)
        _print_model_list(verbose=args.verbose_models)
        return 0
    try:
        startup = _startup_model_from_env(
            dotenv_path,
            config=config,
            cli_provider=args.provider,
            cli_model=args.model,
            cli_thinking=args.thinking,
            cli_models=_split_models_arg(args.models),
            model_registry=provider_control_plane.models,
        )
    except ValueError as error:
        parser.error(str(error))
    if config.api_key:
        provider_control_plane.auth.set_runtime_api_key(startup.model.provider, config.api_key)
    generation_warnings = _generation_param_warnings_for_model(startup.model, config.generation_params)
    _print_generation_param_warnings(generation_warnings)
    runtime_options: dict[str, object] = {}
    if args.max_iterations is not None:
        runtime_options["max_iterations"] = args.max_iterations
    if args.tool_loop_hard_stop:
        runtime_options["tool_loop_guardrails"] = {"blocking_enabled": True}
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
        provider_control_plane=provider_control_plane,
        event_trace=EvalTraceWriter(args.event_trace) if args.event_trace else None,
        conversation_log=ConversationLogWriter(args.conversation_log) if args.conversation_log else None,
        **runtime_options,
    )
    if args.allow_package_install:
        app.session.grant_capability("package_mutation", uses=1)

    prompt = " ".join(args.prompt).strip()
    if prompt:
        app.run_turn(prompt)
        _print_last_assistant(app)
        return 0

    if not args.plain:
        return InteractiveMode(
            app,
            generation_params=config.generation_params,
            generation_param_warnings=generation_warnings,
        ).run()

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


def _generation_params_from_args(args: argparse.Namespace) -> GenerationParams:
    values = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout_seconds,
        "provider_sort": args.provider_sort,
        "stop": args.stop,
    }
    return params_from_mapping(values, source="cli")


def _config_with_cli_generation_params(config: ModelConfig, args: argparse.Namespace) -> ModelConfig:
    cli_params = _generation_params_from_args(args)
    merged = merge_generation_params(config.generation_params, cli_params)
    return replace(
        config,
        temperature=merged.temperature if merged.temperature is not None else config.temperature,
        top_p=merged.top_p if merged.top_p is not None else config.top_p,
        max_tokens=merged.max_tokens if merged.max_tokens is not None else config.max_tokens,
        timeout_seconds=merged.timeout_seconds if merged.timeout_seconds is not None else config.timeout_seconds,
        provider_sort=merged.provider_sort if merged.provider_sort is not None else config.provider_sort,
        stop=list(merged.stop) if merged.stop else list(config.stop),
        generation_params=merged,
    )


def _print_provider_list() -> None:
    providers = sorted(set(get_providers()))
    for provider in providers:
        print(provider)


def _print_model_list(*, verbose: bool = False) -> None:
    for provider in sorted(get_providers()):
        for model in sorted(get_models(provider), key=lambda item: item.id):
            if not verbose:
                print(f"{provider}/{model.id}")
                continue
            input_types = ",".join(getattr(model, "input", []) or [])
            reasoning = "true" if getattr(model, "reasoning", False) else "false"
            print(
                f"{provider}/{model.id} "
                f"context={getattr(model, 'context_window', 0)} "
                f"max_tokens={getattr(model, 'max_tokens', 0)} "
                f"reasoning={reasoning} "
                f"input={input_types or 'text'}"
            )


def _generation_param_warnings_for_model(
    model: Model,
    params: GenerationParams,
) -> list[ProviderParamWarning]:
    try:
        payload = build_generation_payload(
            provider=model.provider,
            api_mode=determine_api_mode(model.provider, model.base_url),
            params=params,
            tools_enabled=True,
        )
    except ValueError:
        return []
    return list(payload.warnings)


def _print_generation_param_warnings(warnings: list[ProviderParamWarning]) -> None:
    for warning in warnings:
        print(
            f"Warning: generation parameter {warning.param} {warning.action}: {warning.reason}",
            file=sys.stderr,
        )


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
