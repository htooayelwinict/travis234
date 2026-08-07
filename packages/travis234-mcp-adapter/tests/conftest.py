from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


@dataclass
class ConfigTree:
    home: Path
    cwd: Path

    def _write(self, path: Path, servers: dict[str, object]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        return path

    def write_global_shared(self, name: str, value: object) -> Path:
        return self._write(self.home / ".config" / "mcp" / "mcp.json", {name: value})

    def write_global_travis(self, name: str, value: object) -> Path:
        return self._write(self.home / ".travis234" / "agent" / "mcp.json", {name: value})

    def write_project_shared(self, name: str, value: object) -> Path:
        return self._write(self.cwd / ".mcp.json", {name: value})

    def write_project_travis(self, name: str, value: object) -> Path:
        return self._write(self.cwd / ".travis234" / "mcp.json", {name: value})


@pytest.fixture
def config_tree(tmp_path: Path) -> ConfigTree:
    home = tmp_path / "home"
    cwd = tmp_path / "repo"
    home.mkdir()
    cwd.mkdir()
    return ConfigTree(home=home, cwd=cwd)
