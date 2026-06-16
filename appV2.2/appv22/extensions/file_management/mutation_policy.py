from __future__ import annotations

from pathlib import Path

PROTECTED_PREFIXES = (".git/", "tests/", "src/", "assets/", "secrets/", "docs/")
PROTECTED_DESTINATION_PREFIXES = (".git/", "tests/", "src/", "assets/", "secrets/")
PROTECTED_NAMES = ("README.md",)
PROTECTED_NAME_PREFIXES = ("keep", "do_not_move", "old_blob")
MANIFEST_PATH = "docs/workspace_manifest.json"
_PROTECTED_PREFIXES_CANONICAL = tuple(prefix.lower() for prefix in PROTECTED_PREFIXES)
_PROTECTED_DESTINATION_PREFIXES_CANONICAL = tuple(prefix.lower() for prefix in PROTECTED_DESTINATION_PREFIXES)
_PROTECTED_NAMES_CANONICAL = tuple(name.lower() for name in PROTECTED_NAMES)
_PROTECTED_NAME_PREFIXES_CANONICAL = tuple(prefix.lower() for prefix in PROTECTED_NAME_PREFIXES)


class FileMutationPolicy:
    capability_id = "file_management.safe_file_mutations"

    def validate(self, operations: list[dict], *, root_path) -> list[str]:
        errors: list[str] = []
        root = Path(root_path).resolve()
        sources: set[str] = set()
        destinations: set[str] = set()
        source_entries: list[tuple[str, str]] = []
        destination_entries: list[tuple[str, str]] = []
        existing_file_keys = _casefolded_existing_file_keys(root)
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
                canonical_source = None if source_absolute else _canonical_relative_path(root, source)
                canonical_destination = None if destination_absolute else _canonical_relative_path(root, destination)
                source_outside = canonical_source is None
                destination_outside = canonical_destination is None
                if source_outside or destination_outside:
                    errors.append(f"path_outside_root:{source}->{destination}")
                if canonical_source and _protected(canonical_source):
                    errors.append(f"protected_source_path:{canonical_source}")
                if canonical_destination and _protected_destination(canonical_destination):
                    errors.append(f"protected_destination_path:{canonical_destination}")
                if canonical_source:
                    canonical_source_key = _casefold_canonical_key(canonical_source)
                    if canonical_source_key in sources:
                        errors.append(f"duplicate_source:{canonical_source}")
                    sources.add(canonical_source_key)
                    source_entries.append((canonical_source_key, canonical_source))
                if canonical_destination:
                    canonical_destination_key = _casefold_canonical_key(canonical_destination)
                    if canonical_destination_key in destinations:
                        errors.append(f"duplicate_destination:{canonical_destination}")
                    destinations.add(canonical_destination_key)
                    destination_entries.append((canonical_destination_key, canonical_destination))
                if canonical_destination and canonical_destination_key in existing_file_keys:
                    errors.append(f"destination_exists:{canonical_destination}")
                if canonical_source and not _protected(canonical_source):
                    source_path = root / canonical_source
                    if not source_path.exists():
                        errors.append(f"missing_source:{canonical_source}")
                    elif not source_path.is_file():
                        errors.append(f"non_file_source:{canonical_source}")
            elif action == "write":
                path = str(operation.get("path", ""))
                path_absolute = _absolute(path)
                if path_absolute:
                    errors.append(f"absolute_path:path:{path}")
                canonical_path = None if path_absolute else _canonical_relative_path(root, path)
                if canonical_path is None:
                    errors.append(f"path_outside_root:{path}->{path}")
                elif _protected_destination(canonical_path):
                    errors.append(f"protected_write_path:{canonical_path}")
            else:
                errors.append(f"unsupported_operation:{action}")
        source_keys = {key for key, _path in source_entries}
        destination_keys = {key for key, _path in destination_entries}
        for destination_key, canonical_destination in destination_entries:
            if destination_key in source_keys:
                errors.append(f"source_destination_collision:{canonical_destination}")
        for source_key, canonical_source in source_entries:
            if source_key in destination_keys:
                errors.append(f"source_destination_collision:{canonical_source}")
        return errors


def _outside(root: Path, relative: str) -> bool:
    return _canonical_relative_path(root, relative) is None


def _canonical_relative_path(root: Path, relative: str) -> str | None:
    if not relative or _absolute(relative):
        return None
    try:
        return (root / relative).resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def _absolute(path: str) -> bool:
    return Path(path).is_absolute()


def _casefold_canonical_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _casefolded_existing_file_keys(root: Path) -> set[str]:
    keys: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file():
            keys.add(_casefold_canonical_key(path.relative_to(root).as_posix()))
    return keys


def _normalize(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix()


def _protected(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/").lower()
    name = Path(normalized).name
    return (
        normalized in _PROTECTED_NAMES_CANONICAL
        or normalized.startswith(_PROTECTED_PREFIXES_CANONICAL)
        or name.startswith(_PROTECTED_NAME_PREFIXES_CANONICAL)
    )


def _protected_destination(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/").lower()
    name = Path(normalized).name
    return (
        normalized in _PROTECTED_NAMES_CANONICAL
        or normalized.startswith(_PROTECTED_DESTINATION_PREFIXES_CANONICAL)
        or name.startswith(_PROTECTED_NAME_PREFIXES_CANONICAL)
    )
