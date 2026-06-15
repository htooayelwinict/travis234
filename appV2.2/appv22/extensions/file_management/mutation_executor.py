from __future__ import annotations

import json
import shutil
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import FileMoveMutationPolicy, _canonical_relative_path


class FileMutationExecutor:
    capability_id = "file_management.file_mutation_executor"

    def apply(self, operations: list[dict], *, root_path) -> dict:
        root = Path(root_path).resolve()
        errors = FileMoveMutationPolicy().validate(operations, root_path=root)
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
        for operation in operations:
            if operation.get("action") == "move":
                source_name = _canonical_relative_path(root, str(operation["source"]))
                destination_name = _canonical_relative_path(root, str(operation["destination"]))
                if source_name is None or destination_name is None:
                    errors.append(f"path_outside_root:{operation['source']}->{operation['destination']}")
                    continue
                source = root / source_name
                destination = root / destination_name
                if not source.exists():
                    errors.append(f"missing_source:{source_name}")
                elif not source.is_file():
                    errors.append(f"non_file_source:{source_name}")
                if destination.exists():
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
        return errors
