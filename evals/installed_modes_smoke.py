"""Offline print/JSON/RPC smoke for an installed Travis234 distribution."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.app import CodingApp
from travis.coding_agent.automation import run_json_mode, run_print_mode
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.rpc import RpcServer


def run_installed_modes_smoke(workspace: str | Path) -> dict[str, str]:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}

    print_output = io.StringIO()
    with _app(root / "print") as app:
        if run_print_mode(app, "smoke", print_output) != 0:
            raise RuntimeError("installed print mode failed")
    results["print"] = print_output.getvalue().strip()

    json_output = io.StringIO()
    with _app(root / "json") as app:
        if run_json_mode(app, "smoke", json_output) != 0:
            raise RuntimeError("installed JSON mode failed")
    json_frames = [json.loads(line) for line in json_output.getvalue().splitlines()]
    results["json"] = str(json_frames[-1]["text"])

    rpc_output = io.StringIO()
    rpc_input = io.StringIO(
        json.dumps({"id": "smoke", "method": "prompt", "params": {"text": "smoke"}})
        + "\n"
    )
    with _app(root / "rpc") as app:
        if RpcServer(app, rpc_input, rpc_output).run() != 0:
            raise RuntimeError("installed RPC mode failed")
    rpc_frames = [json.loads(line) for line in rpc_output.getvalue().splitlines()]
    rpc_result = next(frame["result"] for frame in reversed(rpc_frames) if "result" in frame)
    results["rpc"] = str(rpc_result["text"])
    if set(results.values()) != {"installed smoke"}:
        raise RuntimeError(f"installed mode results differ: {results}")
    return results


class _AppContext:
    def __init__(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        registry = ModelRegistry.in_memory()
        registry.runtime.clear_providers()
        registry.runtime.set_provider(
            create_faux_provider(
                lambda model, _context: text_response_events(model, "installed smoke")
            )
        )
        self.app = CodingApp(
            cwd=str(workspace),
            model=faux_model(),
            enable_tui=False,
            project_trust_override=False,
            model_registry=registry,
        )

    def __enter__(self) -> CodingApp:
        return self.app

    def __exit__(self, *_args: object) -> None:
        self.app.close()


def _app(workspace: Path) -> _AppContext:
    return _AppContext(workspace)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_installed_modes_smoke(args.workspace), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
