from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "travis/resources/skills/orchestration/scripts/orchestrate.py"


def test_guide_emits_one_stable_versioned_json_envelope() -> None:
    completed = subprocess.run(
        [sys.executable, str(HELPER), "guide"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload == {
        "ok": True,
        "schemaVersion": 1,
        "protocolVersion": 1,
        "command": "guide",
        "result": {
            "commands": ["guide"],
            "invocation": "python3 scripts/orchestrate.py <command> [arguments]",
        },
        "nextActions": [],
    }
