from __future__ import annotations

import argparse
from pathlib import Path
import sys

from appv22_ui.textual_controller import TextualTuiController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Textual Pi/Hermes TUI for AppV2.2.")
    parser.add_argument("--workspace", default=".", help="Workspace root for the agent.")
    parser.add_argument("--dotenv", default=".env", help="AppV2 dotenv path.")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--extension", action="append", default=["file_management"])
    args = parser.parse_args(argv)
    try:
        from appv22_ui.textual_runtime import AppV22TextualApp
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            print("Textual is not installed. Run: uv sync", file=sys.stderr)
            return 2
        raise
    app = AppV22TextualApp(
        TextualTuiController(
            workspace=Path(args.workspace).expanduser().resolve(),
            dotenv_path=Path(args.dotenv).expanduser().resolve(),
            max_turns=args.max_turns,
            extensions=tuple(args.extension),
        )
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
