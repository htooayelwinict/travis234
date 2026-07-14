"""CLI entrypoint for the Travis234 terminal coding agent."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from travis.ai.env_config import ModelConfig, get_default_model_for_provider, load_dotenv_values, load_model_config
from travis.ai.model_resolver import ScopedModel, resolve_cli_model, resolve_model_scope
from travis.ai.providers.capabilities import ProviderParamWarning, build_generation_payload
from travis.ai.providers.catalog import determine_api_mode, normalize_provider, provider_catalog
from travis.ai.providers.params import GenerationParams, merge_generation_params, params_from_mapping
from travis.ai.types import Model
from travis.app import CodingApp
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.config import get_agent_dir, get_auth_path, get_models_path
from travis.coding_agent.export_html import export_from_file
from travis.coding_agent.eval_trace import ConversationLogWriter, EvalTraceWriter, SecretRedactor
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.session_catalog import SessionCatalog, SessionCatalogError
from travis.tui.interactive_mode import InteractiveMode


_VALID_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


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


@dataclass(frozen=True)
class _StartupModelSelection:
    model: Model
    thinking_level: str | None = None
    scoped_models: list[ScopedModel] = field(default_factory=list)


@dataclass(frozen=True)
class _StartupSessionSelection:
    cwd: Path
    session_path: str | None
    session_id: str | None
    persistent: bool
    open_resume_picker: bool = False


def _resolve_startup_session(
    args: argparse.Namespace,
    *,
    cwd: Path,
    cwd_was_explicit: bool,
    launch_dir: Path,
    catalog: SessionCatalog,
) -> _StartupSessionSelection:
    if args.resume_session:
        if args.plain or args.prompt:
            raise ValueError("--resume requires interactive TUI mode without an initial prompt")
        return _StartupSessionSelection(
            cwd=cwd,
            session_path=None,
            session_id=None,
            persistent=False,
            open_resume_picker=True,
        )
    if args.no_session:
        return _StartupSessionSelection(cwd, None, None, False)
    if args.continue_session:
        info = catalog.continue_recent(str(cwd))
        return _StartupSessionSelection(cwd, str(info.path), info.session_id, True)
    if args.session_target:
        info = catalog.resolve(args.session_target, cwd=str(cwd), launch_dir=str(launch_dir))
        selected_cwd = cwd if cwd_was_explicit else info.cwd
        if not selected_cwd.exists():
            raise ValueError(
                f"session working directory does not exist: {selected_cwd}. "
                "Pass --cwd to override it."
            )
        if not selected_cwd.is_dir():
            raise ValueError(f"session working directory is not a directory: {selected_cwd}")
        return _StartupSessionSelection(selected_cwd, str(info.path), info.session_id, True)
    session_path, session_id = catalog.new_session_path(str(cwd))
    return _StartupSessionSelection(cwd, session_path, session_id, True)


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
    config = config or load_model_config("TRAVIS234_WORKER_LLM", dotenv_path)
    if model_registry is None:
        model_registry = ModelRegistry.in_memory(provider_config=config)
    env_model = _env_model_from_config(config, model_registry=model_registry)
    registered_models = _registered_models_with_env_fallback(
        env_model,
        model_registry.snapshot(),
    )
    model_registry.replace_all(_dedupe_startup_models(registered_models))
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
            model=env_model,
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


def _registered_models_with_env_fallback(
    env_model: Model,
    registered_models: Iterable[Model] | None = None,
) -> list[Model]:
    models = list(registered_models or [])
    for index, model in enumerate(models):
        if (model.provider, model.id) == (env_model.provider, env_model.id):
            models[index] = env_model
            break
    else:
        models.append(env_model)
    return models


def _env_model_from_config(config: ModelConfig, *, model_registry: ModelRegistry | None = None) -> Model:
    provider = normalize_provider(config.provider) or "openrouter"
    model_id = config.model or get_default_model_for_provider(provider) or "moonshotai/kimi-k2.6"
    registry = model_registry or ModelRegistry.in_memory(provider_config=config)
    catalog_model = registry.find(provider, model_id)
    if catalog_model is not None:
        updates: dict[str, object] = {}
        if config.base_url:
            updates["base_url"] = config.base_url
        if config.context_window is not None:
            updates["context_window"] = config.context_window
        if config.max_tokens is not None:
            updates["max_tokens"] = config.max_tokens
        return replace(catalog_model, **updates)
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url=config.base_url,
        reasoning=False,
        context_window=config.context_window or 128000,
        max_tokens=config.max_tokens or 8192,
    )


def _dedupe_startup_models(models: list[Model]) -> list[Model]:
    deduped: dict[tuple[str, str], Model] = {}
    for model in models:
        deduped[(model.provider, model.id)] = model
    return list(deduped.values())


def _hydrate_models_for_list(config: ModelConfig, model_registry) -> None:
    env_model = _env_model_from_config(config, model_registry=model_registry)
    model_registry.replace_all(
        _dedupe_startup_models([*model_registry.snapshot(), env_model])
    )


def _copy_extension_resources(source, destination: Path) -> None:
    destination.mkdir(exist_ok=True)
    for item in source.iterdir():
        if item.name == "__pycache__" or item.name.endswith((".pyc", ".pyo")):
            continue
        target = destination / item.name
        if item.is_dir():
            _copy_extension_resources(item, target)
            continue
        with item.open("rb") as source_file, target.open("wb") as target_file:
            shutil.copyfileobj(source_file, target_file)


def _install_first_party_extension(name: str, agent_dir: str) -> Path:
    source = resources.files("travis").joinpath("resources", "extensions", name)
    if not source.is_dir():
        raise ValueError(f"unknown first-party extension: {name}")
    parent = Path(agent_dir).expanduser() / "extensions"
    destination = parent / name
    if destination.exists():
        raise FileExistsError(f"extension destination already exists: {destination}")
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=parent))
    try:
        _copy_extension_resources(source, temporary)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Travis234 terminal coding agent")
    parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    parser.add_argument("--cwd", default=None, help="Working directory for tools")
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Dotenv file for Travis234 worker, compression, and provider settings; defaults to nearest .env in --cwd or parents",
    )
    parser.add_argument("--provider", help="Provider name for --model resolution")
    parser.add_argument("--model", help='Model pattern or ID, including optional "provider/id" form')
    parser.add_argument("--models", help="Comma-separated model patterns for scoped cycling")
    parser.add_argument("--thinking", help="Set thinking level: off, minimal, low, medium, high, xhigh, max")
    parser.add_argument("--list-models", action="store_true", help="List available provider/model IDs and exit")
    parser.add_argument("--verbose-models", action="store_true", help="Show model metadata with --list-models")
    parser.add_argument("--list-providers", action="store_true", help="List available providers and exit")
    parser.add_argument("--temperature", help="Override generation temperature")
    parser.add_argument("--top-p", help="Override nucleus sampling top_p")
    parser.add_argument("--max-tokens", type=_positive_int_arg, help="Override generation max tokens")
    parser.add_argument("--timeout-seconds", help="Override provider request timeout")
    parser.add_argument("--provider-sort", help="Override provider routing sort preference where supported")
    parser.add_argument("--stop", help="Comma-separated or JSON-array stop sequences")
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue the most recent session for --cwd",
    )
    session_group.add_argument(
        "-r",
        "--resume",
        dest="resume_session",
        action="store_true",
        help="Browse and select a previous session",
    )
    session_group.add_argument("--session", dest="session_target", help="Open a session path or ID")
    session_group.add_argument("--no-session", action="store_true", help="Run without session persistence")
    parser.add_argument("--tui", action="store_true", help="Render live agent events with the ported differential TUI")
    parser.add_argument("--plain", action="store_true", help="Use the plain stdin loop instead of the interactive TUI")
    parser.add_argument("--export", help="Export a session JSONL file to standalone HTML and exit")
    parser.add_argument("--event-trace", help="Write a sanitized evaluation lifecycle JSONL trace")
    parser.add_argument("--conversation-log", help="Write an authorized, secret-redacted turn transcript")
    parser.add_argument(
        "--install-extension",
        choices=("hypa",),
        help="Install an optional first-party extension into the Travis234 agent directory and exit",
    )
    args = parser.parse_args(argv)

    if args.install_extension:
        try:
            installed = _install_first_party_extension(args.install_extension, get_agent_dir())
        except (OSError, ValueError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(f"Installed {args.install_extension} extension: {installed}")
        return 0

    if args.export:
        output_path = args.prompt[0] if args.prompt else None
        try:
            exported_path = export_from_file(args.export, output_path)
        except Exception as error:  # noqa: BLE001 - CLI should convert export failures to an exit code.
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(f"Exported to: {exported_path}")
        return 0

    cwd_was_explicit = args.cwd is not None
    launch_dir = (_npm_initial_cwd() or Path.cwd()).resolve()
    cwd_path = _resolve_cwd_path(args.cwd or ".")
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

    agent_dir = get_agent_dir()
    session_catalog = SessionCatalog(agent_dir)
    try:
        startup_session = _resolve_startup_session(
            args,
            cwd=cwd_path,
            cwd_was_explicit=cwd_was_explicit,
            launch_dir=launch_dir,
            catalog=session_catalog,
        )
    except (SessionCatalogError, ValueError) as error:
        parser.error(str(error))
    cwd_path = startup_session.cwd

    dotenv_path = _resolve_dotenv_path(args.dotenv, search_start=cwd_path)
    try:
        config = _config_with_cli_generation_params(load_model_config("TRAVIS234_WORKER_LLM", dotenv_path), args)
        compression_config = load_model_config("TRAVIS234_COMPRESSION_LLM", dotenv_path)
    except ValueError as error:
        parser.error(str(error))
    auth_storage = AuthStorage.create(get_auth_path())
    model_registry = ModelRegistry.create(
        auth_storage,
        get_models_path(),
        provider_config=config,
    )
    provider_dotenv_secrets = _register_dotenv_provider_credentials(model_registry, dotenv_path)
    if args.list_providers:
        _print_provider_list(model_registry)
        return 0
    if args.list_models:
        _hydrate_models_for_list(config, model_registry)
        _print_model_list(model_registry, verbose=args.verbose_models)
        return 0
    try:
        startup = _startup_model_from_env(
            dotenv_path,
            config=config,
            cli_provider=args.provider,
            cli_model=args.model,
            cli_thinking=args.thinking,
            cli_models=_split_models_arg(args.models),
            model_registry=model_registry,
        )
    except ValueError as error:
        parser.error(str(error))
    if config.api_key:
        auth_storage.set_runtime_api_key(config.provider, config.api_key)
    evaluation_redactor = SecretRedactor(
        [
            secret
            for secret in [config.api_key, compression_config.api_key, *provider_dotenv_secrets]
            if secret
        ]
    )
    generation_warnings = _generation_param_warnings_for_model(startup.model, config.generation_params)
    _print_generation_param_warnings(generation_warnings)
    runtime_options: dict[str, object] = {}
    if compression_config.enabled:
        runtime_options.update(
            {
                "compression_model": _env_model_from_config(
                    compression_config,
                    model_registry=model_registry,
                ),
                "compression_api_key": compression_config.api_key,
                "compression_timeout_seconds": compression_config.timeout_seconds,
                "compression_generation_params": compression_config.generation_params,
            }
        )
    app = CodingApp(
        cwd=str(cwd_path),
        model=startup.model,
        thinking_level=startup.thinking_level or "off",
        scoped_models=startup.scoped_models,
        enable_tui=args.tui or not args.prompt and not args.plain,
        session_path=startup_session.session_path,
        session_id=startup_session.session_id,
        agent_dir=agent_dir,
        model_registry=model_registry,
        event_trace=(
            EvalTraceWriter(args.event_trace, redactor=evaluation_redactor)
            if args.event_trace
            else None
        ),
        conversation_log=(
            ConversationLogWriter(args.conversation_log, redactor=evaluation_redactor)
            if args.conversation_log
            else None
        ),
        **runtime_options,
    )
    try:
        return _run_configured_app(
            app,
            args,
            config,
            generation_warnings,
            open_resume_picker=startup_session.open_resume_picker,
        )
    finally:
        close = getattr(app, "close", None)
        if callable(close):
            close()


def _run_configured_app(
    app,
    args: argparse.Namespace,
    config: ModelConfig,
    generation_warnings: list[str],
    *,
    open_resume_picker: bool,
) -> int:
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
            open_resume_picker=open_resume_picker,
        ).run()

    while True:
        try:
            prompt = input("travis> ").strip()
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


def _register_dotenv_provider_credentials(
    model_registry: ModelRegistry,
    dotenv_path: str | Path,
) -> list[str]:
    """Bind explicit dotenv credentials to their catalog provider only."""
    values = load_dotenv_values(dotenv_path)
    registered: list[str] = []
    for descriptor in provider_catalog():
        base_url = (
            os.environ.get(descriptor.base_url_env_var)
            or values.get(descriptor.base_url_env_var)
            if descriptor.base_url_env_var
            else None
        )
        if base_url:
            model_registry.set_runtime_provider_override(
                descriptor.slug,
                base_url=base_url,
            )
        api_key = next(
            (
                value
                for key in descriptor.api_key_env_vars
                if (value := os.environ.get(key) or values.get(key))
            ),
            None,
        )
        if not api_key:
            continue
        model_registry.auth_storage.set_runtime_api_key(descriptor.slug, api_key)
        registered.append(api_key)
    return registered


def _print_provider_list(model_registry) -> None:
    providers = sorted(set(model_registry.get_providers()))
    for provider in providers:
        print(provider)


def _print_model_list(model_registry, *, verbose: bool = False) -> None:
    for provider in sorted(model_registry.get_providers()):
        models = [model for model in model_registry.snapshot() if model.provider == provider]
        for model in sorted(models, key=lambda item: item.id):
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
