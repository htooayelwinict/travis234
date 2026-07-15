"""Project trust decisions for behavior-changing repository resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from travis.coding_agent.session_lock import SessionFileLock
from travis.coding_agent.tools.atomic_file import atomic_replace_text

DefaultProjectTrust = Literal["ask", "always", "never"]
ProjectTrustDecision = bool | None
_TRUST_REQUIRING_PROJECT_CONFIG_RESOURCES = (
    "settings.json",
    "extensions",
    "skills",
    "prompts",
    "themes",
    "SYSTEM.md",
    "APPEND_SYSTEM.md",
)


@dataclass(frozen=True)
class ProjectTrustUpdate:
    path: str
    decision: ProjectTrustDecision


@dataclass(frozen=True)
class ProjectTrustOption:
    label: str
    trusted: bool
    updates: tuple[ProjectTrustUpdate, ...]
    saved_path: str | None = None


@dataclass(frozen=True)
class ProjectTrustContext:
    has_ui: bool
    select: Callable[[str, Sequence[str]], str | None] | None


class ProjectTrustError(RuntimeError):
    """Raised when persisted project trust cannot be interpreted safely."""


class ProjectTrustStore:
    """Locked nearest-ancestor project trust decisions."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.path = Path(agent_dir).expanduser().resolve() / "trust.json"

    def get(self, cwd: str | Path) -> ProjectTrustDecision:
        entry = self.get_entry(cwd)
        return entry.decision if entry is not None else None

    def get_entry(self, cwd: str | Path) -> ProjectTrustUpdate | None:
        with SessionFileLock(self.path):
            data = self._read_unlocked()
        current = Path(cwd).expanduser().resolve()
        while True:
            value = data.get(str(current))
            if value is True or value is False:
                return ProjectTrustUpdate(str(current), value)
            if current.parent == current:
                return None
            current = current.parent

    def set(self, cwd: str | Path, decision: ProjectTrustDecision) -> None:
        self.set_many((ProjectTrustUpdate(str(cwd), decision),))

    def set_many(self, updates: Sequence[ProjectTrustUpdate]) -> None:
        with SessionFileLock(self.path):
            data = self._read_unlocked()
            for update in updates:
                key = str(Path(update.path).expanduser().resolve())
                if update.decision is None:
                    data.pop(key, None)
                else:
                    data[key] = update.decision
            ordered = {key: data[key] for key in sorted(data)}
            atomic_replace_text(self.path, json.dumps(ordered, indent=2) + "\n")

    def _read_unlocked(self) -> dict[str, bool | None]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectTrustError(f"Failed to read trust store {self.path}: {error}") from error
        if not isinstance(value, dict):
            raise ProjectTrustError(f"Invalid trust store {self.path}: expected an object")
        for key, decision in value.items():
            valid_decision = decision is True or decision is False or decision is None
            if not isinstance(key, str) or not valid_decision:
                raise ProjectTrustError(
                    f"Invalid trust store {self.path}: decisions must be true, false, or null"
                )
        return value


def has_trust_requiring_project_resources(cwd: str | Path) -> bool:
    """Return whether the project can alter behavior beyond plain context files."""

    current = Path(cwd).expanduser().resolve()
    config_dir = current / ".travis234"
    if any((config_dir / entry).exists() for entry in _TRUST_REQUIRING_PROJECT_CONFIG_RESOURCES):
        return True

    user_skills = (Path.home().expanduser().resolve() / ".agents" / "skills").resolve()
    while True:
        candidate = (current / ".agents" / "skills").resolve()
        if candidate != user_skills and candidate.exists():
            return True
        if current.parent == current:
            return False
        current = current.parent


def get_project_trust_options(
    cwd: str | Path,
    *,
    include_session_only: bool = False,
) -> tuple[ProjectTrustOption, ...]:
    """Return stable project trust choices matching the Pi interaction contract."""

    project = Path(cwd).expanduser().resolve()
    options = [
        ProjectTrustOption(
            label="Trust",
            trusted=True,
            updates=(ProjectTrustUpdate(str(project), True),),
            saved_path=str(project),
        )
    ]
    if project.parent != project:
        parent = project.parent
        options.append(
            ProjectTrustOption(
                label=f"Trust parent folder ({parent})",
                trusted=True,
                updates=(
                    ProjectTrustUpdate(str(parent), True),
                    ProjectTrustUpdate(str(project), None),
                ),
                saved_path=str(parent),
            )
        )
    if include_session_only:
        options.append(ProjectTrustOption("Trust (this session only)", True, ()))
    options.append(
        ProjectTrustOption(
            label="Do not trust",
            trusted=False,
            updates=(ProjectTrustUpdate(str(project), False),),
            saved_path=str(project),
        )
    )
    if include_session_only:
        options.append(ProjectTrustOption("Do not trust (this session only)", False, ()))
    return tuple(options)


async def resolve_project_trust(
    *,
    cwd: str | Path,
    trust_store: ProjectTrustStore,
    context: ProjectTrustContext,
    trust_override: bool | None = None,
    default_project_trust: DefaultProjectTrust = "ask",
    extension_runner: object | None = None,
    on_extension_error: Callable[[str], None] | None = None,
) -> bool:
    """Resolve whether behavior-changing project resources may load."""

    if trust_override is not None:
        return trust_override
    if not has_trust_requiring_project_resources(cwd):
        return True
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    emit_project_trust = getattr(extension_runner, "async_emit_project_trust", None)
    if callable(emit_project_trust):
        try:
            result = await emit_project_trust(
                {"type": "project_trust", "cwd": resolved_cwd},
                context,
            )
        except Exception as error:  # noqa: BLE001 - bootstrap extension failure must fail closed.
            if on_extension_error is not None:
                on_extension_error(str(error))
        else:
            if isinstance(result, dict) and result.get("trusted") in {"yes", "no"}:
                trusted = result["trusted"] == "yes"
                if result.get("remember") is True:
                    trust_store.set(resolved_cwd, trusted)
                return trusted
    saved = trust_store.get(cwd)
    if saved is not None:
        return saved
    if default_project_trust == "always":
        return True
    if default_project_trust == "never":
        return False
    if not context.has_ui or context.select is None:
        return False
    choices = get_project_trust_options(resolved_cwd, include_session_only=True)
    selected = context.select(
        f"Trust project folder?\n{resolved_cwd}",
        [choice.label for choice in choices],
    )
    choice = next((item for item in choices if item.label == selected), None)
    if choice is None:
        return False
    if choice.updates:
        trust_store.set_many(choice.updates)
    return choice.trusted


__all__ = [
    "DefaultProjectTrust",
    "ProjectTrustDecision",
    "ProjectTrustContext",
    "ProjectTrustError",
    "ProjectTrustOption",
    "ProjectTrustStore",
    "ProjectTrustUpdate",
    "get_project_trust_options",
    "has_trust_requiring_project_resources",
    "resolve_project_trust",
]
