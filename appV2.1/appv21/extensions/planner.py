"""Planner extension for AppV2.1.

Planner is advisory. It only plans from observed world state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from appv21.state.models import AgentState


class PlannerExtension:
    def plan_next(self, state: AgentState) -> dict[str, Any]:
        repo_ref = state.world.refs.get("world://repo_snapshot/latest")
        if repo_ref is None:
            return {"needs_observation": ["repo_snapshot"], "steps": []}
        snapshot = repo_ref.payload
        files = [path for path in snapshot.get("files", []) if isinstance(path, str)]
        existing_files = set(files)
        planned_destinations: dict[str, str] = {}
        operations: list[dict[str, Any]] = []
        held: list[str] = []
        collisions: list[dict[str, str]] = []
        manifest_moves: list[dict[str, str]] = []

        for path in files:
            if _is_preserved_source(path):
                continue
            lower = path.lower()
            if "old_blob" in lower or "do_not_move" in lower or "keep" in lower:
                held.append(path)
                continue
            destination = _destination_for(path)
            if destination is None or destination == path:
                continue
            if destination in planned_destinations:
                held.append(path)
                collisions.append({"source": path, "destination": destination, "conflicts_with": planned_destinations[destination]})
                continue
            if destination in existing_files:
                held.append(path)
                collisions.append({"source": path, "destination": destination, "conflicts_with": destination})
                continue
            planned_destinations[destination] = path
            operations.append({"action": "move", "source": path, "destination": destination})
            manifest_moves.append({"source": path, "destination": destination})

        manifest = {
            "generated_by": "appv21",
            "moves": manifest_moves,
            "held": held,
            "collisions": collisions,
        }
        operations.append(
            {
                "action": "write",
                "path": "docs/workspace_manifest.json",
                "content": json.dumps(manifest, indent=2, sort_keys=True),
            }
        )
        return {
            "intent": "workspace cleanup from observed repo state",
            "steps": [
                {"mode": "OBSERVE", "done": True},
                {"mode": "ACT", "operation_batch_id": "workspace_cleanup", "operation_count": len(operations)},
                {"mode": "VERIFY", "manifest_path": "docs/workspace_manifest.json"},
            ],
            "mutation_intent": {"operation_batch_id": "workspace_cleanup", "operations": operations},
            "verification_intent": {"manifest_path": "docs/workspace_manifest.json", "moves": manifest_moves, "held": held},
            "unknowns": [f"collision:{item['source']}->{item['destination']}" for item in collisions],
        }


def _destination_for(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    name = Path(path).name
    if suffix == ".md":
        return f"docs/{name}"
    if suffix in {".json", ".log"}:
        return f"artifacts/logs/{name}"
    if path.startswith("tmp/"):
        return f"artifacts/tmp/{name}"
    return None


def _is_preserved_source(path: str) -> bool:
    if path == "README.md" or path == "docs/workspace_manifest.json":
        return True
    if path.startswith(("tests/", "src/", "assets/", "secrets/", "docs/")):
        return True
    filename = Path(path).name.lower()
    return filename.startswith(("keep", "do_not_move", "old_blob"))
