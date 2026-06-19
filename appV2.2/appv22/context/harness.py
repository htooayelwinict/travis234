from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from appv22.context.budget import estimate_chars
from appv22.context.compressor import _compact_world_ref_payload
from appv22.context.freshness import fresh_world_refs, stale_world_ref_ids
from appv22.context.summary_hygiene import normalized_context_summary
from appv22.context.summary_hygiene import strip_turn_local_operational_progress
from appv22.state.models import AgentState


@dataclass(frozen=True)
class ModelContextPacket:
    provider_prompt: dict[str, Any]
    usage: dict[str, Any]
    context_summary_update: dict[str, list[Any]] | None = None


@dataclass(frozen=True)
class CompactionResult:
    compacted: bool
    before_chars: int
    after_chars: int
    reason: str


class ContextHarness:
    """Hermes-style context lanes for the Pi-style runtime loop.

    Runtime owns the loop. This harness owns context preparation:
    selected prompt context, model-visible live world refs, compacted reference
    summary, and context usage accounting.
    """

    def __init__(self, *, context_selector, prompt_builder, compressor, gateway_guard, tool_registry) -> None:
        self.context_selector = context_selector
        self.prompt_builder = prompt_builder
        self.compressor = compressor
        self.gateway_guard = gateway_guard
        self.tool_registry = tool_registry

    def prepare_turn(self, state: AgentState, resolved, *, pre_turn_mode: str | None = None) -> ModelContextPacket:
        self.compact_if_needed(state)
        selected = self.context_selector.select(state, resolved, pre_turn_mode=pre_turn_mode or state.mode)
        prompt = self.prompt_builder.build(state, selected)
        prompt.setdefault("world", {})
        selected_tool_ids = set(prompt.get("selection", {}).get("selected_tools", []))
        prompt["world"]["world_refs"] = self._compact_world_refs_for_prompt(
            {
                ref_id: ref
                for ref_id, ref in fresh_world_refs(state, state.world_refs, definition_for=self._tool_definition).items()
                if ref.get("kind") in selected_tool_ids and self._world_ref_visible_for_prompt(state, ref)
            }
        )
        prompt["tool_definitions"] = self._selected_tool_definitions(prompt)

        messages = self._provider_prompt_messages(state, prompt)
        compressed = self.compressor.compress(messages, previous_summary=state.context_summary)
        summary_update = self._summary_from_messages(compressed)
        compressed = self.gateway_guard.guard(compressed)

        provider_prompt = self._prompt_from_governed_messages(compressed)
        if compressed != messages or any(
            message.get("name") in {"context_summary", "context_guard_compaction"} for message in compressed
        ):
            provider_prompt["messages"] = deepcopy(compressed)
        provider_prompt.setdefault("state", {})
        provider_prompt["state"]["latest_tool_results"] = self._latest_tool_results_for_prompt(state, selected_tool_ids)
        provider_prompt["state"]["action_refs"] = self._action_refs_for_prompt(state)
        provider_prompt["state"]["context_summary"] = self._fresh_context_summary_for_prompt(
            state,
            self._merge_context_summaries(
                state.context_summary,
                provider_prompt.get("state", {}).get("context_summary", {}),
            ),
            selected_tool_ids=selected_tool_ids,
        )
        if not provider_prompt.get("skills"):
            provider_prompt["skills"] = deepcopy(prompt.get("skills", []))
        if not provider_prompt.get("tools"):
            provider_prompt["tools"] = list(prompt.get("tools", [])) if isinstance(prompt.get("tools"), list) else []
        if not provider_prompt.get("tool_definitions"):
            provider_prompt["tool_definitions"] = deepcopy(prompt.get("tool_definitions", []))
        if not provider_prompt["selection"].get("selected_tools"):
            provider_prompt["selection"] = deepcopy(prompt.get("selection", provider_prompt["selection"]))
        if not provider_prompt["world"].get("world_refs"):
            provider_prompt["world"]["world_refs"] = deepcopy(prompt["world"]["world_refs"])
        state.context_metrics.append(
            self._context_metric(state, provider_prompt, raw_messages=messages, compressed_messages=compressed)
        )
        return ModelContextPacket(
            provider_prompt=provider_prompt,
            usage=self.usage_snapshot(state),
            context_summary_update=summary_update,
        )

    def record_tool_result(self, state: AgentState, result: dict[str, Any]) -> None:
        tool_id = str(result.get("tool_id") or "")
        definition = self._tool_definition(tool_id)
        if result.get("status") == "completed" and definition is not None and definition.category == "act":
            state.mutation_seq += 1

    def compact_if_needed(self, state: AgentState) -> CompactionResult:
        """Compact the durable memory lane without touching the evidence ledger.

        Prompt-sized context is handled by AgentContextCompressor and GatewayContextGuard.
        This method protects the stored compacted-memory lane so old UI/runtime summaries
        cannot grow without bound across turns.
        """

        before_chars = estimate_chars(state.context_summary)
        budget = max(4000, int(getattr(self.compressor, "max_chars", 120_000) * 0.08))
        if before_chars <= budget:
            return CompactionResult(False, before_chars, before_chars, "within_budget")

        compacted = self._bounded_memory_summary(
            self._fresh_context_summary_for_prompt(state, state.context_summary),
        )
        state.context_summary = compacted
        after_chars = estimate_chars(state.context_summary)
        return CompactionResult(True, before_chars, after_chars, "memory_lane_budget")

    def usage_snapshot(self, state: AgentState) -> dict[str, Any]:
        calls = [deepcopy(metric) for metric in state.context_metrics]
        return {
            "context": {
                "model_calls": len(calls),
                "context_window_chars": max((int(metric.get("context_window_chars") or 0) for metric in calls), default=0),
                "context_window_estimated_tokens": max(
                    (int(metric.get("context_window_estimated_tokens") or 0) for metric in calls),
                    default=0,
                ),
                "total_prompt_chars": sum(int(metric.get("prompt_chars") or 0) for metric in calls),
                "total_prompt_estimated_tokens": sum(int(metric.get("estimated_prompt_tokens") or 0) for metric in calls),
                "max_prompt_chars": max((int(metric.get("prompt_chars") or 0) for metric in calls), default=0),
                "max_prompt_estimated_tokens": max((int(metric.get("estimated_prompt_tokens") or 0) for metric in calls), default=0),
                "max_context_utilization": max((float(metric.get("context_utilization") or 0.0) for metric in calls), default=0.0),
                "model_call_contexts": calls,
            }
        }

    def _tool_definition(self, tool_id: str):
        definition = getattr(self.tool_registry, "definition", None)
        if not callable(definition):
            return None
        return definition(tool_id)

    def _selected_tool_definitions(self, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        selected_tools = prompt.get("selection", {}).get("selected_tools", [])
        definitions: list[dict[str, Any]] = []
        for tool_id in selected_tools if isinstance(selected_tools, list) else []:
            if not isinstance(tool_id, str):
                continue
            definition = self._tool_definition(tool_id)
            if definition is None:
                continue
            definitions.append(
                {
                    "tool_id": definition.tool_id,
                    "category": definition.category,
                    "risk_level": definition.risk_level,
                    "argument_schema": self._mutable_json_like(definition.argument_schema),
                    "result_schema": self._mutable_json_like(definition.result_schema),
                    "trust": definition.trust,
                    "guidance": definition.guidance,
                    "freshness": definition.freshness,
                    "invalidated_by_mutation": definition.invalidated_by_mutation,
                }
            )
        return definitions

    def _provider_prompt_messages(self, state: AgentState, prompt: dict[str, Any]) -> list[dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "name": "provider_identity",
                "content": prompt.get("system", {}).get("identity", "AppV2.2 provider context"),
                "payload": deepcopy(prompt.get("system", {})),
            }
        ]
        for section in ("agent", "state", "skills", "tools", "tool_definitions", "world", "selection"):
            payload = deepcopy(prompt.get(section, {} if section not in {"skills", "tools", "tool_definitions"} else []))
            messages.append(
                {
                    "role": "system",
                    "name": "provider_context_section",
                    "section": section,
                    "content": f"{section}: {json.dumps(payload, sort_keys=True, default=str)}",
                    "payload": payload,
                }
            )
        messages.append({"role": "user", "name": "active_user_request", "content": state.request.active_user_request or state.request.user_goal})
        return messages

    @staticmethod
    def _prompt_from_governed_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
        provider_prompt: dict[str, Any] = {
            "system": {},
            "agent": {},
            "state": {"mode": None, "context_summary": {}, "latest_tool_results": [], "action_refs": []},
            "skills": [],
            "tools": [],
            "tool_definitions": [],
            "world": {"world_refs": {}},
            "selection": {"selected_tools": [], "selected_skills": [], "active_extensions": [], "available_tools": []},
        }
        for message in messages:
            if message.get("name") == "provider_identity" and isinstance(message.get("payload"), dict):
                provider_prompt["system"] = deepcopy(message["payload"])
                continue
            if message.get("name") != "provider_context_section":
                continue
            section = message.get("section")
            payload = message.get("payload")
            if section in provider_prompt and isinstance(payload, dict | list):
                provider_prompt[section] = deepcopy(payload)
        return provider_prompt

    @staticmethod
    def _compact_world_refs_for_prompt(world_refs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        compacted: dict[str, dict[str, Any]] = {}
        for ref_id, ref in world_refs.items():
            if not isinstance(ref_id, str) or not isinstance(ref, dict):
                continue
            compacted[ref_id] = {
                "ref_id": ref.get("ref_id", ref_id),
                "kind": ref.get("kind"),
                "arguments": deepcopy(ref.get("arguments", {})) if isinstance(ref.get("arguments"), dict) else {},
                "summary": str(ref.get("summary", ""))[:240],
                "freshness": ref.get("freshness", "stable"),
            }
            payload = ref.get("payload")
            if isinstance(payload, dict):
                compacted[ref_id]["payload"] = _compact_world_ref_payload(payload)
        return compacted

    def _fresh_context_summary_for_prompt(
        self,
        state: AgentState,
        summary: Any,
        *,
        selected_tool_ids: set[str] | None = None,
    ) -> dict[str, list[Any]]:
        normalized = normalized_context_summary(summary)
        stale_refs = stale_world_ref_ids(state, state.world_refs, definition_for=self._tool_definition)
        stale_kinds = {
            str(state.world_refs[ref_id].get("kind"))
            for ref_id in stale_refs
            if isinstance(state.world_refs.get(ref_id), dict) and state.world_refs[ref_id].get("kind")
        }
        selected = set(selected_tool_ids or ())
        hidden_refs = set(stale_refs)
        hidden_kinds = set(stale_kinds)
        if selected_tool_ids is not None:
            for ref_id, ref in state.world_refs.items():
                if not isinstance(ref_id, str) or not isinstance(ref, dict):
                    continue
                kind = ref.get("kind")
                if isinstance(kind, str) and (
                    kind not in selected or not self._world_ref_visible_for_prompt(state, ref)
                ):
                    hidden_refs.add(ref_id)
                    hidden_kinds.add(kind)
        normalized = strip_turn_local_operational_progress(normalized)
        if not hidden_refs and not hidden_kinds:
            normalized["open_risks"] = list(normalized.get("blockers", []))
            return normalized
        normalized["evidence_refs"] = [ref for ref in normalized.get("evidence_refs", []) if str(ref) not in hidden_refs]
        normalized["progress"] = [
            item
            for item in normalized.get("progress", [])
            if not any(str(item).startswith(f"{kind}:") for kind in hidden_kinds)
        ]
        normalized["open_risks"] = list(normalized.get("blockers", []))
        return normalized

    @staticmethod
    def _latest_tool_results_for_prompt(state: AgentState, selected_tool_ids: set[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for result in list(state.tool_results.values())[-4:]:
            if not isinstance(result, dict):
                continue
            tool_id = result.get("tool_id")
            if not isinstance(tool_id, str) or (selected_tool_ids and tool_id not in selected_tool_ids):
                continue
            item = {
                "tool_result_id": result.get("tool_result_id"),
                "tool_id": tool_id,
                "status": result.get("status"),
                "arguments": deepcopy(result.get("arguments", {})) if isinstance(result.get("arguments"), dict) else {},
                "evidence_refs": list(result.get("evidence_refs", [])) if isinstance(result.get("evidence_refs"), list) else [],
            }
            payload = result.get("payload")
            if isinstance(payload, dict):
                item["payload"] = _compact_world_ref_payload(payload)
            model_view = result.get("model_view")
            if isinstance(model_view, str) and model_view.strip():
                item["model_view"] = ContextHarness._compact_model_view_for_prompt(model_view, item.get("payload"))
            results.append(item)
        return results

    def _action_refs_for_prompt(self, state: AgentState) -> list[dict[str, Any]]:
        if not _request_context_wants_action_reference(state):
            return []
        refs: list[dict[str, Any]] = []
        for ref_id, ref in list(state.world_refs.items())[-16:]:
            if not isinstance(ref_id, str) or not isinstance(ref, dict):
                continue
            kind = ref.get("kind")
            if not isinstance(kind, str):
                continue
            definition = self._tool_definition(kind)
            if definition is None or definition.category != "act":
                continue
            raw_paths = _action_ref_paths(ref)
            direction = _action_ref_direction(ref)
            current_paths = direction.get("current_paths")
            paths = list(current_paths) if isinstance(current_paths, list) else raw_paths
            if not paths and not direction.get("obsolete_paths"):
                continue
            item = {
                "ref_id": ref_id,
                "kind": kind,
                "paths": paths,
                "summary": str(ref.get("summary", ""))[:240],
                "freshness": ref.get("freshness", "stable"),
            }
            item.update(direction)
            refs.append(item)
        return refs[-8:]

    @staticmethod
    def _compact_model_view_for_prompt(model_view: str, compacted_payload: Any) -> str:
        text = model_view.strip()
        if len(text) <= 1200:
            return text
        if isinstance(compacted_payload, dict) and compacted_payload:
            return (
                "Tool result model view compacted for prompt budget; "
                f"use compact payload and evidence_refs. Compact payload: {json.dumps(compacted_payload, sort_keys=True, default=str)[:900]}"
            )
        return text[:900] + f"\n[model_view compacted from {len(text)} chars]"

    def _world_ref_visible_for_prompt(self, state: AgentState, ref: dict[str, Any]) -> bool:
        kind = ref.get("kind")
        if not isinstance(kind, str):
            return False
        definition = self._tool_definition(kind)
        if definition is None or definition.category != "act":
            return _request_context_wants_reference_evidence(state)
        return ref.get("request_id") == state.request.request_id

    def _context_metric(
        self,
        state: AgentState,
        provider_prompt: dict[str, Any],
        *,
        raw_messages: list[dict[str, Any]],
        compressed_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt_chars = estimate_chars(provider_prompt)
        max_chars = getattr(self.gateway_guard, "max_chars", 0)
        selected = provider_prompt.get("selection") if isinstance(provider_prompt.get("selection"), dict) else {}
        world = provider_prompt.get("world") if isinstance(provider_prompt.get("world"), dict) else {}
        world_refs = world.get("world_refs") if isinstance(world.get("world_refs"), dict) else {}
        return {
            "call_index": len(state.context_metrics) + 1,
            "mode": state.mode,
            "request_id": state.request.request_id,
            "run_id": state.run_id,
            "message_count": len(compressed_messages),
            "raw_message_count": len(raw_messages),
            "prompt_chars": prompt_chars,
            "raw_prompt_message_chars": estimate_chars(raw_messages),
            "compressed_prompt_message_chars": estimate_chars(compressed_messages),
            "estimated_prompt_tokens": max(1, prompt_chars // 4) if prompt_chars else 0,
            "context_window_chars": max_chars,
            "context_window_estimated_tokens": max(1, max_chars // 4) if max_chars else 0,
            "context_utilization": round(prompt_chars / max_chars, 4) if max_chars else 0.0,
            "selected_tool_count": len(selected.get("selected_tools", [])) if isinstance(selected.get("selected_tools"), list) else 0,
            "visible_world_ref_count": len(world_refs),
            "stored_world_ref_count": len(state.world_refs),
            "turn_feedback_count": len(state.turn_feedback),
        }

    @staticmethod
    def _bounded_memory_summary(summary: dict[str, list[Any]]) -> dict[str, list[Any]]:
        bounded: dict[str, list[Any]] = {}
        for key in ("goals", "decisions", "progress", "blockers"):
            bounded[key] = [str(item)[:500] for item in summary.get(key, [])[-16:] if item]
        bounded["evidence_refs"] = [str(item)[:240] for item in summary.get("evidence_refs", [])[-96:] if item]
        return bounded

    @staticmethod
    def _summary_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        for message in messages:
            summary = message.get("summary")
            if isinstance(summary, dict):
                return deepcopy(summary)
        return None

    @staticmethod
    def _merge_context_summaries(base: Any, overlay: Any) -> dict[str, list[Any]]:
        merged = normalized_context_summary(base)
        incoming = normalized_context_summary(overlay)
        for key, values in incoming.items():
            for value in values:
                if value not in merged.setdefault(key, []):
                    merged[key].append(value)
        return merged

    def _mutable_json_like(self, value: Any) -> Any:
        if isinstance(value, dict) or hasattr(value, "items"):
            return {key: self._mutable_json_like(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [self._mutable_json_like(item) for item in value]
        return value


def _request_context_wants_reference_evidence(state: AgentState) -> bool:
    request = state.request
    parts = [request.active_user_request or request.user_goal, request.user_goal]
    ui_context = request.ui_context if isinstance(request.ui_context, dict) else {}
    summary = ui_context.get("conversation_summary")
    if isinstance(summary, str):
        parts.append(summary)
    text = "\n".join(part for part in parts if isinstance(part, str)).lower()
    latest = str(request.active_user_request or request.user_goal or "").lower()
    normalized_latest = " ".join(latest.split())
    if normalized_latest in {"and", "and?", "and ?", "?", "continue", "continue?", "retry"}:
        return _has_reference_evidence(state)
    cues = (
        "that",
        "those",
        "same",
        "previous",
        "above",
        "line",
        "lines",
        "file",
        "files",
        "repo",
        "repository",
        "code",
        "src",
        ".py",
        ".ts",
        ".js",
        ".md",
        ".json",
        "planner",
        "analyze",
        "analyse",
        "inspect",
        "read",
        "show",
        "list",
    )
    return any(cue in text for cue in cues)


def _request_context_wants_action_reference(state: AgentState) -> bool:
    if _active_request_likely_mutates(state):
        return False
    return _request_context_wants_reference_evidence(state)


def _active_request_likely_mutates(state: AgentState) -> bool:
    request = str(state.request.active_user_request or state.request.user_goal or "").lower()
    if any(
        marker in request
        for marker in (
            "no edit",
            "no edits",
            "do not edit",
            "don't edit",
            "dont edit",
            "no write",
            "no writes",
            "read only",
            "read-only",
            "analysis only",
        )
    ):
        return False
    mutation_words = (
        "add",
        "create",
        "write",
        "edit",
        "update",
        "fix",
        "patch",
        "replace",
        "change",
        "move",
        "rename",
        "delete",
        "remove",
        "copy",
        "organize",
        "clean",
    )
    return any(_contains_word(request, word) for word in mutation_words)


def _action_ref_paths(ref: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for source in (ref.get("arguments"), ref.get("payload")):
        if not isinstance(source, dict):
            continue
        for key in ("path", "source", "destination"):
            value = source.get(key)
            if isinstance(value, str) and value and value not in paths:
                paths.append(value)
    return paths


def _action_ref_direction(ref: dict[str, Any]) -> dict[str, Any]:
    kind = ref.get("kind")
    arguments = ref.get("arguments") if isinstance(ref.get("arguments"), dict) else {}
    payload = ref.get("payload") if isinstance(ref.get("payload"), dict) else {}

    def value(key: str) -> str:
        for source in (arguments, payload):
            item = source.get(key)
            if isinstance(item, str) and item:
                return item
        return ""

    path = value("path")
    source = value("source")
    destination = value("destination")
    if kind == "file_management.move_file":
        return {
            "source": source,
            "destination": destination,
            "current_paths": [destination] if destination else [],
            "obsolete_paths": [source] if source else [],
            "effect": f"moved {source} to {destination}" if source and destination else "moved file",
        }
    if kind == "file_management.copy_file":
        return {
            "source": source,
            "destination": destination,
            "current_paths": [path for path in (source, destination) if path],
            "obsolete_paths": [],
            "effect": f"copied {source} to {destination}" if source and destination else "copied file",
        }
    if kind == "file_management.delete_file":
        return {
            "path": path,
            "current_paths": [],
            "obsolete_paths": [path] if path else [],
            "effect": f"deleted {path}" if path else "deleted file",
        }
    if path:
        return {
            "path": path,
            "current_paths": [path],
            "obsolete_paths": [],
            "effect": f"updated {path}",
        }
    return {"current_paths": [], "obsolete_paths": []}


def _contains_word(text: str, word: str) -> bool:
    import re

    return re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", text) is not None


def _has_reference_evidence(state: AgentState) -> bool:
    for ref in state.world_refs.values():
        if not isinstance(ref, dict):
            continue
        kind = ref.get("kind")
        if isinstance(kind, str) and kind.startswith("file_management."):
            return True
    return False
