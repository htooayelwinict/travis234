"""Trust-aware package installation and resource resolution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

from travis.coding_agent.resource_discovery import collect_resource_files

CONFIG_DIR_NAME = ".travis234"
RESOURCE_TYPES = ("extensions", "skills", "prompts", "themes")

PackageScope = Literal["global", "project", "temporary"]
PackageKind = Literal["local", "git", "python"]


@dataclass(frozen=True)
class PackageSource:
    raw: str
    kind: PackageKind
    location: str
    revision: str | None = None


@dataclass(frozen=True)
class InstalledPackage:
    source: PackageSource
    scope: PackageScope
    install_path: str
    version: str | None


@dataclass(frozen=True)
class PackageDiagnostic:
    type: str
    message: str
    source: str


@dataclass
class ResolvedResource:
    path: str
    enabled: bool
    metadata: dict[str, object]


@dataclass
class ResolvedPaths:
    extensions: list[ResolvedResource] = field(default_factory=list)
    skills: list[ResolvedResource] = field(default_factory=list)
    prompts: list[ResolvedResource] = field(default_factory=list)
    themes: list[ResolvedResource] = field(default_factory=list)
    diagnostics: list[PackageDiagnostic] = field(default_factory=list)


def parse_package_source(source: str, *, cwd: str | Path) -> PackageSource:
    raw = str(source)
    value = raw.strip()
    if not value:
        raise ValueError("Package source cannot be empty")
    base = Path(cwd).expanduser().resolve()
    candidate = Path(value).expanduser()
    if (
        value.startswith(("./", "../", "/", "~"))
        or candidate.is_absolute()
        or (base / candidate).exists()
    ):
        location = candidate if candidate.is_absolute() else base / candidate
        return PackageSource(raw=raw, kind="local", location=str(location.resolve()))
    if value.startswith("git+"):
        location_with_revision = value[4:]
        location, revision = _split_git_revision(location_with_revision)
        return PackageSource(raw=raw, kind="git", location=location, revision=revision)
    if value.startswith("-") or any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError(f"Invalid Python package source: {raw}")
    name_match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
    if name_match is None:
        raise ValueError(f"Invalid Python package source: {raw}")
    version_match = re.search(r"==\s*([^\s;,]+)", value)
    return PackageSource(
        raw=raw,
        kind="python",
        location=name_match.group(0),
        revision=version_match.group(1) if version_match else None,
    )


def sanitized_package_environment(
    package_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    secret_markers = (
        "API_KEY",
        "APIKEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "OAUTH",
        "OPENAI",
        "ANTHROPIC",
        "OPENROUTER",
        "TRAVIS_COMPRESSION",
        "TRAVIS_WORKER",
        "CODEX_",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in secret_markers)
    }
    allowed_overrides = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PIP_CERT",
        "PIP_CLIENT_CERT",
    }
    for key, value in (package_env or {}).items():
        if key.upper() not in allowed_overrides:
            raise ValueError(f"Package subprocess environment key is not allowed: {key}")
        env[key] = value
    return env


class DefaultPackageManager:
    """Install packages transactionally and resolve their Travis resources."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_dir: str,
        package_paths: Sequence[str] | None = None,
        project_trusted: bool = False,
        settings_manager: object | None = None,
        offline: bool = False,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.agent_dir = str(Path(agent_dir).expanduser().resolve())
        self.package_paths = list(package_paths or [])
        self.project_trusted = bool(project_trusted)
        self.settings_manager = settings_manager
        self.offline = bool(offline)
        self._temporary_installed: list[InstalledPackage] = []

    def assert_project_trusted_for_scope(self, scope: PackageScope) -> None:
        if scope == "project" and not self.project_trusted:
            raise RuntimeError("Project package operations require a trusted project")

    def install(
        self,
        source: str | PackageSource,
        *,
        scope: PackageScope = "global",
        package_env: Mapping[str, str] | None = None,
    ) -> InstalledPackage:
        self.assert_project_trusted_for_scope(scope)
        parsed = source if isinstance(source, PackageSource) else parse_package_source(source, cwd=self.cwd)
        if self.offline and parsed.kind != "local":
            raise RuntimeError(
                f'Cannot acquire {parsed.kind} package "{parsed.raw}" in offline mode'
            )
        if scope == "temporary" and parsed.kind == "local":
            root = Path(parsed.location)
            _validate_package_root(root)
            installed = InstalledPackage(
                source=parsed,
                scope=scope,
                install_path=str(root),
                version=_package_identity(root)[1],
            )
            self._temporary_installed = [
                item for item in self._temporary_installed if item.source.raw != parsed.raw
            ]
            self._temporary_installed.append(installed)
            return installed

        install_root = self._install_root(scope)
        install_root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".tmp-", dir=install_root))
        payload = stage / "payload"
        backup: Path | None = None
        target: Path | None = None
        try:
            self._materialize(parsed, payload, package_env)
            _validate_package_root(payload)
            package_name, version = _package_identity(payload)
            target = install_root / _package_directory_name(parsed, package_name)
            _write_install_record(payload, parsed, scope, version)
            if target.exists():
                backup = install_root / f".backup-{target.name}-{os.getpid()}"
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(target, backup)
            os.replace(payload, target)
            if backup is not None:
                shutil.rmtree(backup)
                backup = None
        except Exception:
            if backup is not None and target is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        installed = InstalledPackage(
            source=parsed,
            scope=scope,
            install_path=str(target),
            version=version,
        )
        self._persist_source(parsed.raw, scope, add=True)
        return installed

    def remove(self, identifier: str, *, scope: PackageScope = "global") -> bool:
        self.assert_project_trusted_for_scope(scope)
        installed = self._find_installed(identifier, scope)
        if installed is None:
            return False
        path = Path(installed.install_path)
        if scope == "temporary":
            self._temporary_installed = [item for item in self._temporary_installed if item != installed]
        elif path.exists():
            shutil.rmtree(path)
        self._persist_source(installed.source.raw, scope, add=False)
        return True

    def update(
        self,
        identifier: str | None = None,
        *,
        scope: PackageScope = "global",
        package_env: Mapping[str, str] | None = None,
    ) -> list[InstalledPackage]:
        self.assert_project_trusted_for_scope(scope)
        installed = self.list_installed(scope=scope)
        if identifier is not None:
            installed = [item for item in installed if _matches_installed(item, identifier)]
            if not installed:
                raise KeyError(f"Installed package not found: {identifier}")
        return [
            self.install(item.source, scope=scope, package_env=package_env)
            for item in installed
        ]

    def list_installed(self, *, scope: PackageScope | None = None) -> list[InstalledPackage]:
        scopes: tuple[PackageScope, ...] = (scope,) if scope else ("global", "project", "temporary")
        result: list[InstalledPackage] = []
        for current_scope in scopes:
            if current_scope == "project" and not self.project_trusted:
                continue
            if current_scope == "temporary":
                result.extend(self._temporary_installed)
                continue
            root = self._install_root(current_scope)
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir(), key=lambda path: path.name):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                record = _read_install_record(child)
                if record is not None:
                    result.append(record)
        return result

    def resolve(self) -> ResolvedPaths:
        resolved = ResolvedPaths()
        installed = self.list_installed()
        requested = [*self._configured_sources(), *self.package_paths]
        seen_sources: set[str] = set()
        for raw_source in requested:
            if raw_source in seen_sources:
                continue
            seen_sources.add(raw_source)
            try:
                source = parse_package_source(raw_source, cwd=self.cwd)
            except ValueError as error:
                resolved.diagnostics.append(PackageDiagnostic("error", str(error), raw_source))
                continue
            matching = next((item for item in installed if item.source.raw == raw_source), None)
            if matching is not None:
                self._collect_package_resources(Path(matching.install_path), source, matching.scope, resolved)
                continue
            if source.kind == "local" and Path(source.location).exists():
                self._collect_package_resources(Path(source.location), source, "temporary", resolved)
                continue
            resolved.diagnostics.append(
                PackageDiagnostic(
                    "warning",
                    f'Configured package "{raw_source}" is not installed; run package install explicitly',
                    raw_source,
                )
            )
        for item in installed:
            if item.source.raw not in seen_sources:
                self._collect_package_resources(Path(item.install_path), item.source, item.scope, resolved)
        self._add_auto_discovered_resources(resolved)
        return resolved

    def _materialize(
        self,
        source: PackageSource,
        destination: Path,
        package_env: Mapping[str, str] | None,
    ) -> None:
        if source.kind == "local":
            root = Path(source.location)
            if not root.is_dir():
                raise FileNotFoundError(f"Local package does not exist: {root}")
            shutil.copytree(root, destination)
            return
        env = sanitized_package_environment(package_env)
        if source.kind == "git":
            clone_command = ["git", "clone", "--filter=blob:none"]
            if source.revision is not None:
                clone_command.append("--no-checkout")
            clone_command.extend([source.location, str(destination)])
            _run_package_command(clone_command, env)
            if source.revision is not None:
                _run_package_command(
                    ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", source.revision],
                    env,
                )
                _run_package_command(
                    ["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
                    env,
                )
            return
        destination.mkdir(parents=True, exist_ok=True)
        _run_package_command(
            [sys.executable, "-m", "pip", "install", "--target", str(destination), source.raw],
            env,
        )

    def _install_root(self, scope: PackageScope) -> Path:
        if scope == "global":
            return Path(self.agent_dir) / "packages"
        if scope == "project":
            return Path(self.cwd) / CONFIG_DIR_NAME / "packages"
        return Path(self.agent_dir) / "packages" / ".temporary"

    def _find_installed(self, identifier: str, scope: PackageScope) -> InstalledPackage | None:
        return next(
            (item for item in self.list_installed(scope=scope) if _matches_installed(item, identifier)),
            None,
        )

    def _configured_sources(self) -> list[str]:
        settings = self.settings_manager
        if settings is None:
            return []
        entries: list[object] = []
        global_settings = getattr(settings, "global_settings", None)
        if isinstance(global_settings, dict):
            entries.extend(global_settings.get("packages", []))
        project_settings = getattr(settings, "project_settings", None)
        if self.project_trusted and isinstance(project_settings, dict):
            entries.extend(project_settings.get("packages", []))
        if not isinstance(global_settings, dict):
            getter = getattr(settings, "get_packages", None) or getattr(settings, "getPackages", None)
            if callable(getter):
                entries.extend(getter())
        result: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                result.append(entry)
            elif isinstance(entry, dict) and entry.get("enabled", True) is not False:
                source = entry.get("source")
                if isinstance(source, str):
                    result.append(source)
        return result

    def _persist_source(self, raw_source: str, scope: PackageScope, *, add: bool) -> None:
        if scope == "temporary" or self.settings_manager is None:
            return
        settings = self.settings_manager
        source_settings = (
            getattr(settings, "global_settings", {})
            if scope == "global"
            else getattr(settings, "project_settings", {})
        )
        current = list(source_settings.get("packages", [])) if isinstance(source_settings, dict) else []
        filtered = [entry for entry in current if configured_package_source(entry) != raw_source]
        if add:
            filtered.append(raw_source)
        if scope == "global":
            setter = getattr(settings, "set_packages", None) or getattr(settings, "setPackages", None)
        else:
            sync_trust = getattr(settings, "set_project_trusted", None)
            if callable(sync_trust):
                sync_trust(True)
            setter = getattr(settings, "set_project_packages", None) or getattr(settings, "setProjectPackages", None)
        if callable(setter):
            setter(filtered)

    @staticmethod
    def _collect_package_resources(
        package_root: Path,
        source: PackageSource,
        scope: PackageScope,
        resolved: ResolvedPaths,
    ) -> None:
        resources, _name, _version = _read_package_manifest(package_root)
        metadata = {
            "source": source.kind,
            "scope": scope,
            "origin": "package",
            "baseDir": str(package_root),
            "packageSource": source.raw,
        }
        for resource_type in RESOURCE_TYPES:
            entries = resources.get(resource_type)
            paths = (
                _collect_manifest_entries(package_root, entries, resource_type)
                if entries is not None
                else collect_resource_files(package_root / resource_type, resource_type)
            )
            target = getattr(resolved, resource_type)
            target.extend(
                ResolvedResource(path=str(path), enabled=True, metadata=dict(metadata))
                for path in paths
            )

    def _add_auto_discovered_resources(self, resolved: ResolvedPaths) -> None:
        bases: list[tuple[Path, str, str]] = [(Path(self.agent_dir), "user", "user")]
        if self.project_trusted:
            bases.append((Path(self.cwd) / CONFIG_DIR_NAME, "project", "project"))
        for base, scope, source in bases:
            metadata = {
                "source": source,
                "scope": scope,
                "origin": "top-level",
                "baseDir": str(base),
            }
            for resource_type in RESOURCE_TYPES:
                target = getattr(resolved, resource_type)
                target.extend(
                    ResolvedResource(path=str(path), enabled=True, metadata=dict(metadata))
                    for path in collect_resource_files(base / resource_type, resource_type)
                )


def _split_git_revision(value: str) -> tuple[str, str | None]:
    location, separator, revision = value.rpartition("@")
    if separator and revision and (location.endswith(".git") or ".git/" in location):
        return location, revision
    return value, None


def _run_package_command(command: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "package command failed").strip()
        raise RuntimeError(f"Package command failed ({command[0]}): {detail}")


def _read_package_manifest(package_root: Path) -> tuple[dict[str, list[str]], str | None, str | None]:
    package_json = package_root / "package.json"
    if package_json.is_file():
        data = json.loads(package_json.read_text(encoding="utf-8"))
        manifest = data.get("travis")
        return (
            _normalize_resource_manifest(manifest),
            str(data["name"]) if data.get("name") is not None else None,
            str(data["version"]) if data.get("version") is not None else None,
        )
    pyproject = package_root / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        manifest = tool.get("travis234", tool.get("travis"))
        return (
            _normalize_resource_manifest(manifest),
            str(project["name"]) if project.get("name") is not None else None,
            str(project["version"]) if project.get("version") is not None else None,
        )
    return {}, None, None


def _normalize_resource_manifest(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Package Travis manifest must be a mapping")
    result: dict[str, list[str]] = {}
    for resource_type in RESOURCE_TYPES:
        entries = value.get(resource_type)
        if entries is None:
            continue
        if isinstance(entries, str):
            result[resource_type] = [entries]
        elif isinstance(entries, list) and all(isinstance(entry, str) for entry in entries):
            result[resource_type] = list(entries)
        else:
            raise ValueError(f'Package manifest "{resource_type}" must be a string or list of strings')
    return result


def _validate_package_root(package_root: Path) -> None:
    if not package_root.is_dir():
        raise FileNotFoundError(f"Package root does not exist: {package_root}")
    resources, _name, _version = _read_package_manifest(package_root)
    root = package_root.resolve()
    for resource_type, entries in resources.items():
        for entry in entries:
            if entry.startswith("!"):
                continue
            candidate = (root / entry).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as error:
                raise ValueError(f'Package resource "{entry}" escapes package root') from error
            if not candidate.exists():
                raise ValueError(f'Package resource does not exist: "{entry}"')
            if resource_type == "extensions" and candidate.is_file() and candidate.suffix != ".py":
                raise ValueError(f'Python extension entry point must end in .py: "{entry}"')


def _collect_manifest_entries(package_root: Path, entries: Sequence[str], resource_type: str) -> list[Path]:
    result: list[Path] = []
    for entry in entries:
        if entry.startswith("!"):
            continue
        result.extend(collect_resource_files(package_root / entry, resource_type))
    return result


def _package_identity(package_root: Path) -> tuple[str | None, str | None]:
    _resources, name, version = _read_package_manifest(package_root)
    if name is not None or version is not None:
        return name, version
    metadata_files = sorted(package_root.glob("*.dist-info/METADATA"))
    if not metadata_files:
        return None, None
    name_value: str | None = None
    version_value: str | None = None
    for line in metadata_files[0].read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Name: "):
            name_value = line[6:].strip()
        elif line.startswith("Version: "):
            version_value = line[9:].strip()
    return name_value, version_value


def _package_directory_name(source: PackageSource, package_name: str | None) -> str:
    fallback = Path(source.location.rstrip("/")).stem or "package"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", package_name or fallback).strip("-.") or "package"
    digest = hashlib.sha256(source.raw.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def _write_install_record(
    package_root: Path,
    source: PackageSource,
    scope: PackageScope,
    version: str | None,
) -> None:
    (package_root / ".travis-package.json").write_text(
        json.dumps(
            {
                "source": {
                    "raw": source.raw,
                    "kind": source.kind,
                    "location": source.location,
                    "revision": source.revision,
                },
                "scope": scope,
                "version": version,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_install_record(package_root: Path) -> InstalledPackage | None:
    record_path = package_root / ".travis-package.json"
    if not record_path.is_file():
        return None
    try:
        data = json.loads(record_path.read_text(encoding="utf-8"))
        source_data = data["source"]
        source = PackageSource(
            raw=str(source_data["raw"]),
            kind=source_data["kind"],
            location=str(source_data["location"]),
            revision=str(source_data["revision"]) if source_data.get("revision") is not None else None,
        )
        scope = data["scope"]
        if scope not in {"global", "project", "temporary"}:
            return None
        return InstalledPackage(
            source=source,
            scope=scope,
            install_path=str(package_root),
            version=str(data["version"]) if data.get("version") is not None else None,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _matches_installed(package: InstalledPackage, identifier: str) -> bool:
    return identifier in {
        package.source.raw,
        package.source.location,
        Path(package.install_path).name,
        Path(package.source.location.rstrip("/")).stem,
    }


def configured_package_source(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("source"), str):
        return entry["source"]
    return None


__all__ = [
    "DefaultPackageManager",
    "InstalledPackage",
    "PackageDiagnostic",
    "PackageKind",
    "PackageScope",
    "PackageSource",
    "ResolvedPaths",
    "ResolvedResource",
    "parse_package_source",
    "sanitized_package_environment",
    "configured_package_source",
]
