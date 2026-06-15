from __future__ import annotations

from pathlib import Path

PROTECTED_PREFIXES = (".git/", "tests/", "src/", "assets/", "secrets/", "docs/")
PROTECTED_NAMES = ("README.md",)
PROTECTED_NAME_PREFIXES = ("keep", "do_not_move", "old_blob")
MANIFEST_PATH = "docs/workspace_manifest.json"


class FileMoveMutationPolicy:
    capability_id = "file_management.safe_file_moves"

    def validate(self, operations: list[dict], *, root_path) -> list[str]:
        errors: list[str] = []
        root = Path(root_path).resolve()
        destinations: set[str] = set()
        for operation in operations:
            action = operation.get("action")
            if action == "move":
                source = str(operation.get("source", ""))
                destination = str(operation.get("destination", ""))
                source_absolute = _absolute(source)
                destination_absolute = _absolute(destination)
                if source_absolute:
                    errors.append(f"absolute_path:source:{source}")
                if destination_absolute:
                    errors.append(f"absolute_path:destination:{destination}")
                source_outside = _outside(root, source)
                destination_outside = _outside(root, destination)
                if source_outside or destination_outside:
                    errors.append(f"path_outside_root:{source}->{destination}")
                if _protected(source):
                    errors.append(f"protected_source_path:{source}")
                if destination and not destination_absolute and not destination_outside:
                    normalized_destination = _normalize(destination)
                    if normalized_destination in destinations:
                        errors.append(f"duplicate_destination:{normalized_destination}")
                    destinations.add(normalized_destination)
                if destination and not destination_outside and (root / destination).exists():
                    errors.append(f"destination_exists:{destination}")
                if source and not source_absolute and not source_outside and not _protected(source):
                    source_path = root / source
                    if not source_path.exists():
                        errors.append(f"missing_source:{source}")
                    elif not source_path.is_file():
                        errors.append(f"non_file_source:{source}")
            elif action == "write":
                path = str(operation.get("path", ""))
                if _absolute(path):
                    errors.append(f"absolute_path:path:{path}")
                if _outside(root, path):
                    errors.append(f"path_outside_root:{path}->{path}")
                if path != MANIFEST_PATH:
                    errors.append(f"unsupported_write_path:{operation.get('path')}")
            else:
                errors.append(f"unsupported_operation:{action}")
        return errors


def _outside(root: Path, relative: str) -> bool:
    if not relative:
        return True
    try:
        (root / relative).resolve().relative_to(root)
    except ValueError:
        return True
    return False


def _absolute(path: str) -> bool:
    return Path(path).is_absolute()


def _normalize(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix()


def _protected(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    name = Path(normalized).name.lower()
    return (
        normalized in PROTECTED_NAMES
        or normalized.startswith(PROTECTED_PREFIXES)
        or name.startswith(PROTECTED_NAME_PREFIXES)
    )
