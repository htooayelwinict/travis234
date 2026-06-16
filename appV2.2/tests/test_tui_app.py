from __future__ import annotations

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
                "world_refs": {"world://repo/latest": {"summary": "snapshot"}},
                "context_summary": {"open_risks": ["risk"], "progress": ["snapshot"]},
                "events": [],
            }
        )

        self.assertEqual(state.session_id, "sess_test")
        self.assertEqual(state.world_ref_count, 1)
        self.assertEqual(state.context_summary["open_risks"], ["risk"])

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
                    "open_risks": [
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

    def test_tui_does_not_reuse_previous_runtime_result_as_active_state(self) -> None:
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

        self.assertIsNone(previous)

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
            app.state.conversation = [ConversationLine("user", "bad pasted chrome")]

            should_exit = app._command("/reset-ui")
            loaded = app.store.load()

        self.assertFalse(should_exit)
        self.assertEqual(app.state.conversation, [])
        self.assertEqual(loaded["conversation"], [])
        self.assertIn("UI conversation reset", app.state.notice)

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
