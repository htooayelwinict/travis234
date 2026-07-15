"""Deterministic two-process smoke for CLI/TUI session continuation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


_RESULT_PREFIX = "TRAVIS234_SESSION_SMOKE="


def run_smoke(*, workspace: Path, agent_dir: Path, marker: str) -> dict[str, object]:
    workspace = workspace.expanduser().resolve()
    agent_dir = agent_dir.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)

    first = _run_worker(workspace=workspace, agent_dir=agent_dir, marker=marker, continue_session=False)
    continued = _run_worker(workspace=workspace, agent_dir=agent_dir, marker=marker, continue_session=True)
    jsonl_files = list((agent_dir / "sessions").rglob("*.jsonl"))
    return {
        "first_exit_code": first["exit_code"],
        "continued_exit_code": continued["exit_code"],
        "first_session_path": first["session_path"],
        "continued_session_path": continued["session_path"],
        "first_session_id": first["session_id"],
        "continued_session_id": continued["session_id"],
        "jsonl_count": len(jsonl_files),
        "restored_marker": continued["assistant_text"],
        "pre_compaction_tokens": first["pre_compaction_usage"]["tokens"],
        "post_compaction_tokens": first["post_compaction_usage"]["tokens"],
        "post_compaction_confidence": first["post_compaction_usage"]["confidence"],
        "next_prompt_tokens": continued["context_usage"]["tokens"],
        "next_prompt_confidence": continued["context_usage"]["confidence"],
        "follow_up_delta": abs(
            continued["context_usage"]["tokens"]
            - first["post_compaction_usage"]["tokens"]
        ),
    }


def _run_worker(
    *,
    workspace: Path,
    agent_dir: Path,
    marker: str,
    continue_session: bool,
) -> dict[str, object]:
    package_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(package_root), *([existing_pythonpath] if existing_pythonpath else [])]
    )
    environment.pop("TRAVIS234_CODING_AGENT_SESSION_DIR", None)
    command = [
        sys.executable,
        "-m",
        "evals.session_resume_smoke",
        "--worker",
        "--workspace",
        str(workspace),
        "--agent-dir",
        str(agent_dir),
        "--marker",
        marker,
    ]
    if continue_session:
        command.append("--continue-session")
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"session smoke worker failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line[len(_RESULT_PREFIX) :])
    raise RuntimeError("session smoke worker produced no result")


def _worker(*, workspace: Path, agent_dir: Path, marker: str, continue_session: bool) -> int:
    import travis.cli as cli
    from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
    from travis.ai.context_estimate import estimate_full_context_tokens
    from travis.ai.types import TextContent, UserMessage, now_ms
    from travis.app import CodingApp
    from travis.coding_agent.model_registry import ModelRegistry
    from travis.tui.interactive_mode import InteractiveMode
    from travis.tui.terminal import FakeTerminal

    model = faux_model()
    model.context_window = 128_000
    model.max_tokens = 8_192

    def message_text(message: object) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        return " ".join(block.text for block in content if isinstance(block, TextContent))

    def provider_script(active_model, context):
        transcript = "\n".join(message_text(message) for message in context.messages)
        match = re.search(r"remember-[a-zA-Z0-9-]+", transcript)
        response = match.group(0) if match else "marker-not-found"
        events = text_response_events(active_model, response)
        prompt_tokens = estimate_full_context_tokens(context).tokens
        events[-1].message.usage.input = prompt_tokens
        events[-1].message.usage.total_tokens = (
            prompt_tokens + events[-1].message.usage.output
        )
        return events

    def create_registry(auth_storage, models_path, *, provider_config=None):
        registry = ModelRegistry(
            auth_storage,
            models_path,
            provider_config=provider_config,
        )
        registry.runtime.clear_providers()
        registry.runtime.set_provider(create_faux_provider(provider_script))
        return registry

    os.environ["TRAVIS234_CODING_AGENT_DIR"] = str(agent_dir)
    os.environ["TRAVIS234_MODEL_CATALOG_STARTUP_FETCH"] = "false"
    cli.ModelRegistry.create = staticmethod(create_registry)
    cli._startup_model_from_env = lambda dotenv_path, **kwargs: cli._StartupModelSelection(model=model)

    captured: dict[str, object] = {}

    def app_factory(**kwargs):
        app = CodingApp(terminal=FakeTerminal(columns=140, rows=40), **kwargs)
        app.session.model.context_window = model.context_window
        app.session.model.max_tokens = model.max_tokens
        app.compressor.update_context_window(
            model.context_window,
            max_tokens=model.max_tokens,
            model=f"{model.provider}/{model.id}",
        )
        captured["app"] = app
        if not continue_session:
            seed_messages = [
                UserMessage(
                    content=f"historical seed {index} " + ("x" * 2_000),
                    timestamp=now_ms() + index,
                )
                for index in range(80)
            ]
            app.session.agent.state.messages.extend(seed_messages)
            if app.session._session_store is not None:
                for message in seed_messages:
                    app.session._session_store.append_message(message)
            original_compact = app.session.compact

            def tracked_compact(*args, **compact_kwargs):
                captured["pre_compaction_usage"] = app.session.get_context_usage()
                status = original_compact(*args, **compact_kwargs)
                captured["post_compaction_usage"] = app.session.get_context_usage()
                return status

            app.session.compact = tracked_compact
        return app

    first_prompt = f"Remember this token for later: {marker}. Confirm once."
    continued_prompt = "What token did I ask you to remember earlier? Reply with only the token."
    inputs = iter(
        [continued_prompt, "/session", "/exit"]
        if continue_session
        else [first_prompt, "/compact", "/session", "/exit"]
    )

    def mode_factory(app, **kwargs):
        return InteractiveMode(app, input_fn=lambda _prompt: next(inputs), **kwargs)

    cli.CodingApp = app_factory
    cli.InteractiveMode = mode_factory
    argv = ["--cwd", str(workspace)]
    if continue_session:
        argv.append("--continue")
    exit_code = cli.main(argv)
    app = captured["app"]
    assistant_text = ""
    for message in reversed(app.messages):
        if getattr(message, "role", None) != "assistant":
            continue
        assistant_text = message_text(message)
        if assistant_text:
            break
    result = {
        "exit_code": exit_code,
        "session_path": app.session.session_path,
        "session_id": app.session.session_id,
        "assistant_text": assistant_text,
        "context_usage": app.session.get_context_usage(),
    }
    if not continue_session:
        result["pre_compaction_usage"] = captured.get("pre_compaction_usage")
        result["post_compaction_usage"] = captured.get("post_compaction_usage")
    print(f"{_RESULT_PREFIX}{json.dumps(result, separators=(',', ':'))}")
    return int(exit_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--agent-dir", type=Path, required=True)
    parser.add_argument("--marker", default="remember-7f31")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--continue-session", action="store_true")
    args = parser.parse_args(argv)
    if args.worker:
        return _worker(
            workspace=args.workspace.resolve(),
            agent_dir=args.agent_dir.resolve(),
            marker=args.marker,
            continue_session=args.continue_session,
        )
    print(json.dumps(run_smoke(workspace=args.workspace, agent_dir=args.agent_dir, marker=args.marker), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
