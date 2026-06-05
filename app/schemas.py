"""Core runtime schemas for Phase 1."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from app.repair_policy import WRITE_OPERATION_REPAIR_ATTEMPTS


ResultStatus = Literal[
    "completed",
    "completed_with_failed_verification",
    "failed",
    "blocked",
    "budget_exceeded",
    "needs_replan",
    "kernel_error",
]
WorkerIssueType = Literal["instance_failure", "plan_failure", "kernel_failure"]
TrustLevel = Literal["unknown", "worker_reported", "verified"]
WritePolicyMode = Literal["readonly", "advisory", "strict"]
ExactLiteralKind = Literal["json_key", "path", "filename", "artifact_id", "symbol", "other"]
ExactLiteralSource = Literal["user_input", "runtime_observation", "model"]


class PermissionSet(BaseModel):
    """Runtime-normalized worker permissions with dict-like compatibility."""

    model_config = ConfigDict(extra="forbid")

    read_files: bool = False
    write_files: bool = False
    run_commands: bool = False
    web_research: bool = False
    write_paths: list[str] = Field(default_factory=list)
    write_paths_from_artifacts: list[str] = Field(default_factory=list)
    provided_keys: SkipJsonSchema[set[str]] = Field(default_factory=set, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_mapping(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if value is None:
            value = {}
        if not isinstance(value, dict):
            return value

        provided_keys = set(value.keys())
        data = dict(value)
        for key in ("read_files", "write_files", "run_commands", "web_research"):
            data.setdefault(key, False)
        for key in ("write_paths", "write_paths_from_artifacts"):
            if data.get(key) is None:
                data[key] = []
        data["provided_keys"] = provided_keys
        return data

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.provided_keys

    def __getitem__(self, key: str) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in type(self).model_fields:
            raise KeyError(key)
        setattr(self, key, value)
        self.provided_keys.add(key)

    def pop(self, key: str, default: Any = None) -> Any:
        value = self.get(key, default)
        if key in {"read_files", "write_files", "run_commands", "web_research"}:
            setattr(self, key, False)
        elif key in {"write_paths", "write_paths_from_artifacts"}:
            setattr(self, key, [])
        self.provided_keys.discard(key)
        return value

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude={"provided_keys"})


class ArtifactPayload(BaseModel):
    """Runtime artifact with provenance fields and legacy extra-key support."""

    model_config = ConfigDict(extra="allow")

    id: str
    content: Any = None
    kind: str | None = None
    producer: str | None = None
    step_id: str | None = None
    attempt_id: str | None = None
    trust_level: TrustLevel = "worker_reported"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_artifact(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "id" not in data and "artifact_id" in data:
            data["id"] = data["artifact_id"]
        return data

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        extra = self.__pydantic_extra__ or {}
        return extra.get(key, default)

    def __getitem__(self, key: str) -> Any:
        value = self.get(key, None)
        if value is None and key not in type(self).model_fields and key not in (self.__pydantic_extra__ or {}):
            raise KeyError(key)
        return value


class ExactLiteral(BaseModel):
    """Exact user/runtime literals that models must preserve without synonymizing."""

    model_config = ConfigDict(extra="forbid")

    value: str
    kind: ExactLiteralKind = "other"
    source: ExactLiteralSource = "user_input"

    @model_validator(mode="after")
    def validate_literal(self) -> "ExactLiteral":
        self.value = " ".join(str(self.value or "").strip().split())
        if not self.value:
            raise ValueError("exact literal value must be non-empty")
        return self


class MutationMove(BaseModel):
    """One scoped file move operation inside a mutation scope."""

    model_config = ConfigDict(extra="forbid")

    source: str
    destination: str

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_move(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return value

        data = dict(value)
        source = data.get("source") or data.get("from") or data.get("src")
        destination = data.get("destination") or data.get("to") or data.get("dest") or data.get("target")
        return {"source": source, "destination": destination}

    @model_validator(mode="after")
    def validate_paths(self) -> "MutationMove":
        source = normalize_repo_relative_path(self.source)
        destination = normalize_repo_relative_path(self.destination)
        if source is None:
            raise ValueError(f"mutation_scope.move_pairs.source contains invalid repo-relative path: {self.source}")
        if destination is None:
            raise ValueError(
                f"mutation_scope.move_pairs.destination contains invalid repo-relative path: {self.destination}"
            )
        self.source = source
        self.destination = destination
        return self


class MutationScope(BaseModel):
    """Worker-proposed write scope resolved at the kernel boundary.

    DESIGN workers may emit flexible proposal shapes. For bounded mutation, the
    kernel can use this as advisory context while the write tools remain the
    final authority for concrete file operations.
    """

    model_config = ConfigDict(extra="forbid")

    target_paths: list[str]
    create_paths: list[str] = Field(default_factory=list)
    update_paths: list[str] = Field(default_factory=list)
    delete_paths: list[str] = Field(default_factory=list)
    manifest_paths: list[str] = Field(default_factory=list)
    directory_paths: list[str] = Field(default_factory=list)
    move_pairs: list[MutationMove] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    forbidden_globs: list[str] = Field(default_factory=list)
    reason: str = "derived from mutation scope artifact"
    max_files: int = 5
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_scope(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value

        if isinstance(value, ArtifactPayload):
            value = value.content

        if not isinstance(value, dict):
            return {
                "target_paths": extract_repo_path_candidates(value),
                "reason": "derived from unstructured mutation scope artifact",
            }

        data = dict(value)
        target_paths = _collect_scope_paths(
            data,
            keys=(
                "target_paths",
                "write_paths",
                "write_scope_paths",
                "paths",
                "files",
                "allowed_paths",
                "candidate_paths",
                "path",
                "file",
            ),
            fallback=True,
        )
        create_paths = _collect_scope_paths(
            data,
            keys=("create_paths", "creations", "created_paths", "new_files", "files_to_create", "write_files"),
        )
        update_paths = _collect_scope_paths(
            data,
            keys=("update_paths", "updates", "updated_paths", "modified_paths", "edit_paths", "files_to_update"),
        )
        delete_paths = _collect_scope_paths(
            data,
            keys=("delete_paths", "deletions", "deleted_paths", "remove_paths", "files_to_delete"),
        )
        manifest_paths = _collect_scope_paths(data, keys=("manifest_paths", "manifests", "manifest_target"))
        directory_paths = _collect_scope_paths(
            data,
            keys=("directory_paths", "directories", "created_directories", "dirs"),
        )
        move_pairs = _collect_move_pairs(data.get("move_pairs") or data.get("moves") or data.get("renames") or [])
        operation_paths = _operation_scope_paths(
            create_paths=create_paths,
            update_paths=update_paths,
            delete_paths=delete_paths,
            manifest_paths=manifest_paths,
            directory_paths=directory_paths,
            move_pairs=move_pairs,
        )
        target_paths = _dedupe_paths(target_paths + operation_paths)
        test_paths = _collect_scope_paths(data, keys=("test_paths", "tests", "test_files", "test_path"))
        forbidden_paths, forbidden_globs = _collect_forbidden_scope_paths(
            data,
            keys=("forbidden_paths", "forbidden", "forbidden_files", "excluded_paths", "forbidden_globs"),
        )

        return {
            "target_paths": target_paths,
            "create_paths": create_paths,
            "update_paths": update_paths,
            "delete_paths": delete_paths,
            "manifest_paths": manifest_paths,
            "directory_paths": directory_paths,
            "move_pairs": move_pairs,
            "test_paths": test_paths,
            "forbidden_paths": forbidden_paths,
            "forbidden_globs": forbidden_globs,
            "reason": str(data.get("reason") or data.get("notes") or data.get("rationale") or "derived from mutation scope artifact"),
            "max_files": data.get("max_files", max(1, len(target_paths))),
            "metadata": data.get("metadata") or {},
        }

    @model_validator(mode="after")
    def validate_scope(self) -> "MutationScope":
        self.target_paths = _dedupe_paths(self.target_paths)
        self.create_paths = _dedupe_paths(self.create_paths)
        self.update_paths = _dedupe_paths(self.update_paths)
        self.delete_paths = _dedupe_paths(self.delete_paths)
        self.manifest_paths = _dedupe_paths(self.manifest_paths)
        self.directory_paths = _dedupe_paths(self.directory_paths)
        self.test_paths = _dedupe_paths(self.test_paths)
        self.forbidden_paths = _dedupe_paths(self.forbidden_paths)
        self.forbidden_globs = _dedupe_globs(self.forbidden_globs)
        self.target_paths = _dedupe_paths(
            self.target_paths
            + _operation_scope_paths(
                create_paths=self.create_paths,
                update_paths=self.update_paths,
                delete_paths=self.delete_paths,
                manifest_paths=self.manifest_paths,
                directory_paths=self.directory_paths,
                move_pairs=self.move_pairs,
            )
        )
        if not self.target_paths:
            raise ValueError("mutation_scope.target_paths must be non-empty")
        if self.max_files < 1:
            raise ValueError("mutation_scope.max_files must be positive")
        if len(self.target_paths) > self.max_files:
            declared_max_files = self.max_files
            self.max_files = len(self.target_paths)
            self.metadata = {
                **self.metadata,
                "declared_max_files": declared_max_files,
                "max_files_widened": True,
                "validation_warnings": [
                    *list(self.metadata.get("validation_warnings") or []),
                    {
                        "code": "max_files_widened",
                        "message": (
                            f"mutation_scope contains {len(self.target_paths)} target paths, "
                            f"exceeding max_files={declared_max_files}; max_files was widened "
                            "and concrete writes remain tool-gated"
                        ),
                    },
                ],
            }
        return self

    @property
    def write_scope_paths(self) -> list[str]:
        return _dedupe_paths(
            self.target_paths
            + _operation_scope_paths(
                create_paths=self.create_paths,
                update_paths=self.update_paths,
                delete_paths=self.delete_paths,
                manifest_paths=self.manifest_paths,
                directory_paths=self.directory_paths,
                move_pairs=self.move_pairs,
            )
        )


def resolve_mutation_scope_proposal(value: Any, *, source_artifact_id: str | None = None) -> MutationScope:
    """Resolve a flexible worker proposal into the strict write-scope contract."""

    scope = MutationScope.model_validate(value)
    metadata = {
        **scope.metadata,
        "resolver": "mutation_scope_proposal_v1",
    }
    if source_artifact_id:
        metadata["source_artifact_id"] = source_artifact_id
    return scope.model_copy(update={"metadata": metadata})


class WritePolicy(BaseModel):
    """Worker-facing write policy enforced by the kernel/tool boundary."""

    model_config = ConfigDict(extra="forbid")

    mode: WritePolicyMode = "advisory"
    advisory_paths: list[str] = Field(default_factory=list)
    strict_allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    forbidden_globs: list[str] = Field(default_factory=list)
    batch_max_files: int = 6
    step_max_files: int = 25
    repair_attempts: int = WRITE_OPERATION_REPAIR_ATTEMPTS
    validation_warnings: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "WritePolicy":
        self.advisory_paths = _dedupe_policy_paths(self.advisory_paths)
        self.strict_allowed_paths = _dedupe_policy_paths(self.strict_allowed_paths)
        self.forbidden_paths = _dedupe_policy_paths(self.forbidden_paths)
        self.forbidden_globs = _dedupe_globs(
            glob
            for glob in (_normalize_repo_glob(pattern) for pattern in self.forbidden_globs)
            if glob is not None
        )
        if self.strict_allowed_paths:
            self.mode = "strict"
        elif self.mode == "strict":
            self.mode = "advisory"
        if self.batch_max_files < 1:
            raise ValueError("write_policy.batch_max_files must be positive")
        if self.step_max_files < 1:
            raise ValueError("write_policy.step_max_files must be positive")
        if self.repair_attempts < 0:
            raise ValueError("write_policy.repair_attempts cannot be negative")
        return self


class MutationOperationDenial(BaseModel):
    """Structured denial returned to a worker when a proposed mutation is unsafe."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    tool_name: str | None = None
    touched_paths: list[str] = Field(default_factory=list)
    rejected_paths: list[str] = Field(default_factory=list)
    repairable: bool = True
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: WorkerIssueType
    code: str
    message: str
    step_id: str | None = None
    worker_type: str | None = None
    attempt_id: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    failed_step_id: str
    issue_codes: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    partial_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    raw_input: str
    normalized_input: str
    user_goal: str | None = None

    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    complexity_hint: str = "medium"
    confidence: float = 0.0

    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    literal_contract: list[ExactLiteral] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    step_id: str
    worker_type: str
    phase: str | None = None
    mode: Literal["observe_only", "plan_only", "bounded_mutation", "verify_only", "summarize_only"] | None = None
    task_id: str | None = None

    instruction: str

    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)

    max_tool_calls: int = 3
    max_model_calls: int = 1

    permissions: PermissionSet = Field(default_factory=PermissionSet)


class Plan(BaseModel):
    plan_id: str
    request_id: str

    planner: str
    objective: str
    strategy: str
    execution_pattern: str | None = None

    steps: list[PlanStep]
    budget: dict[str, Any] = Field(default_factory=dict)
    global_invariants: list[str] = Field(default_factory=list)

    success_criteria: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplanRequest(BaseModel):
    request_id: str
    plan_id: str
    run_id: str
    failed_step_id: str
    reason: str

    worker_result: dict[str, Any] = Field(default_factory=dict)
    completed_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    carryover_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    completed_step_ids: list[str] = Field(default_factory=list)
    remaining_budget: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None
    issues: list[WorkerIssue] = Field(default_factory=list)
    partial_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    failed_step_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    failed_step: dict[str, Any] = Field(default_factory=dict)
    failure_observation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def default_carryover_artifacts(self) -> "ReplanRequest":
        if not self.carryover_artifacts:
            self.carryover_artifacts = list(self.completed_artifacts)
        return self


class Task(BaseModel):
    task_id: str
    run_id: str
    step_id: str

    worker_type: str
    instruction: str

    input_artifacts: list[ArtifactPayload] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)

    max_tool_calls: int = 3
    max_model_calls: int = 1

    permissions: PermissionSet = Field(default_factory=PermissionSet)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    run_id: str
    producer: str

    status: ResultStatus
    summary: str

    artifacts: list[ArtifactPayload] = Field(default_factory=list)

    usage: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(TypedDict, total=False):
    user_input: str
    envelope: dict[str, Any]
    plan: dict[str, Any]
    result: dict[str, Any]
    runtime_matrix: dict[str, Any]
    errors: list[str]


_KNOWN_FILE_SUFFIXES = {
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".go",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_KNOWN_FILE_NAMES = {
    ".babelrc",
    ".coveragerc",
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".eslintignore",
    ".eslintrc",
    ".flake8",
    ".gitignore",
    ".node-version",
    ".npmrc",
    ".nvmrc",
    ".prettierignore",
    ".prettierrc",
    ".python-version",
    ".stylelintrc",
    "Dockerfile",
    "Makefile",
    "Pipfile",
    "README",
    "README.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}
_LABELED_PATH_RE = re.compile(
    r"(?:^|[\s,;([{])(?:file|path|target|source|test file|test path)s?:\s*`?([^`\n,;]+)`?",
    re.IGNORECASE,
)
_GENERIC_PATH_RE = re.compile(r"`?((?:[A-Za-z0-9_.@+\-]+/)+[A-Za-z0-9_.@+\-]+|[A-Za-z0-9_.@+\-]+\.[A-Za-z0-9_]+)`?")
_SNAKE_LITERAL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_FILENAME_LITERAL_RE = re.compile(r"\b[A-Za-z0-9_.@+\-]+\.[A-Za-z0-9_]+\b")


def extract_repo_path_candidates(value: Any) -> list[str]:
    """Extract safe-looking repo-relative path candidates from structured or legacy text."""

    candidates: list[str] = []
    if isinstance(value, str):
        candidates.extend(_paths_from_text(value))
    elif isinstance(value, list):
        for item in value:
            candidates.extend(extract_repo_path_candidates(item))
    elif isinstance(value, dict):
        for child in value.values():
            candidates.extend(extract_repo_path_candidates(child))
    return _dedupe_paths(candidates)


def extract_literal_contract(value: Any, *, source: ExactLiteralSource = "user_input") -> list[ExactLiteral]:
    """Extract exact model-preservation literals from user/runtime text.

    This is intentionally syntax-only: it records tokens already present at the
    boundary so later LLM stages cannot replace them with placeholders or
    synonyms, but it does not add new semantic facts.
    """

    if isinstance(value, list):
        literals: list[ExactLiteral] = []
        for item in value:
            literals.extend(extract_literal_contract(item, source=source))
        return _dedupe_literals(literals)
    if isinstance(value, dict):
        literals = []
        for item in value.values():
            literals.extend(extract_literal_contract(item, source=source))
        return _dedupe_literals(literals)
    if not isinstance(value, str):
        return []

    text = value
    literals: list[ExactLiteral] = []
    path_spans: list[tuple[int, int]] = []

    for match in _GENERIC_PATH_RE.finditer(text):
        normalized = _normalize_repo_relative_path(match.group(1), allow_bare_filename=True)
        if normalized is None:
            continue
        path_spans.append(match.span(1))
        kind: ExactLiteralKind = "path" if "/" in normalized else "filename"
        literals.append(ExactLiteral(value=normalized, kind=kind, source=source))

    for match in _FILENAME_LITERAL_RE.finditer(text):
        if _span_inside(match.span(), path_spans):
            continue
        normalized = _normalize_repo_relative_path(match.group(0), allow_bare_filename=True)
        if normalized is not None:
            path_spans.append(match.span())
            literals.append(ExactLiteral(value=normalized, kind="filename", source=source))

    for match in _SNAKE_LITERAL_RE.finditer(text):
        if _span_inside(match.span(), path_spans):
            continue
        token = match.group(0)
        if len(token) > 80:
            continue
        kind: ExactLiteralKind = (
            "json_key"
            if _looks_like_json_key_context(text, match.span()) or _looks_like_manifest_key_token(token)
            else "symbol"
        )
        literals.append(ExactLiteral(value=token, kind=kind, source=source))

    return _dedupe_literals(literals)


def normalize_repo_relative_path(value: str) -> str | None:
    return _normalize_repo_relative_path(value, allow_bare_filename=True)


def _collect_scope_paths(
    data: dict[str, Any],
    *,
    keys: tuple[str, ...],
    fallback: bool = False,
    field_name: str = "target_paths",
) -> list[str]:
    paths: list[str] = []
    for key in keys:
        if key in data:
            paths.extend(_strict_scope_path_values(data[key], field_name=key))
    if fallback and not paths:
        paths.extend(_collect_legacy_scope_paths(data, field_name=field_name))
    return _dedupe_paths(paths)


def _collect_forbidden_scope_paths(data: dict[str, Any], *, keys: tuple[str, ...]) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    globs: list[str] = []
    for key in keys:
        if key not in data:
            continue
        value_paths, value_globs = _forbidden_scope_values(data[key], field_name=key)
        paths.extend(value_paths)
        globs.extend(value_globs)
    return _dedupe_paths(paths), _dedupe_globs(globs)


def _collect_move_pairs(value: Any) -> list[MutationMove]:
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []

    moves: list[MutationMove] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        move = MutationMove.model_validate(item)
        key = (move.source, move.destination)
        if key in seen:
            continue
        seen.add(key)
        moves.append(move)
    return moves


def _operation_scope_paths(
    *,
    create_paths: list[str],
    update_paths: list[str],
    delete_paths: list[str],
    manifest_paths: list[str],
    directory_paths: list[str],
    move_pairs: list[MutationMove],
) -> list[str]:
    move_paths: list[str] = []
    for move in move_pairs:
        move_paths.extend([move.source, move.destination])
    return _dedupe_paths(
        create_paths
        + update_paths
        + delete_paths
        + manifest_paths
        + directory_paths
        + move_paths
    )


def _forbidden_scope_values(value: Any, *, field_name: str) -> tuple[list[str], list[str]]:
    if isinstance(value, str):
        if _has_glob_meta(value):
            normalized = _normalize_repo_glob(value)
            if normalized is None:
                raise ValueError(f"mutation_scope.{field_name} contains invalid repo-relative glob: {value}")
            return [], [normalized]
        return _strict_scope_path_values(value, field_name=field_name), []
    if isinstance(value, list):
        paths: list[str] = []
        globs: list[str] = []
        for item in value:
            item_paths, item_globs = _forbidden_scope_values(item, field_name=field_name)
            paths.extend(item_paths)
            globs.extend(item_globs)
        return _dedupe_paths(paths), _dedupe_globs(globs)
    if isinstance(value, dict):
        paths: list[str] = []
        globs: list[str] = []
        path_keys = (
            "forbidden_paths",
            "forbidden",
            "forbidden_files",
            "excluded_paths",
            "path",
            "file",
        )
        glob_keys = ("forbidden_globs", "excluded_globs", "glob", "pattern")
        for key in path_keys + glob_keys:
            if key not in value:
                continue
            item_paths, item_globs = _forbidden_scope_values(value[key], field_name=key)
            paths.extend(item_paths)
            globs.extend(item_globs)
        return _dedupe_paths(paths), _dedupe_globs(globs)
    return [], []


def _strict_scope_path_values(value: Any, *, field_name: str) -> list[str]:
    if isinstance(value, str):
        whole_path = _normalize_repo_relative_path(value, allow_bare_filename=True)
        paths = [whole_path] if whole_path is not None else _paths_from_text(value)
        if not paths and value.strip():
            raise ValueError(f"mutation_scope.{field_name} contains invalid repo-relative path: {value}")
        return _dedupe_paths(paths)
    if isinstance(value, list):
        paths: list[str] = []
        for item in value:
            paths.extend(_strict_scope_path_values(item, field_name=field_name))
        return _dedupe_paths(paths)
    if isinstance(value, dict):
        paths: list[str] = []
        if "moves" in value:
            paths.extend(_collect_move_paths(value["moves"]))
        path_keys = (
            "target_paths",
            "test_paths",
            "paths",
            "files",
            "allowed_paths",
            "candidate_paths",
            "path",
            "file",
            "manifest_target",
            "source",
            "destination",
        )
        selected_values = [value[key] for key in path_keys if key in value]
        for item in selected_values:
            paths.extend(_strict_scope_path_values(item, field_name=field_name))
        return _dedupe_paths(paths)
    return []


def _collect_legacy_scope_paths(data: Any, *, field_name: str) -> list[str]:
    if isinstance(data, str):
        return _extract_legacy_scope_paths(data)
    if isinstance(data, list):
        paths: list[str] = []
        for item in data:
            paths.extend(_extract_legacy_scope_paths(item))
        return paths
    if not isinstance(data, dict):
        return []

    paths: list[str] = []
    legacy_keys = (
        "evidence",
        "moves",
        "operations",
        "manifest_target",
        "source",
        "destination",
        "path",
        "file",
        "target_paths",
        "paths",
        "files",
        "allowed_paths",
        "candidate_paths",
    )
    for key in legacy_keys:
        if key in data:
            value = data[key]
            if key == "moves":
                paths.extend(_collect_move_paths(value))
            else:
                paths.extend(_extract_legacy_scope_paths(value))
    return paths


def _extract_legacy_scope_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        return extract_repo_path_candidates(value)
    if isinstance(value, list):
        paths: list[str] = []
        for item in value:
            paths.extend(_extract_legacy_scope_paths(item))
        return _dedupe_paths(paths)
    if isinstance(value, dict):
        paths: list[str] = []
        path_keys = (
            "target_paths",
            "paths",
            "files",
            "allowed_paths",
            "candidate_paths",
            "path",
            "file",
            "manifest_target",
            "source",
            "destination",
            "moves",
            "operations",
        )
        for key in path_keys:
            if key in value:
                paths.extend(_extract_legacy_scope_paths(value[key]))
        return _dedupe_paths(paths)
    return []


def _collect_move_paths(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return _extract_legacy_scope_paths(value)

    paths: list[str] = []
    for move in value:
        if isinstance(move, dict):
            if "destination" in move:
                paths.extend(_extract_legacy_scope_paths(move["destination"]))
            if "target" in move:
                paths.extend(_extract_legacy_scope_paths(move["target"]))
            if "path" in move:
                paths.extend(_extract_legacy_scope_paths(move["path"]))
            if "file" in move:
                paths.extend(_extract_legacy_scope_paths(move["file"]))
            if "source" in move:
                paths.extend(_extract_legacy_scope_paths(move["source"]))
            if not any(key in move for key in ("destination", "target", "path", "file", "source")):
                for sub_value in move.values():
                    paths.extend(_extract_legacy_scope_paths(sub_value))
        elif isinstance(move, str):
            paths.extend(_extract_legacy_scope_paths(move))
    return _dedupe_paths(paths)


def _paths_from_text(value: str) -> list[str]:
    candidates: list[str] = []
    normalized_whole = _normalize_repo_relative_path(value, allow_bare_filename=True)
    if normalized_whole is not None:
        candidates.append(normalized_whole)

    for match in _LABELED_PATH_RE.finditer(value):
        normalized = _normalize_repo_relative_path(match.group(1), allow_bare_filename=True)
        if normalized is not None:
            candidates.append(normalized)

    for match in _GENERIC_PATH_RE.finditer(value):
        normalized = _normalize_repo_relative_path(match.group(1), allow_bare_filename=False)
        if normalized is not None:
            candidates.append(normalized)
    return _dedupe_paths(candidates)


def _normalize_repo_relative_path(value: str, *, allow_bare_filename: bool) -> str | None:
    raw = _strip_path_token(value)
    if raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    if _has_glob_meta(raw):
        return None
    if raw.startswith(("-", "~")) or ":" in raw or "://" in raw or "\\" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    name = path.name
    if "/" not in raw and not allow_bare_filename:
        return None
    if "/" not in raw and name not in _KNOWN_FILE_NAMES and path.suffix not in _KNOWN_FILE_SUFFIXES:
        return None
    return path.as_posix()


def _normalize_write_policy_path(value: str) -> str | None:
    raw = _strip_path_token(value)
    if raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    if _has_glob_meta(raw):
        return None
    if raw.startswith(("-", "~")) or ":" in raw or "://" in raw or "\\" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _normalize_repo_glob(value: str) -> str | None:
    raw = _strip_path_token(value)
    if raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return None
    if not _has_glob_meta(raw):
        return None
    if raw.startswith(("-", "~")) or ":" in raw or "://" in raw or "\\" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _has_glob_meta(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _strip_path_token(value: str) -> str:
    raw = value.strip().strip("`'\",;)]}")
    if raw.endswith(".") and raw != ".":
        raw = raw[:-1]
    return raw


def _dedupe_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = normalize_repo_relative_path(str(raw_path))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _dedupe_policy_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = _normalize_write_policy_path(str(raw_path))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _dedupe_globs(globs: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_glob in globs:
        normalized = _normalize_repo_glob(str(raw_glob))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _span_inside(span: tuple[int, int], containers: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in containers)


def _dedupe_literals(literals: list[ExactLiteral]) -> list[ExactLiteral]:
    deduped: list[ExactLiteral] = []
    seen: set[tuple[str, str]] = set()
    for literal in literals:
        key = (literal.kind, literal.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(literal)
    return deduped


def _looks_like_json_key_context(text: str, span: tuple[int, int]) -> bool:
    before = text[max(0, span[0] - 80) : span[0]].lower()
    after = text[span[1] : span[1] + 80].lower()
    window = f"{before} {after}"
    code_signals = ("function", "method", "class", "variable", "symbol", "module", "callable")
    json_signals = (
        "json",
        "manifest",
        "schema",
        "payload",
        "field",
        "fields",
        "key",
        "keys",
        "report",
        "inventory",
        "index",
    )
    if any(signal in window for signal in code_signals) and not any(
        signal in window for signal in ("json", "manifest", "schema", "payload")
    ):
        return False
    return any(signal in window for signal in json_signals)


def _looks_like_manifest_key_token(token: str) -> bool:
    normalized = token.lower()
    return (
        normalized.startswith("moved_")
        or normalized in {"held_items", "skipped_items", "excluded_items", "preserved_items"}
        or normalized.startswith("total_")
        or normalized.endswith("_total")
    )
