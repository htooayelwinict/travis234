"""Append-only coding-agent session store."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from travis.agent.types import AgentMessage
from travis.ai.types import (
    AssistantMessage,
    Cost,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.coding_agent.session_lock import SessionFileLock
from travis.coding_agent.session_index import SessionIndex

CURRENT_SESSION_VERSION = 3


class SessionCorruptionError(ValueError):
    def __init__(self, path: Path, line_number: int, detail: str) -> None:
        self.path = path
        self.line_number = line_number
        self.detail = detail
        super().__init__(f"Session file {path} is corrupt at line {line_number}: {detail}")


@dataclass
class SessionContextSnapshot:
    messages: list[AgentMessage]
    thinking_level: str
    model: dict[str, str] | None
    session_name: str | None


@dataclass
class BranchSummaryMessage:
    summary: str
    from_id: str
    timestamp: int
    role: str = "branchSummary"



@dataclass
class CompactionSummaryMessage:
    summary: str
    tokens_before: int
    timestamp: int
    details: Any | None = None
    role: str = "compactionSummary"



@dataclass
class BashExecutionMessage:
    command: str
    output: str
    exit_code: int | None
    cancelled: bool
    truncated: bool
    full_output_path: str | None
    timestamp: int
    exclude_from_context: bool | None = None
    role: str = "bashExecution"





@dataclass
class CustomMessage:
    custom_type: str
    content: str | list[TextContent | ImageContent]
    display: bool
    details: Any | None
    timestamp: int
    role: str = "custom"



class SessionStore:
    """Small JSONL session manager matching the established typed entry shape."""

    def __init__(
        self,
        path: str,
        *,
        cwd: str,
        parent_session: str | None = None,
        session_id: str | None = None,
        index: SessionIndex | None = None,
    ) -> None:
        self.path = Path(path)
        self.index = index
        self.index_diagnostics: list[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file_entries: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.leaf_id: str | None = None
        self._disk_leaf_id: str | None = None
        self._disk_offset = 0
        self._disk_identity: tuple[int, int] | None = None
        self._explicit_parent_selection = False
        self._thread_lock = threading.RLock()
        self.recovered_tail_path: Path | None = None
        with self._thread_lock, SessionFileLock(self.path):
            if self.path.exists() and self.path.stat().st_size > 0:
                self._load()
            else:
                self._write_header(cwd=cwd, parent_session=parent_session, session_id=session_id)

    def _write_header(self, *, cwd: str, parent_session: str | None, session_id: str | None = None) -> None:
        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": session_id or uuid.uuid4().hex,
            "timestamp": _timestamp(),
            "cwd": cwd,
        }
        if parent_session:
            header["parentSession"] = parent_session
        payload = (json.dumps(header, separators=(",", ":")) + "\n").encode("utf-8")
        _atomic_write(self.path, payload)
        self.file_entries = [header]
        self.by_id = {}
        self.leaf_id = None
        self._disk_leaf_id = None
        self._disk_offset = len(payload)
        self._disk_identity = _disk_signature(self.path)
        self._explicit_parent_selection = False
        if self.index is not None:
            try:
                self.index.record_header(self.path, header, self.path.stat())
            except Exception as error:  # noqa: BLE001 - JSONL is authoritative; reconciliation repairs the cache.
                self.index_diagnostics.append(str(error))

    def _load(self) -> None:
        raw = self._read_range(0)
        self._rebuild_from_bytes(raw)
        stat = self.path.stat()
        self._disk_offset = stat.st_size
        self._disk_identity = (stat.st_dev, stat.st_ino)
        self._explicit_parent_selection = False

    def _rebuild_from_bytes(self, raw: bytes) -> None:
        self.file_entries = []
        self.by_id = {}
        self.leaf_id = None
        self.recovered_tail_path = None
        lines = raw.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                is_incomplete_tail = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
                if is_incomplete_tail:
                    self.recovered_tail_path = self._recover_truncated_tail(
                        valid_prefix=b"".join(lines[:index]),
                        tail=raw_line,
                    )
                    break
                raise SessionCorruptionError(self.path, index + 1, str(error)) from error
            self._apply_loaded_entry(entry)
        self._disk_leaf_id = self.leaf_id

    def _read_range(self, start: int) -> bytes:
        with self.path.open("rb") as handle:
            handle.seek(start)
            return handle.read()

    def _sync_from_disk(self) -> None:
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self._disk_identity != identity or stat.st_size < self._disk_offset:
            self._load()
            return
        if stat.st_size == self._disk_offset:
            return
        suffix = self._read_range(self._disk_offset)
        if not suffix.endswith((b"\n", b"\r")):
            self._load()
            return
        base_line = len(self.file_entries)
        for line_number, raw_line in enumerate(suffix.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SessionCorruptionError(
                    self.path,
                    base_line + line_number,
                    str(error),
                ) from error
            self._apply_loaded_entry(entry)
        self._disk_offset = stat.st_size
        self._disk_identity = identity
        self._disk_leaf_id = self.leaf_id

    def _apply_loaded_entry(self, entry: dict[str, Any]) -> None:
        self.file_entries.append(entry)
        entry_id = entry.get("id")
        if entry.get("type") != "session" and entry_id:
            self.by_id[entry_id] = entry
            self.leaf_id = entry_id

    def _recover_truncated_tail(self, *, valid_prefix: bytes, tail: bytes) -> Path:
        quarantine = self.path.with_name(f"{self.path.name}.truncated-{uuid.uuid4().hex}.partial")
        quarantine_fd = os.open(quarantine, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(quarantine_fd, "wb") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            quarantine.unlink(missing_ok=True)
            raise

        temp_fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(temp_fd, self.path.stat().st_mode & 0o777)
            with os.fdopen(temp_fd, "wb") as handle:
                handle.write(valid_prefix)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.close(temp_fd)
            except OSError:
                pass
            Path(temp_name).unlink(missing_ok=True)
            raise
        return quarantine

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [entry for entry in self.file_entries if entry.get("type") != "session"]

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        return self.by_id.get(entry_id)

    def get_leaf_id(self) -> str | None:
        return self.leaf_id


    def get_children(self, parent_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.by_id.values() if entry.get("parentId") == parent_id]


    def get_label(self, entry_id: str) -> str | None:
        label: str | None = None
        for entry in self.file_entries:
            if entry.get("type") == "label" and entry.get("targetId") == entry_id:
                value = entry.get("label")
                label = value if value else None
        return label

    def session_tree(self) -> list[dict[str, Any]]:
        """Return the complete session tree in stable depth-first order."""

        entries = self.entries
        known_ids = {
            str(entry["id"])
            for entry in entries
            if isinstance(entry.get("id"), str) and entry.get("id")
        }
        children: dict[str | None, list[dict[str, Any]]] = {}
        for entry in entries:
            entry_id = entry.get("id")
            if not isinstance(entry_id, str) or not entry_id:
                continue
            parent_id = entry.get("parentId")
            if not isinstance(parent_id, str) or parent_id not in known_ids:
                parent_id = None
            children.setdefault(parent_id, []).append(entry)

        active_ids = {str(entry["id"]) for entry in self.get_branch()}
        nodes: list[dict[str, Any]] = []
        visited: set[str] = set()

        def visit(entry: dict[str, Any], depth: int) -> None:
            entry_id = str(entry["id"])
            if entry_id in visited:
                return
            visited.add(entry_id)
            nodes.append(
                {
                    "id": entry_id,
                    "parentId": entry.get("parentId"),
                    "type": str(entry.get("type") or "unknown"),
                    "depth": depth,
                    "active": entry_id == self.leaf_id,
                    "inActiveBranch": entry_id in active_ids,
                    "label": self.get_label(entry_id),
                    "summary": _tree_entry_summary(entry),
                    "entry": entry,
                }
            )
            for child in children.get(entry_id, []):
                visit(child, depth + 1)

        for root in children.get(None, []):
            visit(root, 0)
        for entry in entries:
            entry_id = entry.get("id")
            if isinstance(entry_id, str) and entry_id not in visited:
                visit(entry, 0)
        return nodes


    def append_message(self, message: AgentMessage) -> str:
        return self._append_entry({"type": "message", "message": serialize_message(message)}, durable=True)

    def append_thinking_level_change(self, thinking_level: str) -> str:
        return self._append_entry({"type": "thinking_level_change", "thinkingLevel": thinking_level}, durable=True)

    def append_model_change(self, provider: str, model_id: str) -> str:
        return self._append_entry({"type": "model_change", "provider": provider, "modelId": model_id}, durable=True)

    def append_session_info(self, name: str | None) -> str:
        return self._append_entry({"type": "session_info", "name": (name or "").strip()}, durable=True)

    def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details=None,
        *,
        parent_id: str | None = None,
    ) -> str:
        entry = {
            "type": "compaction",
            "summary": summary,
            "firstKeptEntryId": first_kept_entry_id,
            "tokensBefore": tokens_before,
        }
        if details is not None:
            entry["details"] = details
        previous_leaf = self.leaf_id
        previous_explicit = self._explicit_parent_selection
        if parent_id is not None:
            if parent_id not in self.by_id:
                raise ValueError(f"Entry {parent_id} not found")
            self.leaf_id = parent_id
            self._explicit_parent_selection = True
        try:
            return self._append_entry(entry, durable=True)
        except Exception:
            self.leaf_id = previous_leaf
            self._explicit_parent_selection = previous_explicit
            raise

    def append_custom_entry(self, custom_type: str, data: Any | None = None) -> str:
        entry = {"type": "custom", "customType": custom_type}
        if data is not None:
            entry["data"] = data
        return self._append_entry(entry, durable=True)


    def append_custom_message_entry(
        self,
        custom_type: str,
        content: str | list[TextContent | ImageContent],
        display: bool,
        details: Any | None = None,
    ) -> str:
        entry = {
            "type": "custom_message",
            "customType": custom_type,
            "content": _serialize_content(content),
            "display": bool(display),
        }
        if details is not None:
            entry["details"] = details
        return self._append_entry(entry, durable=True)


    def append_label_change(self, target_id: str, label: str | None) -> str:
        if target_id not in self.by_id:
            raise ValueError(f"Entry {target_id} not found")
        return self._append_entry({"type": "label", "targetId": target_id, "label": label or None}, durable=True)


    def branch(self, entry_id: str) -> None:
        if entry_id not in self.by_id:
            raise ValueError(f"Invalid entry ID for branching: {entry_id}")
        self.leaf_id = entry_id
        self._explicit_parent_selection = True

    def reset_leaf(self) -> None:
        self.leaf_id = None
        self._explicit_parent_selection = True


    def branch_with_summary(
        self,
        branch_from_id: str | None,
        summary: str,
        details: Any | None = None,
        from_hook: bool | None = None,
    ) -> str:
        if branch_from_id is not None and branch_from_id not in self.by_id:
            raise ValueError(f"Entry {branch_from_id} not found")
        self.leaf_id = branch_from_id
        self._explicit_parent_selection = True
        entry = {
            "type": "branch_summary",
            "fromId": branch_from_id or "root",
            "summary": summary,
        }
        if details is not None:
            entry["details"] = details
        if from_hook is not None:
            entry["fromHook"] = from_hook
        return self._append_entry(entry, durable=True)


    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str:
        branch_entries = self.get_branch(leaf_id)
        if not branch_entries:
            raise ValueError(f"Entry {leaf_id} not found")

        target_path = Path(path) if path else self.path.parent / f"session-{uuid.uuid4().hex}.jsonl"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": uuid.uuid4().hex,
            "timestamp": _timestamp(),
            "cwd": self.header.get("cwd", ""),
            "parentSession": str(self.path),
        }

        copied_entries: list[dict[str, Any]] = []
        parent_id: str | None = None
        for entry in branch_entries:
            if entry.get("type") == "label":
                continue
            copied = json.loads(json.dumps(entry))
            copied["parentId"] = parent_id
            copied_entries.append(copied)
            parent_id = copied["id"]

        resolved_labels: dict[str, dict[str, Any]] = {}
        for entry in self.file_entries:
            if entry.get("type") != "label" or not isinstance(entry.get("targetId"), str):
                continue
            target_id = str(entry["targetId"])
            if entry.get("label"):
                resolved_labels[target_id] = entry
            else:
                resolved_labels.pop(target_id, None)

        retained_ids = {str(entry["id"]) for entry in copied_entries}
        for entry in list(copied_entries):
            target_id = str(entry["id"])
            source_label = resolved_labels.get(target_id)
            if source_label is None:
                continue
            label_id = uuid.uuid4().hex
            while label_id in retained_ids:
                label_id = uuid.uuid4().hex
            retained_ids.add(label_id)
            copied_entries.append(
                {
                    "type": "label",
                    "id": label_id,
                    "parentId": parent_id,
                    "timestamp": source_label.get("timestamp") or _timestamp(),
                    "targetId": target_id,
                    "label": source_label.get("label"),
                }
            )
            parent_id = label_id

        payload = "".join(
            json.dumps(entry, separators=(",", ":")) + "\n" for entry in [header, *copied_entries]
        ).encode("utf-8")
        _atomic_write(target_path, payload)

        return str(target_path)

    def export_to_jsonl(self, output_path: str | None = None) -> str:
        if output_path is None:
            file_name = f"session-{_timestamp().replace(':', '-').replace('.', '-')}.jsonl"
            target_path = Path.cwd() / file_name
        else:
            target_path = Path(output_path).expanduser()
            if not target_path.is_absolute():
                target_path = Path.cwd() / target_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": self.header.get("id", ""),
            "timestamp": _timestamp(),
            "cwd": self.header.get("cwd", ""),
        }
        branch_entries = self.get_branch()
        lines = [json.dumps(header, separators=(",", ":"))]
        previous_id: str | None = None
        for entry in branch_entries:
            linear = json.loads(json.dumps(entry))
            linear["parentId"] = previous_id
            lines.append(json.dumps(linear, separators=(",", ":")))
            previous_id = entry.get("id")

        _atomic_write(target_path, ("\n".join(lines) + "\n").encode("utf-8"))
        return str(target_path)


    @property
    def header(self) -> dict[str, Any]:
        for entry in self.file_entries:
            if entry.get("type") == "session":
                return entry
        return {}

    def get_branch(self, from_id: str | None = None) -> list[dict[str, Any]]:
        path: list[dict[str, Any]] = []
        current_id = from_id or self.leaf_id
        current = self.by_id.get(current_id) if current_id else None
        while current:
            path.insert(0, current)
            parent_id = current.get("parentId")
            current = self.by_id.get(parent_id) if parent_id else None
        return path

    def build_context(self, *, default_thinking_level: str = "off") -> SessionContextSnapshot:
        branch = self.get_branch()
        messages: list[AgentMessage] = []
        thinking_level = default_thinking_level
        model: dict[str, str] | None = None
        session_name: str | None = None
        compaction_entry: dict[str, Any] | None = None

        for entry in branch:
            entry_type = entry.get("type")
            if entry_type == "thinking_level_change":
                thinking_level = entry.get("thinkingLevel", thinking_level)
            elif entry_type == "model_change":
                model = {"provider": entry.get("provider", ""), "modelId": entry.get("modelId", "")}
            elif entry_type == "session_info":
                session_name = (entry.get("name") or "").strip() or None
            elif entry_type == "compaction" and entry.get("summary"):
                compaction_entry = entry

        if compaction_entry:
            messages.append(_entry_to_message(compaction_entry))
            compaction_index = branch.index(compaction_entry)
            first_kept_id = compaction_entry.get("firstKeptEntryId")
            found_first_kept = first_kept_id is None
            for entry in branch[:compaction_index]:
                if entry.get("id") == first_kept_id:
                    found_first_kept = True
                if found_first_kept:
                    message = _entry_to_message(entry)
                    if message is not None:
                        messages.append(message)
            for entry in branch[compaction_index + 1 :]:
                message = _entry_to_message(entry)
                if message is not None:
                    messages.append(message)
        else:
            for entry in branch:
                message = _entry_to_message(entry)
                if message is not None:
                    messages.append(message)

        return SessionContextSnapshot(
            messages=messages,
            thinking_level=thinking_level,
            model=model,
            session_name=session_name,
        )

    def append_checkpoint(self, entry: dict[str, Any]) -> str:
        return self._append_entry(entry, durable=True)

    def _append_entry(self, entry: dict[str, Any], durable: bool = False) -> str:
        with self._thread_lock, SessionFileLock(self.path):
            selected_parent = self.leaf_id
            follows_disk_leaf = (
                not self._explicit_parent_selection
                and selected_parent == self._disk_leaf_id
            )
            self._sync_from_disk()
            parent_id = self.leaf_id if follows_disk_leaf else selected_parent
            committed = {
                **entry,
                "id": self._generate_id(),
                "parentId": parent_id,
                "timestamp": _timestamp(),
            }
            payload = _record_payload(committed)
            self._write_record(committed, durable=durable, payload=payload)
            self._apply_committed_entry(committed)
            self._disk_offset += len(payload)
            self._disk_identity = _disk_signature(self.path)
            self._explicit_parent_selection = False
            return committed["id"]

    def _write_record(
        self,
        entry: dict[str, Any],
        *,
        durable: bool,
        payload: bytes | None = None,
    ) -> None:
        payload = payload if payload is not None else _record_payload(entry)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("session append made no progress")
                view = view[written:]
            if durable:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if self.index is not None:
            try:
                self.index.record_append(self.path, entry, self.path.stat())
            except Exception as error:  # noqa: BLE001 - JSONL append remains committed if indexing fails.
                self.index_diagnostics.append(str(error))

    def _apply_committed_entry(self, entry: dict[str, Any]) -> None:
        self.file_entries.append(entry)
        self.by_id[entry["id"]] = entry
        self.leaf_id = entry["id"]
        self._disk_leaf_id = entry["id"]

    def _generate_id(self) -> str:
        while True:
            entry_id = uuid.uuid4().hex[:8]
            if entry_id not in self.by_id:
                return entry_id


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _record_payload(entry: dict[str, Any]) -> bytes:
    return (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")


def _tree_entry_summary(entry: dict[str, Any], limit: int = 120) -> str:
    entry_type = str(entry.get("type") or "unknown")
    if entry_type == "message":
        message = entry.get("message")
        if isinstance(message, dict):
            role = str(message.get("role") or "message")
            text = serialized_content_text(message.get("content"))
            return _bounded_tree_text(f"{role}: {text}" if text else role, limit)
    if entry_type == "custom_message":
        custom_type = str(entry.get("customType") or "custom")
        text = serialized_content_text(entry.get("content"))
        return _bounded_tree_text(f"{custom_type}: {text}" if text else custom_type, limit)
    if entry_type == "model_change":
        return _bounded_tree_text(
            f"model: {entry.get('provider', '')}/{entry.get('modelId', '')}",
            limit,
        )
    if entry_type == "thinking_level_change":
        return _bounded_tree_text(f"thinking: {entry.get('thinkingLevel', '')}", limit)
    if entry_type == "session_info":
        return _bounded_tree_text(f"session: {entry.get('name') or '(unnamed)'}", limit)
    if entry_type == "label":
        return _bounded_tree_text(f"label: {entry.get('label') or '(cleared)'}", limit)
    if entry_type in {"compaction", "branch_summary"}:
        return _bounded_tree_text(f"{entry_type}: {entry.get('summary') or ''}", limit)
    if entry_type == "custom":
        return _bounded_tree_text(f"custom: {entry.get('customType') or ''}", limit)
    return _bounded_tree_text(entry_type, limit)


def serialized_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _bounded_tree_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _disk_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino)


def _entry_to_message(entry: dict[str, Any]) -> AgentMessage | None:
    entry_type = entry.get("type")
    if entry_type == "message":
        return deserialize_message(entry["message"])
    if entry_type == "custom_message":
        return CustomMessage(
            custom_type=entry.get("customType", ""),
            content=_deserialize_content(entry.get("content")),
            display=bool(entry.get("display", True)),
            details=entry.get("details"),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
        )
    if entry_type == "branch_summary" and entry.get("summary"):
        return BranchSummaryMessage(
            summary=entry["summary"],
            from_id=entry.get("fromId", "root"),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
        )
    if entry_type == "compaction" and entry.get("summary"):
        return CompactionSummaryMessage(
            summary=entry["summary"],
            tokens_before=int(entry.get("tokensBefore", 0) or 0),
            timestamp=_timestamp_to_ms(entry.get("timestamp")),
            details=entry.get("details"),
        )
    return None


def serialize_message(message: AgentMessage) -> dict[str, Any]:
    role = getattr(message, "role", None)
    if role == "bashExecution":
        return {
            "role": "bashExecution",
            "command": message.command,
            "output": message.output,
            "exitCode": message.exit_code,
            "cancelled": message.cancelled,
            "truncated": message.truncated,
            "fullOutputPath": message.full_output_path,
            "timestamp": message.timestamp,
            "excludeFromContext": message.exclude_from_context,
        }
    if role == "user":
        return {
            "role": "user",
            "content": _serialize_content(message.content),
            "timestamp": message.timestamp,
        }
    if role == "assistant":
        return {
            "role": "assistant",
            "content": [_serialize_block(block) for block in message.content],
            "api": message.api,
            "provider": message.provider,
            "model": message.model,
            "usage": _serialize_usage(message.usage),
            "stopReason": message.stop_reason,
            "responseModel": message.response_model,
            "responseId": message.response_id,
            "diagnostics": message.diagnostics,
            "errorMessage": message.error_message,
            "timestamp": message.timestamp,
        }
    if role == "toolResult":
        return {
            "role": "toolResult",
            "toolCallId": message.tool_call_id,
            "toolName": message.tool_name,
            "content": [_serialize_block(block) for block in message.content],
            "isError": message.is_error,
            "details": message.details,
            "addedToolNames": message.added_tool_names,
            "timestamp": message.timestamp,
        }
    raise TypeError(f"Unsupported session message role: {role}")


def deserialize_message(data: dict[str, Any]) -> AgentMessage:
    role = data.get("role")
    if role == "bashExecution":
        return BashExecutionMessage(
            command=data.get("command", ""),
            output=data.get("output", ""),
            exit_code=data.get("exitCode"),
            cancelled=bool(data.get("cancelled", False)),
            truncated=bool(data.get("truncated", False)),
            full_output_path=data.get("fullOutputPath"),
            timestamp=data.get("timestamp", now_ms()),
            exclude_from_context=data.get("excludeFromContext"),
        )
    if role == "user":
        return UserMessage(content=_deserialize_content(data.get("content")), timestamp=data.get("timestamp", now_ms()))
    if role == "assistant":
        return AssistantMessage(
            content=[_deserialize_block(block) for block in data.get("content", [])],
            api=data.get("api", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            usage=_deserialize_usage(data.get("usage")),
            stop_reason=data.get("stopReason", data.get("stop_reason", "stop")),
            response_model=data.get("responseModel"),
            response_id=data.get("responseId"),
            diagnostics=data.get("diagnostics"),
            error_message=data.get("errorMessage"),
            timestamp=data.get("timestamp", now_ms()),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=data.get("toolCallId", ""),
            tool_name=data.get("toolName", ""),
            content=[_deserialize_block(block) for block in data.get("content", [])],
            is_error=bool(data.get("isError", False)),
            details=data.get("details"),
            added_tool_names=data.get("addedToolNames"),
            timestamp=data.get("timestamp", now_ms()),
        )
    raise TypeError(f"Unsupported session message role: {role}")


def _serialize_content(content) -> Any:
    if isinstance(content, str):
        return content
    return [_serialize_block(block) for block in content]


def _deserialize_content(content) -> str | list[TextContent | ImageContent]:
    if isinstance(content, str):
        return content
    return [_deserialize_block(block) for block in content or []]


def _serialize_block(block) -> dict[str, Any]:
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text, "textSignature": block.text_signature}
    if isinstance(block, ImageContent):
        return {"type": "image", "data": block.data, "mimeType": block.mime_type}
    if isinstance(block, ThinkingContent):
        return {
            "type": "thinking",
            "thinking": block.thinking,
            "thinkingSignature": block.thinking_signature,
            "redacted": block.redacted,
        }
    if isinstance(block, ToolCall):
        return {
            "type": "toolCall",
            "id": block.id,
            "name": block.name,
            "arguments": block.arguments,
            "thoughtSignature": block.thought_signature,
        }
    raise TypeError(f"Unsupported content block: {type(block).__name__}")


def _deserialize_block(data: dict[str, Any]):
    block_type = data.get("type")
    if block_type == "text":
        return TextContent(text=data.get("text", ""), text_signature=data.get("textSignature"))
    if block_type == "image":
        return ImageContent(data=data.get("data", ""), mime_type=data.get("mimeType", data.get("mime_type", "")))
    if block_type == "thinking":
        return ThinkingContent(
            thinking=data.get("thinking", ""),
            thinking_signature=data.get("thinkingSignature"),
            redacted=bool(data.get("redacted", False)),
        )
    if block_type == "toolCall":
        return ToolCall(
            id=data.get("id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
            thought_signature=data.get("thoughtSignature"),
        )
    raise TypeError(f"Unsupported content block type: {block_type}")


def _serialize_usage(usage: Usage) -> dict[str, Any]:
    return {
        "input": usage.input,
        "output": usage.output,
        "cacheRead": usage.cache_read,
        "cacheWrite": usage.cache_write,
        "cacheWrite1h": usage.cache_write_1h,
        "totalTokens": usage.total_tokens,
        "cost": {
            "input": usage.cost.input,
            "output": usage.cost.output,
            "cacheRead": usage.cost.cache_read,
            "cacheWrite": usage.cost.cache_write,
            "total": usage.cost.total,
        },
    }


def _deserialize_usage(data: dict[str, Any] | None) -> Usage:
    if not data:
        return empty_usage()
    cost_data = data.get("cost") or {}
    return Usage(
        input=data.get("input", 0),
        output=data.get("output", 0),
        cache_read=data.get("cacheRead", data.get("cache_read", 0)),
        cache_write=data.get("cacheWrite", data.get("cache_write", 0)),
        cache_write_1h=data.get("cacheWrite1h", data.get("cache_write_1h", 0)),
        total_tokens=data.get("totalTokens", data.get("total_tokens", 0)),
        cost=Cost(
            input=cost_data.get("input", 0.0),
            output=cost_data.get("output", 0.0),
            cache_read=cost_data.get("cacheRead", cost_data.get("cache_read", 0.0)),
            cache_write=cost_data.get("cacheWrite", cost_data.get("cache_write", 0.0)),
            total=cost_data.get("total", 0.0),
        ),
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_to_ms(value: str | None) -> int:
    if not value:
        return now_ms()
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return now_ms()
