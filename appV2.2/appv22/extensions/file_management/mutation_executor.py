from __future__ import annotations

import json
import shutil
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import (
    FileMutationPolicy,
    _canonical_relative_path,
    _casefold_canonical_key,
    _casefolded_existing_file_keys,
)


class FileMutationExecutor:
    capability_id = "file_management.file_mutation_executor"

    def apply(self, operations: list[dict], *, root_path) -> dict:
        root = Path(root_path).resolve()
        errors = FileMutationPolicy().validate(operations, root_path=root)
        if errors:
            return {"status": "denied", "touched_paths": [], "errors": errors}

        preflight_errors = self._preflight(operations, root=root)
        if preflight_errors:
            status = (
                "failed"
                if any(
                    error.startswith("blocked_write_parent:") or error.startswith("write_target_is_directory:")
                    for error in preflight_errors
                )
                else "denied"
            )
            return {"status": status, "touched_paths": [], "errors": preflight_errors}

        touched: list[str] = []
        for operation in operations:
            action = operation["action"]
            if action == "move":
                source_name = _canonical_relative_path(root, str(operation["source"]))
                destination_name = _canonical_relative_path(root, str(operation["destination"]))
                if source_name is None or destination_name is None:
                    return {
                        "status": "denied",
                        "touched_paths": sorted(set(touched)),
                        "errors": [f"path_outside_root:{operation['source']}->{operation['destination']}"],
                    }
                source = root / source_name
                destination = root / destination_name
                if not source.is_file():
                    return {
                        "status": "failed",
                        "touched_paths": sorted(set(touched)),
                        "errors": [f"missing_source:{source_name}"],
                    }
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                touched.extend([source_name, destination_name])
            elif action == "write":
                path_name = _canonical_relative_path(root, str(operation["path"]))
                if path_name is None:
                    return {
                        "status": "denied",
                        "touched_paths": sorted(set(touched)),
                        "errors": [f"path_outside_root:{operation['path']}->{operation['path']}"],
                    }
                path = root / path_name
                path.parent.mkdir(parents=True, exist_ok=True)
                content = operation.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, indent=2, sort_keys=True)
                path.write_text(content, encoding="utf-8")
                touched.append(path_name)
        return {"status": "applied", "touched_paths": sorted(set(touched)), "errors": []}

    def _preflight(self, operations: list[dict], *, root: Path) -> list[str]:
        errors: list[str] = []
        sources: set[str] = set()
        destinations: set[str] = set()
        source_entries: list[tuple[str, str]] = []
        destination_entries: list[tuple[str, str]] = []
        existing_file_keys = _casefolded_existing_file_keys(root)
        for operation in operations:
            if operation.get("action") == "move":
                source_name = _canonical_relative_path(root, str(operation["source"]))
                destination_name = _canonical_relative_path(root, str(operation["destination"]))
                if source_name is None or destination_name is None:
                    errors.append(f"path_outside_root:{operation['source']}->{operation['destination']}")
                    continue
                source_key = _casefold_canonical_key(source_name)
                destination_key = _casefold_canonical_key(destination_name)
                if source_key in sources:
                    errors.append(f"duplicate_source:{source_name}")
                sources.add(source_key)
                source_entries.append((source_key, source_name))
                if destination_key in destinations:
                    errors.append(f"duplicate_destination:{destination_name}")
                destinations.add(destination_key)
                destination_entries.append((destination_key, destination_name))
                source = root / source_name
                if not source.exists():
                    errors.append(f"missing_source:{source_name}")
                elif not source.is_file():
                    errors.append(f"non_file_source:{source_name}")
                if destination_key in existing_file_keys:
                    errors.append(f"destination_exists:{destination_name}")
            elif operation.get("action") == "write":
                path_name = _canonical_relative_path(root, str(operation["path"]))
                if path_name is None:
                    errors.append(f"path_outside_root:{operation['path']}->{operation['path']}")
                    continue
                path = root / path_name
                if path.exists() and path.is_dir():
                    errors.append(f"write_target_is_directory:{path_name}")
                    continue
                parent = path.parent
                candidate = root
                for part in parent.relative_to(root).parts:
                    candidate = candidate / part
                    if candidate.exists() and not candidate.is_dir():
                        errors.append(f"blocked_write_parent:{path_name}")
                        break
        source_keys = {key for key, _path in source_entries}
        destination_keys = {key for key, _path in destination_entries}
        for destination_key, destination_name in destination_entries:
            if destination_key in source_keys:
                errors.append(f"source_destination_collision:{destination_name}")
        for source_key, source_name in source_entries:
            if source_key in destination_keys:
                errors.append(f"source_destination_collision:{source_name}")
        return errors
