from __future__ import annotations

import os
import stat
import asyncio
import json
from pathlib import Path

import pytest

from travis.agent.types import AbortSignal
from travis.coding_agent.language_services.documents import DocumentTracker
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.language_services.workspace_edit import (
    WorkspaceEditError,
    WorkspaceEditPreviewStore,
)
from travis.coding_agent.tools.atomic_file import atomic_replace_bytes
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.language_services.tool import create_lsp_tool_definition
from travis.coding_agent.policy import ToolPolicyEngine, ToolPolicySettings
from tests.test_language_service_workspace_edit import _PreviewManager


def _range(start: int, end: int) -> dict:
    return {
        "start": {"line": 0, "character": start},
        "end": {"line": 0, "character": end},
    }


def _preview(store: WorkspaceEditPreviewStore, tracker: DocumentTracker, edits: dict[Path, list[dict]]):
    return store.create(
        {"changes": {path.as_uri(): values for path, values in edits.items()}},
        tracker,
        position_encoding="utf-16",
        server_generation=1,
        config_generation=1,
    )


def test_apply_changes_multiple_files_preserves_modes_and_consumes_token(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("alpha\n", encoding="utf-8")
    second.write_text("beta\n", encoding="utf-8")
    first.chmod(0o640)
    second.chmod(0o600)
    tracker = DocumentTracker(tmp_path)
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(
        store,
        tracker,
        {
            first: [{"range": _range(0, 5), "newText": "ALPHA"}],
            second: [{"range": _range(0, 4), "newText": "BETA"}],
        },
    )

    report = store.apply(
        preview.token,
        server_generation=1,
        config_generation=1,
    )

    assert report.applied is True
    assert report.changed == ("a.py", "b.py")
    assert report.restored == ()
    assert report.unresolved == ()
    assert first.read_text(encoding="utf-8") == "ALPHA\n"
    assert second.read_text(encoding="utf-8") == "BETA\n"
    assert stat.S_IMODE(first.stat().st_mode) == 0o640
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    with pytest.raises(WorkspaceEditError, match="unknown"):
        store.apply(preview.token, server_generation=1, config_generation=1)


def test_stale_hash_fails_before_mutation_and_leaves_token_available(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("old\n", encoding="utf-8")
    tracker = DocumentTracker(tmp_path)
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(store, tracker, {source: [{"range": _range(0, 3), "newText": "new"}]})
    source.write_text("drift\n", encoding="utf-8")

    with pytest.raises(WorkspaceEditError, match="changed since preview"):
        store.apply(preview.token, server_generation=1, config_generation=1)

    assert store.get(preview.token) is preview
    assert source.read_text(encoding="utf-8") == "drift\n"


def test_apply_rejects_missing_nonregular_permission_and_size_before_writes(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("old\n", encoding="utf-8")
    tracker = DocumentTracker(tmp_path)

    missing_store = WorkspaceEditPreviewStore(tmp_path)
    missing = _preview(missing_store, tracker, {source: []})
    source.unlink()
    with pytest.raises(WorkspaceEditError, match="regular file"):
        missing_store.apply(missing.token, server_generation=1, config_generation=1)

    source.write_text("old\n", encoding="utf-8")
    permission_store = WorkspaceEditPreviewStore(tmp_path)
    permission = _preview(permission_store, tracker, {source: []})
    source.chmod(0o400)
    try:
        with pytest.raises(WorkspaceEditError, match="writable"):
            permission_store.apply(permission.token, server_generation=1, config_generation=1)
    finally:
        source.chmod(0o600)

    size_store = WorkspaceEditPreviewStore(
        tmp_path,
        limits=LanguageServiceLimits(max_apply_original_bytes=2),
    )
    oversized = _preview(size_store, tracker, {source: []})
    with pytest.raises(WorkspaceEditError, match="64|limit|original bytes"):
        size_store.apply(oversized.token, server_generation=1, config_generation=1)


def test_later_write_failure_rolls_back_prior_files_and_reports_restored(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("aaa\n", encoding="utf-8")
    second.write_text("bbb\n", encoding="utf-8")
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(
        store,
        DocumentTracker(tmp_path),
        {
            first: [{"range": _range(0, 3), "newText": "AAA"}],
            second: [{"range": _range(0, 3), "newText": "BBB"}],
        },
    )

    def fail_second(path: Path, data: bytes, mode: int) -> None:
        if path == second and data.startswith(b"BBB"):
            raise OSError("injected write failure")
        atomic_replace_bytes(path, data, mode=mode)

    report = store.apply(
        preview.token,
        server_generation=1,
        config_generation=1,
        write_bytes=fail_second,
    )

    assert report.applied is False
    assert report.changed == ("a.py",)
    assert report.restored == ("a.py",)
    assert report.unresolved == ()
    assert first.read_text(encoding="utf-8") == "aaa\n"
    assert second.read_text(encoding="utf-8") == "bbb\n"


def test_rollback_failure_is_reported_as_unresolved(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("aaa\n", encoding="utf-8")
    second.write_text("bbb\n", encoding="utf-8")
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(
        store,
        DocumentTracker(tmp_path),
        {
            first: [{"range": _range(0, 3), "newText": "AAA"}],
            second: [{"range": _range(0, 3), "newText": "BBB"}],
        },
    )
    writes = 0

    def fail_write_and_rollback(path: Path, data: bytes, mode: int) -> None:
        nonlocal writes
        writes += 1
        if writes >= 2:
            raise OSError("injected failure")
        atomic_replace_bytes(path, data, mode=mode)

    report = store.apply(
        preview.token,
        server_generation=1,
        config_generation=1,
        write_bytes=fail_write_and_rollback,
    )

    assert report.applied is False
    assert report.changed == ("a.py",)
    assert report.restored == ()
    assert report.unresolved == ("a.py",)
    assert first.read_text(encoding="utf-8") == "AAA\n"


def test_symlink_replacement_after_preview_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    outside = tmp_path.parent / "outside-apply.py"
    source.write_text("old\n", encoding="utf-8")
    outside.write_text("outside\n", encoding="utf-8")
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(store, DocumentTracker(tmp_path), {source: []})
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(WorkspaceEditError, match="containment|symlink|workspace"):
        store.apply(preview.token, server_generation=1, config_generation=1)
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_cancellation_before_lock_keeps_token_and_during_writes_rolls_back(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("aaa\n", encoding="utf-8")
    second.write_text("bbb\n", encoding="utf-8")
    store = WorkspaceEditPreviewStore(tmp_path)
    preview = _preview(
        store,
        DocumentTracker(tmp_path),
        {
            first: [{"range": _range(0, 3), "newText": "AAA"}],
            second: [{"range": _range(0, 3), "newText": "BBB"}],
        },
    )
    before = AbortSignal()
    before.abort()
    with pytest.raises(WorkspaceEditError, match="aborted"):
        store.apply(preview.token, server_generation=1, config_generation=1, signal=before)
    assert store.get(preview.token) is preview

    during = AbortSignal()

    def abort_after_first(path: Path, data: bytes, mode: int) -> None:
        atomic_replace_bytes(path, data, mode=mode)
        if path == first and data.startswith(b"AAA"):
            during.abort()

    report = store.apply(
        preview.token,
        server_generation=1,
        config_generation=1,
        signal=during,
        write_bytes=abort_after_first,
    )
    assert report.applied is False
    assert report.restored == ("a.py",)
    assert first.read_text(encoding="utf-8") == "aaa\n"
    assert second.read_text(encoding="utf-8") == "bbb\n"


def _execute_tool(definition, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(definition.execute("apply-tool", args, None, None, None))
    return json.loads(result.content[0].text)


def test_lsp_apply_action_uses_preview_token_and_rejects_replay(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("old\n", encoding="utf-8")
    manager = _PreviewManager(
        tmp_path,
        {
            "textDocument/rename": {
                "changes": {
                    source.as_uri(): [
                        {"range": _range(0, 3), "newText": "new"}
                    ]
                }
            }
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)
    preview = _execute_tool(
        definition,
        {
            "action": "rename_preview",
            "path": "main.py",
            "line": 0,
            "character": 1,
            "newName": "new",
        },
    )

    applied = _execute_tool(
        definition,
        {"action": "apply", "previewToken": preview["previewToken"]},
    )

    assert applied == {
        "applied": True,
        "changed": ["main.py"],
        "restored": [],
        "unresolved": [],
    }
    assert source.read_text(encoding="utf-8") == "new\n"
    with pytest.raises(WorkspaceEditError, match="unknown"):
        _execute_tool(
            definition,
            {"action": "apply", "previewToken": preview["previewToken"]},
        )


def test_phase1d_denial_does_not_consume_preview_or_mutate(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("old\n", encoding="utf-8")
    manager = _PreviewManager(
        tmp_path,
        {
            "textDocument/rename": {
                "changes": {
                    source.as_uri(): [
                        {"range": _range(0, 3), "newText": "new"}
                    ]
                }
            }
        },
    )
    definition = create_lsp_tool_definition(manager, ArtifactRegistry(), tmp_path)
    preview = _execute_tool(
        definition,
        {
            "action": "rename_preview",
            "path": "main.py",
            "line": 0,
            "character": 1,
            "newName": "new",
        },
    )
    engine = ToolPolicyEngine(
        ToolPolicySettings(mode="enforce", auto_allow_effects=frozenset({"read"}))
    )

    decision = asyncio.run(
        engine.authorize(
            definition,
            {"action": "apply", "previewToken": preview["previewToken"]},
        )
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_unavailable"
    preview_store = definition.execute.preview_store
    assert preview_store.get(preview["previewToken"]).token == preview["previewToken"]
    assert source.read_text(encoding="utf-8") == "old\n"
