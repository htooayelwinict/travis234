from __future__ import annotations

from pathlib import Path
import unittest

from appv22.runtime.agent_loop import AppV22AgentRuntime
from appv22.runtime.decisions import RuntimeDecision
from appv22.runtime.services import AppV22Services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope
from appv22_ui.live import LiveEventBuffer
from appv22_ui.renderers.plain import PlainRenderer


class LiveUIEventSinkTests(unittest.TestCase):
    def test_runtime_event_sink_receives_event_dict_after_state_reduction(self) -> None:
        captured: list[dict] = []
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
            event_sink=captured.append,
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))

        runtime._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))

        self.assertEqual(state.mode, "THINK")
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["event_type"], "ModeChanged")
        self.assertEqual(captured[0]["payload"]["mode"], "THINK")

    def test_live_event_buffer_renders_incremental_events(self) -> None:
        buffer = LiveEventBuffer()

        rendered = buffer.on_event({"event_type": "ModeChanged", "payload": {"mode": "OBSERVE"}})

        self.assertIn("LIVE AGENT LOOP", rendered)
        self.assertIn("mode: OBSERVE", rendered)

    def test_finalize_payload_message_becomes_public_assistant_message(self) -> None:
        runtime = AppV22AgentRuntime(root_path=Path("."), services=_unused_services())
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "hi", "."))

        runtime._route(
            state,
            RuntimeDecision(
                kind="finalize",
                reason="Greeting completed.",
                payload={"message": "Hello. How can I help?"},
            ),
            _resolved(),
        )

        self.assertTrue(state.terminal)
        self.assertEqual(state.result["assistant_message"], "Hello. How can I help?")

    def test_plain_renderer_shows_public_assistant_message(self) -> None:
        rendered = PlainRenderer().render(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {},
                "assistant_message": "Hello. How can I help?",
                "events": [],
            }
        )

        self.assertIn("assistant: Hello. How can I help?", rendered)


def _unused_services() -> AppV22Services:
    return AppV22Services(
        root_path=Path("."),
        provider=object(),
        extension_registry=_ExtensionRegistry(),
        tool_registry=object(),
        broker=object(),
        context_selector=object(),
        prompt_builder=object(),
        gateway_guard=object(),
        compressor=object(),
    )


class _ExtensionRegistry:
    pass


class _Resolved:
    extension_ids: list[str] = []


def _resolved() -> _Resolved:
    return _Resolved()


if __name__ == "__main__":
    unittest.main()
