from __future__ import annotations

from appv22.extensions.base import ObservationContract, SkillCard
from appv22.state.models import AgentState


class FileManagementCodeSearchSkillCard(SkillCard):
    def activates_for(self, state: AgentState) -> bool:
        if super().activates_for(state):
            return True
        if not _is_code_context_followup_request(state):
            return False
        return _has_prior_code_evidence(state)


def _is_code_context_followup_request(state: AgentState) -> bool:
    text = (state.request.active_user_request or state.request.user_goal).strip().lower()
    normalized = " ".join(text.split())
    if normalized in {
        "and",
        "and?",
        "and ?",
        "?",
        "continue",
        "continue?",
        "go on",
        "more",
        "retry",
        "retyr",
        "what else",
        "what else?",
    }:
        return True
    referential_terms = ("that", "it", "those", "same file", "same code", "above", "previous")
    code_followup_terms = ("line", "lines", "count", "how many", "length", "retry")
    return any(term in normalized for term in referential_terms) and any(
        term in normalized for term in code_followup_terms
    )


def _has_prior_code_evidence(state: AgentState) -> bool:
    for ref in state.world_refs.values():
        if not isinstance(ref, dict):
            continue
        if _is_code_evidence_ref(ref):
            return True
    return False


def _is_code_evidence_ref(ref: dict) -> bool:
    kind = ref.get("kind")
    if kind in {"file_management.grep", "file_management.read_range", "file_management.search_text"}:
        return True
    if kind == "file_management.read_file":
        return _has_code_path(ref.get("arguments")) or _has_code_path(ref.get("payload"))
    if kind == "file_management.read_many":
        return _has_code_paths(ref.get("arguments")) or _has_code_paths(ref.get("payload"))
    return False


def _has_code_paths(value) -> bool:
    if not isinstance(value, dict):
        return False
    paths = value.get("paths")
    if isinstance(paths, list | tuple):
        return any(_is_code_path(item) for item in paths)
    files = value.get("files")
    if isinstance(files, list | tuple):
        for item in files:
            if isinstance(item, dict) and _is_code_path(item.get("path")):
                return True
            if _is_code_path(item):
                return True
    return _has_code_path(value)


def _has_code_path(value) -> bool:
    return isinstance(value, dict) and _is_code_path(value.get("path"))


def _is_code_path(value) -> bool:
    if not isinstance(value, str):
        return False
    return value.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".md", ".json"))


WORKSPACE_NAVIGATION_SKILL = SkillCard(
    skill_id="file_management.workspace_navigation",
    extension_id="file_management",
    triggers=(
        "workspace",
        "files",
        "path",
        "paths",
        "clean",
        "cleanup",
        "mess",
        "organize",
        "reorganize",
        "folder",
        "folders",
        "dir",
        "directory",
        "directories",
        "ls",
        "list",
        "tree",
        "scan",
        "inspect",
        "inside",
        "inisde",
        "explore",
        "codebase",
        "repository",
        "source tree",
        "project tree",
        ".venv",
        "node_modules",
        "exclude",
    ),
    modes=("START", "THINK", "OBSERVE", "VERIFY"),
    summary="Pi-style workspace navigation tools for compact repo layout, file discovery, and bounded snapshots.",
    always_active=True,
    tool_ids=(
        "file_management.tree",
        "file_management.repo_snapshot",
        "file_management.find_files",
    ),
    instructions=(
        "Use file_management.tree first for compact repo layout when the user asks what is inside a path or wants codebase orientation.",
        "Use file_management.find_files to select candidate files by path/glob before reading code.",
        "Use file_management.repo_snapshot only when directory/file lists plus lightweight previews are useful; pass path/exclude limits to avoid dependency noise.",
        "Default to focused navigation over broad snapshots when the request names a file or symbol.",
        "Never call unregistered generic tools such as observe; use only selected file_management.* tool IDs.",
    ),
    observation_contract=ObservationContract(
        evidence_refs=("world://file_management.repo_snapshot/latest",),
        evidence_kinds=("file_management.repo_snapshot", "file_management.tree", "file_management.find_files"),
        preferred_tool_id="file_management.tree",
    ),
)


CODE_SEARCH_SKILL = FileManagementCodeSearchSkillCard(
    skill_id="file_management.code_search",
    extension_id="file_management",
    triggers=(
        "explain",
        "breakdown",
        "design pattern",
        "design patterns",
        "code",
        "module",
        "class",
        "function",
        "method",
        "symbol",
        "grep",
        "search",
        "find",
        "read",
        "open",
        "show",
        "print",
        "display",
        "content",
        "contents",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".md",
        ".json",
    ),
    modes=("START", "THINK", "OBSERVE", "VERIFY"),
    summary="Pi-style code inspection tools for symbol search, exact line-range reads, and bounded file content evidence.",
    always_active=True,
    tool_ids=(
        "file_management.grep",
        "file_management.read_range",
        "file_management.read_file",
        "file_management.search_text",
        "file_management.find_files",
    ),
    instructions=(
        "For a bare filename or follow-up like 'next one is <file>', preserve the previous task shape when recent turns make it clear, then use file_management.find_files before file_management.read_file.",
        "For a named file explanation or design-pattern breakdown, prefer grep for symbols/headings, then read_range for relevant slices, then answer from exact evidence.",
        "If file_management.read_file reports missing_file for a partial path, recover with file_management.find_files using the bare filename before answering.",
        "Use file_management.read_range instead of full-file reads when only a class, function, or nearby lines are needed.",
        "Use file_management.read_file when the user explicitly asks for full exact file contents or the file is small enough to inspect directly.",
        "Use file_management.search_text for literal text search; use file_management.grep when regex or code-oriented search is useful.",
        "Do not use broad multi-file reads for a single-file question unless the user asks for cross-file context.",
        "Never call unregistered aliases such as read_file without the file_management. prefix.",
    ),
    observation_contract=ObservationContract(
        evidence_kinds=(
            "file_management.grep",
            "file_management.read_range",
            "file_management.read_file",
            "file_management.search_text",
        ),
        preferred_tool_id="file_management.grep",
    ),
)


CODE_SCAN_SKILL = SkillCard(
    skill_id="file_management.code_scan",
    extension_id="file_management",
    triggers=(
        "full code",
        "code lvl",
        "code-level",
        "code review",
        "full review",
        "review in src",
        "scan the codebase",
        "codebase scan",
    ),
    modes=("START", "THINK", "OBSERVE", "VERIFY"),
    summary="Pi-style bounded multi-file code scan tools for broader reviews after candidate files are selected.",
    always_active=True,
    tool_ids=(
        "file_management.tree",
        "file_management.find_files",
        "file_management.grep",
        "file_management.read_range",
        "file_management.read_many",
    ),
    instructions=(
        "For broad code reviews, use tree/find_files first, then read_many only for a small selected set of high-value files.",
        "Prefer grep plus read_range when reviewing a specific subsystem or symbol.",
        "Keep read_many bounded; do not read dependency, cache, or generated directories.",
        "Summarize findings from tool evidence and mention when deeper inspection would require additional focused reads.",
    ),
    observation_contract=ObservationContract(
        evidence_kinds=("file_management.find_files", "file_management.read_many", "file_management.read_range"),
        preferred_tool_id="file_management.tree",
    ),
)


FILE_MUTATION_SKILL = SkillCard(
    skill_id="file_management.file_mutation",
    extension_id="file_management",
    triggers=(
        "write",
        "add",
        "fix",
        "bugfix",
        "update",
        "create",
        "make",
        "mkdir",
        "move",
        "rename",
        "copy",
        "delete",
        "remove",
        "edit",
        "patch",
        "replace",
        "record",
        "leave",
        "useful",
        "next person",
        "capture",
        "decision",
        "durable file",
        "checklist",
        "note",
        "notes",
        "onboarding",
        "runbook",
        "stub",
        "document",
        "handoff",
    ),
    modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
    summary="Pi-style file mutation tools for explicit workspace writes, moves, copies, directory creation, and deletion.",
    always_active=False,
    tool_ids=(
        "file_management.write_file",
        "file_management.edit_file",
        "file_management.mkdir",
        "file_management.move_file",
        "file_management.copy_file",
        "file_management.delete_file",
        "file_management.read_file",
        "file_management.repo_snapshot",
    ),
    instructions=(
        "Use edit_file for existing-file targeted edits after reading exact current content; each oldText must match exactly once.",
        "Use write_file only for new files or complete file rewrites; create parent directories as needed.",
        "Use mkdir, move_file, copy_file, and delete_file only when the latest user request asks for filesystem changes.",
        "Use read_file before mutation only when exact current content is required.",
        "Use file-content cues before cleanup moves; when sources have a colliding basename, the first clear colliding source claims the common destination and later colliding sources should be held or recorded.",
        "When the user asks for a durable cleanup record, write docs/workspace_manifest.json with moves, held items, collisions, and deletions.",
        "Treat human-authored artifacts as the primary cleanup inputs and machine/session traces as lower-priority evidence unless the user asks otherwise.",
        "prefer move_file for reorganization, copy_file only when preserving the source is required, and remove obvious junk with file_management.delete_file when deletion is requested.",
        "Include deletions in docs/workspace_manifest.json when deleting files.",
        "Do not invent manifests, moves, deletes, or records unless the latest user request asks for them.",
        "Do not emit finalize until every requested mutation and record entry is backed by tool evidence.",
        "For multi-step file requests, continue using selected tools until each clear requested file operation is backed by tool evidence before finalizing.",
    ),
    observation_contract=ObservationContract(
        evidence_refs=("world://file_management.repo_snapshot/latest",),
        evidence_kinds=("file_management.repo_snapshot",),
        preferred_tool_id="file_management.repo_snapshot",
    ),
)


FILE_MANAGEMENT_SKILLS = (
    WORKSPACE_NAVIGATION_SKILL,
    CODE_SEARCH_SKILL,
    CODE_SCAN_SKILL,
    FILE_MUTATION_SKILL,
)

# Compatibility alias for older imports/tests that expect one file-management card.
FILE_MANAGEMENT_SKILL = FILE_MUTATION_SKILL
