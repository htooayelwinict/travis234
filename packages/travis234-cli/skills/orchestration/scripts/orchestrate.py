from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
from typing import Iterator, Sequence


SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
ENV_AGENT_DIR = "TRAVIS234_CODING_AGENT_DIR"

DEFAULT_MAX_WORKERS = 2
HARD_MAX_WORKERS = 3
DEFAULT_MAX_ROUNDS = 4
HARD_MAX_ROUNDS = 12
MAX_WAIT_SECONDS = 60
MAX_MESSAGE_LIMIT = 50
MAX_REQUEST_BYTES = 256 * 1024
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
TRANSCRIPT_TAIL_BYTES = 128 * 1024
MAX_UNIX_SOCKET_PATH_BYTES = 100

RUN_STATUSES = {"active", "completed", "abandoned"}
TASK_STATUSES = {
    "pending",
    "active",
    "awaiting_coordinator",
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
}
WORKER_STATUSES = {
    "starting",
    "ready",
    "busy",
    "idle",
    "retained",
    "stopped",
    "lost",
    "outcome_unknown",
}
DISPATCH_STATUSES = {
    "queued",
    "accepted",
    "running",
    "awaiting_coordinator",
    "succeeded",
    "failed",
    "cancelled",
    "abandoned",
    "outcome_unknown",
}
MESSAGE_KINDS = {"question", "reply", "status", "handoff", "failure", "heartbeat"}

TASK_MODES = {"supervised", "full_handoff"}
COMMIT_POLICIES = {"commit", "no_commit"}
RUN_CREATE_KEYS = {"objective", "coordinatorSessionId"}
TASK_CREATE_KEYS = {
    "objective",
    "ownership",
    "acceptanceCriteria",
    "dependencies",
    "mode",
    "maxRounds",
    "commitPolicy",
}

ID_PATTERNS = {
    "run": re.compile(r"^run_[0-9a-f]{24}$"),
    "task": re.compile(r"^task_[0-9a-f]{24}$"),
    "worker": re.compile(r"^worker_[0-9a-f]{24}$"),
    "dispatch": re.compile(r"^dispatch_[0-9a-f]{24}$"),
    "message": re.compile(r"^message_[0-9a-f]{24}$"),
}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|token)", re.IGNORECASE
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-(?:proj-)?|ghp_|github_pat_|xox[baprs]-|npm_)[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
WORKTREE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

GUIDE_COMMANDS = [
    "guide",
    "run-create",
    "run-show",
    "run-list",
    "task-create",
    "task-show",
    "task-list",
]


class HelperError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_actions: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_actions = list(next_actions)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise HelperError(
            "invalid_arguments",
            "Arguments are invalid; run guide for the supported command surface",
            next_actions=("Run python3 scripts/orchestrate.py guide.",),
        )


@dataclass(frozen=True)
class WorktreeRequest:
    repository: Path | str
    workspace_mode: str
    worktree_name: str | None = None
    branch: str | None = None
    base: str | None = None


@dataclass(frozen=True)
class RepositoryInspection:
    repository: Path
    common_dir: Path
    base_commit: str
    head_commit: str
    branch: str | None
    dirty: bool


def git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _git_probe(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return git(repo, *args, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("git_unavailable", "Git repository inspection could not run") from exc


def _validated_revision(base: str) -> str:
    if (
        not isinstance(base, str)
        or not base
        or len(base) > 512
        or base.startswith("-")
        or any(character in base for character in "\x00\r\n")
    ):
        raise HelperError("invalid_base", "Git base is invalid or unavailable")
    return base


def inspect_repository(repository: Path | str, base: str = "HEAD") -> RepositoryInspection:
    requested = Path(repository).expanduser()
    if not requested.is_dir():
        raise HelperError("not_repository", "Workspace is not a Git repository")
    top_level = _git_probe(requested, "rev-parse", "--show-toplevel")
    if top_level.returncode != 0 or not top_level.stdout.strip():
        raise HelperError("not_repository", "Workspace is not a Git repository")
    repo = Path(top_level.stdout.strip()).resolve()
    common = _git_probe(repo, "rev-parse", "--git-common-dir")
    if common.returncode != 0 or not common.stdout.strip():
        raise HelperError("not_repository", "Git common directory is unavailable")
    common_value = Path(common.stdout.strip())
    common_dir = (
        common_value.resolve()
        if common_value.is_absolute()
        else (repo / common_value).resolve()
    )
    revision = _validated_revision(base)
    resolved_base = _git_probe(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if resolved_base.returncode != 0 or not resolved_base.stdout.strip():
        raise HelperError("invalid_base", "Git base is invalid or unavailable")
    head = _git_probe(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode != 0 or not head.stdout.strip():
        raise HelperError("invalid_base", "Repository HEAD is unavailable")
    branch_result = _git_probe(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    status = _git_probe(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    if status.returncode != 0:
        raise HelperError("git_inspection_failed", "Git status inspection failed")
    return RepositoryInspection(
        repository=repo,
        common_dir=common_dir,
        base_commit=resolved_base.stdout.strip(),
        head_commit=head.stdout.strip(),
        branch=branch,
        dirty=bool(status.stdout),
    )


def _ignore_source_is_repository_owned(inspection: RepositoryInspection) -> bool:
    probe = ".worktrees/.travis234-orchestration-probe"
    ignored = _git_probe(
        inspection.repository,
        "check-ignore",
        "-v",
        "--no-index",
        probe,
    )
    if ignored.returncode != 0 or not ignored.stdout.strip():
        return False
    first_line = ignored.stdout.splitlines()[0]
    annotation = first_line.split("\t", 1)[0]
    parts = annotation.rsplit(":", 2)
    if len(parts) != 3 or not parts[0]:
        return False
    source_value = Path(parts[0])
    source = (
        source_value.resolve()
        if source_value.is_absolute()
        else (inspection.repository / source_value).resolve()
    )
    if source == (inspection.common_dir / "info" / "exclude").resolve():
        return True
    try:
        relative = source.relative_to(inspection.repository)
    except ValueError:
        return False
    tracked = _git_probe(
        inspection.repository,
        "ls-files",
        "--error-unmatch",
        "--",
        relative.as_posix(),
    )
    return tracked.returncode == 0


def _registered_worktree_paths(repo: Path) -> set[Path]:
    listed = _git_probe(repo, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        raise HelperError("git_inspection_failed", "Git worktree inspection failed")
    paths: set[Path] = set()
    for line in listed.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line.removeprefix("worktree ")).resolve())
    return paths


def _worktree_target(inspection: RepositoryInspection, name: str) -> tuple[Path, bool]:
    if _ignore_source_is_repository_owned(inspection):
        return inspection.repository / ".worktrees" / name, True
    repository_key = hashlib.sha256(
        str(inspection.repository).encode("utf-8")
    ).hexdigest()[:16]
    return agent_dir() / "orchestration" / "worktrees" / repository_key / name, False


def _prepare_target_parent(target: Path, *, repository_local: bool) -> None:
    parent = target.parent
    if parent.is_symlink():
        raise HelperError("unsafe_workspace", "Worktree parent cannot be a symlink")
    if parent.exists() and not parent.is_dir():
        raise HelperError("workspace_conflict", "Worktree path has a parent conflict")
    if repository_local:
        parent.mkdir(parents=True, exist_ok=True)
        return
    root = orchestration_root()
    worktrees = root / "worktrees"
    _private_directory(worktrees)
    repository_root = worktrees / target.parent.name
    _private_directory(repository_root)


def _workspace_receipt(
    inspection: RepositoryInspection,
    *,
    mode: str,
    workspace: Path,
    worktree: Path | None,
    branch: str | None,
) -> dict[str, object]:
    return {
        "workspaceMode": mode,
        "repository": str(inspection.repository),
        "workspace": str(workspace.resolve()),
        "worktree": str(worktree.resolve()) if worktree is not None else None,
        "branch": branch,
        "baseCommit": inspection.base_commit,
        "dirty": inspection.dirty,
        "uncommittedChangesTransferred": mode == "current",
        "automaticIntegration": False,
    }


def prepare_workspace(request: WorktreeRequest) -> dict[str, object]:
    if request.workspace_mode not in {"current", "worktree"}:
        raise HelperError("invalid_workspace", "Workspace mode is invalid")
    if request.workspace_mode == "current":
        if request.worktree_name is not None or request.branch is not None:
            raise HelperError("invalid_workspace", "Current workspace cannot create a branch or path")
        inspection = inspect_repository(request.repository, request.base or "HEAD")
        if request.base is not None and inspection.base_commit != inspection.head_commit:
            raise HelperError("workspace_conflict", "Current workspace base has a conflict")
        return _workspace_receipt(
            inspection,
            mode="current",
            workspace=inspection.repository,
            worktree=None,
            branch=inspection.branch,
        )

    if (
        not isinstance(request.worktree_name, str)
        or WORKTREE_NAME_PATTERN.fullmatch(request.worktree_name) is None
        or not isinstance(request.branch, str)
        or not request.branch
        or not isinstance(request.base, str)
    ):
        raise HelperError("invalid_workspace", "Worktree name, branch, or base is invalid")
    inspection = inspect_repository(request.repository, request.base)
    branch_check = _git_probe(
        inspection.repository,
        "check-ref-format",
        "--branch",
        request.branch,
    )
    if branch_check.returncode != 0:
        raise HelperError("invalid_workspace", "Git branch name is invalid")
    existing_branch = _git_probe(
        inspection.repository,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{request.branch}",
    )
    if existing_branch.returncode == 0:
        raise HelperError("workspace_conflict", "Requested branch has a conflict")
    if existing_branch.returncode not in {0, 1}:
        raise HelperError("git_inspection_failed", "Git branch inspection failed")
    target, repository_local = _worktree_target(inspection, request.worktree_name)
    target = Path(os.path.abspath(target))
    if target.exists() or target.is_symlink():
        raise HelperError("workspace_conflict", "Requested worktree path has a conflict")
    if target.parent.is_symlink():
        raise HelperError("unsafe_workspace", "Worktree parent cannot be a symlink")
    if target.resolve() in _registered_worktree_paths(inspection.repository):
        raise HelperError("workspace_conflict", "Requested worktree registration has a conflict")

    _prepare_target_parent(target, repository_local=repository_local)
    try:
        git(
            inspection.repository,
            "worktree",
            "add",
            "-b",
            request.branch,
            str(target),
            inspection.base_commit,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise HelperError(
            "outcome_unknown",
            "Git worktree creation may have partially completed",
            next_actions=(
                "Run git worktree list --porcelain in the source repository.",
                "Run git show-ref --verify for the requested branch.",
                "Inspect the exact requested path; do not prune or delete automatically.",
            ),
        ) from exc

    registered = _registered_worktree_paths(inspection.repository)
    created_head = _git_probe(target, "rev-parse", "--verify", "HEAD^{commit}")
    if (
        target not in registered
        or created_head.returncode != 0
        or created_head.stdout.strip() != inspection.base_commit
    ):
        raise HelperError(
            "outcome_unknown",
            "Git worktree creation could not be verified",
            next_actions=(
                "Run git worktree list --porcelain in the source repository.",
                "Inspect the requested branch and worktree without automatic cleanup.",
            ),
        )
    return _workspace_receipt(
        inspection,
        mode="worktree",
        workspace=target,
        worktree=target,
        branch=request.branch,
    )


def envelope(
    command: str,
    result: dict[str, object],
    *,
    next_actions: Sequence[str] = (),
) -> dict[str, object]:
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "command": command,
        "result": result,
        "nextActions": list(next_actions),
    }


def error_envelope(command: str, error: HelperError) -> dict[str, object]:
    return {
        "ok": False,
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "command": command,
        "error": {"code": error.code, "message": error.message},
        "nextActions": error.next_actions,
    }


def guide() -> dict[str, object]:
    return envelope(
        "guide",
        {
            "commands": GUIDE_COMMANDS,
            "invocation": "python3 scripts/orchestrate.py <command> [arguments]",
        },
    )


def agent_dir() -> Path:
    configured = os.environ.get(ENV_AGENT_DIR)
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".travis234" / "agent").resolve()
    )


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise HelperError("unsafe_state", "Orchestration state cannot use a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise HelperError("unsafe_state", "Orchestration state directory is invalid")
    os.chmod(path, 0o700)


def orchestration_root() -> Path:
    base = agent_dir()
    if not base.exists():
        base.mkdir(parents=True, mode=0o700)
    root = base / "orchestration"
    _private_directory(root)
    _private_directory(root / "sockets")
    _private_directory(root / "runs")
    return root


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    if prefix not in ID_PATTERNS:
        raise HelperError("internal_error", "Command failed; inspect safe state and retry")
    return f"{prefix}_{secrets.token_hex(12)}"


def validate_id(kind: str, value: str) -> str:
    pattern = ID_PATTERNS.get(kind)
    if pattern is None or not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise HelperError("invalid_id", "Identifier has an invalid format")
    return value


def validate_status(kind: str, value: str) -> str:
    allowed = {
        "run": RUN_STATUSES,
        "task": TASK_STATUSES,
        "worker": WORKER_STATUSES,
        "dispatch": DISPATCH_STATUSES,
    }.get(kind)
    if allowed is None or value not in allowed:
        raise HelperError("invalid_state", "Stored orchestration state is invalid")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HelperError("invalid_json", "Request JSON is invalid")
        result[key] = value
    return result


def _contains_secret_like_value(value: object, state: StateStore | None = None) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if SENSITIVE_KEY_PATTERN.search(key):
                return True
            if _contains_secret_like_value(nested, state):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret_like_value(item, state) for item in value)
    if not isinstance(value, str):
        return False
    if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
        return True
    return state.is_known_capability(value) if state is not None else False


def reject_secret_like(value: object, state: StateStore | None = None) -> None:
    if _contains_secret_like_value(value, state):
        raise HelperError(
            "secret_like_input",
            "Request contains credential-shaped or sensitive input",
        )


def validate_idempotency_key(value: str, state: StateStore | None = None) -> str:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(value or "") is None:
        if _contains_secret_like_value(value, state):
            raise HelperError(
                "secret_like_input",
                "Idempotency key resembles sensitive input",
            )
        raise HelperError("invalid_idempotency_key", "Idempotency key is invalid")
    reject_secret_like(value, state)
    return value


def _read_private_request(path_value: str, *, consume: bool) -> dict[str, object]:
    path = Path(path_value).expanduser()
    validated_identity: tuple[int, int] | None = None
    try:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise HelperError("request_file_missing", "Request file is unavailable") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or mode & 0o077
            or not mode & stat.S_IRUSR
        ):
            raise HelperError(
                "unsafe_request_file",
                "Request file must be regular, nonsymlinked, and owner-private",
            )
        if metadata.st_size > MAX_REQUEST_BYTES:
            raise HelperError("request_too_large", "Request file exceeds the size limit")
        validated_identity = (metadata.st_dev, metadata.st_ino)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise HelperError("unsafe_request_file", "Request file could not be opened safely") from exc
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != validated_identity or not stat.S_ISREG(
                opened.st_mode
            ):
                raise HelperError("unsafe_request_file", "Request file changed during validation")
            chunks: list[bytes] = []
            remaining = MAX_REQUEST_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_REQUEST_BYTES:
            raise HelperError("request_too_large", "Request file exceeds the size limit")
        try:
            decoded = raw.decode("utf-8")
            request = json.loads(decoded, object_pairs_hook=_json_object)
        except HelperError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HelperError("invalid_json", "Request JSON is invalid") from exc
        if not isinstance(request, dict):
            raise HelperError("invalid_json", "Request JSON must be an object")
        return request
    finally:
        if consume and validated_identity is not None:
            try:
                current = path.lstat()
            except OSError:
                current = None
            if (
                current is not None
                and stat.S_ISREG(current.st_mode)
                and not stat.S_ISLNK(current.st_mode)
                and (current.st_dev, current.st_ino) == validated_identity
            ):
                path.unlink()


def _required_text(request: dict[str, object], key: str, *, maximum: int = 131_072) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HelperError("invalid_request", "Request fields have invalid types or bounds")
    return value


def _optional_text(request: dict[str, object], key: str, *, maximum: int = 512) -> str | None:
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise HelperError("invalid_request", "Request fields have invalid types or bounds")
    return value


def _text_list(value: object, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise HelperError("invalid_request", "Request fields have invalid types or bounds")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in value):
        raise HelperError("invalid_request", "Request fields have invalid types or bounds")
    return list(value)


def validate_run_request(request: dict[str, object], state: StateStore) -> dict[str, object]:
    reject_secret_like(request, state)
    if set(request) - RUN_CREATE_KEYS or "objective" not in request:
        raise HelperError("invalid_request", "Run request has unknown or missing fields")
    return {
        "objective": _required_text(request, "objective"),
        "coordinatorSessionId": _optional_text(request, "coordinatorSessionId"),
    }


def validate_task_request(request: dict[str, object], state: StateStore) -> dict[str, object]:
    reject_secret_like(request, state)
    required = {"objective", "ownership", "acceptanceCriteria", "mode", "commitPolicy"}
    if set(request) - TASK_CREATE_KEYS or not required <= set(request):
        raise HelperError("invalid_request", "Task request has unknown or missing fields")
    ownership = request["ownership"]
    if not isinstance(ownership, dict):
        raise HelperError("invalid_request", "Request fields have invalid types or bounds")
    acceptance = _text_list(request["acceptanceCriteria"], allow_empty=False)
    dependencies = _text_list(request.get("dependencies", []))
    for dependency in dependencies:
        validate_id("task", dependency)
    mode = request["mode"]
    if not isinstance(mode, str) or mode not in TASK_MODES:
        raise HelperError("invalid_request", "Task mode is invalid")
    rounds = request.get("maxRounds", DEFAULT_MAX_ROUNDS)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= HARD_MAX_ROUNDS:
        raise HelperError("invalid_request", "Task prompt budget is invalid")
    commit_policy = request["commitPolicy"]
    if not isinstance(commit_policy, str) or commit_policy not in COMMIT_POLICIES:
        raise HelperError("invalid_request", "Task commit policy is invalid")
    return {
        "objective": _required_text(request, "objective"),
        "ownership": ownership,
        "acceptanceCriteria": acceptance,
        "dependencies": dependencies,
        "mode": mode,
        "maxRounds": rounds,
        "commitPolicy": commit_policy,
    }


class StateStore:
    def __init__(self, root: Path, path: Path, connection: sqlite3.Connection) -> None:
        self.root = root
        self.path = path
        self.connection = connection

    @classmethod
    def open(cls) -> StateStore:
        previous_umask = os.umask(0o077)
        try:
            root = orchestration_root()
            path = root / "state.sqlite3"
            if path.is_symlink():
                raise HelperError("unsafe_state", "Orchestration database cannot use a symlink")
            connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            state = cls(root, path, connection)
            try:
                state._initialize()
                state._privatize()
            except BaseException:
                connection.close()
                raise
            return state
        finally:
            os.umask(previous_umask)

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        meta_exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if meta_exists is not None:
            existing = dict(self.connection.execute("SELECT key, value FROM meta"))
            if (
                existing.get("schema_version") not in {None, str(SCHEMA_VERSION)}
                or existing.get("protocol_version") not in {None, str(PROTOCOL_VERSION)}
            ):
                return
        statements = (
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            """
            CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                coordinator_session_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tasks(
                task_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                objective TEXT NOT NULL,
                ownership_json TEXT NOT NULL,
                acceptance_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                mode TEXT NOT NULL,
                max_rounds INTEGER NOT NULL,
                prompt_count INTEGER NOT NULL,
                commit_policy TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS workers(
                worker_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                workspace TEXT,
                repository TEXT,
                branch TEXT,
                base_commit TEXT,
                worktree_path TEXT,
                tmux_session TEXT UNIQUE,
                socket_path TEXT UNIQUE,
                travis_session_id TEXT,
                status TEXT NOT NULL,
                retained INTEGER NOT NULL,
                protocol_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS dispatches(
                dispatch_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                worker_id TEXT NOT NULL REFERENCES workers(worker_id),
                capability_hash TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                parent_message_id TEXT,
                status TEXT NOT NULL,
                accepted_at TEXT,
                settled_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages(
                message_id TEXT PRIMARY KEY,
                dispatch_id TEXT NOT NULL REFERENCES dispatches(dispatch_id),
                sender TEXT NOT NULL,
                kind TEXT NOT NULL,
                parent_message_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_delivered_at TEXT,
                delivery_count INTEGER NOT NULL,
                acknowledged_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS idempotency(
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(scope, key)
            )
            """,
        )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                self.connection.execute(statement)
            self.connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('protocol_version', ?)",
                (str(PROTOCOL_VERSION),),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _privatize(self) -> None:
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise HelperError("unsafe_state", "Orchestration state contains a symlink")
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")
            self._privatize()

    def metadata(self) -> dict[str, str]:
        return dict(self.connection.execute("SELECT key, value FROM meta"))

    def require_compatible(self) -> None:
        metadata = self.metadata()
        if metadata.get("schema_version") != str(SCHEMA_VERSION) or metadata.get(
            "protocol_version"
        ) != str(PROTOCOL_VERSION):
            raise HelperError(
                "incompatible_state",
                "Orchestration state version is incompatible with this helper",
                next_actions=("Use read-only show commands or recover --inspect-only.",),
            )

    def is_known_capability(self, value: str) -> bool:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        try:
            row = self.connection.execute(
                "SELECT 1 FROM dispatches WHERE capability_hash = ? LIMIT 1", (digest,)
            ).fetchone()
        except sqlite3.DatabaseError:
            return False
        return row is not None

    def _run_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        validate_id("run", row["run_id"])
        validate_status("run", row["status"])
        return {
            "runId": row["run_id"],
            "objective": row["objective"],
            "coordinatorSessionId": row["coordinator_session_id"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _task_from_row(self, row: sqlite3.Row) -> dict[str, object]:
        validate_id("task", row["task_id"])
        validate_id("run", row["run_id"])
        validate_status("task", row["status"])
        if (
            not isinstance(row["mode"], str)
            or row["mode"] not in TASK_MODES
            or not isinstance(row["commit_policy"], str)
            or row["commit_policy"] not in COMMIT_POLICIES
        ):
            raise HelperError("invalid_state", "Stored orchestration state is invalid")
        try:
            ownership = json.loads(row["ownership_json"])
            acceptance = json.loads(row["acceptance_json"])
            dependencies = json.loads(row["dependencies_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise HelperError("invalid_state", "Stored orchestration state is invalid") from exc
        if (
            not isinstance(ownership, dict)
            or not isinstance(acceptance, list)
            or not isinstance(dependencies, list)
            or isinstance(row["max_rounds"], bool)
            or not isinstance(row["max_rounds"], int)
            or not 1 <= row["max_rounds"] <= HARD_MAX_ROUNDS
            or isinstance(row["prompt_count"], bool)
            or not isinstance(row["prompt_count"], int)
            or row["prompt_count"] < 0
        ):
            raise HelperError("invalid_state", "Stored orchestration state is invalid")
        return {
            "taskId": row["task_id"],
            "runId": row["run_id"],
            "objective": row["objective"],
            "ownership": ownership,
            "acceptanceCriteria": acceptance,
            "dependencies": dependencies,
            "mode": row["mode"],
            "maxRounds": row["max_rounds"],
            "promptCount": row["prompt_count"],
            "commitPolicy": row["commit_policy"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def get_run(self, run_id: str) -> dict[str, object]:
        validate_id("run", run_id)
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise HelperError("not_found", "Run was not found")
        return self._run_from_row(row)

    def list_runs(self, limit: int) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT * FROM runs ORDER BY created_at, run_id LIMIT ?", (limit,)
        ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def create_run(self, request: dict[str, object], key: str) -> dict[str, object]:
        self.require_compatible()
        normalized = validate_run_request(request, self)
        validate_idempotency_key(key, self)
        scope = "run-create"
        with self.transaction():
            previous = self.connection.execute(
                "SELECT response_json FROM idempotency WHERE scope = ? AND key = ?",
                (scope, key),
            ).fetchone()
            if previous is not None:
                try:
                    saved = json.loads(previous["response_json"])
                    run_id = saved["run"]["runId"]
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise HelperError("invalid_state", "Stored idempotency state is invalid") from exc
                return {"effect": "reused", "run": self.get_run(run_id)}
            run_id = new_id("run")
            timestamp = utc_now()
            self.connection.execute(
                """
                INSERT INTO runs(
                    run_id, objective, coordinator_session_id, status, created_at, updated_at
                ) VALUES(?, ?, ?, 'active', ?, ?)
                """,
                (
                    run_id,
                    normalized["objective"],
                    normalized["coordinatorSessionId"],
                    timestamp,
                    timestamp,
                ),
            )
            result = {"effect": "created", "run": self.get_run(run_id)}
            self.connection.execute(
                "INSERT INTO idempotency(scope, key, response_json, created_at) VALUES(?, ?, ?, ?)",
                (scope, key, _canonical_json(result), timestamp),
            )
            return result

    def get_task(self, task_id: str) -> dict[str, object]:
        validate_id("task", task_id)
        row = self.connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise HelperError("not_found", "Task was not found")
        return self._task_from_row(row)

    def list_tasks(self, run_id: str, limit: int) -> list[dict[str, object]]:
        self.get_run(run_id)
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY created_at, task_id LIMIT ?",
            (run_id, limit),
        ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def create_task(
        self,
        run_id: str,
        request: dict[str, object],
        key: str,
    ) -> dict[str, object]:
        self.require_compatible()
        validate_id("run", run_id)
        normalized = validate_task_request(request, self)
        validate_idempotency_key(key, self)
        scope = f"task-create:{run_id}"
        with self.transaction():
            self.get_run(run_id)
            previous = self.connection.execute(
                "SELECT response_json FROM idempotency WHERE scope = ? AND key = ?",
                (scope, key),
            ).fetchone()
            if previous is not None:
                try:
                    saved = json.loads(previous["response_json"])
                    task_id = saved["task"]["taskId"]
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise HelperError("invalid_state", "Stored idempotency state is invalid") from exc
                return {"effect": "reused", "task": self.get_task(task_id)}
            dependencies = normalized["dependencies"]
            assert isinstance(dependencies, list)
            for dependency in dependencies:
                dependency_task = self.get_task(dependency)
                if dependency_task["runId"] != run_id:
                    raise HelperError("invalid_request", "Task dependency belongs to another Run")
            task_id = new_id("task")
            timestamp = utc_now()
            self.connection.execute(
                """
                INSERT INTO tasks(
                    task_id, run_id, objective, ownership_json, acceptance_json,
                    dependencies_json, mode, max_rounds, prompt_count, commit_policy,
                    status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'pending', ?, ?)
                """,
                (
                    task_id,
                    run_id,
                    normalized["objective"],
                    _canonical_json(normalized["ownership"]),
                    _canonical_json(normalized["acceptanceCriteria"]),
                    _canonical_json(dependencies),
                    normalized["mode"],
                    normalized["maxRounds"],
                    normalized["commitPolicy"],
                    timestamp,
                    timestamp,
                ),
            )
            result = {"effect": "created", "task": self.get_task(task_id)}
            self.connection.execute(
                "INSERT INTO idempotency(scope, key, response_json, created_at) VALUES(?, ?, ?, ?)",
                (scope, key, _canonical_json(result), timestamp),
            )
            return result


def _bounded_limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= MAX_MESSAGE_LIMIT:
        raise HelperError("invalid_arguments", "List limit must be between 1 and 50")
    return value


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(add_help=False, prog="orchestrate.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("guide", add_help=False)

    run_create = subparsers.add_parser("run-create", add_help=False)
    run_create.add_argument("--request-file", required=True)
    run_create.add_argument("--consume-request-file", action="store_true")
    run_create.add_argument("--idempotency-key", required=True)

    run_show = subparsers.add_parser("run-show", add_help=False)
    run_show.add_argument("--run-id", required=True)

    run_list = subparsers.add_parser("run-list", add_help=False)
    run_list.add_argument("--limit", type=int, default=MAX_MESSAGE_LIMIT)

    task_create = subparsers.add_parser("task-create", add_help=False)
    task_create.add_argument("--run-id", required=True)
    task_create.add_argument("--request-file", required=True)
    task_create.add_argument("--consume-request-file", action="store_true")
    task_create.add_argument("--idempotency-key", required=True)

    task_show = subparsers.add_parser("task-show", add_help=False)
    task_show.add_argument("--task-id", required=True)

    task_list = subparsers.add_parser("task-list", add_help=False)
    task_list.add_argument("--run-id", required=True)
    task_list.add_argument("--limit", type=int, default=MAX_MESSAGE_LIMIT)

    return parser


def execute(arguments: Sequence[str]) -> dict[str, object]:
    if not arguments or any(argument in {"-h", "--help"} for argument in arguments):
        return guide()
    parsed = build_parser().parse_args(list(arguments))
    command = parsed.command
    if command == "guide":
        return guide()
    with StateStore.open() as state:
        if command == "run-create":
            request = _read_private_request(
                parsed.request_file,
                consume=parsed.consume_request_file,
            )
            result = state.create_run(request, parsed.idempotency_key)
            return envelope(
                command,
                result,
                next_actions=("Create a Task in this Run before starting a Worker.",),
            )
        if command == "run-show":
            return envelope(command, {"run": state.get_run(parsed.run_id)})
        if command == "run-list":
            return envelope(command, {"runs": state.list_runs(_bounded_limit(parsed.limit))})
        if command == "task-create":
            request = _read_private_request(
                parsed.request_file,
                consume=parsed.consume_request_file,
            )
            result = state.create_task(parsed.run_id, request, parsed.idempotency_key)
            return envelope(
                command,
                result,
                next_actions=("Start a Worker only after confirming workspace ownership.",),
            )
        if command == "task-show":
            return envelope(command, {"task": state.get_task(parsed.task_id)})
        if command == "task-list":
            return envelope(
                command,
                {"tasks": state.list_tasks(parsed.run_id, _bounded_limit(parsed.limit))},
            )
    raise HelperError("invalid_arguments", "Command is not implemented")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    command = arguments[0] if arguments and not arguments[0].startswith("-") else "guide"
    original_umask = os.umask(0o077)
    try:
        try:
            payload = execute(arguments)
        except HelperError as error:
            print(
                json.dumps(error_envelope(command, error), separators=(",", ":")),
                file=sys.stderr,
            )
            return 2
        except KeyboardInterrupt:
            error = HelperError("interrupted", "Command was interrupted before completion")
            print(
                json.dumps(error_envelope(command, error), separators=(",", ":")),
                file=sys.stderr,
            )
            return 130
        except BaseException:
            error = HelperError(
                "internal_error",
                "Command failed; inspect safe state and retry only with the same idempotency key",
            )
            print(
                json.dumps(error_envelope(command, error), separators=(",", ":")),
                file=sys.stderr,
            )
            return 1
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    finally:
        os.umask(original_umask)


if __name__ == "__main__":
    raise SystemExit(main())
