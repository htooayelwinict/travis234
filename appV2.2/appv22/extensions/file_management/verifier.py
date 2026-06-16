from __future__ import annotations

import json
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import MANIFEST_PATH, _outside
from appv22.extensions.file_management.schemas import WORKSPACE_MANIFEST_SCHEMA


class WorkspaceManifestVerifier:
    capability_id = "file_management.manifest_verifier"

    def verify(self, *, root_path, verification_intent: dict) -> dict:
        root = Path(root_path).resolve()
        if isinstance(verification_intent.get("created_files"), list):
            return _verify_created_files(root, verification_intent["created_files"])

        relative = verification_intent.get("manifest_path", MANIFEST_PATH)
        checks: list[dict[str, object]] = []
        if _outside(root, str(relative)):
            return {
                "status": "failed",
                "checks": [{"name": "manifest_path_inside_root", "passed": False}],
                "manifest": {},
            }

        manifest_path = root / str(relative)
        exists = manifest_path.is_file()
        checks.append({"name": "manifest_exists", "passed": exists})
        manifest = {}
        if exists:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                checks.append({"name": "manifest_json_valid", "passed": isinstance(manifest, dict)})
                if not isinstance(manifest, dict):
                    manifest = {}
            except json.JSONDecodeError:
                checks.append({"name": "manifest_json_valid", "passed": False})
                manifest = {}

        for key in WORKSPACE_MANIFEST_SCHEMA["required"]:
            checks.append({"name": f"manifest_has_{key}", "passed": key in manifest})
        for key, spec in WORKSPACE_MANIFEST_SCHEMA["properties"].items():
            if key in manifest:
                checks.append({"name": f"manifest_type_{key}", "passed": _matches_type(manifest[key], spec["type"])})
        for key in ("moves", "held", "collisions"):
            if key in verification_intent:
                checks.append({"name": f"verification_{key}_match", "passed": _canonical(manifest.get(key)) == _canonical(verification_intent[key])})
        intended_moves = verification_intent.get("moves", manifest.get("moves", []))
        if isinstance(intended_moves, list):
            for move in intended_moves:
                if not isinstance(move, dict):
                    checks.append({"name": "move_shape_valid", "passed": False})
                    continue
                source = str(move.get("source", ""))
                destination = str(move.get("destination", ""))
                destination_inside = not _outside(root, destination)
                source_inside = not _outside(root, source)
                checks.append({
                    "name": f"move_destination_exists:{destination}",
                    "passed": destination_inside and (root / destination).is_file(),
                })
                checks.append({
                    "name": f"move_source_absent:{source}",
                    "passed": source_inside and not (root / source).exists(),
                })
        return {
            "status": "passed" if all(bool(check["passed"]) for check in checks) else "failed",
            "checks": checks,
            "manifest": manifest,
        }


def _verify_created_files(root: Path, created_files: list[object]) -> dict:
    checks: list[dict[str, object]] = []
    verified: list[dict[str, object]] = []
    for record in created_files:
        if not isinstance(record, dict):
            checks.append({"name": "created_file_shape_valid", "passed": False})
            continue
        path = str(record.get("path", ""))
        content = record.get("content")
        inside = not _outside(root, path)
        exists = inside and (root / path).is_file()
        checks.append({"name": f"created_file_inside_root:{path}", "passed": inside})
        checks.append({"name": f"created_file_exists:{path}", "passed": exists})
        if isinstance(content, str):
            matches = exists and (root / path).read_text(encoding="utf-8") == content
            checks.append({"name": f"created_file_content_match:{path}", "passed": matches})
        verified.append({"path": path, "exists": exists})
    return {
        "status": "passed" if checks and all(bool(check["passed"]) for check in checks) else "failed",
        "checks": checks,
        "created_files": verified,
    }


def _matches_type(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    return False


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
