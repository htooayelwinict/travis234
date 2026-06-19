from __future__ import annotations

import json
import io
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.runtime.agent_loop import AppV22AgentRuntime
from appv22.runtime.services import create_appv22_services
from appv22_ui.session import SessionStore
from appv22_ui.context_manager import TuiContextManager
from appv22_ui.renderers.tui import TuiRenderer
from appv22_ui.tui_app import AppV22Tui
from appv22_ui.tui_layout import render_tui
from appv22_ui.tui_state import ConversationLine, TuiState


class TuiAppTests(unittest.TestCase):
    def test_tui_layout_uses_pi_stream_with_hermes_compaction_status(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))
        state.add_user("hi")
        state.conversation_summary = "- User preference/context: stay inside Pi + Hermes design."
        state.ui_context_metrics = {
            "tokens_before": 1200,
            "compaction_count": 1,
            "summary_source": "api",
            "hot_lines": 4,
        }
        state.apply_event({"event_type": "ModeChanged", "payload": {"mode": "THINK"}})
        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {},
                "assistant_message": "Hello.",
                "events": [],
            }
        )

        rendered = render_tui(state)

        self.assertNotIn("CONVERSATION", rendered)
        self.assertNotIn("PI AGENT LOOP", rendered)
        self.assertNotIn("HERMES CONTEXT", rendered)
        self.assertIn("> hi", rendered)
        self.assertIn("Hello.", rendered)
        self.assertIn("[compaction]", rendered)
        self.assertIn("compacted from 1,200 tokens", rendered)
        self.assertIn("context refs 0 compact 1 source api hot 4", rendered)

    def test_tui_state_tracks_world_refs_and_context(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))

        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {"world://repo/snapshot": {"summary": "snapshot"}},
                "context_summary": {"blockers": ["approval required: risk"], "progress": ["snapshot"]},
                "events": [],
            }
        )

        self.assertEqual(state.session_id, "sess_test")
        self.assertEqual(state.world_ref_count, 1)
        self.assertEqual(state.context_summary["blockers"], ["approval required: risk"])

    def test_tui_state_strips_turn_local_action_guidance_from_live_result(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))

        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {
                    "world://file_management.edit_file/current": {
                        "kind": "file_management.edit_file",
                        "summary": "file_management.edit_file result",
                    }
                },
                "context_summary": {
                    "progress": ["file_management.edit_file: file_management.edit_file result"],
                    "blockers": [
                        "Current source file evidence has been read for the requested existing-file edit; the next decision must be a tool_call to file_management.edit_file for tests/test_calculator.py before finalizing.",
                        "Finalization guidance names selected tool file_management.edit_file; the next decision should call file_management.edit_file before finalizing.",
                    ],
                    "evidence_refs": ["world://file_management.edit_file/current"],
                },
                "events": [],
            }
        )

        self.assertEqual(state.context_summary["blockers"], [])
        self.assertEqual(state.context_summary["evidence_refs"], ["world://file_management.edit_file/current"])

    def test_tui_layout_keeps_multiline_assistant_text_in_stream_order(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))
        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {},
                "assistant_message": "Line one\nLine two\nLine three",
                "events": [],
            }
        )

        rendered = render_tui(state)

        self.assertIn("Line one", rendered)
        self.assertIn("Line two", rendered)
        self.assertIn("Line three", rendered)

    def test_noninteractive_tui_renderer_uses_pi_stream_not_panels(self) -> None:
        rendered = TuiRenderer().render(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {"world://file_management.read_file/ok": {"kind": "file_management.read_file"}},
                "context_summary": {"progress": ["read completed"]},
                "assistant_message": "Done.",
                "events": [{"event_type": "ContextSummaryUpdated", "payload": {"blockers": []}}],
                "usage": {"context": {"model_calls": 1}},
            }
        )

        self.assertNotIn("APPV22 SESSION", rendered)
        self.assertNotIn("MODEL / TOOL METRICS", rendered)
        self.assertNotIn("HERMES CONTEXT", rendered)
        self.assertNotIn("PI-STYLE AGENT LOOP", rendered)
        self.assertIn("status completed", rendered)
        self.assertIn("Done.", rendered)
        self.assertIn("[compaction]", rendered)

    def test_tui_context_hides_stale_inactive_tool_risks_after_progress(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))
        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {
                    "progress": ["calendar.lookup: calendar.lookup result"],
                    "blockers": [
                        "list_dir reported error: inactive_tool:list_dir",
                        "list_dir request was denied for argument keys []; treat that denial as evidence.",
                    ],
                },
                "events": [],
            }
        )

        rendered = render_tui(state)

        self.assertIn("progress: 1", rendered)
        self.assertNotIn("inactive_tool:list_dir", rendered)

    def test_session_store_persists_tui_conversation_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {},
                },
                conversation=[
                    ConversationLine("user", "my name is lewis"),
                    ConversationLine("assistant", "Hello, Lewis."),
                ],
            )

            loaded = TuiState.from_session(Path(tmp), store.load())

        self.assertEqual([line.text for line in loaded.conversation], ["my name is lewis", "Hello, Lewis."])

    def test_session_store_without_extension_does_not_persist_domain_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {
                        "world://file_management.read_file/domain": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "src/agents/facebook_surfer.py"},
                            "payload": {
                                "path": "src/agents/facebook_surfer.py",
                                "content": "class FacebookSurferAgent:\n    pass\n",
                            },
                        }
                    },
                    "context_summary": {
                        "progress": ["file_management.read_file: file_management.read_file result"],
                        "evidence_refs": ["world://file_management.read_file/domain"],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        ref = loaded["world_refs"]["world://file_management.read_file/domain"]
        self.assertEqual(ref["kind"], "file_management.read_file")
        self.assertEqual(ref["arguments"], {"path": "src/agents/facebook_surfer.py"})
        self.assertNotIn("payload", ref)
        self.assertIn("world://file_management.read_file/domain", loaded["context_summary"]["evidence_refs"])

    def test_session_store_uses_extension_sanitizer_for_domain_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp), extensions=(FileManagementExtension(),))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {
                        "world://file_management.read_file/domain": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "src/agents/facebook_surfer.py"},
                            "payload": {
                                "path": "src/agents/facebook_surfer.py",
                                "content": "class FacebookSurferAgent:\n    pass\n",
                            },
                        }
                    },
                    "context_summary": {
                        "progress": ["file_management.read_file: file_management.read_file result"],
                        "evidence_refs": ["world://file_management.read_file/domain"],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        payload = loaded["world_refs"]["world://file_management.read_file/domain"]["payload"]
        self.assertEqual(payload["path"], "src/agents/facebook_surfer.py")
        self.assertEqual(payload["line_count"], 2)

    def test_tui_runtime_prompt_carries_recent_conversation_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.conversation = [
                ConversationLine("user", "my name is lewis"),
                ConversationLine("assistant", "Hello, Lewis."),
                ConversationLine("user", "who am i"),
            ]

            prompt = app._runtime_prompt("who am i")

        self.assertIn("RECENT UI TURNS", prompt)
        self.assertIn("user: my name is lewis", prompt)
        self.assertIn("[CURRENT USER REQUEST]\nwho am i", prompt)

    def test_tui_runtime_prompt_preserves_history_until_compaction_happens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.conversation = [
                ConversationLine("user" if index % 2 == 0 else "assistant", f"line {index}")
                for index in range(8)
            ]
            app.state.add_user("continue")

            app._runtime_prompt("continue")

        self.assertEqual(len(app.state.conversation), 9)
        self.assertEqual(app.state.ui_context_metrics["compaction_count"], 0)

    def test_tui_context_manager_compacts_old_conversation_before_prompt(self) -> None:
        lines = []
        for index in range(20):
            lines.append(ConversationLine("user", f"user turn {index} my name is lewis"))
            lines.append(ConversationLine("assistant", f"assistant turn {index}"))
        manager = TuiContextManager(max_hot_lines=4, compact_after_lines=8)

        prompt, compacted_lines, summary = manager.prepare_prompt(
            current_user_message="who am i",
            conversation=lines,
            existing_summary="",
        )

        self.assertLessEqual(len(compacted_lines), 4)
        self.assertIn("UI SESSION SUMMARY - REFERENCE ONLY", prompt)
        self.assertIn("Lewis", prompt)
        self.assertIn("[CURRENT USER REQUEST]\nwho am i", prompt)
        self.assertNotIn("user turn 0 my name is lewis\nassistant turn 0", prompt)
        self.assertTrue(summary.content)

    def test_tui_context_manager_uses_api_compactor_when_available(self) -> None:
        lines = [ConversationLine("user", f"old turn {index}") for index in range(20)]
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=4,
            api_compactor=lambda _: "API summary: User is Lewis.",
        )

        prompt, compacted_lines, summary = manager.prepare_prompt(
            current_user_message="who am i",
            conversation=lines,
            existing_summary="",
        )

        self.assertLessEqual(len(compacted_lines), 2)
        self.assertEqual(summary.source, "api")
        self.assertIn("API summary: User is Lewis.", prompt)

    def test_tui_context_manager_api_compactor_preserves_deterministic_tool_ledger(self) -> None:
        lines = [
            ConversationLine("user", "try reading .env"),
            ConversationLine("assistant", "The .env file is blocked (protected_path:.env). Contents were not exposed."),
            ConversationLine("user", "read docs/ghost_notes.md"),
            ConversationLine("assistant", "docs/ghost_notes.md is missing (missing_file:docs/ghost_notes.md)."),
            ConversationLine("user", "create a run summary from known protected/missing events"),
        ]
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=3,
            api_compactor=lambda _: "Session involved creating agent notes files.",
        )

        prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="create a run summary from known protected/missing events",
            conversation=lines,
            existing_summary="- Historical tool result: protected_path:.env was hit earlier.",
            compaction_count=1,
        )

        self.assertEqual(summary.source, "api")
        self.assertIn("Session involved creating agent notes files.", summary.content)
        self.assertIn("protected_path:.env", summary.content)
        self.assertIn("missing_file:docs/ghost_notes.md", prompt)

    def test_tui_context_manager_does_not_recompact_ledger_header_as_fact(self) -> None:
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=3,
            api_compactor=lambda _: "Session involved continuing the file workflow.",
        )
        lines = [
            ConversationLine("user", "continue"),
            ConversationLine("assistant", "continuing"),
            ConversationLine("user", "next"),
            ConversationLine("assistant", "next done"),
            ConversationLine("user", "summarize"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="summarize",
            conversation=lines,
            existing_summary=(
                "Prior summary.\n"
                "Deterministic reference ledger:\n"
                "- Historical tool result: protected_path:.env\n"
            ),
            compaction_count=2,
        )

        self.assertEqual(summary.content.count("Deterministic reference ledger:"), 1)
        self.assertIn("Historical tool result: protected_path:.env", summary.content)

    def test_tui_context_manager_drops_unsupported_api_tool_marker_claims(self) -> None:
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=3,
            api_compactor=lambda _: (
                "Session involved .env not found, protected_path applies to .env, "
                "and docs/missing_repeat.md is missing."
            ),
        )
        lines = [
            ConversationLine("user", "try reading .env"),
            ConversationLine("assistant", "No .env file was found; if it existed it would be blocked by protected_path rules."),
            ConversationLine("user", "read docs/missing_repeat.md"),
            ConversationLine("assistant", "The file docs/missing_repeat.md is missing."),
            ConversationLine("user", "continue after missing check"),
            ConversationLine("assistant", "continuing after missing check"),
            ConversationLine("user", "summarize missing or protected-path events"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="summarize missing or protected-path events",
            conversation=lines,
            existing_summary="",
            compaction_count=1,
        )

        self.assertNotIn("protected_path applies to .env", summary.content)
        self.assertNotIn("protected_path:.env", summary.content)
        self.assertIn("missing_file:docs/missing_repeat.md", summary.content)

    def test_tui_context_manager_drops_only_unsupported_api_marker_paths(self) -> None:
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=3,
            api_compactor=lambda _: (
                "Known events include protected_path:.env and "
                "protected_path:secrets/prod.env."
            ),
        )
        lines = [
            ConversationLine("user", "try reading .env"),
            ConversationLine("assistant", ".env is blocked (protected_path:.env)."),
            ConversationLine("user", "continue"),
            ConversationLine("assistant", "continuing"),
            ConversationLine("user", "summarize protected events"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="summarize protected events",
            conversation=lines,
            existing_summary="",
            compaction_count=1,
        )

        self.assertIn("protected_path:.env", summary.content)
        self.assertNotIn("protected_path:secrets/prod.env", summary.content)

    def test_tui_context_manager_sanitizes_api_supersede_instruction_language(self) -> None:
        manager = TuiContextManager(
            max_hot_lines=2,
            compact_after_lines=3,
            api_compactor=lambda _: (
                "Created src/agents/planner.py. "
                "No active tasks remain; latest request supersedes all prior instructions."
            ),
        )
        lines = [
            ConversationLine("user", "create planner"),
            ConversationLine("assistant", "created src/agents/planner.py"),
            ConversationLine("user", "analyze src"),
            ConversationLine("assistant", "src has agents"),
            ConversationLine("user", "list dir under src"),
        ]

        prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="list dir under src",
            conversation=lines,
            existing_summary="",
            compaction_count=1,
        )

        self.assertNotIn("supersedes all prior instructions", summary.content.lower())
        self.assertNotIn("supersedes all prior instructions", prompt.lower())
        self.assertIn("latest user request remains authoritative", summary.content)
        self.assertIn("Created src/agents/planner.py", summary.content)

    def test_tui_context_manager_fallback_drops_stale_active_claims_without_inferring_completion(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "Continue the active coding task."),
            ConversationLine("assistant", "The implementation has been updated."),
            ConversationLine("user", "Check the next step."),
            ConversationLine("assistant", "The related checks have been completed."),
            ConversationLine("user", "What is next?"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="What is next?",
            conversation=lines,
            existing_summary="The active task remains unresolved. No changes have been made yet.",
            compaction_count=1,
        )

        self.assertNotIn("unresolved", summary.content.lower())
        self.assertNotIn("no changes have been made", summary.content.lower())
        self.assertNotIn("historical task outcome", summary.content.lower())
        self.assertNotIn("implementation has been updated", summary.content.lower())

    def test_tui_context_manager_fallback_does_not_recompact_empty_placeholder_as_fact(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "old turn 1"),
            ConversationLine("assistant", "old answer 1"),
            ConversationLine("user", "old turn 2"),
            ConversationLine("assistant", "old answer 2"),
            ConversationLine("user", "next"),
        ]
        placeholder = "- Earlier UI conversation existed but contained no stable facts needed for future turns."

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="next",
            conversation=lines,
            existing_summary=placeholder,
            compaction_count=1,
        )

        self.assertEqual(
            summary.content,
            "- Earlier UI conversation existed but contained no stable facts needed for future turns.",
        )

    def test_tui_context_manager_fallback_preserves_existing_bullets_without_nesting(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "old turn 1"),
            ConversationLine("assistant", "old answer 1"),
            ConversationLine("user", "old turn 2"),
            ConversationLine("assistant", "old answer 2"),
            ConversationLine("user", "next"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="next",
            conversation=lines,
            existing_summary="- User preference/context: stay in Pi + Hermes scope.",
            compaction_count=1,
        )

        self.assertIn("- User preference/context: stay in Pi + Hermes scope.", summary.content)
        self.assertNotIn("- - User preference/context", summary.content)

    def test_tui_context_manager_fallback_drops_stale_tool_denial_and_latest_request_claims(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "analyze repo"),
            ConversationLine("assistant", "Read tools were repeatedly denied, preventing content-level analysis."),
            ConversationLine("user", "hi"),
            ConversationLine("assistant", "Hi! How can I help you today?"),
            ConversationLine("user", "list src"),
        ]

        _prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="list src",
            conversation=lines,
            existing_summary=(
                "- User preference/context: stay in Pi + Hermes scope.\n"
                "- Read tools were repeatedly denied, preventing content-level analysis.\n"
                "- Latest user request 'hi' initiates a new session."
            ),
            compaction_count=1,
        )

        self.assertIn("Pi + Hermes scope", summary.content)
        self.assertNotIn("read tools", summary.content.lower())
        self.assertNotIn("latest user request", summary.content.lower())

    def test_tui_context_manager_fallback_preserves_concrete_tool_denial_history(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "try reading .env"),
            ConversationLine("assistant", "The .env file is blocked (protected_path:.env). Contents were not exposed."),
            ConversationLine("user", "read docs/missing_recovery.md"),
            ConversationLine("assistant", "docs/missing_recovery.md is missing (missing_file:docs/missing_recovery.md)."),
            ConversationLine("user", "continue"),
        ]

        prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="mention any tool denials or failures in this session",
            conversation=lines,
            existing_summary="",
            compaction_count=1,
        )

        self.assertIn("protected_path:.env", summary.content)
        self.assertIn("missing_file:docs/missing_recovery.md", prompt)
        self.assertIn("explicitly asks about prior UI/session events", prompt)

    def test_tui_context_manager_fallback_preserves_plain_missing_file_history(self) -> None:
        manager = TuiContextManager(max_hot_lines=2, compact_after_lines=3)
        lines = [
            ConversationLine("user", "read docs/ghost_notes.md"),
            ConversationLine("assistant", "docs/ghost_notes.md is missing."),
            ConversationLine("user", "continue"),
            ConversationLine("assistant", "continuing"),
            ConversationLine("user", "summarize known missing events"),
        ]

        prompt, _hot_lines, summary = manager.prepare_prompt(
            current_user_message="summarize known missing events",
            conversation=lines,
            existing_summary="",
            compaction_count=1,
        )

        self.assertIn("missing_file:docs/ghost_notes.md", summary.content)
        self.assertIn("missing_file:docs/ghost_notes.md", prompt)

    def test_tui_runtime_prompt_is_bounded_after_many_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.conversation = [
                ConversationLine("user" if index % 2 == 0 else "assistant", f"line {index} " + ("x" * 200))
                for index in range(80)
            ]

            prompt = app._runtime_prompt("summarize me")

        self.assertLess(len(prompt), 5000)
        self.assertIn("UI SESSION SUMMARY - REFERENCE ONLY", prompt)
        self.assertIn("RECENT UI TURNS", prompt)

    def test_tui_reuses_sanitized_previous_runtime_result_for_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_old",
                    "world_refs": {"world://file_management.write_file/old": {"summary": "old write"}},
                    "context_summary": {"progress": ["old write completed"]},
                },
                conversation=[ConversationLine("assistant", "Old file task completed.")],
            )

            previous = app._previous_result()

        self.assertIsInstance(previous, dict)
        self.assertEqual(previous["session_id"], "sess_old")
        self.assertIn("world://file_management.write_file/old", previous["world_refs"])

    def test_tui_saved_code_session_drives_referential_runtime_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = AppV22Tui(workspace=root, dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_old",
                    "world_refs": {
                        "world://file_management.read_file/facebook": {
                            "kind": "file_management.read_file",
                            "freshness": "stable",
                            "arguments": {"path": "src/agents/facebook_surfer.py"},
                            "payload": {
                                "path": "src/agents/facebook_surfer.py",
                                "content": "line one\nline two\nline three\n",
                            },
                            "summary": "file_management.read_file result",
                        }
                    },
                    "context_summary": {
                        "progress": ["file_management.read_file: file_management.read_file result"],
                        "evidence_refs": ["world://file_management.read_file/facebook"],
                    },
                },
                conversation=[
                    ConversationLine("user", "ok read"),
                    ConversationLine("assistant", "src/agents/facebook_surfer.py:\nline one\nline two\nline three"),
                ],
            )
            app = AppV22Tui(workspace=root, dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.add_user("how many lines in that")
            runtime_prompt = app._runtime_prompt("how many lines in that")
            provider = _CaptureProvider()
            services = create_appv22_services(root_path=root, provider=provider, extensions=[FileManagementExtension()])
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=1)

            runtime.continue_run(
                app._previous_result(),
                runtime_prompt,
                active_user_request="how many lines in that",
                ui_context=app._ui_context_payload(),
            )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("RECENT UI TURNS", runtime_prompt)
        self.assertIn("[CURRENT USER REQUEST]\nhow many lines in that", runtime_prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("world://file_management.read_file/facebook", provider.prompt["world"]["world_refs"])

    def test_session_store_does_not_persist_inactive_tool_risks_as_active_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {
                        "progress": ["write completed"],
                        "blockers": [
                            "file.read reported error: inactive_tool:file.read",
                            "file.read request was denied for argument keys ['path']; treat that denial as evidence.",
                            "approval required: real unresolved risk",
                        ],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        self.assertEqual(loaded["context_summary"]["blockers"], ["approval required: real unresolved risk"])
        self.assertEqual(loaded["last_result"]["context_summary"]["blockers"], ["approval required: real unresolved risk"])

    def test_session_store_reconciles_tool_risks_against_persisted_world_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {
                        "world://file_management.read_file/ok": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                        }
                    },
                    "context_summary": {
                        "progress": ["file_management.read_file: file_management.read_file result"],
                        "blockers": [
                            "file_management.read_file reported error: missing_file:cat.txt",
                            "approval required: other_tool still_active",
                        ],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        self.assertEqual(loaded["context_summary"]["blockers"], ["approval required: other_tool still_active"])
        self.assertIn(
            "file_management.read_file: prior failed/denied tool risk resolved by later successful result",
            loaded["context_summary"]["progress"],
        )

    def test_session_store_does_not_persist_turn_local_repair_risks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "failed",
                    "reason": "max_turns_exceeded",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {
                        "blockers": [
                            "Malformed tool_call decision was missing payload.tool_id; the next decision must call one selected tool.",
                            "approval required: real task risk",
                        ],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        self.assertEqual(loaded["context_summary"]["blockers"], ["approval required: real task risk"])
        self.assertEqual(loaded["last_result"]["context_summary"]["blockers"], ["approval required: real task risk"])

    def test_session_store_does_not_persist_turn_local_operational_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {
                        "progress": [
                            "Duplicate completed tool call suppressed; existing tool result already proves the requested action.",
                            "src/math_utils.py updated with square(x)",
                        ],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        self.assertEqual(loaded["context_summary"]["progress"], ["src/math_utils.py updated with square(x)"])
        self.assertEqual(
            loaded["last_result"]["context_summary"]["progress"],
            ["src/math_utils.py updated with square(x)"],
        )

    def test_session_store_does_not_persist_turn_local_action_guidance_as_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "failed",
                    "reason": "max_turns_exceeded",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {
                        "blockers": [
                            "The latest file mutation request has no completed write evidence; the next decision must be a tool_call to file_management.write_file before finalizing.",
                            "Finalization guidance names selected tool file_management.write_file; the next decision should call file_management.write_file before finalizing.",
                            "Recovery guidance names selected tool file_management.write_file; the next decision must be a tool_call to file_management.write_file with corrected arguments instead of repeating previously denied arguments.",
                            "Current source file evidence has been read for the requested existing-file edit; the next decision must be a tool_call to file_management.edit_file for docs/existing.md before finalizing.",
                            "Finalization guidance names selected tool file_management.edit_file; the next decision should call file_management.edit_file before finalizing.",
                            "file_management.read_file reported a protected path; do not retry that path, and continue using non-protected workspace evidence.",
                            "approval required: real durable blocker",
                        ],
                    },
                },
                conversation=[],
            )

            loaded = store.load()

        self.assertEqual(loaded["context_summary"]["blockers"], ["approval required: real durable blocker"])
        self.assertEqual(
            loaded["last_result"]["context_summary"]["blockers"],
            ["approval required: real durable blocker"],
        )

    def test_session_store_persists_bounded_runtime_events_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.save(
                {
                    "status": "failed",
                    "reason": "max_turns_exceeded",
                    "session_id": "sess_test",
                    "world_refs": {},
                    "context_summary": {},
                    "events": [
                        {
                            "event_type": "DecisionProposed",
                            "payload": {"kind": "tool_call", "index": index, "large": "x" * 1200},
                        }
                        for index in range(90)
                    ],
                },
                conversation=[],
            )

            loaded = store.load()
            state = TuiState.from_session(Path(tmp), loaded)

        events = loaded["last_result"]["events"]
        self.assertEqual(len(events), 80)
        self.assertEqual(events[0]["payload"]["index"], 10)
        self.assertLessEqual(len(events[-1]["payload"]["large"]), 700)
        self.assertEqual(len(state.events), 80)
        self.assertEqual(state.events[-1].kind, "DecisionProposed")

    def test_session_store_persists_lightweight_observe_payloads_for_rehydration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp), extensions=(FileManagementExtension(),))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {
                        "world://file_management.repo_snapshot/current": {
                            "kind": "file_management.repo_snapshot",
                            "summary": "file_management.repo_snapshot result",
                            "arguments": {},
                            "freshness": "turn",
                            "request_id": "req_test",
                            "run_id": "run_test",
                            "mutation_seq": 0,
                            "payload": {
                                "files": ["note.txt", "docs/output.md"],
                                "directories": ["docs"],
                                "text_previews": {"docs/output.md": "x" * 1000},
                            },
                        },
                        "world://file_management.read_file/ok": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "note.txt"},
                            "payload": {"path": "note.txt", "content": "note", "line_count": 1},
                        },
                        "world://file_management.find_files/ok": {
                            "kind": "file_management.find_files",
                            "summary": "file_management.find_files result",
                            "arguments": {"path": "src", "patterns": ["*.py"]},
                            "payload": {"matches": ["src/agents/planner.py"]},
                        },
                        "world://file_management.search_text/ok": {
                            "kind": "file_management.search_text",
                            "summary": "file_management.search_text result",
                            "arguments": {"path": "src", "query": "Planner"},
                            "payload": {
                                "matches": [
                                    {"path": "src/agents/planner.py", "line": 12, "snippet": "class Planner:"}
                                ]
                            },
                        },
                        "world://file_management.read_many/ok": {
                            "kind": "file_management.read_many",
                            "summary": "file_management.read_many result",
                            "arguments": {"paths": ["src/agents/planner.py"]},
                            "payload": {
                                "files": [
                                    {
                                        "path": "src/agents/planner.py",
                                        "content": "class Planner:\n    pass\n",
                                        "bytes_read": 24,
                                        "line_count": 2,
                                        "truncated": False,
                                    }
                                ]
                            },
                        },
                        "world://file_management.tree/ok": {
                            "kind": "file_management.tree",
                            "summary": "file_management.tree result",
                            "arguments": {"path": "src"},
                            "payload": {"entries": ["agents/", "  planner.py"]},
                        },
                        "world://file_management.grep/ok": {
                            "kind": "file_management.grep",
                            "summary": "file_management.grep result",
                            "arguments": {"path": "src", "pattern": "class Planner"},
                            "payload": {
                                "matches": [
                                    {"path": "src/agents/planner.py", "line": 1, "snippet": "class Planner:"}
                                ]
                            },
                        },
                        "world://file_management.read_range/ok": {
                            "kind": "file_management.read_range",
                            "summary": "file_management.read_range result",
                            "arguments": {"path": "src/agents/planner.py", "start_line": 1, "end_line": 2},
                            "payload": {
                                "path": "src/agents/planner.py",
                                "start_line": 1,
                                "end_line": 2,
                                "content": "1: class Planner:\n2:     pass",
                            },
                        },
                    },
                    "context_summary": {},
                    "usage": {"context": {"model_calls": 1, "total_prompt_estimated_tokens": 123}},
                },
                conversation=[],
            )

            loaded = store.load()

        snapshot = loaded["world_refs"]["world://file_management.repo_snapshot/current"]["payload"]
        snapshot_ref = loaded["world_refs"]["world://file_management.repo_snapshot/current"]
        read = loaded["world_refs"]["world://file_management.read_file/ok"]["payload"]
        found = loaded["world_refs"]["world://file_management.find_files/ok"]["payload"]
        searched = loaded["world_refs"]["world://file_management.search_text/ok"]["payload"]
        read_many = loaded["world_refs"]["world://file_management.read_many/ok"]["payload"]
        tree_payload = loaded["world_refs"]["world://file_management.tree/ok"]["payload"]
        grep_payload = loaded["world_refs"]["world://file_management.grep/ok"]["payload"]
        read_range_payload = loaded["world_refs"]["world://file_management.read_range/ok"]["payload"]
        self.assertEqual(snapshot["files"], ["note.txt", "docs/output.md"])
        self.assertEqual(snapshot["directories"], ["docs"])
        self.assertLessEqual(len(snapshot["text_previews"]["docs/output.md"]), 700)
        self.assertEqual(snapshot_ref["freshness"], "turn")
        self.assertEqual(snapshot_ref["request_id"], "req_test")
        self.assertEqual(snapshot_ref["run_id"], "run_test")
        self.assertEqual(snapshot_ref["mutation_seq"], 0)
        self.assertEqual(read["content"], "note")
        self.assertEqual(read["line_count"], 1)
        self.assertEqual(found["matches"], ["src/agents/planner.py"])
        self.assertEqual(searched["matches"][0]["snippet"], "class Planner:")
        self.assertEqual(read_many["files"][0]["content"], "class Planner:\n    pass\n")
        self.assertEqual(read_many["files"][0]["line_count"], 2)
        self.assertEqual(tree_payload["entries"], ["agents/", "  planner.py"])
        self.assertEqual(grep_payload["matches"][0]["snippet"], "class Planner:")
        self.assertEqual(read_range_payload["content"], "1: class Planner:\n2:     pass")
        self.assertEqual(loaded["usage"]["context"]["model_calls"], 1)
        self.assertEqual(loaded["last_result"]["usage"]["context"]["total_prompt_estimated_tokens"], 123)

    def test_session_store_derives_line_counts_for_legacy_read_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp), extensions=(FileManagementExtension(),))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_legacy",
                    "world_refs": {
                        "world://file_management.read_file/legacy": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "src/agents/facebook_surfer.py"},
                            "payload": {
                                "path": "src/agents/facebook_surfer.py",
                                "content": "class FacebookSurferAgent:\n    pass\n",
                            },
                        },
                        "world://file_management.read_many/legacy": {
                            "kind": "file_management.read_many",
                            "summary": "file_management.read_many result",
                            "arguments": {"paths": ["src/agents/facebook_surfer.py"]},
                            "payload": {
                                "files": [
                                    {
                                        "path": "src/agents/facebook_surfer.py",
                                        "content": "class FacebookSurferAgent:\n\n    pass\n",
                                        "bytes_read": 37,
                                        "truncated": False,
                                    }
                                ]
                            },
                        },
                    },
                    "context_summary": {},
                },
                conversation=[],
            )

            loaded = store.load()

        read = loaded["world_refs"]["world://file_management.read_file/legacy"]["payload"]
        read_many = loaded["world_refs"]["world://file_management.read_many/legacy"]["payload"]
        self.assertEqual(read["line_count"], 2)
        self.assertEqual(read_many["files"][0]["line_count"], 3)

    def test_session_store_does_not_derive_line_counts_from_truncated_legacy_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp), extensions=(FileManagementExtension(),))
            long_read_file_content = "\n".join(f"line {index}" for index in range(2000))
            long_read_many_content = "\n".join(f"line {index}" for index in range(800))
            store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_legacy_truncated",
                    "world_refs": {
                        "world://file_management.read_file/legacy": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "src/agents/facebook_surfer.py"},
                            "payload": {
                                "path": "src/agents/facebook_surfer.py",
                                "content": long_read_file_content,
                            },
                        },
                        "world://file_management.read_many/legacy": {
                            "kind": "file_management.read_many",
                            "summary": "file_management.read_many result",
                            "arguments": {"paths": ["src/agents/facebook_surfer.py"]},
                            "payload": {
                                "files": [
                                    {
                                        "path": "src/agents/facebook_surfer.py",
                                        "content": long_read_many_content,
                                        "bytes_read": len(long_read_many_content.encode("utf-8")),
                                        "truncated": False,
                                    }
                                ]
                            },
                        },
                        "world://file_management.read_file/exact_limit": {
                            "kind": "file_management.read_file",
                            "summary": "file_management.read_file result",
                            "arguments": {"path": "src/agents/exact.py"},
                            "payload": {
                                "path": "src/agents/exact.py",
                                "content": "x" * 12000,
                            },
                        },
                        "world://file_management.read_many/exact_limit": {
                            "kind": "file_management.read_many",
                            "summary": "file_management.read_many result",
                            "arguments": {"paths": ["src/agents/exact.py"]},
                            "payload": {
                                "files": [
                                    {
                                        "path": "src/agents/exact.py",
                                        "content": "x" * 4000,
                                        "bytes_read": 4000,
                                        "truncated": False,
                                    }
                                ]
                            },
                        },
                    },
                    "context_summary": {},
                },
                conversation=[],
            )

            loaded = store.load()

        read = loaded["world_refs"]["world://file_management.read_file/legacy"]["payload"]
        read_many = loaded["world_refs"]["world://file_management.read_many/legacy"]["payload"]
        exact_read = loaded["world_refs"]["world://file_management.read_file/exact_limit"]["payload"]
        exact_many = loaded["world_refs"]["world://file_management.read_many/exact_limit"]["payload"]
        self.assertNotIn("line_count", read)
        self.assertTrue(read["content_truncated_by_session"])
        self.assertNotIn("line_count", read_many["files"][0])
        self.assertTrue(read_many["files"][0]["content_truncated_by_session"])
        self.assertNotIn("line_count", exact_read)
        self.assertTrue(exact_read["content_truncated_by_session"])
        self.assertNotIn("line_count", exact_many["files"][0])
        self.assertTrue(exact_many["files"][0]["content_truncated_by_session"])

    def test_session_load_drops_legacy_latest_world_refs_and_evidence_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.path.write_text(
                json.dumps(
                    {
                        "session_id": "sess_test",
                        "status": "completed",
                        "reason": "tool_loop_completed",
                        "world_refs": {
                            "world://file_management.repo_snapshot/latest": {
                                "kind": "file_management.repo_snapshot",
                                "payload": {"files": ["stale.txt"], "directories": []},
                            },
                            "world://file_management.repo_snapshot/fresh": {
                                "kind": "file_management.repo_snapshot",
                                "payload": {"files": ["fresh.txt"], "directories": []},
                            },
                        },
                        "context_summary": {
                            "evidence_refs": [
                                "world://file_management.repo_snapshot/latest",
                                "world://file_management.repo_snapshot/fresh",
                            ],
                            "progress": ["file_management.repo_snapshot: file_management.repo_snapshot result"],
                        },
                        "conversation": [],
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.load()

        self.assertNotIn("world://file_management.repo_snapshot/latest", loaded["world_refs"])
        self.assertNotIn("world://file_management.repo_snapshot/latest", loaded["last_result"]["world_refs"])
        self.assertEqual(
            loaded["context_summary"]["evidence_refs"],
            ["world://file_management.repo_snapshot/fresh"],
        )

    def test_tui_context_manager_preserves_compaction_metrics_without_recompacting_hot_tail(self) -> None:
        manager = TuiContextManager(max_hot_lines=6, compact_after_lines=12)
        lines = [ConversationLine("user", "my name is lewis"), ConversationLine("assistant", "Hello, Lewis.")]

        prompt, compacted_lines, summary = manager.prepare_prompt(
            current_user_message="who am i",
            conversation=lines,
            existing_summary="- User name: Lewis.",
            compaction_count=2,
        )

        self.assertEqual(summary.compaction_count, 2)
        self.assertEqual(summary.source, "existing")
        self.assertEqual(summary.tokens_before, 0)
        self.assertEqual(len(compacted_lines), 2)
        self.assertIn("User name: Lewis", prompt)

    def test_tui_rejects_pasted_screen_chrome_as_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))

            accepted = app._accept_user_prompt("appv22  /tmp/workspace\ncontext refs 1\n09 run completed :: tool_loop_completed")

        self.assertIsNone(accepted)
        self.assertEqual(app.state.conversation, [])
        self.assertIn("ignored pasted TUI output", app.state.notice)

    def test_tui_extracts_exit_command_from_pasted_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))

            accepted = app._accept_user_prompt("that /exit")

        self.assertEqual(accepted, "/exit")

    def test_tui_result_notice_distinguishes_failed_turns_from_completed_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            events: queue.Queue[tuple[str, object]] = queue.Queue()
            events.put(
                (
                    "result",
                    {
                        "status": "failed",
                        "reason": "max_turns_exceeded",
                        "session_id": "sess_test",
                        "world_refs": {},
                        "context_summary": {},
                        "turn_feedback": ["Turn budget exhausted before current action evidence was produced."],
                    },
                )
            )

            app._drain_events(events)

        self.assertEqual(app.state.status, "failed")
        self.assertIn("turn failed: max_turns_exceeded", app.state.notice)
        self.assertNotIn("turn completed", app.state.notice)

    def test_tui_state_filters_pasted_screen_chrome_when_loading_session(self) -> None:
        session = {
            "conversation": [
                {"role": "user", "text": "appv22  /tmp/workspace\ncontext refs 1\n09 run completed :: tool_loop_completed"},
                {"role": "assistant", "text": "Hello, Lewis."},
            ],
            "last_result": {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {},
            },
        }

        state = TuiState.from_session(Path("/tmp/workspace"), session)

        self.assertEqual([line.text for line in state.conversation], ["Hello, Lewis."])

    def test_tui_state_does_not_duplicate_last_persisted_assistant_message(self) -> None:
        session = {
            "conversation": [
                {"role": "user", "text": "Inspect the module."},
                {"role": "assistant", "text": "It defines double."},
            ],
            "last_result": {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {},
                "assistant_message": "It defines double.",
            },
        }

        state = TuiState.from_session(Path("/tmp/workspace"), session)

        self.assertEqual(
            [line.text for line in state.conversation],
            ["Inspect the module.", "It defines double."],
        )

    def test_tui_module_entrypoint_accepts_exit_command_without_name_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path.cwd() / "appV2.2")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "appv22_ui.tui_app",
                    "--workspace",
                    tmp,
                    "--dotenv",
                    ".env",
                    "--max-turns",
                    "1",
                ],
                input="/exit\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("NameError", completed.stderr)

    def test_appv22_exposes_tui_only_no_cli_entrypoints(self) -> None:
        app_root = Path(__file__).resolve().parents[1]

        self.assertFalse((app_root / "appv22_ui" / "cli.py").exists())
        self.assertFalse((app_root / "scripts" / "appv22_cli.py").exists())
        self.assertTrue((app_root / "scripts" / "appv22_tui.py").exists())
        self.assertFalse((app_root / "scripts" / "appv22_textual.py").exists())
        self.assertFalse((app_root / "appv22_ui" / "textual_app.py").exists())
        self.assertFalse((app_root / "appv22_ui" / "textual_runtime.py").exists())
        self.assertFalse((app_root / "appv22_ui" / "textual_controller.py").exists())

    def test_pi_tui_frontend_ports_pi_component_runtime(self) -> None:
        app_root = Path(__file__).resolve().parents[1]
        frontend = app_root / "appv22_ui" / "pi_tui" / "app.mjs"
        package_json = app_root / "package.json"

        self.assertTrue(frontend.exists())
        self.assertTrue(package_json.exists())
        source = frontend.read_text(encoding="utf-8")
        manifest = json.loads(package_json.read_text(encoding="utf-8"))

        self.assertIn("@earendil-works/pi-tui", manifest["dependencies"])
        self.assertIn("new TUI(new ProcessTerminal()", source)
        self.assertIn("new Container()", source)
        self.assertIn("new Input()", source)
        self.assertNotIn("readline.createInterface", source)
        self.assertNotIn("process.stdout.write('\\x1b[2J", source)

        subprocess.run(["node", "--check", str(frontend)], check=True)

    def test_pi_tui_bridge_status_uses_jsonl_without_model_call(self) -> None:
        app_root = Path(__file__).resolve().parents[1]
        bridge = app_root / "scripts" / "appv22_tui_bridge.py"
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(bridge),
                    "--workspace",
                    tmp,
                    "--dotenv",
                    ".env",
                    "--max-turns",
                    "2",
                ],
                input='{"type":"status"}\n{"type":"exit"}\n',
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(lines[0]["type"], "status")
        self.assertEqual(lines[0]["session"]["status"], "empty")
        self.assertEqual(lines[-1]["type"], "exit")

    def test_tui_draw_skips_identical_frames_to_avoid_background_flooding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            output = io.StringIO()

            with patch("sys.stdout", output):
                app._draw()
                app._draw()

        self.assertEqual(output.getvalue().count("appv22> "), 1)

    def test_reset_ui_command_clears_corrupted_conversation_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {"world://file_management.read_file/ok": {"kind": "file_management.read_file"}},
                    "context_summary": {"progress": ["read completed"]},
                },
                conversation=[],
            )
            app.state.conversation = [ConversationLine("user", "bad pasted chrome")]

            should_exit = app._command("/reset-ui")
            loaded = app.store.load()

        self.assertFalse(should_exit)
        self.assertEqual(app.state.conversation, [])
        self.assertEqual(loaded["conversation"], [])
        self.assertIn("world://file_management.read_file/ok", loaded["last_result"]["world_refs"])
        self.assertEqual(loaded["last_result"]["context_summary"]["progress"], ["read completed"])
        self.assertIn("UI conversation reset", app.state.notice)

class _CaptureProvider:
    def __init__(self) -> None:
        self.prompt = None

    def decide(self, prompt):
        self.prompt = prompt
        return {"kind": "finalize", "payload": {"assistant_message": "captured"}}


if __name__ == "__main__":
    unittest.main()
