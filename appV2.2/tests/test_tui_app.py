from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from appv22_ui.session import SessionStore
from appv22_ui.context_manager import TuiContextManager
from appv22_ui.textual_controller import TextualTuiController
from appv22_ui.tui_app import AppV22Tui
from appv22_ui.tui_layout import render_tui
from appv22_ui.tui_state import ConversationLine, TuiState


class TuiAppTests(unittest.TestCase):
    def test_tui_layout_has_pi_and_hermes_panes(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))
        state.add_user("hi")
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

        self.assertIn("CONVERSATION", rendered)
        self.assertIn("PI AGENT LOOP", rendered)
        self.assertIn("HERMES CONTEXT", rendered)
        self.assertIn("assistant: Hello.", rendered)

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

    def test_tui_layout_keeps_multiline_assistant_text_inside_panel_rows(self) -> None:
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

        self.assertIn("assistant: Line one", rendered)
        self.assertIn("Line two", rendered)
        self.assertIn("Line three", rendered)
        self.assertNotIn("Line one\nLine two", rendered)

    def test_tui_context_hides_stale_inactive_tool_risks_after_progress(self) -> None:
        state = TuiState(workspace=Path("/tmp/workspace"))
        state.apply_result(
            {
                "status": "completed",
                "reason": "tool_loop_completed",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {
                    "progress": ["file_management.repo_snapshot: file_management.repo_snapshot result"],
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

    def test_session_store_persists_lightweight_observe_payloads_for_rehydration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp))
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
                            "payload": {"path": "note.txt", "content": "note"},
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
        self.assertEqual(snapshot["files"], ["note.txt", "docs/output.md"])
        self.assertEqual(snapshot["directories"], ["docs"])
        self.assertLessEqual(len(snapshot["text_previews"]["docs/output.md"]), 700)
        self.assertEqual(snapshot_ref["freshness"], "turn")
        self.assertEqual(snapshot_ref["request_id"], "req_test")
        self.assertEqual(snapshot_ref["run_id"], "run_test")
        self.assertEqual(snapshot_ref["mutation_seq"], 0)
        self.assertEqual(read["content"], "note")
        self.assertEqual(loaded["usage"]["context"]["model_calls"], 1)
        self.assertEqual(loaded["last_result"]["usage"]["context"]["total_prompt_estimated_tokens"], 123)

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

            accepted = app._accept_user_prompt("| CONVERSATION | | | 09 run completed :: tool_loop_completed")

        self.assertIsNone(accepted)
        self.assertEqual(app.state.conversation, [])
        self.assertIn("ignored pasted TUI output", app.state.notice)

    def test_tui_extracts_exit_command_from_pasted_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))

            accepted = app._accept_user_prompt("that /exit")

        self.assertEqual(accepted, "/exit")

    def test_tui_state_filters_pasted_screen_chrome_when_loading_session(self) -> None:
        session = {
            "conversation": [
                {"role": "user", "text": "| CONVERSATION | | | 09 run completed :: tool_loop_completed"},
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

    def test_textual_reset_ui_preserves_runtime_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = TextualTuiController(
                workspace=Path(tmp),
                dotenv_path=Path(".env"),
                max_turns=4,
                extensions=("file_management",),
            )
            controller.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_test",
                    "world_refs": {"world://file_management.write_file/ok": {"kind": "file_management.write_file"}},
                    "context_summary": {"progress": ["write completed"]},
                },
                conversation=[],
            )
            controller.state.conversation = [ConversationLine("user", "bad pasted chrome")]

            should_exit = controller.handle_command("/reset-ui")
            loaded = controller.store.load()

        self.assertFalse(should_exit)
        self.assertEqual(controller.state.conversation, [])
        self.assertEqual(loaded["conversation"], [])
        self.assertIn("world://file_management.write_file/ok", loaded["last_result"]["world_refs"])
        self.assertEqual(loaded["last_result"]["context_summary"]["progress"], ["write completed"])

    def test_textual_controller_tracks_input_history_for_arrow_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = TextualTuiController(
                workspace=Path(tmp),
                dotenv_path=Path(".env"),
                max_turns=4,
                extensions=("file_management",),
            )
            controller.record_submitted_text("first")
            controller.record_submitted_text("second")

            self.assertEqual(controller.previous_history(), "second")
            self.assertEqual(controller.previous_history(), "first")
            self.assertEqual(controller.next_history(), "second")

    def test_textual_controller_builds_compacted_runtime_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = TextualTuiController(
                workspace=Path(tmp),
                dotenv_path=Path(".env"),
                max_turns=4,
                extensions=("file_management",),
            )
            controller.state.conversation = [
                ConversationLine("user" if index % 2 == 0 else "assistant", f"line {index} " + ("x" * 200))
                for index in range(80)
            ]

            prompt = controller.build_runtime_prompt("who am i")

        self.assertLess(len(prompt), 5000)
        self.assertIn("UI SESSION SUMMARY - REFERENCE ONLY", prompt)
        self.assertIn("[CURRENT USER REQUEST]\nwho am i", prompt)


if __name__ == "__main__":
    unittest.main()
