"""One bounded language-service tool with normalized read actions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifacts import ArtifactRegistry, artifact_read_instruction
from travis.coding_agent.language_services.documents import position_from_server
from travis.coding_agent.language_services.manager import LanguageServiceManager
from travis.coding_agent.language_services.types import DocumentPosition
from travis.coding_agent.language_services.workspace_edit import (
    ActionTokenStore,
    WorkspaceEditError,
    WorkspaceEditPreview,
    WorkspaceEditPreviewStore,
)
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.coding_agent.tools.types import ToolDefinition

_READ_ACTIONS = (
    "status",
    "diagnostics",
    "symbols",
    "hover",
    "definition",
    "references",
    "code_actions",
    "rename_preview",
    "code_action_preview",
    "apply",
)

LSP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_READ_ACTIONS)},
        "path": {"type": "string"},
        "query": {"type": "string"},
        "line": {"type": "integer", "minimum": 0},
        "character": {"type": "integer", "minimum": 0},
        "start": {
            "type": "object",
            "properties": {
                "line": {"type": "integer", "minimum": 0},
                "character": {"type": "integer", "minimum": 0},
            },
            "required": ["line", "character"],
            "additionalProperties": False,
        },
        "end": {
            "type": "object",
            "properties": {
                "line": {"type": "integer", "minimum": 0},
                "character": {"type": "integer", "minimum": 0},
            },
            "required": ["line", "character"],
            "additionalProperties": False,
        },
        "newName": {"type": "string", "minLength": 1},
        "actionToken": {"type": "string", "pattern": "^lsp-action-[0-9a-f]{32}$"},
        "previewToken": {"type": "string", "pattern": "^lsp-preview-[0-9a-f]{32}$"},
    },
    "required": ["action"],
    "additionalProperties": False,
}


def _nonnegative(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"lsp {name} must be a nonnegative integer")
    return value


def _path_arg(args: dict[str, object]) -> str:
    value = args.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("lsp path must be a non-blank string")
    return value


def _validate_args(args: object) -> tuple[str, dict[str, object]]:
    if not isinstance(args, dict):
        raise ValueError("lsp arguments must be an object")
    action = args.get("action")
    if action not in _READ_ACTIONS:
        raise ValueError("lsp action is unsupported")
    if action == "status":
        return action, args
    if action == "symbols":
        has_path = isinstance(args.get("path"), str) and bool(str(args["path"]).strip())
        has_query = isinstance(args.get("query"), str) and bool(str(args["query"]).strip())
        if has_path == has_query:
            raise ValueError("lsp symbols requires exactly one of path or query")
        return action, args
    if action in {"code_action_preview", "apply"}:
        field = "actionToken" if action == "code_action_preview" else "previewToken"
        prefix = "lsp-action-" if action == "code_action_preview" else "lsp-preview-"
        token = args.get(field)
        if not isinstance(token, str) or not token.startswith(prefix):
            raise ValueError(f"lsp {action} requires a {field}")
        return action, args
    path = _path_arg(args)
    if action in {"hover", "definition", "references", "rename_preview"}:
        _nonnegative(args.get("line"), "line")
        _nonnegative(args.get("character"), "character")
    if action == "rename_preview":
        new_name = args.get("newName")
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("lsp rename_preview requires a non-blank newName")
    if action == "code_actions":
        for name in ("start", "end"):
            position = args.get(name)
            if not isinstance(position, dict):
                raise ValueError(f"lsp code_actions {name} must be a position")
            _nonnegative(position.get("line"), f"{name}.line")
            _nonnegative(position.get("character"), f"{name}.character")
    return action, {**args, "path": path}


def _resolve_workspace_path(workspace: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    resolved = (raw if raw.is_absolute() else workspace / raw).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError("lsp path escapes the workspace")
    return resolved


def _uri_path(workspace: Path, uri: object) -> Path | None:
    if not isinstance(uri, str):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    try:
        resolved = Path(unquote(parsed.path)).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved != workspace and workspace not in resolved.parents:
        return None
    return resolved


def _server_position(value: object) -> DocumentPosition | None:
    if not isinstance(value, dict):
        return None
    line = value.get("line")
    character = value.get("character")
    if (
        not isinstance(line, int)
        or isinstance(line, bool)
        or line < 0
        or not isinstance(character, int)
        or isinstance(character, bool)
        or character < 0
    ):
        return None
    return DocumentPosition(line, character)


def _normalized_range(path: Path, value: object, encoding: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    start = _server_position(value.get("start"))
    end = _server_position(value.get("end"))
    if start is None or end is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
        stable_start = position_from_server(text, start, encoding)  # type: ignore[arg-type]
        stable_end = position_from_server(text, end, encoding)  # type: ignore[arg-type]
    except (OSError, UnicodeError, ValueError):
        return None
    return {
        "start": {"line": stable_start.line, "character": stable_start.character},
        "end": {"line": stable_end.line, "character": stable_end.character},
    }


def _normalize_locations(
    raw: object,
    *,
    workspace: Path,
    encoding: str,
) -> tuple[list[dict[str, object]], int]:
    values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    locations: list[dict[str, object]] = []
    omitted = 0
    for value in values:
        if not isinstance(value, dict):
            continue
        uri = value.get("uri", value.get("targetUri"))
        path = _uri_path(workspace, uri)
        if path is None:
            omitted += 1
            continue
        range_value = value.get("range", value.get("targetSelectionRange", value.get("targetRange")))
        normalized = _normalized_range(path, range_value, encoding)
        if normalized is None:
            continue
        locations.append({"path": path.relative_to(workspace).as_posix(), "range": normalized})
    locations.sort(
        key=lambda item: (
            item["path"],
            item["range"]["start"]["line"],  # type: ignore[index]
            item["range"]["start"]["character"],  # type: ignore[index]
        )
    )
    return locations, omitted


def _hover_contents(raw: object) -> object:
    contents = raw.get("contents") if isinstance(raw, dict) else None
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return contents.get("value") if isinstance(contents.get("value"), str) else contents
    if isinstance(contents, list):
        return [item.get("value", item) if isinstance(item, dict) else item for item in contents]
    return None


def _flatten_results(raw: object) -> list[object]:
    if not isinstance(raw, list):
        return [] if raw is None else [raw]
    flattened: list[object] = []
    for item in raw:
        if isinstance(item, list):
            flattened.extend(item)
        else:
            flattened.append(item)
    return flattened


def _normalize_symbols(raw: object, *, workspace: Path, encoding: str, default_path: Path | None) -> list[dict[str, object]]:
    symbols: list[dict[str, object]] = []
    for item in _flatten_results(raw):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        result: dict[str, object] = {"name": item["name"]}
        if isinstance(item.get("kind"), int):
            result["kind"] = item["kind"]
        if isinstance(item.get("containerName"), str):
            result["containerName"] = item["containerName"]
        location = item.get("location")
        if isinstance(location, dict):
            path = _uri_path(workspace, location.get("uri"))
            range_value = location.get("range")
        else:
            path = default_path
            range_value = item.get("selectionRange", item.get("range"))
        if path is not None:
            normalized = _normalized_range(path, range_value, encoding)
            result["path"] = path.relative_to(workspace).as_posix()
            if normalized is not None:
                result["range"] = normalized
        symbols.append(result)
    symbols.sort(
        key=lambda item: (
            str(item.get("name", "")).casefold(),
            str(item.get("path", "")),
            json.dumps(item.get("range"), sort_keys=True),
        )
    )
    return symbols


async def _prepared_position(manager: object, path: str, value: dict[str, object]) -> dict[str, int]:
    line = _nonnegative(value.get("line"), "line")
    character = _nonnegative(value.get("character"), "character")
    prepare = getattr(manager, "prepare_position", None)
    if callable(prepare):
        return await prepare(path, line, character)
    return {"line": line, "character": character}


async def _execute_read_action(
    manager: LanguageServiceManager,
    workspace: Path,
    action: str,
    args: dict[str, object],
    signal,
    action_store: ActionTokenStore,
) -> dict[str, object]:
    if action == "status":
        return manager.status()
    if action == "symbols" and isinstance(args.get("query"), str):
        raw = await manager.workspace_request("workspace/symbol", {"query": args["query"]}, signal=signal)
        return {"symbols": _normalize_symbols(raw, workspace=workspace, encoding="utf-16", default_path=None)}

    path_arg = _path_arg(args)
    path = _resolve_workspace_path(workspace, path_arg)
    uri = path.as_uri()
    if action == "diagnostics":
        raw = await manager.request(path, "textDocument/diagnostic", {"textDocument": {"uri": uri}}, signal=signal)
    elif action == "symbols":
        raw = await manager.request(path, "textDocument/documentSymbol", {"textDocument": {"uri": uri}}, signal=signal)
    elif action in {"hover", "definition", "references", "rename_preview"}:
        position = await _prepared_position(
            manager,
            path_arg,
            {"line": args["line"], "character": args["character"]},
        )
        method = {
            "hover": "textDocument/hover",
            "definition": "textDocument/definition",
            "references": "textDocument/references",
            "rename_preview": "textDocument/rename",
        }[action]
        params: dict[str, object] = {"textDocument": {"uri": uri}, "position": position}
        if action == "references":
            params["context"] = {"includeDeclaration": True}
        if action == "rename_preview":
            params["newName"] = args["newName"]
        raw = await manager.request(path, method, params, signal=signal)
    else:
        start = await _prepared_position(manager, path_arg, args["start"])  # type: ignore[arg-type]
        end = await _prepared_position(manager, path_arg, args["end"])  # type: ignore[arg-type]
        raw = await manager.request(
            path,
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {"start": start, "end": end},
                "context": {"diagnostics": []},
            },
            signal=signal,
        )

    context = manager.response_context(path)
    base = {
        "generation": context["generation"],
        "documentHash": context["documentHash"],
    }
    encoding = str(context["positionEncoding"])
    if action == "hover":
        return {**base, "contents": _hover_contents(raw)}
    if action in {"definition", "references"}:
        locations, omitted = _normalize_locations(raw, workspace=workspace, encoding=encoding)
        return {**base, "locations": locations, "omittedOutsideWorkspace": omitted}
    if action == "symbols":
        return {**base, "symbols": _normalize_symbols(raw, workspace=workspace, encoding=encoding, default_path=path)}
    if action == "diagnostics":
        items = raw.get("items", []) if isinstance(raw, dict) else []
        diagnostics: list[dict[str, object]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            normalized_range = _normalized_range(path, item.get("range"), encoding)
            if normalized_range is None or not isinstance(item.get("message"), str):
                continue
            diagnostic = {"range": normalized_range, "message": item["message"]}
            for key in ("severity", "source", "code"):
                if isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool):
                    diagnostic[key] = item[key]
            diagnostics.append(diagnostic)
        diagnostics.sort(key=lambda item: (item["range"]["start"]["line"], item["range"]["start"]["character"], item["message"]))  # type: ignore[index]
        return {**base, "diagnostics": diagnostics}
    if action == "rename_preview":
        return {**base, "workspaceEdit": raw}
    actions = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            continue
        token = action_store.create(
            item,
            path=path_arg,
            server_generation=int(context["generation"]),
            config_generation=int(context.get("configGeneration", 1)),
        )
        actions.append(
            {
                "title": item["title"],
                "kind": item.get("kind"),
                "hasEdit": isinstance(item.get("edit"), dict),
                "hasCommand": isinstance(item.get("command"), dict),
                "actionToken": token.token,
            }
        )
    actions.sort(key=lambda item: (str(item["title"]).casefold(), str(item.get("kind", ""))))
    return {**base, "actions": actions}


def _preview_payload(preview: WorkspaceEditPreview) -> dict[str, object]:
    return {
        "previewToken": preview.token,
        "files": [
            {
                "path": file.relative_path,
                "originalHash": file.original_hash,
                "targetHash": file.target_hash,
                "originalBytes": len(file.original_bytes),
                "targetBytes": len(file.target_bytes),
            }
            for file in preview.files
        ],
        "diff": preview.diff,
        "serverGeneration": preview.server_generation,
        "configGeneration": preview.config_generation,
    }


async def _create_mutation_preview(
    manager: LanguageServiceManager,
    preview_store: WorkspaceEditPreviewStore,
    action_store: ActionTokenStore,
    workspace: Path,
    action: str,
    args: dict[str, object],
    signal,
) -> dict[str, object]:
    if action == "rename_preview":
        read_payload = await _execute_read_action(
            manager,
            workspace,
            action,
            args,
            signal,
            action_store,
        )
        edit = read_payload.get("workspaceEdit")
        path = _resolve_workspace_path(workspace, _path_arg(args))
        context = manager.response_context(path)
    else:
        token_value = action_store.peek(str(args["actionToken"]))
        path = _resolve_workspace_path(workspace, token_value.path)
        context = manager.response_context(path)
        bound = action_store.resolve(
            token_value.token,
            server_generation=int(context["generation"]),
            config_generation=int(context.get("configGeneration", 1)),
        )
        resolved_action = bound.action
        if not isinstance(resolved_action.get("edit"), dict) and "data" in resolved_action:
            candidate = await manager.request(
                path,
                "codeAction/resolve",
                resolved_action,
                signal=signal,
            )
            if not isinstance(candidate, dict):
                raise WorkspaceEditError("selected code action did not resolve to an edit")
            resolved_action = candidate
        if isinstance(resolved_action.get("command"), dict):
            raise WorkspaceEditError("command-only and edit-plus-command code actions are unsupported")
        edit = resolved_action.get("edit")
        if not isinstance(edit, dict):
            raise WorkspaceEditError("selected code action does not contain a workspace edit")

    if not isinstance(edit, dict):
        raise WorkspaceEditError("language server did not return a workspace edit")
    preview = preview_store.create(
        edit,
        manager.documents,
        position_encoding=str(context["positionEncoding"]),  # type: ignore[arg-type]
        server_generation=int(context["generation"]),
        config_generation=int(context.get("configGeneration", 1)),
        source_path=path,
    )
    if action == "code_action_preview":
        action_store.consume(
            str(args["actionToken"]),
            server_generation=int(context["generation"]),
            config_generation=int(context.get("configGeneration", 1)),
        )
    return _preview_payload(preview)


def _bounded_result(
    payload: dict[str, object],
    *,
    artifacts: ArtifactRegistry,
    limits,
    tool_call_id: str,
) -> AgentToolResult:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    spool = OutputSpool(
        max_lines=1_000_000,
        max_bytes=limits.max_inline_output_bytes,
        temp_file_prefix="travis-lsp",
        artifact_registry=artifacts,
        artifact_kind="language-service-result",
        tool_call_id=tool_call_id,
    )
    try:
        spool.append(encoded)
        spool.finish()
        snapshot = spool.snapshot(persist_if_truncated=True)
        if not snapshot.truncation.truncated:
            return AgentToolResult(content=[TextContent(text=encoded.decode("utf-8"))], details={"truncated": False})
        envelope = {
            "truncated": True,
            "preview": snapshot.content,
            "artifactId": snapshot.artifact_id,
            "totalBytes": snapshot.truncation.total_bytes,
        }
        if snapshot.artifact_id:
            envelope["readInstruction"] = artifact_read_instruction(snapshot.artifact_id)
        return AgentToolResult(
            content=[TextContent(text=json.dumps(envelope, sort_keys=True, separators=(",", ":")))],
            details={
                "truncated": True,
                "artifactId": snapshot.artifact_id,
                "totalBytes": snapshot.truncation.total_bytes,
                "artifactUnavailable": snapshot.artifact_unavailable,
            },
        )
    finally:
        spool.close()


def create_lsp_tool_definition(
    manager: LanguageServiceManager,
    artifacts: ArtifactRegistry,
    workspace: str | Path,
) -> ToolDefinition:
    root = Path(workspace).expanduser().resolve()
    action_store = ActionTokenStore(limits=manager.limits)
    preview_store = WorkspaceEditPreviewStore(root, limits=manager.limits)

    async def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        del on_update, ctx
        action, validated = _validate_args(args)
        if action == "apply":
            preview = preview_store.get(str(validated["previewToken"]))
            current_generation = preview.server_generation
            current_config_generation = int(
                getattr(manager, "config_generation", preview.config_generation)
            )
            if preview.source_path is not None:
                context = manager.response_context(preview.source_path)
                current_generation = int(context["generation"])
                current_config_generation = int(
                    context.get("configGeneration", current_config_generation)
                )
            report = await asyncio.to_thread(
                preview_store.apply,
                str(validated["previewToken"]),
                server_generation=current_generation,
                config_generation=current_config_generation,
                signal=signal,
            )
            payload = {
                "applied": report.applied,
                "changed": list(report.changed),
                "restored": list(report.restored),
                "unresolved": list(report.unresolved),
            }
        elif action in {"rename_preview", "code_action_preview"}:
            payload = await _create_mutation_preview(
                manager,
                preview_store,
                action_store,
                root,
                action,
                validated,
                signal,
            )
        else:
            payload = await _execute_read_action(
                manager,
                root,
                action,
                validated,
                signal,
                action_store,
            )
        return _bounded_result(
            payload,
            artifacts=artifacts,
            limits=manager.limits,
            tool_call_id=str(tool_call_id),
        )

    execute.preview_store = preview_store  # type: ignore[attr-defined]
    execute.action_store = action_store  # type: ignore[attr-defined]

    return ToolDefinition(
        name="lsp",
        label="lsp",
        description=(
            "Query configured language servers with zero-based lines and UTF-16 character offsets. "
            "Language servers are user-managed and bounded by Travis234."
        ),
        parameters=LSP_SCHEMA,
        execute=execute,
        execution_mode="sequential",
        prompt_snippet="Use configured language intelligence for diagnostics, symbols, and navigation",
        prompt_guidelines=[
            "Use zero-based LSP line numbers and UTF-16 code-unit character offsets.",
        ],
        effects=frozenset({"read", "write", "execute", "network"}),
        policy_context=lambda args: {
            "action": str((args or {}).get("action", ""))[:80],
            **(
                {"target": str((args or {}).get("path", ""))[:240]}
                if (args or {}).get("path")
                else {}
            ),
        },
    )


__all__ = ["LSP_SCHEMA", "create_lsp_tool_definition"]
