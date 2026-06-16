from __future__ import annotations

import argparse
from pathlib import Path
import sys

from appv22_ui.live import LiveEventBuffer, make_printing_event_sink
from appv22_ui.renderers import create_renderer
from appv22_ui.runtime_adapter import RuntimeAdapter, RuntimeAdapterConfig
from appv22_ui.session import SessionStore


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).expanduser().resolve()
    dotenv_path = Path(args.dotenv).expanduser().resolve()
    renderer = create_renderer(args.render)
    store = SessionStore(workspace)

    if args.command == "status":
        print(renderer.render(store.load()))
        return 0

    adapter = RuntimeAdapter(
        RuntimeAdapterConfig(
            workspace=workspace,
            dotenv_path=dotenv_path,
            max_turns=args.max_turns,
            extensions=tuple(args.extension),
        )
    )

    if args.command == "run":
        loaded = store.load() if args.resume else None
        previous = loaded.get("last_result") if loaded else None
        event_sink = _event_sink(args.live)
        result = adapter.run(
            args.prompt,
            active_user_request=args.prompt,
            previous_result=previous,
            event_sink=event_sink,
        )
        store.save(result)
        print(renderer.render(result))
        return 0

    if args.command == "chat":
        return _chat(adapter, store, renderer, live=args.live)

    parser.error(f"unsupported command: {args.command}")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pi-style CLI/TUI interface for AppV2.2.")
    parser.add_argument("--workspace", default=".", help="Workspace root for the agent.")
    parser.add_argument("--dotenv", default=".env", help="AppV2 dotenv path.")
    parser.add_argument("--render", choices=("plain", "tui", "json"), default="tui")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--extension", action="append", default=["file_management"])
    parser.add_argument("--live", action="store_true", help="Render agent-loop events as they are emitted.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run one AppV22 task.")
    run.add_argument("prompt")
    run.add_argument("--resume", action="store_true", help="Continue from the last saved session context.")

    subparsers.add_parser("chat", help="Interactive prompt loop using runtime continuation.")
    subparsers.add_parser("status", help="Render the last persisted session without invoking the model.")
    return parser


def _chat(adapter: RuntimeAdapter, store: SessionStore, renderer, *, live: bool) -> int:
    previous = None
    loaded = store.load()
    if loaded:
        previous = loaded.get("last_result")
    print("AppV22 chat. Type /exit to stop, /status to render saved state.")
    while True:
        try:
            prompt = input("appv22> ").strip()
        except EOFError:
            print()
            return 0
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return 0
        if prompt == "/status":
            print(renderer.render(store.load()))
            continue
        try:
            result = adapter.run(
                prompt,
                active_user_request=prompt,
                previous_result=previous,
                event_sink=_event_sink(live),
            )
        except KeyboardInterrupt:
            print("\ninterrupted")
            return 130
        store.save(result)
        loaded = store.load()
        previous = loaded.get("last_result") if loaded else None
        print(renderer.render(result))


def _event_sink(enabled: bool):
    if not enabled:
        return None
    return make_printing_event_sink(LiveEventBuffer())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
