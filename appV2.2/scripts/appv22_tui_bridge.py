#!/usr/bin/env python3
"""JSONL/plain bridge for the new pi+hermes appv22 stack.

Compatibility entrypoint for users or frontends that still call the old bridge
path. This is not the deleted legacy runtime bridge; it delegates to `CodingApp`.
Input lines:
- JSON: {"type":"prompt","text":"..."}
- Plain text: treated as a prompt
- /exit, /quit, or JSON {"type":"exit"}: exits
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


APPV22_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APPV22_ROOT.parent
if str(APPV22_ROOT) not in sys.path:
    sys.path.insert(0, str(APPV22_ROOT))


def _maybe_reexec_project_python() -> None:
    if os.getenv("APPV22_NO_VENV_REEXEC") == "1":
        return
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_maybe_reexec_project_python()

from appv22.ai.env_config import load_model_config  # noqa: E402
from appv22.ai.register_builtins import register_builtin_providers  # noqa: E402
from appv22.ai.types import Model  # noqa: E402
from appv22.app import CodingApp  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSONL bridge for appv22 CodingApp")
    parser.add_argument("--workspace", "--cwd", dest="cwd", default=".")
    parser.add_argument("--dotenv", default=".env")
    args = parser.parse_args(argv)

    dotenv_path = _resolve_path(args.dotenv)
    cwd = _resolve_path(args.cwd)
    register_builtin_providers(dotenv_path=str(dotenv_path))
    app = CodingApp(cwd=str(cwd), model=_model_from_env(dotenv_path), enable_tui=False)

    _emit({"type": "ready", "workspace": str(cwd)})
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = _parse_input_line(line)
            if message.get("type") in {"exit", "quit"}:
                _emit({"type": "exit"})
                return 0
            if message.get("type") != "prompt":
                _emit({"type": "error", "message": f"unsupported message type: {message.get('type')}"})
                continue
            prompt = str(message.get("text") or "").strip()
            if not prompt:
                _emit({"type": "error", "message": "prompt text is required"})
                continue
            _emit({"type": "event", "event": {"kind": "AgentStarted", "title": "agent started", "payload": {"status": "started"}}})
            app.run_turn(prompt)
            _emit({"type": "result", "result": _result_payload(app)})
        except Exception as exc:  # noqa: BLE001 - bridge reports errors as JSONL
            _emit({"type": "error", "message": str(exc)})
    return 0


def _parse_input_line(line: str) -> dict[str, Any]:
    if line in {"/exit", "/quit", "exit", "quit"}:
        return {"type": "exit"}
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "prompt", "text": line}
    if not isinstance(parsed, dict):
        return {"type": "error", "message": "input JSON must be an object"}
    return parsed


def _result_payload(app: CodingApp) -> dict[str, Any]:
    assistant = _last_assistant_text(app)
    tool_ids = [getattr(message, "tool_name", "") for message in app.messages if getattr(message, "role", None) == "toolResult"]
    return {
        "assistant_message": assistant,
        "status": "completed",
        "tool_ids": [tool_id for tool_id in tool_ids if tool_id],
        "tool_result_count": len(tool_ids),
    }


def _last_assistant_text(app: CodingApp) -> str:
    for message in reversed(app.messages):
        if getattr(message, "role", None) != "assistant":
            continue
        return "".join(block.text for block in getattr(message, "content", []) if getattr(block, "type", None) == "text")
    return ""


def _model_from_env(dotenv_path: Path) -> Model:
    config = load_model_config("APPV2_WORKER_LLM", dotenv_path)
    model_id = config.model or "xiaomi/mimo-v2.5-pro"
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openrouter",
        base_url=config.base_url,
        context_window=128000,
        max_tokens=config.max_tokens or 8192,
    )


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
