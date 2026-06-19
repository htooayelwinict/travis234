from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from appv22.ai.providers.faux import create_faux_provider, text_response_events
from appv22.ai.stream import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_bridge_plain_parser_accepts_raw_text() -> None:
    import importlib.util

    bridge_path = Path(__file__).resolve().parents[1] / "scripts" / "appv22_tui_bridge.py"
    spec = importlib.util.spec_from_file_location("appv22_tui_bridge", bridge_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._parse_input_line("hi") == {"type": "prompt", "text": "hi"}
    assert module._parse_input_line("/exit") == {"type": "exit"}
    assert module._parse_input_line('{"type":"prompt","text":"hi"}') == {"type": "prompt", "text": "hi"}


def test_bridge_help_runs() -> None:
    bridge_path = Path(__file__).resolve().parents[1] / "scripts" / "appv22_tui_bridge.py"
    completed = subprocess.run(
        [sys.executable, str(bridge_path), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "JSONL bridge" in completed.stdout
