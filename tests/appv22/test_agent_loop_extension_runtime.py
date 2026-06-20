import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.base import SkillCard
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.runtime.decisions import RuntimeDecision
from appv22.runtime.reducer import apply_event
from appv22.runtime.services import create_appv22_services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.definitions import ToolDefinition


class RecordingProvider:
    provider_id = "recording"
    model_id = "recording-model"

    def __init__(self, decision):
        self.decision = decision
        self.prompts = []

    def decide(self, prompt: dict):
        self.prompts.append(prompt)
        return self.decision


class MalformedDecisionProvider:
    provider_id = "malformed"

    def decide(self, prompt: dict):
        class MalformedDecision:
            kind = "plan"

            def to_dict(self):
                raise TypeError("malformed decision payload")

        return MalformedDecision()


class SequenceProvider:
    provider_id = "sequence"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []

    def decide(self, prompt: dict):
        self.prompts.append(prompt)
        return self.decisions.pop(0)


class TransientFailingProvider(SequenceProvider):
    provider_id = "transient"

    def __init__(self, decisions):
        super().__init__(decisions)
        self.failures_left = 1

    def decide(self, prompt: dict):
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("transport leaked SECRET_PROVIDER_TOKEN=tok_retry_private")
        return super().decide(prompt)


class OverflowThenFinalizeProvider:
    provider_id = "overflow_then_finalize"

    def __init__(self):
        self.prompts = []

    def decide(self, prompt: dict):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            raise RuntimeError("The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)")
        return RuntimeDecision("finalize", "recovered", {"message": "Recovered after compacting context."})


class PromptMutatingGuard:
    def guard(self, messages):
        guarded = []
        for message in messages:
            copied = dict(message)
            if copied.get("name") == "provider_context_section" and copied.get("section") == "agent":
                copied["payload"] = dict(copied["payload"])
                copied["payload"]["guard_marker"] = "actual-provider-context"
            guarded.append(copied)
        return guarded


class SummaryInjectingCompressor:
    def compress(self, messages, *, previous_summary):
        compressed = []
        for message in messages:
            copied = dict(message)
            if copied.get("name") == "provider_context_section":
                copied["summary"] = {
                    "goals": ["persisted summary"],
                    "decisions": [],
                    "progress": [],
                    "open_risks": [],
                    "evidence_refs": [],
                }
            compressed.append(copied)
        return compressed


class RecordingCompressor(AgentContextCompressor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.records = []

    def compress(self, messages, *, previous_summary):
        from appv22.context.budget import estimate_chars

        before = estimate_chars(messages)
        compressed = super().compress(messages, previous_summary=previous_summary)
        after = estimate_chars(compressed)
        self.records.append(
            {
                "before": before,
                "after": after,
                "triggered": before > int(self.max_chars * self.threshold),
                "preserved_sections": [
                    message.get("section")
                    for message in compressed
                    if message.get("name") == "provider_context_section"
                ],
            }
        )
        return compressed


class OneSkillExtension:
    extension_id = "one_skill"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="one_skill.active",
                extension_id=self.extension_id,
                triggers=("workspace",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test skill",
                tool_ids=(),
            )
        ]


class GuidanceExtension(OneSkillExtension):
    def tool_result_guidance(self, result):
        if result.get("status") == "denied":
            return "extension-owned denial guidance"
        return ""

    def finalize_guidance(self, state):
        if any(
            isinstance(result, dict) and result.get("status") == "completed"
            for result in state.tool_results.values()
        ):
            return "extension-owned finalize guidance"
        return ""


class BeforeToolGuardExtension:
    extension_id = "before_tool_guard"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="before_tool_guard.active",
                extension_id=self.extension_id,
                triggers=("guarded",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test pre-tool guard skill",
                tool_ids=("guarded.publish",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "guarded.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                {
                    "type": "object",
                    "properties": {
                        "accepted": {"type": "boolean"},
                        "text": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["accepted", "text", "errors"],
                },
                "runtime_observed",
                "Publish guarded text.",
            ),
            self.publish,
        )

    def before_tool_call(self, _state, tool_id, arguments):
        if tool_id == "guarded.publish" and "safe" not in str(arguments.get("text", "")).lower():
            return {
                "reason": "unsafe_text",
                "errors": ["unsafe_text"],
                "payload": {"safe_hint": "Use text containing the word safe."},
            }
        return None

    def tool_result_guidance(self, result):
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if "unsafe_text" in payload.get("errors", []):
            return "pre-tool guard says retry guarded.publish with safe text."
        return ""

    def publish(self, args, _context):
        return {"status": "completed", "accepted": True, "text": str(args.get("text", "")), "errors": []}


class GuardRedactionExtension:
    extension_id = "guard_redaction"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="guard_redaction.active",
                extension_id=self.extension_id,
                triggers=("redact-denied",),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="test redaction of denied argument values from retained world refs",
                tool_ids=("guard_redaction.lookup", "guard_redaction.publish"),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "guard_redaction.lookup",
                "observe",
                "low",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "unsafe_draft": {"type": "string"}, "errors": {"type": "array", "items": {"type": "string"}}},
                    "required": ["key", "unsafe_draft", "errors"],
                },
                "runtime_observed",
                "Return unsafe draft evidence for guard redaction test.",
            ),
            lambda args, _context: {"status": "completed", "key": args.get("key", ""), "unsafe_draft": "unsafe denied draft value", "errors": []},
        )
        registry.register(
            ToolDefinition(
                "guard_redaction.publish",
                "act",
                "medium",
                {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                {
                    "type": "object",
                    "properties": {"accepted": {"type": "boolean"}, "text": {"type": "string"}, "errors": {"type": "array", "items": {"type": "string"}}},
                    "required": ["accepted", "text", "errors"],
                },
                "runtime_observed",
                "Publish redaction test text.",
            ),
            lambda args, _context: {"status": "completed", "accepted": True, "text": str(args.get("text", "")), "errors": []},
        )

    def before_tool_call(self, _state, tool_id, arguments):
        if tool_id == "guard_redaction.publish" and arguments.get("text") == "unsafe denied draft value":
            return {"reason": "unsafe_text", "errors": ["unsafe_text"], "payload": {"safe_text": "safe replacement"}}
        return None

    def tool_result_guidance(self, result):
        if result.get("tool_id") == "guard_redaction.publish" and result.get("status") == "denied":
            return "guard_redaction.publish was blocked; retry guard_redaction.publish with text 'safe replacement'."
        return ""


class AfterToolRedactionExtension:
    extension_id = "after_tool_redaction"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="after_tool_redaction.active",
                extension_id=self.extension_id,
                triggers=("redact",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test post-tool redaction skill",
                tool_ids=("redaction.fetch",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "redaction.fetch",
                "act",
                "medium",
                {"type": "object", "properties": {}},
                {
                    "type": "object",
                    "properties": {
                        "public": {"type": "string"},
                        "secret": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["public", "secret", "errors"],
                },
                "runtime_observed",
                "Fetch redaction test payload.",
            ),
            self.fetch,
        )

    def after_tool_call(self, _state, result):
        if result.get("tool_id") != "redaction.fetch":
            return None
        redacted = dict(result)
        payload = dict(redacted.get("payload") or {})
        payload["secret"] = "[redacted]"
        redacted["payload"] = payload
        return redacted

    def fetch(self, _args, _context):
        return {"status": "completed", "public": "safe public value", "secret": "tok_after_private", "errors": []}


class ValidationErrorGuidanceExtension:
    extension_id = "validation_error_guidance"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="validation_error_guidance.active",
                extension_id=self.extension_id,
                triggers=("validation-guidance",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test generic validation guidance",
                tool_ids=("validation.publish",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "validation.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                    "required": ["note"],
                },
                {
                    "type": "object",
                    "properties": {
                        "accepted": {"type": "boolean"},
                        "note": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["accepted", "note", "errors"],
                },
                "runtime_observed",
                "Publish validation note.",
            ),
            self.publish,
        )

    def publish(self, args, _context):
        note = str(args.get("note", ""))
        if "safe lab note" not in note.lower():
            return {
                "status": "denied",
                "accepted": False,
                "note": note,
                "errors": ["note_missing_terms:safe lab note"],
            }
        return {"status": "completed", "accepted": True, "note": note, "errors": []}


class CompactAfterObservationGuidanceExtension:
    extension_id = "compact_after_observation_guidance"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="compact_after_observation_guidance.active",
                extension_id=self.extension_id,
                triggers=("compact-after-observe",),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="test compact after observation guidance",
                tool_ids=("compact_guidance.lookup", "compact_guidance.publish"),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "compact_guidance.lookup",
                "observe",
                "low",
                {"type": "object", "properties": {}, "required": []},
                {
                    "type": "object",
                    "properties": {"ready": {"type": "boolean"}, "errors": {"type": "array", "items": {"type": "string"}}},
                    "required": ["ready", "errors"],
                },
                "runtime_observed",
                "Observe publish facts.",
            ),
            lambda _args, _context: {"status": "completed", "ready": True, "errors": []},
        )
        registry.register(
            ToolDefinition(
                "compact_guidance.publish",
                "act",
                "medium",
                {"type": "object", "properties": {}, "required": []},
                {
                    "type": "object",
                    "properties": {"accepted": {"type": "boolean"}, "errors": {"type": "array", "items": {"type": "string"}}},
                    "required": ["accepted", "errors"],
                },
                "runtime_observed",
                "Publish after observation.",
            ),
            lambda _args, _context: {"status": "completed", "accepted": True, "errors": []},
        )

    def finalize_guidance(self, state):
        observed = any(
            isinstance(result, dict)
            and result.get("tool_id") == "compact_guidance.lookup"
            and result.get("status") == "completed"
            for result in state.tool_results.values()
        )
        published = any(
            isinstance(result, dict)
            and result.get("tool_id") == "compact_guidance.publish"
            and result.get("status") == "completed"
            for result in state.tool_results.values()
        )
        if observed and not published:
            return "Observation is complete; call compact_guidance.publish before finalizing."
        return ""

class FailingAfterHookExtension(AfterToolRedactionExtension):
    extension_id = "failing_after_hook"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="failing_after_hook.active",
                extension_id=self.extension_id,
                triggers=("hook exception",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test failing after hook skill",
                tool_ids=("redaction.fetch",),
            )
        ]

    def after_tool_call(self, _state, _result):
        raise RuntimeError("tok_hook_exception_private backend trace")


class MalformedAfterHookExtension(AfterToolRedactionExtension):
    extension_id = "malformed_after_hook"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="malformed_after_hook.active",
                extension_id=self.extension_id,
                triggers=("malformed after",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test malformed after hook skill",
                tool_ids=("redaction.fetch",),
            )
        ]

    def after_tool_call(self, _state, result):
        malformed = dict(result)
        payload = dict(malformed.get("payload") or {})
        payload.pop("public", None)
        malformed["payload"] = payload
        return malformed


class FailingFinalizeHookExtension(AfterToolRedactionExtension):
    extension_id = "failing_finalize_hook"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="failing_finalize_hook.active",
                extension_id=self.extension_id,
                triggers=("finalize hook exception",),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="test failing finalize hook skill",
                tool_ids=("redaction.fetch",),
            )
        ]

    def after_tool_call(self, _state, result):
        return result

    def finalize_guidance(self, _state):
        raise RuntimeError("tok_finalize_private backend trace")


class RetryableFailureExtension:
    extension_id = "retryable_failure"

    def __init__(self):
        self.calls = 0

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="retryable_failure.active",
                extension_id=self.extension_id,
                triggers=("retryable",),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="retryable failure test",
                tool_ids=("retryable.fetch",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "retryable.fetch",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "retryable": {"type": "boolean"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "value", "retryable", "errors"],
                },
                "runtime_observed",
                "Fetch a value; failed results with retryable true may be retried.",
            ),
            self.fetch,
        )

    def fetch(self, args, _context):
        self.calls += 1
        if self.calls == 1:
            return {
                "status": "failed",
                "key": args.get("key", ""),
                "value": "",
                "retryable": True,
                "errors": ["transient"],
            }
        return {
            "status": "completed",
            "key": args.get("key", ""),
            "value": "stable",
            "retryable": False,
            "errors": [],
        }


class AlternateRecoveryExtension:
    extension_id = "alternate_recovery"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="alternate_recovery.active",
                extension_id=self.extension_id,
                triggers=("alternate-recovery",),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="alternate recovery test",
                tool_ids=("alternate_recovery.broken", "alternate_recovery.safe"),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "alternate_recovery.broken",
                "observe",
                "low",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "errors"],
                },
                "runtime_observed",
                "Broken alternate recovery tool.",
            ),
            lambda args, _context: {"status": "failed", "key": args.get("key", ""), "errors": ["broken"]},
        )
        registry.register(
            ToolDefinition(
                "alternate_recovery.safe",
                "observe",
                "low",
                {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "value", "errors"],
                },
                "runtime_observed",
                "Safe alternate recovery tool.",
            ),
            lambda args, _context: {"status": "completed", "key": args.get("key", ""), "value": "safe", "errors": []},
        )

    def tool_result_guidance(self, result):
        if result.get("tool_id") == "alternate_recovery.broken" and result.get("status") == "failed":
            return "alternate_recovery.broken failed; recover with alternate_recovery.safe using key 'alpha'."
        return ""


class ObserveOnlyExtension:
    extension_id = "observe_only"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="observe_only.lookup",
                extension_id=self.extension_id,
                triggers=("handoff",),
                modes=("START", "THINK", "OBSERVE"),
                summary="observe-only test skill",
                tool_ids=("observe_only.lookup",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "observe_only.lookup",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
                {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["key", "value"],
                },
                "runtime_observed",
                "Lookup test evidence.",
            ),
            lambda args, _context: {
                "status": "completed",
                "key": args.get("key", ""),
                "value": "observed",
            },
        )


class ActionOnlyExtension:
    extension_id = "action_only"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="action_only.publish",
                extension_id=self.extension_id,
                triggers=("handoff",),
                modes=("ACT", "VERIFY"),
                summary="action-only test skill",
                tool_ids=("action_only.publish",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "action_only.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                {
                    "type": "object",
                    "properties": {"accepted": {"type": "boolean"}, "message": {"type": "string"}},
                    "required": ["accepted", "message"],
                },
                "runtime_observed",
                "Publish test handoff.",
            ),
            lambda args, _context: {
                "status": "completed",
                "accepted": True,
                "message": args.get("message", ""),
            },
        )

    def finalize_guidance(self, state):
        observed = any(
            isinstance(result, dict)
            and result.get("tool_id") == "observe_only.lookup"
            and result.get("status") == "completed"
            for result in state.tool_results.values()
        )
        published = any(
            isinstance(result, dict)
            and result.get("tool_id") == "action_only.publish"
            and result.get("status") == "completed"
            and isinstance(result.get("payload"), dict)
            and result["payload"].get("accepted") is True
            for result in state.tool_results.values()
        )
        if observed and not published:
            return "Observation exists but publish action is missing; call action_only.publish."
        return ""


class ContinueActionExtension(ActionOnlyExtension):
    extension_id = "continue_action"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="continue_action.publish",
                extension_id=self.extension_id,
                triggers=("continue", "handoff"),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="continuation action test skill",
                tool_ids=("action_only.publish",),
            )
        ]

    def finalize_guidance(self, state):
        published = any(
            isinstance(result, dict)
            and result.get("tool_id") == "action_only.publish"
            and result.get("status") == "completed"
            and isinstance(result.get("payload"), dict)
            and result["payload"].get("accepted") is True
            for result in state.tool_results.values()
        )
        if published:
            return ""
        if state.world_refs:
            return "Carried evidence exists but publish action is missing; call action_only.publish."
        return ""


class OversizedPromptExtension:
    extension_id = "oversized_prompt"

    def __init__(self, raw_marker):
        self.raw_marker = raw_marker

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="oversized_prompt.active",
                extension_id=self.extension_id,
                triggers=("workspace",),
                modes=("START",),
                summary=f"oversized prompt material {self.raw_marker}",
                tool_ids=(),
            )
        ]


class ContextPayloadExtension:
    extension_id = "context_payload"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="context_payload.active",
                extension_id=self.extension_id,
                triggers=("workspace",),
                modes=("START",),
                summary="test Pi-style context and provider payload hooks",
                tool_ids=(),
            )
        ]

    def context(self, _state, messages):
        copied = []
        for message in messages:
            item = dict(message)
            if item.get("name") == "provider_context_section" and item.get("section") == "agent":
                item["payload"] = dict(item.get("payload") or {})
                item["payload"]["extension_context_marker"] = "context-hook-active"
                item["content"] = f"agent: {json.dumps(item['payload'], sort_keys=True, default=str)}"
            copied.append(item)
        return copied

    def before_provider_request(self, _state, payload):
        current = dict(payload)
        current["extension_payload_marker"] = "before-provider-request-active"
        current["state"] = dict(current.get("state") or {})
        current["state"]["before_provider_request_marker"] = "payload-hook-active"
        return current


class ToolExecutionEndExtension:
    extension_id = "tool_execution_event"

    def __init__(self) -> None:
        self.tool_events = []

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="tool_execution_event.active",
                extension_id=self.extension_id,
                triggers=("tool hook",),
                modes=("START", "OBSERVE"),
                summary="test Pi-style tool_execution_end hook",
                tool_ids=("tool_execution_event.echo",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "tool_execution_event.echo",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                {
                    "type": "object",
                    "properties": {
                        "echo": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["echo", "errors"],
                },
                "runtime_observed",
                "Echo text for tool execution hook tests.",
            ),
            lambda args, _context: {"status": "completed", "echo": str(args.get("text", "")), "errors": []},
        )

    def tool_execution_end(self, event):
        self.tool_events.append(event)


class TurnEndExtension(ToolExecutionEndExtension):
    extension_id = "turn_end_event"

    def __init__(self) -> None:
        super().__init__()
        self.turn_events = []

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="turn_end_event.active",
                extension_id=self.extension_id,
                triggers=("turn hook",),
                modes=("START", "OBSERVE", "THINK"),
                summary="test Pi-style turn_end hook",
                tool_ids=("turn_end_event.echo",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "turn_end_event.echo",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                {
                    "type": "object",
                    "properties": {
                        "echo": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["echo", "errors"],
                },
                "runtime_observed",
                "Echo text for turn end hook tests.",
            ),
            lambda args, _context: {"status": "completed", "echo": str(args.get("text", "")), "errors": []},
        )

    def turn_end(self, event):
        self.turn_events.append(event)


class ToolsUpdateExtension(ToolExecutionEndExtension):
    extension_id = "tools_update_event"

    def __init__(self) -> None:
        super().__init__()
        self.tools_events = []

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="tools_update_event.active",
                extension_id=self.extension_id,
                triggers=("tools update hook",),
                modes=("START", "THINK"),
                summary="test Pi-style tools_update hook",
                tool_ids=("tools_update_event.echo",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "tools_update_event.echo",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                {
                    "type": "object",
                    "properties": {
                        "echo": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["echo", "errors"],
                },
                "runtime_observed",
                "Echo text for tools update hook tests.",
            ),
            lambda args, _context: {"status": "completed", "echo": str(args.get("text", "")), "errors": []},
        )

    def tools_update(self, event):
        self.tools_events.append(event)


class PassiveToolsUpdateExtension:
    extension_id = "passive_tools_update_event"

    def __init__(self) -> None:
        self.tools_events = []

    def skill_cards(self):
        return []

    def tools_update(self, event):
        self.tools_events.append(event)


class LargeToolResultExtension:
    extension_id = "large_tool_result"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="large_tool_result.active",
                extension_id=self.extension_id,
                triggers=("large tool result",),
                modes=("START", "THINK", "OBSERVE"),
                summary="test Hermes large tool result offload",
                tool_ids=("large_tool_result.dump",),
            )
        ]

    def register_tools(self, registry):
        registry.register(
            ToolDefinition(
                "large_tool_result.dump",
                "observe",
                "low",
                {"type": "object", "properties": {}, "required": []},
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "runtime_observed",
                "Return a large generic output for Hermes offload tests.",
            ),
            lambda _args, _context: {"status": "completed", "text": "x" * 25_000},
        )


class RuntimeSessionStartExtension:
    extension_id = "runtime_session_start"

    def __init__(self) -> None:
        self.session_events = []

    def skill_cards(self):
        return []

    def session_start(self, event):
        self.session_events.append(event)


class PassingPolicy:
    def validate(self, operations, *, root_path):
        return []


class PassingExecutor:
    def apply(self, operations, *, root_path):
        return {"status": "applied"}


class PassingVerifier:
    def verify(self, *, root_path, verification_intent):
        return {"status": "passed"}


def test_agent_loop_rejects_non_executable_deterministic_plan_without_cleanup_fallback(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("a", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace before planning",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("compact", "need another reasoning turn", {}),
            RuntimeDecision("pause", "stop after repair guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"


def test_agent_loop_emits_pi_style_context_and_provider_payload_extension_hooks(tmp_path):
    provider = SequenceProvider([RuntimeDecision("pause", "inspect extension hook payload")])
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ContextPayloadExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "inspect workspace context hooks"
    )

    prompt = provider.prompts[0]
    assert prompt["agent"]["extension_context_marker"] == "context-hook-active"
    assert prompt["extension_payload_marker"] == "before-provider-request-active"
    assert prompt["state"]["before_provider_request_marker"] == "payload-hook-active"


def test_agent_loop_emits_pi_style_tool_execution_end_extension_hook(tmp_path):
    extension = ToolExecutionEndExtension()
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "run echo",
                {"tool_id": "tool_execution_event.echo", "arguments": {"text": "hook payload"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[extension],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "run the tool hook"
    )

    assert result["status"] == "completed"
    assert len(extension.tool_events) == 1
    event = extension.tool_events[0]
    assert event["type"] == "tool_execution_end"
    assert event["toolName"] == "tool_execution_event.echo"
    assert isinstance(event["toolCallId"], str) and event["toolCallId"]
    assert event["result"]["payload"]["echo"] == "hook payload"
    assert event["isError"] is False


def test_agent_loop_emits_pi_style_turn_end_extension_hook(tmp_path):
    extension = TurnEndExtension()
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "run echo",
                {"tool_id": "turn_end_event.echo", "arguments": {"text": "turn payload"}},
            ),
            RuntimeDecision("finalize", "done", {"message": "turn complete"}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[extension],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "run the turn hook"
    )

    assert result["status"] == "completed"
    assert len(extension.turn_events) == 2
    tool_turn = extension.turn_events[0]
    final_turn = extension.turn_events[1]
    assert tool_turn["type"] == "turn_end"
    assert tool_turn["turnIndex"] == 0
    assert tool_turn["message"]["kind"] == "tool_call"
    assert tool_turn["toolResults"][0]["tool_id"] == "turn_end_event.echo"
    assert tool_turn["toolResults"][0]["payload"]["echo"] == "turn payload"
    assert final_turn["turnIndex"] == 1
    assert final_turn["message"]["kind"] == "finalize"
    assert final_turn["toolResults"] == []


def test_agent_loop_emits_pi_style_tools_update_extension_hook(tmp_path):
    active_extension = ToolsUpdateExtension()
    passive_extension = PassiveToolsUpdateExtension()
    provider = RecordingProvider(RuntimeDecision("finalize", "done", {"message": "tools update captured"}))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[active_extension, passive_extension],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "tools update hook"
    )

    assert result["status"] == "completed"
    assert result["model"] == {"provider": "recording", "modelId": "recording-model"}
    assert result["active_tool_ids"] == ["tools_update_event.echo"]
    assert len(active_extension.tools_events) == 1
    assert len(passive_extension.tools_events) == 1
    event = active_extension.tools_events[0]
    assert event["type"] == "tools_update"
    assert event["toolNames"] == ["tools_update_event.echo"]
    assert event["previousToolNames"] == ["tools_update_event.echo"]
    assert event["activeToolNames"] == ["tools_update_event.echo"]
    assert event["previousActiveToolNames"] == []
    assert event["source"] == "set"
    assert passive_extension.tools_events[0] == event


def test_agent_loop_emits_pi_style_runtime_session_start_for_new_and_resume(tmp_path):
    extension = RuntimeSessionStartExtension()
    provider = SequenceProvider(
        [
            RuntimeDecision("finalize", "first", {"message": "first done"}),
            RuntimeDecision("finalize", "second", {"message": "second done"}),
        ]
    )
    runtime = AppV22AgentRuntime(
        root_path=tmp_path,
        services=create_appv22_services(
            root_path=tmp_path,
            provider=provider,
            extensions=[extension],
        ),
        max_turns=1,
    )

    first = runtime.run("hello")
    second = runtime.continue_run(first, "hello again")

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert extension.session_events == [
        {"type": "session_start", "reason": "new"},
        {"type": "session_start", "reason": "resume"},
    ]


def test_agent_loop_offloads_large_tool_result_like_hermes_reference_store(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "dump large output", {"tool_id": "large_tool_result.dump", "arguments": {}}),
            RuntimeDecision("finalize", "done", {"message": "large output captured"}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[LargeToolResultExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "large tool result"
    )

    tool_result = result["tool_results"][0]
    payload = tool_result["payload"]
    ref_path = tmp_path / payload["offloaded_ref"]["path"]
    raw = json.loads(ref_path.read_text(encoding="utf-8"))

    assert result["status"] == "completed"
    assert payload["offloaded_ref"]["mode"] == "hermes-tool-result-offload"
    assert payload["offloaded_ref"]["original_bytes"] > 20_000
    assert len(payload["preview"]) <= 1400
    assert raw["payload"]["text"] == "x" * 25_000
    assert result["world_refs"][tool_result["payload_ref"]]["payload"] == payload


def test_agent_loop_executes_model_file_creation_tool_call(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace before planning",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "write a useful handoff record from observed context",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/NEXT_STEPS.md",
                        "content": "# Next Steps\n\nHandoff note for whoever picks this up next.\n",
                    },
                },
                ["world://repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "verify created file"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "make a small useful record for whoever picks this up next"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "NEXT_STEPS.md").read_text(encoding="utf-8") == (
        "# Next Steps\n\nHandoff note for whoever picks this up next.\n"
    )
    assert any(
        event["event_type"] == "ToolCallCompleted"
        and event["payload"]["tool_id"] == "file_management.write_file"
        for event in result["events"]
    )


def test_agent_loop_continues_after_write_tool_call_until_finalize(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace before planning",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "write a useful handoff record from observed context",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "notes/handoff.md",
                        "content": "# Handoff\n\nNext steps for the next person.\n",
                    },
                },
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "make a small useful record for whoever picks this up next"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "notes" / "handoff.md").read_text(encoding="utf-8") == (
        "# Handoff\n\nNext steps for the next person.\n"
    )
    assert len(provider.prompts) == 3


def test_agent_loop_executes_direct_observation_request_without_plan_mode(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "Need current repo map before writing.",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "create from observed context",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "notes/observed.md",
                        "content": "# Observed\n\nRepo map was collected.\n",
                    },
                },
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "make a useful record after inspecting the repo"
    )

    assert result["status"] == "completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert tool_events[0]["payload"]["tool_id"] == "file_management.repo_snapshot"
    assert (tmp_path / "notes" / "observed.md").is_file()


def test_agent_loop_rejects_nested_plan_tool_call_shape(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "Invalid nested tool request should not be repaired as a planning artifact.",
                {"tool_call": {"tool_id": "file_management.repo_snapshot", "arguments": {}}},
            ),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "Make a small useful handoff record."
    )

    assert result["status"] == "failed"
    assert result["reason"] == "malformed_tool_call"
    assert not (tmp_path / "notes" / "handoff.md").exists()


def test_agent_loop_does_not_repair_tool_names_hidden_in_prose(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "pause",
                "I should have used the selected tool file_management.repo_snapshot before deciding.",
                {},
            ),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "inspect this workspace, do not mutate files, and pause after enough evidence"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert tool_events == []


def test_agent_loop_completes_observation_only_task_after_evidence_without_mutation(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "Need current repo map before deciding.",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "Evidence collected; user requested no mutation.", {}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "Inspect this repository, do not mutate files, and pause after you have enough evidence."
    )

    assert result["status"] == "completed"
    assert result["reason"] == "observation_only_completed"
    assert result["evidence_refs"] == ["world://file_management.repo_snapshot/latest"]


def test_agent_loop_treats_pause_after_observation_only_evidence_as_completed(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe repository",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "pause after enough evidence", {}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "Inspect this repository and tell the runtime enough evidence. Do not mutate files. Pause after you have enough evidence."
    )

    assert result["status"] == "completed"
    assert result["reason"] == "observation_only_completed"


def test_agent_loop_suppresses_duplicate_broad_reobserve_after_summary_evidence(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "Need current repo map before deciding.",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "Need to rehydrate repo_snapshot before proceeding.",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("pause", "Evidence collected; user requested no mutation.", {}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "Inspect this repository, do not mutate files, and pause after you have enough evidence."
    )

    assert result["status"] == "completed"
    tool_events = [
        event for event in result["events"]
        if event["event_type"] == "ToolCallCompleted"
        and event["payload"]["tool_id"] == "file_management.repo_snapshot"
    ]
    assert len(tool_events) == 2


def test_agent_loop_accepts_model_write_file_tool_call(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace before planning",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "write a useful handoff record from observed context",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/handoff.md",
                        "content": "# Handoff\n\nNext person should wire tax checkout.\n",
                    },
                },
                ["world://repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "verify created file"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "leave something useful for the next person"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "handoff.md").is_file()


def test_agent_loop_records_duplicate_completed_write_tool_call_as_tool_result(tmp_path):
    write_payload = {
        "tool_id": "file_management.write_file",
        "arguments": {
            "path": "notes/handoff.md",
            "content": "# Handoff\n\nAlready complete.\n",
        },
    }
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "write handoff",
                write_payload,
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision(
                "tool_call",
                "duplicate write after completion",
                write_payload,
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "done after duplicate tool result"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "leave something useful for the next person"
    )

    assert result["status"] == "completed"
    assert result["reason"] == "tool_loop_completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == [
        "file_management.repo_snapshot",
        "file_management.write_file",
    ]
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    assert [event["payload"]["tool_id"] for event in denied_events] == ["file_management.write_file"]


def test_agent_loop_recovers_from_malformed_tool_call_when_tool_id_is_missing(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "missing tool id", {"next_step": "observe"}),
            RuntimeDecision(
                "tool_call",
                "recover with selected action",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ActionOnlyExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run("prepare the handoff")

    assert result["status"] == "completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == ["action_only.publish"]
    assert any(
        "Malformed tool_call decision was missing payload.tool_id" in item
        for item in result["turn_feedback"]
    )


def test_agent_loop_repairs_finalize_payload_with_selected_tool_call(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "finalize",
                "wrong kind but selected tool payload",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ActionOnlyExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("prepare the handoff")

    assert result["status"] == "completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == ["action_only.publish"]


def test_agent_loop_adds_recovery_pressure_when_model_compacts_after_tool_denial(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "try unsafe publish first",
                {"tool_id": "guarded.publish", "arguments": {"text": "bad draft"}},
            ),
            RuntimeDecision("compact", "need to think after denial", {}),
            RuntimeDecision("compact", "still thinking instead of acting", {}),
            RuntimeDecision(
                "tool_call",
                "recover with safe publish",
                {"tool_id": "guarded.publish", "arguments": {"text": "safe final"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[BeforeToolGuardExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "publish guarded text"
    )

    assert result["status"] == "completed"
    assert any(
        "Recent tool feedback remains unresolved" in item
        for item in provider.prompts[3]["state"]["context_summary"]["open_risks"]
    )
    assert any(
        event["event_type"] == "ToolCallCompleted"
        and event["payload"]["tool_id"] == "guarded.publish"
        for event in result["events"]
    )


def test_agent_loop_continue_run_carries_session_world_refs_and_summary(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "observe_only.lookup", "arguments": {"key": "handoff"}},
            ),
            RuntimeDecision("finalize", "first done"),
            RuntimeDecision(
                "tool_call",
                "publish from carried evidence",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "second done"),
        ]
    )
    first_services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ObserveOnlyExtension()],
    )
    first_runtime = AppV22AgentRuntime(root_path=tmp_path, services=first_services, max_turns=2)

    first = first_runtime.run("prepare the handoff by observing first")
    second_services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ContinueActionExtension()],
    )
    second_runtime = AppV22AgentRuntime(root_path=tmp_path, services=second_services, max_turns=2)
    second = second_runtime.continue_run(first, "continue the handoff and publish from carried evidence")

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first["session_id"] == second["session_id"]
    assert any(
        isinstance(ref, dict) and ref.get("kind") == "observe_only.lookup"
        for ref in second["world_refs"].values()
    )
    assert any(
        result.get("tool_id") == "action_only.publish"
        for result in second["tool_results"]
        if isinstance(result, dict)
    )


def test_agent_loop_continue_run_carries_world_refs_from_failed_run(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe before failure",
                {"tool_id": "observe_only.lookup", "arguments": {"key": "handoff"}},
            ),
            RuntimeDecision(
                "tool_call",
                "publish after failed-run continuation",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "second done"),
        ]
    )
    first_services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ObserveOnlyExtension()],
    )
    first_runtime = AppV22AgentRuntime(root_path=tmp_path, services=first_services, max_turns=1)

    first = first_runtime.run("prepare the handoff by observing first")
    second_services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ContinueActionExtension()],
    )
    second_runtime = AppV22AgentRuntime(root_path=tmp_path, services=second_services, max_turns=2)
    second = second_runtime.continue_run(first, "continue the handoff and publish from carried evidence")

    assert first["status"] == "failed"
    assert first["reason"] == "max_turns_exceeded"
    assert first["world_refs"]
    assert first["context_summary"]["evidence_refs"]
    assert second["status"] == "completed"
    assert first["session_id"] == second["session_id"]
    assert any(
        isinstance(ref, dict) and ref.get("kind") == "observe_only.lookup"
        for ref in second["world_refs"].values()
    )


def test_agent_loop_retries_transient_provider_failure_without_leaking_details(tmp_path):
    provider = TransientFailingProvider(
        [
            RuntimeDecision(
                "tool_call",
                "publish after retry",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ActionOnlyExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4, provider_retry_attempts=1).run(
        "prepare the handoff"
    )

    serialized = json.dumps(result, sort_keys=True, default=str)
    assert result["status"] == "completed"
    assert "tok_retry_private" not in serialized
    assert "SECRET_PROVIDER_TOKEN" not in serialized
    provider_failures = [event for event in result["events"] if event["event_type"] == "ProviderCallFailed"]
    assert provider_failures == [
        {
            **provider_failures[0],
            "payload": {
                "status": "failed",
                "reason": "provider_decision_error",
                "attempt": 1,
                "will_retry": True,
            },
        }
    ]


def test_agent_loop_compacts_prompt_before_retrying_provider_context_overflow(tmp_path):
    provider = OverflowThenFinalizeProvider()
    raw_marker = "RAW_PROVIDER_OVERFLOW_SENTINEL_" + ("x" * 60_000)
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[OversizedPromptExtension(raw_marker)],
    )
    services.compressor = AgentContextCompressor(max_chars=120_000)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2, provider_retry_attempts=1).run(
        "workspace cleanup"
    )

    assert result["status"] == "completed"
    assert len(provider.prompts) == 2
    first_prompt = json.dumps(provider.prompts[0], sort_keys=True, default=str)
    second_prompt = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    assert provider.prompts[1] != provider.prompts[0]
    assert len(second_prompt) < len(first_prompt)
    assert raw_marker not in second_prompt
    assert provider.prompts[1]["state"]["provider_overflow_recovery"]["mode"] == "hermes-overflow-recovery"
    assert "Provider context overflow triggered Hermes recovery compaction." in json.dumps(
        provider.prompts[1]["state"]["context_summary"],
        sort_keys=True,
        default=str,
    )
    provider_failures = [event for event in result["events"] if event["event_type"] == "ProviderCallFailed"]
    assert provider_failures[0]["payload"]["reason"] == "context_overflow"


def test_agent_loop_recovers_from_malformed_context_request_after_observation_evidence(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "model forgot compacted evidence",
                {"next_step": "request_observation"},
            ),
            RuntimeDecision(
                "tool_call",
                "write from preserved evidence",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/office-story.md",
                        "content": "# Office Story\n\nEvidence survived compaction.\n",
                    },
                },
                ["world://repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "verify"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "leave a useful office story"
    )

    assert result["status"] == "completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == [
        "file_management.repo_snapshot",
        "file_management.write_file",
    ]
    assert (tmp_path / "docs" / "office-story.md").is_file()


def test_agent_loop_reexecutes_repeated_observation_tool_like_pi(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "repeat observe",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "inspect anti-thrash"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "leave a useful office story"
    )

    assert result["status"] == "completed"
    assert result["reason"] == "observation_only_completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(tool_events) == 2


def test_agent_loop_reprompts_after_non_executable_plan_payload(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("compact", "need another reasoning turn", {}),
            RuntimeDecision(
                "tool_call",
                "write after model loop guidance",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/repaired.md",
                        "content": "# Repaired\n\nExecutable tool call after runtime guidance.\n",
                    },
                },
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "leave a useful record"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "repaired.md").is_file()
    assert any("Model requested compaction" in item for item in provider.prompts[2]["state"]["context_summary"]["progress"])


def test_agent_loop_preserves_world_refs_when_gateway_guard_runs_after_hermes_compaction(tmp_path):
    for index in range(90):
        path = tmp_path / "docs" / f"note-{index:03d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("BadgeCo Maya Priya Ken catering " * 30, encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "inspect compacted prompt"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.compressor = RecordingCompressor(max_chars=9_000, threshold=0.45)
    services.gateway_guard = GatewayContextGuard(max_chars=12_000, threshold=1.0)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "leave a useful office story"
    )

    assert result["status"] == "completed"
    assert result["reason"] == "observation_only_completed"
    assert any(record["triggered"] for record in services.compressor.records)
    assert provider.prompts[1]["world"]["world_refs"] == {}
    assert provider.prompts[1]["state"]["latest_tool_results"][0]["evidence_refs"] == [
        "world://file_management.repo_snapshot/latest"
    ]
    assert "world://file_management.repo_snapshot/latest" in json.dumps(
        provider.prompts[1].get("messages", []), sort_keys=True, default=str
    )


def test_agent_loop_allows_exact_rehydration_before_read_file(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "deep-policy.md").write_text(
        ("filler " * 300) + "EXACT CODE: ORCHID-77-BRIDGE\n",
        encoding="utf-8",
    )
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "compact",
                "snapshot preview is truncated; must rehydrate docs/deep-policy.md before writing",
                {
                    "reasoning": ["rehydrate docs/deep-policy.md before writing"],
                },
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision(
                "tool_call",
                "read exact policy",
                {"tool_id": "file_management.read_file", "arguments": {"path": "docs/deep-policy.md"}},
            ),
            RuntimeDecision(
                "tool_call",
                "write after exact rehydration",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/ops-handoff.md",
                        "content": "# Handoff\n\nEmergency code: ORCHID-77-BRIDGE\n",
                    },
                },
                ["world://file_management.repo_snapshot/latest"],
            ),
            RuntimeDecision("finalize", "verify"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "create handoff with exact emergency code from docs/deep-policy.md"
    )

    assert result["status"] == "completed"
    assert "ORCHID-77-BRIDGE" in (tmp_path / "docs" / "ops-handoff.md").read_text(encoding="utf-8")
    read_events = [
        event
        for event in result["events"]
        if event["event_type"] == "ToolCallCompleted"
        and event["payload"]["tool_id"] == "file_management.read_file"
    ]
    assert len(read_events) == 1
    world_events = [
        event["payload"]
        for event in result["events"]
        if event["event_type"] == "WorldRefAdded"
        and event["payload"]["kind"] == "file_management.read_file"
    ]
    assert world_events[0]["arguments"] == {"path": "docs/deep-policy.md"}


def test_agent_loop_rejects_malformed_tool_call_without_active_tools(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "missing tool id", {"next_step": "observe"}),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "no matching skill"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "malformed_tool_call"
    assert result["message"] == "tool_call decision missing tool_id"


def test_agent_loop_fails_when_max_turns_exceeded(tmp_path):
    services = create_appv22_services(
        root_path=tmp_path,
        provider=RecordingProvider(RuntimeDecision("pause", "not reached")),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=0).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "max_turns_exceeded"


def test_agent_loop_guards_actual_provider_bound_prompt_and_persists_summary(tmp_path):
    provider = RecordingProvider(RuntimeDecision("pause", "stop after prompt inspection"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = PromptMutatingGuard()
    services.compressor = SummaryInjectingCompressor()

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record"
    )

    assert provider.prompts[0]["agent"]["guard_marker"] == "actual-provider-context"
    assert any(
        message.get("summary", {}).get("goals") == ["persisted summary"]
        for message in provider.prompts[0]["messages"]
    )
    summary_events = [event for event in result["events"] if event["event_type"] == "ContextSummaryUpdated"]
    assert summary_events
    assert summary_events[0]["payload"]["goals"] == ["persisted summary"]


def test_agent_loop_default_context_governance_compacts_oversized_provider_prompt(tmp_path):
    raw_marker = "RAW_PROVIDER_PROMPT_LEAK_SENTINEL_" + ("x" * 35_000)
    provider = RecordingProvider(RuntimeDecision("pause", "stop after prompt inspection"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[OversizedPromptExtension(raw_marker)],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "workspace cleanup"
    )

    provider_payload = json.dumps(provider.prompts[0], sort_keys=True, default=str)
    assert raw_marker not in provider_payload
    assert [skill["skill_id"] for skill in provider.prompts[0]["skills"]] == ["oversized_prompt.active"]
    assert any(message.get("name") == "context_summary" for message in provider.prompts[0]["messages"])


def test_dual_context_compacts_large_world_context_and_carries_summary_to_next_turn(tmp_path):
    raw_marker = "RAW_DUAL_CONTEXT_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(80):
        (noisy_root / f"{raw_marker}_{index:03d}_workspace_note.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe oversized workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "stop after compacted prompt inspection"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_500, threshold=0.50)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"
    assert len(provider.prompts) == 2
    first_prompt_payload = json.dumps(provider.prompts[0], sort_keys=True, default=str)
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    assert raw_marker not in first_prompt_payload
    assert raw_marker not in second_prompt_payload
    assert provider.prompts[1]["world"]["world_refs"] == {}
    assert provider.prompts[1]["state"]["latest_tool_results"][0]["tool_id"] == "file_management.repo_snapshot"
    assert provider.prompts[1]["state"]["latest_tool_results"][0]["evidence_refs"] == [
        "world://file_management.repo_snapshot/latest"
    ]
    assert any(message.get("name") == "context_summary" for message in provider.prompts[1]["messages"])
    summary_events = [event for event in result["events"] if event["event_type"] == "ContextSummaryUpdated"]
    assert summary_events
    assert any(event["payload"].get("evidence_refs") for event in summary_events)


def test_dual_context_preserves_compact_observation_contract(tmp_path):
    raw_marker = "RAW_OBSERVATION_CONTRACT_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(100):
        (noisy_root / f"{raw_marker}_{index:03d}.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "inspect compacted prompt"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_800, threshold=0.50)

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "make this workspace sane and keep a record"
    )

    assert len(provider.prompts) == 2
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    assert raw_marker not in second_prompt_payload
    assert provider.prompts[1]["world"]["world_refs"] == {}
    assert provider.prompts[1]["state"]["latest_tool_results"][0]["tool_id"] == "file_management.repo_snapshot"
    assert provider.prompts[1]["state"]["latest_tool_results"][0]["evidence_refs"] == [
        "world://file_management.repo_snapshot/latest"
    ]
    assert any(message.get("name") == "context_summary" for message in provider.prompts[1]["messages"])
    contracts = [
        skill.get("observation_contract")
        for skill in provider.prompts[1]["skills"]
        if isinstance(skill.get("observation_contract"), dict)
    ]
    assert any("file_management.repo_snapshot" in contract["evidence_kinds"] for contract in contracts)
    assert any(contract["preferred_tool_id"] == "file_management.repo_snapshot" for contract in contracts)


def test_dual_context_allows_tool_rehydration_after_compaction(tmp_path):
    raw_marker = "RAW_REHYDRATION_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(120):
        (noisy_root / f"{raw_marker}_{index:03d}_workspace_note.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe oversized workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "rehydrate exact workspace evidence from compacted summary",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "stop after rehydration proof"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_500, threshold=0.50)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "make this workspace sane and recover exact repo details if needed"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"
    assert len(provider.prompts) == 3
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    third_prompt_payload = json.dumps(provider.prompts[2], sort_keys=True, default=str)
    assert raw_marker not in second_prompt_payload
    assert raw_marker not in third_prompt_payload
    summary_messages = [
        message for message in provider.prompts[1]["messages"] if message.get("name") == "context_summary"
    ]
    assert summary_messages
    assert summary_messages[0]["summary"]["evidence_refs"]
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(tool_events) == 2
    assert all(event["payload"]["tool_id"] == "file_management.repo_snapshot" for event in tool_events)


def test_agent_loop_converts_malformed_decision_to_failed_result(tmp_path):
    services = create_appv22_services(
        root_path=tmp_path,
        provider=MalformedDecisionProvider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "runtime_loop_error"
    assert result["error_type"] == "runtime_exception"
    assert result["message"] == "runtime loop failed before a safe decision was available"


def test_agent_loop_has_no_plan_checkpoint_or_planner_cardinality_path(tmp_path):
    provider = RecordingProvider(RuntimeDecision("compact", "model requested context compaction"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension(), OneSkillExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record workspace"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "max_turns_exceeded"


def test_agent_loop_allows_multiple_workspace_actions_and_tools_enforce_safety(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("original\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "write useful new note",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/handoff.md", "content": "handoff\n"},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "unsafe second write should be denied by tool",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/existing.md", "content": "changed\n"},
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "create one handoff note"
    )

    assert result["status"] == "completed"
    assert result["reason"] == "tool_loop_completed"
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == ["file_management.write_file"]
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    assert [event["payload"]["tool_id"] for event in denied_events] == ["file_management.write_file"]
    assert (tmp_path / "docs" / "handoff.md").read_text(encoding="utf-8") == "handoff\n"
    assert (tmp_path / "docs" / "existing.md").read_text(encoding="utf-8") == "original\n"


def test_agent_loop_uses_extension_owned_tool_result_guidance(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "call inactive tool",
                {"tool_id": "missing.tool", "arguments": {}},
            ),
            RuntimeDecision("pause", "stop"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[GuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run("workspace")

    assert any(
        "extension-owned denial guidance" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_agent_loop_records_extension_tool_guidance_as_standalone_risk_after_long_denial(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "unsafe publish with oversized arguments",
                {"tool_id": "guarded.publish", "arguments": {"text": "bad " + ("noise " * 300)}},
            ),
            RuntimeDecision("pause", "inspect guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[BeforeToolGuardExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run("publish guarded text")

    risks = provider.prompts[1]["state"]["context_summary"].get("open_risks", [])
    assert "pre-tool guard says retry guarded.publish with safe text." in risks


def test_agent_loop_records_tool_payload_errors_as_standalone_repair_guidance(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "publish note missing exact phrase",
                {"tool_id": "validation.publish", "arguments": {"note": "Lab LAB-44 is safe."}},
            ),
            RuntimeDecision("pause", "inspect generic repair guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ValidationErrorGuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "validation-guidance publish note"
    )

    risks = provider.prompts[1]["state"]["context_summary"].get("open_risks", [])
    assert "validation.publish reported error: note_missing_terms:safe lab note" in risks


def test_agent_loop_repeated_denial_records_repeated_tool_results(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "publish note missing exact phrase",
                {"tool_id": "validation.publish", "arguments": {"note": "Lab LAB-44 is safe."}},
            ),
            RuntimeDecision(
                "tool_call",
                "repeat same denied call",
                {"tool_id": "validation.publish", "arguments": {"note": "Lab LAB-44 is safe."}},
            ),
            RuntimeDecision("pause", "inspect duplicate denial guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ValidationErrorGuidanceExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "validation-guidance publish note"
    )

    risks = provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    duplicate_guidance = "\n".join(risk for risk in risks if "validation.publish" in risk)
    assert "validation.publish reported error: note_missing_terms:safe lab note" in duplicate_guidance
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    assert [event["payload"]["tool_id"] for event in denied_events] == [
        "validation.publish",
        "validation.publish",
    ]


def test_agent_loop_failed_tool_guidance_steers_named_alternate_tool(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "call broken recovery source",
                {"tool_id": "alternate_recovery.broken", "arguments": {"key": "alpha"}},
            ),
            RuntimeDecision("pause", "inspect alternate recovery guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[AlternateRecoveryExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "alternate-recovery exercise failure"
    )

    risks = provider.prompts[1]["state"]["context_summary"].get("open_risks", [])
    guidance = "\n".join(risk for risk in risks if "alternate_recovery" in risk)
    assert "Recovery guidance names selected tool alternate_recovery.safe" in guidance
    assert "next decision should call alternate_recovery.safe" in guidance
    assert provider.prompts[1]["state"]["mode"] == "OBSERVE"


def test_agent_loop_records_finalize_guidance_when_model_compacts_after_successful_observation(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "observe first", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("compact", "think after successful observation"),
            RuntimeDecision("pause", "inspect guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[CompactAfterObservationGuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "compact-after-observe publish after lookup"
    )

    risks = provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    assert "Observation is complete; call compact_guidance.publish before finalizing." in risks


def test_agent_loop_records_finalize_guidance_immediately_after_successful_observation(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "observe first", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("pause", "inspect immediate guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[CompactAfterObservationGuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "compact-after-observe publish after lookup"
    )

    risks = provider.prompts[1]["state"]["context_summary"].get("open_risks", [])
    assert "Observation is complete; call compact_guidance.publish before finalizing." in risks


def test_agent_loop_repeated_observe_records_repeated_tool_results(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "observe first", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("tool_call", "duplicate observe", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("pause", "inspect duplicate observe guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[CompactAfterObservationGuidanceExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "compact-after-observe publish after lookup"
    )

    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert [event["payload"]["tool_id"] for event in tool_events] == [
        "compact_guidance.lookup",
        "compact_guidance.lookup",
    ]
    progress = "\n".join(provider.prompts[2]["state"]["context_summary"].get("progress", []))
    assert "Observation already satisfied" not in progress


def test_agent_loop_moves_to_act_when_finalize_guidance_requires_tool_call(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "observe first", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("finalize", "premature finalize"),
            RuntimeDecision("pause", "inspect mode"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[CompactAfterObservationGuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "compact-after-observe publish after lookup"
    )

    assert provider.prompts[2]["state"]["mode"] == "ACT"
    assert any(
        "Observation is complete; call compact_guidance.publish before finalizing." in item
        for item in provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    )


def test_agent_loop_records_named_finalize_tool_guidance(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "observe first", {"tool_id": "compact_guidance.lookup", "arguments": {}}),
            RuntimeDecision("finalize", "premature finalize"),
            RuntimeDecision("pause", "inspect named finalize guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[CompactAfterObservationGuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "compact-after-observe publish after lookup"
    )

    risks = provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    guidance = "\n".join(risk for risk in risks if "compact_guidance.publish" in risk)
    assert "Finalization guidance names selected tool compact_guidance.publish" in guidance
    assert "next decision should call compact_guidance.publish before finalizing" in guidance


def test_agent_loop_uses_extension_owned_before_tool_call_guard(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "unsafe publish",
                {"tool_id": "guarded.publish", "arguments": {"text": "raw text"}},
            ),
            RuntimeDecision(
                "tool_call",
                "safe publish",
                {"tool_id": "guarded.publish", "arguments": {"text": "safe text"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    extension = BeforeToolGuardExtension()
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[extension],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run("guarded publish")

    assert result["status"] == "completed"
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    world_ref_events = [event for event in result["events"] if event["event_type"] == "WorldRefAdded"]
    assert len(denied_events) == 1
    assert denied_events[0]["payload"]["payload"]["errors"] == ["unsafe_text"]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["payload"]["text"] == "safe text"
    assert world_ref_events[0]["payload"]["kind"] == "guarded.publish"
    assert world_ref_events[0]["payload"]["mutates_world"] is True
    assert any(
        "pre-tool guard says retry guarded.publish with safe text" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert provider.prompts[2]["state"]["mode"] == "ACT"
    assert not any(
        "pre-tool guard says retry guarded.publish with safe text" in item
        or "Recovery guidance names selected tool guarded.publish" in item
        or "This denied pre-tool attempt already satisfies any instruction to exercise a guard or blocked-call path" in item
        for item in provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "guarded.publish: prior failed/denied tool risk resolved by later successful result" in item
        for item in provider.prompts[2]["state"]["context_summary"].get("progress", [])
    )


def test_agent_loop_redacts_denied_argument_values_from_world_refs(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "lookup unsafe evidence",
                {"tool_id": "guard_redaction.lookup", "arguments": {"key": "draft"}},
            ),
            RuntimeDecision(
                "tool_call",
                "try unsafe draft",
                {"tool_id": "guard_redaction.publish", "arguments": {"text": "unsafe denied draft value"}},
            ),
            RuntimeDecision(
                "tool_call",
                "retry safe text",
                {"tool_id": "guard_redaction.publish", "arguments": {"text": "safe replacement"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[GuardRedactionExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=5).run("redact-denied")

    assert result["status"] == "completed"
    assert "unsafe denied draft value" not in json.dumps(provider.prompts[2]["world"]["world_refs"])
    assert "safe replacement" in json.dumps(provider.prompts[2]["state"]["context_summary"])


def test_agent_loop_uses_extension_owned_after_tool_call_transform_before_world_ref(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "fetch redaction payload", {"tool_id": "redaction.fetch", "arguments": {}}),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[AfterToolRedactionExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("redact payload")

    assert result["status"] == "completed"
    serialized = json.dumps(result, sort_keys=True, default=str)
    assert "tok_after_private" not in serialized
    assert "[redacted]" in serialized
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert completed_events[0]["payload"]["payload"]["secret"] == "[redacted]"
    world_refs = result["world_refs"]
    assert list(world_refs.values())[0]["payload"]["secret"] == "[redacted]"


def test_agent_loop_isolates_after_tool_call_hook_exception_without_leak(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "fetch despite hook exception", {"tool_id": "redaction.fetch", "arguments": {}}),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FailingAfterHookExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("hook exception fetch")

    assert result["status"] == "completed"
    serialized = json.dumps(result, sort_keys=True, default=str)
    assert "tok_hook_exception_private" not in serialized
    assert "backend trace" not in serialized
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["tool_id"] == "redaction.fetch"


def test_agent_loop_revalidates_after_tool_call_transform_before_world_ref(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "fetch malformed after hook payload", {"tool_id": "redaction.fetch", "arguments": {}}),
            RuntimeDecision("pause", "stop"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[MalformedAfterHookExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run("malformed after fetch")

    assert result["status"] == "failed"
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    world_ref_events = [event for event in result["events"] if event["event_type"] == "WorldRefAdded"]
    assert len(denied_events) == 1
    assert denied_events[0]["payload"]["status"] == "failed"
    assert denied_events[0]["payload"]["payload"]["reason"] == "after_tool_result_schema_invalid"
    assert "missing_result:public" in denied_events[0]["payload"]["payload"]["errors"]
    assert world_ref_events == []


def test_agent_loop_ignores_finalize_guidance_hook_exception_without_leak(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("tool_call", "fetch before finalize exception", {"tool_id": "redaction.fetch", "arguments": {}}),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FailingFinalizeHookExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("finalize hook exception fetch")

    assert result["status"] == "completed"
    serialized = json.dumps(result, sort_keys=True, default=str)
    assert "tok_finalize_private" not in serialized
    assert "backend trace" not in serialized


def test_agent_loop_uses_extension_owned_finalize_guidance(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "write once",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/result.md", "content": "result\n"},
                },
            ),
            RuntimeDecision("finalize", "too early"),
            RuntimeDecision("pause", "stop"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension(), GuidanceExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("workspace write")

    assert any(
        "extension-owned finalize guidance" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_agent_loop_allows_retry_of_retryable_failed_tool_result(tmp_path):
    extension = RetryableFailureExtension()
    retry_call = RuntimeDecision(
        "tool_call",
        "fetch",
        {"tool_id": "retryable.fetch", "arguments": {"key": "DEP-515"}},
    )
    provider = SequenceProvider(
        [
            retry_call,
            retry_call,
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[extension],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "retryable fetch"
    )

    assert result["status"] == "completed"
    assert extension.calls == 2
    assert [
        item["status"]
        for item in result["tool_results"]
        if item["tool_id"] == "retryable.fetch"
    ] == ["failed", "completed"]


def test_file_tool_uses_request_context_to_deny_overwrite_when_user_forbids_it(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("original\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "model tries overwrite despite user constraint",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/existing.md",
                        "content": "changed\n",
                        "overwrite": True,
                    },
                },
            ),
            RuntimeDecision(
                "tool_call",
                "recover with alternate path",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/existing-1.md",
                        "content": "changed\n",
                    },
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=5).run(
        "write the update but do not overwrite anything"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "existing.md").read_text(encoding="utf-8") == "original\n"
    assert (tmp_path / "docs" / "existing-1.md").read_text(encoding="utf-8") == "changed\n"
    denied = [item for item in result["tool_results"] if item["status"] == "denied"]
    assert denied[0]["payload"]["errors"] == ["existing_file_requires_overwrite:docs/existing.md"]
    assert denied[0]["payload"]["suggested_path"] == "docs/existing-1.md"


def test_file_management_extension_blocks_finalize_when_record_was_requested_but_not_written(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "standup.md").write_text("standup\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "move clear note",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "notes/standup.md", "destination": "docs/standup.md"},
                },
            ),
            RuntimeDecision("finalize", "done without record"),
            RuntimeDecision(
                "tool_call",
                "write requested record",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/workspace_manifest.json",
                        "content": '{"moves":[{"source":"notes/standup.md","destination":"docs/standup.md"}]}\n',
                    },
                },
            ),
            RuntimeDecision("finalize", "done with record"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "clean this up safely and keep a record"
    )

    assert result["status"] == "completed"
    assert result["reason"] == "tool_loop_completed"
    assert (tmp_path / "docs" / "workspace_manifest.json").exists()
    assert any(
        "record was requested" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "docs/workspace_manifest" in item and "file_management.write_file" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_file_management_extension_blocks_finalize_when_record_omits_changed_paths(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "junk.log").write_text("junk\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "delete junk",
                {"tool_id": "file_management.delete_file", "arguments": {"path": "tmp/junk.log"}},
            ),
            RuntimeDecision(
                "tool_call",
                "write incomplete record",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/workspace_manifest.json", "content": '{"deleted":[]}\n'},
                },
            ),
            RuntimeDecision("finalize", "done without deleted path"),
            RuntimeDecision(
                "tool_call",
                "repair record",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/workspace_manifest.json",
                        "content": '{"deleted":["tmp/junk.log"]}\n',
                        "overwrite": True,
                    },
                },
            ),
            RuntimeDecision("finalize", "done with deleted path"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=7).run(
        "remove junk and keep a record"
    )

    assert result["status"] == "completed"
    assert "tmp/junk.log" in (tmp_path / "docs" / "workspace_manifest.json").read_text(encoding="utf-8")
    assert any(
        "missing changed paths" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "file_management.write_file" in item and "docs/workspace_manifest.json" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert provider.prompts[3]["state"]["mode"] == "ACT"


def test_file_management_extension_blocks_finalize_when_manifest_winner_was_not_moved(tmp_path):
    (tmp_path / "notes" / "team").mkdir(parents=True)
    (tmp_path / "projects" / "alpha").mkdir(parents=True)
    (tmp_path / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "tmp" / "other").mkdir(parents=True)
    (tmp_path / "tmp" / "session").mkdir(parents=True)
    (tmp_path / "notes" / "team" / "standup.md").write_text("move note\n", encoding="utf-8")
    (tmp_path / "projects" / "alpha" / "spec.md").write_text("winner spec\n", encoding="utf-8")
    (tmp_path / "projects" / "beta" / "spec.md").write_text("held spec\n", encoding="utf-8")
    (tmp_path / "tmp" / "other" / "run.log").write_text("winner log\n", encoding="utf-8")
    (tmp_path / "tmp" / "session" / "run.log").write_text("held log\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "move clear note",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "notes/team/standup.md", "destination": "docs/standup.md"},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "write incomplete manifest with winners",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/workspace_manifest.json",
                        "content": json.dumps(
                            {
                                "moves": [
                                    {
                                        "source": "notes/team/standup.md",
                                        "destination": "docs/standup.md",
                                    }
                                ],
                                "held": [
                                    {
                                        "source": "projects/beta/spec.md",
                                        "reason": "docs/spec.md is claimed by projects/alpha/spec.md",
                                    },
                                    {
                                        "source": "tmp/session/run.log",
                                        "reason": "artifacts/logs/run.log is claimed by tmp/other/run.log",
                                    },
                                ],
                                "collisions": [
                                    {
                                        "basename": "spec.md",
                                        "sources": ["projects/alpha/spec.md", "projects/beta/spec.md"],
                                        "winner": "projects/alpha/spec.md",
                                    },
                                    {
                                        "basename": "run.log",
                                        "sources": ["tmp/other/run.log", "tmp/session/run.log"],
                                        "winner": "tmp/other/run.log",
                                    },
                                ],
                            }
                        ),
                    },
                },
            ),
            RuntimeDecision("finalize", "done without winner moves"),
            RuntimeDecision(
                "tool_call",
                "move winning spec",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "projects/alpha/spec.md", "destination": "docs/spec.md"},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "move winning log",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "tmp/other/run.log", "destination": "artifacts/logs/run.log"},
                },
            ),
            RuntimeDecision("finalize", "done with winners moved"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=9).run(
        "Can you clean this mess up safely and keep a record?"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "spec.md").read_text(encoding="utf-8") == "winner spec\n"
    assert (tmp_path / "artifacts" / "logs" / "run.log").read_text(encoding="utf-8") == "winner log\n"
    assert any(
        "manifest names unresolved winning sources" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "file_management.move_file" in item and "projects/alpha/spec.md" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_file_management_extension_blocks_finalize_when_snapshot_winner_was_not_moved(tmp_path):
    (tmp_path / "notes" / "team").mkdir(parents=True)
    (tmp_path / "projects" / "alpha").mkdir(parents=True)
    (tmp_path / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "tmp" / "other").mkdir(parents=True)
    (tmp_path / "tmp" / "session").mkdir(parents=True)
    (tmp_path / "notes" / "team" / "standup.md").write_text("Move this standup note into docs.\n", encoding="utf-8")
    (tmp_path / "projects" / "alpha" / "spec.md").write_text("Move this alpha spec into docs/spec.md.\n", encoding="utf-8")
    (tmp_path / "projects" / "beta" / "spec.md").write_text("Hold this beta spec because docs/spec.md is claimed.\n", encoding="utf-8")
    (tmp_path / "tmp" / "other" / "run.log").write_text("Move this run log into artifacts/logs.\n", encoding="utf-8")
    (tmp_path / "tmp" / "session" / "run.log").write_text(
        "Hold this log because artifacts/logs/run.log is claimed.\n", encoding="utf-8"
    )
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "move clear note",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "notes/team/standup.md", "destination": "docs/standup.md"},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "move winning log",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "tmp/other/run.log", "destination": "artifacts/logs/run.log"},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "write manifest missing spec winner",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/workspace_manifest.json",
                        "content": json.dumps(
                            {
                                "moves": [
                                    {
                                        "source": "notes/team/standup.md",
                                        "destination": "docs/standup.md",
                                    },
                                    {
                                        "source": "tmp/other/run.log",
                                        "destination": "artifacts/logs/run.log",
                                    },
                                ],
                                "held": [
                                    {
                                        "source": "projects/beta/spec.md",
                                        "reason": "docs/spec.md is claimed by projects/alpha/spec.md",
                                    },
                                    {
                                        "source": "tmp/session/run.log",
                                        "reason": "artifacts/logs/run.log is claimed by tmp/other/run.log",
                                    },
                                ],
                                "collisions": [
                                    {
                                        "basename": "run.log",
                                        "sources": ["tmp/other/run.log", "tmp/session/run.log"],
                                        "winner": "tmp/other/run.log",
                                    }
                                ],
                            }
                        ),
                    },
                },
            ),
            RuntimeDecision("finalize", "done without spec winner"),
            RuntimeDecision(
                "tool_call",
                "move winning spec",
                {
                    "tool_id": "file_management.move_file",
                    "arguments": {"source": "projects/alpha/spec.md", "destination": "docs/spec.md"},
                },
            ),
            RuntimeDecision("finalize", "done with spec winner"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=10).run(
        "Can you clean this mess up safely and keep a record?"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "spec.md").read_text(encoding="utf-8") == "Move this alpha spec into docs/spec.md.\n"
    assert not (tmp_path / "projects" / "alpha" / "spec.md").exists()
    assert any(
        "snapshot evidence contains unresolved winning sources" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "file_management.move_file" in item and "projects/alpha/spec.md" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_file_management_extension_blocks_finalize_when_manifest_deletion_was_not_deleted(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "tmp" / "junk.log").write_text("delete this junk\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "write manifest before deleting",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {
                        "path": "docs/workspace_manifest.json",
                        "content": json.dumps({"deletions": [{"path": "tmp/junk.log"}]}),
                    },
                },
            ),
            RuntimeDecision("finalize", "done without delete"),
            RuntimeDecision(
                "tool_call",
                "delete manifest-listed junk",
                {"tool_id": "file_management.delete_file", "arguments": {"path": "tmp/junk.log"}},
            ),
            RuntimeDecision("finalize", "done with delete"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=6).run(
        "remove obvious junk and keep a record"
    )

    assert result["status"] == "completed"
    assert not (tmp_path / "tmp" / "junk.log").exists()
    assert any(
        "manifest names unresolved deletions" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )
    assert any(
        "file_management.delete_file" in item and "tmp/junk.log" in item
        for prompt in provider.prompts
        for item in prompt["state"]["context_summary"].get("open_risks", [])
    )


def test_agent_loop_records_finalize_guidance_immediately_after_completed_tool_when_work_remains(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "junk.log").write_text("junk\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "delete junk",
                {"tool_id": "file_management.delete_file", "arguments": {"path": "tmp/junk.log"}},
            ),
            RuntimeDecision(
                "tool_call",
                "write incomplete record",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/workspace_manifest.json", "content": '{"deleted":[]}\n'},
                },
            ),
            RuntimeDecision("pause", "inspect immediate guidance"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "remove junk and keep a record"
    )

    risks = provider.prompts[2]["state"]["context_summary"].get("open_risks", [])
    assert any("tmp/junk.log" in item and "file_management.write_file" in item for item in risks)
    assert provider.prompts[2]["state"]["mode"] == "ACT"


def test_agent_loop_exposes_selected_tool_argument_schemas_to_provider(tmp_path):
    provider = SequenceProvider([RuntimeDecision("pause", "inspect prompt")])
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "copy a file and preserve the source"
    )

    copy_definition = next(
        tool
        for tool in provider.prompts[0]["tool_definitions"]
        if tool["tool_id"] == "file_management.copy_file"
    )
    assert "preserve_source" in copy_definition["argument_schema"]["properties"]


def test_agent_loop_records_repeated_denied_tool_call_and_allows_recovery(tmp_path):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "payroll.env").write_text("TOKEN=secret\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "try protected read",
                {"tool_id": "file_management.read_file", "arguments": {"path": "secrets/payroll.env"}},
            ),
            RuntimeDecision(
                "tool_call",
                "repeat protected read",
                {"tool_id": "file_management.read_file", "arguments": {"path": "secrets/payroll.env"}},
            ),
            RuntimeDecision(
                "tool_call",
                "recover with public note",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/safe-note.md", "content": "public onboarding\n"},
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=5).run(
        "check protected payroll if possible, then write a public note"
    )

    assert result["status"] == "completed"
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(denied_events) == 2
    assert denied_events[0]["payload"]["arguments"] == {"path": "secrets/payroll.env"}
    assert denied_events[1]["payload"]["arguments"] == {"path": "secrets/payroll.env"}
    assert [event["payload"]["tool_id"] for event in completed_events] == ["file_management.write_file"]
    assert (tmp_path / "docs" / "safe-note.md").read_text(encoding="utf-8") == "public onboarding\n"
    assert "TOKEN=secret" not in (tmp_path / "docs" / "safe-note.md").read_text(encoding="utf-8")


def test_agent_loop_keeps_action_only_extension_active_for_finalize_guidance(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": "observe_only.lookup", "arguments": {"key": "handoff"}},
            ),
            RuntimeDecision("finalize", "premature finalize"),
            RuntimeDecision(
                "tool_call",
                "publish after guidance",
                {"tool_id": "action_only.publish", "arguments": {"message": "handoff ready"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[ObserveOnlyExtension(), ActionOnlyExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=5).run(
        "prepare the handoff"
    )

    assert result["status"] == "completed"
    completed_tools = [
        event["payload"]["tool_id"]
        for event in result["events"]
        if event["event_type"] == "ToolCallCompleted"
    ]
    assert completed_tools == ["observe_only.lookup", "action_only.publish"]
    assert any(
        event["event_type"] == "ContextSummaryUpdated"
        and "Observation exists but publish action is missing; call action_only.publish."
        in json.dumps(event["payload"])
        for event in result["events"]
    )


def test_agent_loop_records_repeated_malformed_tool_arguments_and_allows_recovery(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "malformed multi-read",
                {
                    "tool_id": "file_management.read_file",
                    "arguments": {"paths": ["README.md", "docs/context.md"]},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "repeat malformed multi-read",
                {
                    "tool_id": "file_management.read_file",
                    "arguments": {"paths": ["README.md", "docs/context.md"]},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "recover with public note",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "docs/recovery-note.md", "content": "public recovery note\n"},
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=5).run(
        "write a public recovery note after checking files if possible"
    )

    assert result["status"] == "completed"
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(denied_events) == 2
    assert denied_events[0]["payload"]["payload"]["errors"] == ["missing_argument:path"]
    assert denied_events[1]["payload"]["payload"]["errors"] == ["missing_argument:path"]
    assert [event["payload"]["tool_id"] for event in completed_events] == ["file_management.write_file"]
    assert (tmp_path / "docs" / "recovery-note.md").read_text(encoding="utf-8") == "public recovery note\n"


def test_agent_loop_surfaces_malformed_edit_guidance_and_allows_corrected_edit(tmp_path):
    target = tmp_path / "src" / "agents" / "planner.py"
    target.parent.mkdir(parents=True)
    target.write_text('class Planner:\n    def plan(self):\n        return "plan"\n', encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "malformed edit payload",
                {
                    "tool_id": "file_management.edit_file",
                    "arguments": {"path": "src/agents/planner.py", "edits": ["oldText", "newText"]},
                },
            ),
            RuntimeDecision(
                "tool_call",
                "corrected edit payload",
                {
                    "tool_id": "file_management.edit_file",
                    "arguments": {
                        "path": "src/agents/planner.py",
                        "edits": [{"oldText": 'return "plan"', "newText": 'return "planned"'}],
                    },
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "fix src/agents/planner.py so plan returns planned"
    )

    assert result["status"] == "completed"
    assert 'return "planned"' in target.read_text(encoding="utf-8")
    denied_events = [event for event in result["events"] if event["event_type"] == "ToolCallDenied"]
    completed_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert denied_events[0]["payload"]["payload"]["errors"] == [
        "invalid_argument_type:edits.0:expected_object",
        "invalid_argument_type:edits.1:expected_object",
    ]
    assert [event["payload"]["tool_id"] for event in completed_events] == ["file_management.edit_file"]
    second_prompt_text = json.dumps(provider.prompts[1])
    assert "array of objects" in second_prompt_text
    assert "corrected arguments" in second_prompt_text
    assert "docs/workspace_manifest.json" not in second_prompt_text


def test_agent_loop_does_not_persist_raw_model_reason_or_argument_content(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "backend trace leaked tok_loop_private_552 LOOP_SECRET",
                {
                    "tool_id": "file_management.write_file",
                    "arguments": {"path": "note.md", "content": "do not expose tok_loop_private_552"},
                },
            ),
            RuntimeDecision("finalize", "done"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run("write a note")

    proposed = [event for event in result["events"] if event["event_type"] == "DecisionProposed"]
    assert proposed
    serialized = json.dumps(proposed, sort_keys=True)
    assert "tok_loop_private_552" not in serialized
    assert "LOOP_SECRET" not in serialized
    assert "backend trace" not in serialized
    assert proposed[0]["payload"]["reason"] == "model_decision"
    assert proposed[0]["payload"]["payload"]["argument_keys"] == ["content", "path"]


def test_reducer_deep_copies_payloads_stored_in_state():
    state = AgentState("session", "run", RequestEnvelope("request", "goal", "/tmp/root"))
    payload = {"ref_id": "world://x", "nested": {"items": ["original"]}}

    apply_event(state, RuntimeEvent("WorldRefAdded", payload))
    payload["nested"]["items"].append("mutated")

    assert state.world_refs["world://x"]["nested"]["items"] == ["original"]
