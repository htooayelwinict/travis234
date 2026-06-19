#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


APPV22_ROOT = Path(__file__).resolve().parents[1]
if str(APPV22_ROOT) not in sys.path:
    sys.path.insert(0, str(APPV22_ROOT))

from appv22_ui.tui_app import AppV22Tui  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JSONL bridge for the AppV2.2 Pi TUI frontend.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--extension", action="append", default=["file_management"])
    args = parser.parse_args(argv)

    app = AppV22Tui(
        workspace=Path(args.workspace).expanduser().resolve(),
        dotenv_path=Path(args.dotenv).expanduser().resolve(),
        max_turns=args.max_turns,
        extensions=tuple(args.extension),
    )

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            _emit({"type": "error", "message": f"invalid json: {exc}"})
            continue
        if not isinstance(message, dict):
            _emit({"type": "error", "message": "bridge message must be a JSON object"})
            continue

        message_type = str(message.get("type") or "")
        if message_type == "exit":
            _emit({"type": "exit"})
            return 0
        if message_type == "status":
            _emit({"type": "status", "session": _session_payload(app)})
            continue
        if message_type == "prompt":
            prompt = str(message.get("text") or "").strip()
            if not prompt:
                _emit({"type": "error", "message": "prompt text is required"})
                continue
            _run_prompt(app, prompt)
            continue
        _emit({"type": "error", "message": f"unknown bridge message type: {message_type}"})
    return 0


def _run_prompt(app: AppV22Tui, prompt: str) -> None:
    app.state.add_user(prompt)
    app.state.running = True
    app.state.mode = "START"
    previous = app._previous_result()

    def event_sink(event: dict[str, Any]) -> None:
        app.state.apply_event(event)
        _emit({"type": "event", "event": event, "session": _session_payload(app)})

    try:
        result = app.adapter.run(
            app._runtime_prompt(prompt),
            active_user_request=prompt,
            ui_context=app._ui_context_payload(),
            previous_result=previous,
            event_sink=event_sink,
        )
    except BaseException as exc:  # noqa: BLE001 - bridge must report runtime failures as JSONL.
        app.state.running = False
        app.state.status = "failed"
        app.state.reason = type(exc).__name__
        _emit({"type": "error", "message": str(exc), "session": _session_payload(app)})
        return

    app.state.apply_result(result)
    enriched = app._with_ui_context(result)
    app.store.save(enriched, conversation=app.state.conversation)
    _emit({"type": "result", "result": enriched, "session": _session_payload(app)})


def _session_payload(app: AppV22Tui) -> dict[str, Any]:
    return {
        "workspace": str(app.workspace),
        "status": app.state.status,
        "reason": app.state.reason,
        "mode": app.state.mode,
        "running": app.state.running,
        "session_id": app.state.session_id,
        "world_ref_count": app.state.world_ref_count,
        "world_refs": list(app.state.world_refs),
        "context_summary": dict(app.state.context_summary),
        "conversation_summary": app.state.conversation_summary,
        "ui_context_metrics": dict(app.state.ui_context_metrics),
        "conversation": [
            {"role": item.role, "text": item.text}
            for item in app.state.conversation[-40:]
        ],
        "events": [event.to_dict() for event in app.state.events[-80:]],
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
