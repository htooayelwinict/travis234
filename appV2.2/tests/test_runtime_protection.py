from __future__ import annotations

from pathlib import Path
import queue
import tempfile
import unittest

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.file_management.skills import CODE_SEARCH_SKILL
from appv22.extensions.file_management.tools import (
    find_files,
    grep,
    mkdir,
    read_file,
    read_many,
    read_range,
    repo_snapshot,
    search_text,
    tree,
    write_file,
)
from appv22.context.freshness import is_world_ref_fresh
from appv22.runtime.agent_loop import AppV22AgentRuntime
from appv22.runtime.services import AppV22Services
from appv22.runtime.services import create_appv22_services
from appv22.providers.appv2_env import _appv22_decision_prompt
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.broker import ToolBroker
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry
from appv22_ui.session import SessionStore
from appv22_ui.tui_app import AppV22Tui


class RuntimeProtectionTests(unittest.TestCase):
    def test_repo_snapshot_skips_symlinked_files_and_exact_protected_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            outside_secret = Path(outside) / "secret.txt"
            outside_secret.write_text("external secret", encoding="utf-8")
            (root / "safe.txt").write_text("safe", encoding="utf-8")
            (root / "secrets").write_text("local secret", encoding="utf-8")
            (root / "linked.txt").symlink_to(outside_secret)

            result = repo_snapshot({}, {"root_path": root})

        self.assertEqual(result["status"], "completed")
        self.assertIn("safe.txt", result["files"])
        self.assertNotIn("secrets", result["files"])
        self.assertNotIn("linked.txt", result["files"])
        self.assertNotIn("secrets", result["text_previews"])
        self.assertNotIn("linked.txt", result["text_previews"])

    def test_repo_snapshot_respects_path_and_default_dependency_excludes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
            (root / ".venv" / "lib").mkdir(parents=True)
            (root / ".venv" / "lib" / "noise.py").write_text("noise", encoding="utf-8")

            result = repo_snapshot({"path": "src"}, {"root_path": root})

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["root"], "src")
        self.assertIn("src/app.py", result["files"])
        self.assertFalse(any(path.startswith(".venv/") for path in result["files"]))
        self.assertFalse(any(path.startswith(".venv/") for path in result["directories"]))

    def test_find_search_and_read_many_support_code_scan_arsenal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("class BrowserBot:\n    pass\n", encoding="utf-8")
            (root / "src" / "notes.md").write_text("BrowserBot notes\n", encoding="utf-8")

            found = find_files({"path": "src", "patterns": ["*.py"]}, {"root_path": root})
            searched = search_text({"path": "src", "query": "BrowserBot"}, {"root_path": root})
            read = read_many({"paths": ["src/app.py", "src/notes.md"]}, {"root_path": root})

        self.assertEqual(found["status"], "completed")
        self.assertEqual(found["matches"], ["src/app.py"])
        self.assertEqual(searched["status"], "completed")
        self.assertEqual({item["path"] for item in searched["matches"]}, {"src/app.py", "src/notes.md"})
        self.assertEqual(read["status"], "completed")
        self.assertEqual([item["path"] for item in read["files"]], ["src/app.py", "src/notes.md"])

    def test_read_file_and_read_many_expose_exact_line_counts_to_model_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "agents").mkdir(parents=True)
            content = (
                "class FacebookSurferAgent:\n"
                "    def __init__(self):\n"
                "        self.name = \"facebook\"\n"
                "\n"
                "    def invoke(self, task):\n"
                "        return task\n"
                "\n"
                "    def stream(self, task):\n"
                "        yield task\n"
            )
            (root / "src" / "agents" / "facebook_surfer.py").write_text(content, encoding="utf-8")

            single = read_file({"path": "src/agents/facebook_surfer.py"}, {"root_path": root})
            many = read_many({"paths": ["src/agents/facebook_surfer.py"]}, {"root_path": root})

        self.assertEqual(single["status"], "completed")
        self.assertEqual(single["line_count"], 9)
        self.assertEqual(many["files"][0]["line_count"], 9)

        extension = FileManagementExtension()
        single_view = extension.transform_tool_result(
            {
                "tool_id": "file_management.read_file",
                "status": "completed",
                "payload": single,
            }
        )
        many_view = extension.transform_tool_result(
            {
                "tool_id": "file_management.read_many",
                "status": "completed",
                "payload": many,
            }
        )

        self.assertIsNotNone(single_view)
        self.assertIsNotNone(many_view)
        self.assertIn("Line count: 9", single_view["model_view"])
        self.assertIn("9 lines", many_view["model_view"])

    def test_tree_grep_and_read_range_support_precise_code_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "agents").mkdir(parents=True)
            (root / "src" / "agents" / "planner.py").write_text(
                "class Planner:\n"
                "    def plan(self):\n"
                "        return 'ok'\n",
                encoding="utf-8",
            )
            (root / ".venv" / "lib").mkdir(parents=True)
            (root / ".venv" / "lib" / "noise.py").write_text("class Planner: pass\n", encoding="utf-8")

            layout = tree({"path": "src", "max_depth": 4}, {"root_path": root})
            matches = grep({"path": "src", "pattern": "def plan", "glob": "*.py"}, {"root_path": root})
            sliced = read_range(
                {"path": "src/agents/planner.py", "start_line": 1, "end_line": 2},
                {"root_path": root},
            )

        self.assertEqual(layout["status"], "completed")
        self.assertTrue(any("planner.py" in entry for entry in layout["entries"]))
        self.assertFalse(any(".venv" in entry for entry in layout["entries"]))
        self.assertEqual(matches["status"], "completed")
        self.assertEqual(matches["matches"][0]["path"], "src/agents/planner.py")
        self.assertEqual(sliced["status"], "completed")
        self.assertIn("1: class Planner:", sliced["content"])
        self.assertIn("2:     def plan(self):", sliced["content"])

    def test_tree_keeps_sibling_directory_children_under_correct_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "tools").mkdir(parents=True)
            (root / "src" / "tools" / "registry.py").write_text("class ToolRegistry: pass\n", encoding="utf-8")
            (root / "src" / "agents").mkdir(parents=True)
            (root / "src" / "agents" / "planner.py").write_text("class PlannerAgent: pass\n", encoding="utf-8")

            layout = tree({"path": "src", "max_depth": 4}, {"root_path": root})

        self.assertEqual(layout["status"], "completed")
        self.assertEqual(
            layout["entries"],
            ["agents/", "  planner.py", "tools/", "  registry.py"],
        )

    def test_observe_tools_exclude_runtime_session_and_exact_env_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
            (root / ".env.example").write_text("SECRET=\n", encoding="utf-8")
            (root / ".appv22-ui").mkdir()
            (root / ".appv22-ui" / "session.json").write_text("{}", encoding="utf-8")
            (root / ".playwright-mcp" / "facebook" / "Default" / "Cache").mkdir(parents=True)
            (root / ".playwright-mcp" / "facebook" / "Default" / "Cache" / "blob").write_text("noise", encoding="utf-8")
            (root / "qdrant_db" / "collection").mkdir(parents=True)
            (root / "qdrant_db" / "collection" / "segment").write_text("noise", encoding="utf-8")

            layout = tree({"path": "."}, {"root_path": root})
            snapshot = repo_snapshot({"path": "."}, {"root_path": root})

        self.assertEqual(layout["status"], "completed")
        self.assertIn("app.py", "\n".join(layout["entries"]))
        self.assertIn(".env.example", layout["entries"])
        blocked_layout_text = "\n".join(layout["entries"])
        self.assertNotIn(".env", layout["entries"])
        self.assertNotIn(".appv22-ui", blocked_layout_text)
        self.assertNotIn(".playwright-mcp", blocked_layout_text)
        self.assertNotIn("qdrant_db", blocked_layout_text)
        self.assertIn(".env.example", snapshot["files"])
        self.assertNotIn(".env", snapshot["files"])
        self.assertFalse(any(path.startswith(".appv22-ui/") for path in snapshot["files"]))
        self.assertFalse(any(path.startswith(".playwright-mcp/") for path in snapshot["files"]))
        self.assertFalse(any(path.startswith("qdrant_db/") for path in snapshot["files"]))

    def test_read_range_beyond_eof_fails_without_empty_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "agents").mkdir(parents=True)
            (root / "src" / "agents" / "planner.py").write_text(
                "class Planner:\n"
                "    pass\n",
                encoding="utf-8",
            )

            result = read_range(
                {"path": "src/agents/planner.py", "start_line": 3, "end_line": 20},
                {"root_path": root},
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["content"], "")
        self.assertIn("line_range_out_of_bounds:src/agents/planner.py:3:2", result["errors"])

    def test_exact_protected_names_are_denied_for_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = mkdir({"path": "secrets"}, {"root_path": root})

        self.assertEqual(result["status"], "denied")
        self.assertIn("protected_path:secrets", result["errors"])

    def test_overwrite_policy_uses_active_request_not_reference_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("old", encoding="utf-8")
            context = {
                "root_path": root,
                "request": {
                    "user_goal": "[UI SESSION SUMMARY]\nUser previously said do not overwrite.\n[CURRENT USER REQUEST]\noverwrite note.txt",
                    "active_user_request": "overwrite note.txt",
                },
            }

            result = write_file({"path": "note.txt", "content": "new", "overwrite": True}, context)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["overwritten"])
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "new")

    def test_update_named_file_does_not_auto_overwrite_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_math_utils.py").write_text("def test_double():\n    assert True\n", encoding="utf-8")
            context = {
                "root_path": root,
                "request": {
                    "user_goal": "Update tests/test_math_utils.py with a test for square(5) == 25. Preserve the existing tests.",
                    "active_user_request": (
                        "Update tests/test_math_utils.py with a test for square(5) == 25. Preserve the existing tests."
                    ),
                },
            }

            result = write_file(
                {
                    "path": "tests/test_math_utils.py",
                    "content": "def test_double():\n    assert True\n\ndef test_square():\n    assert square(5) == 25\n",
                },
                context,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["errors"], ["existing_file_requires_overwrite:tests/test_math_utils.py"])
            self.assertNotIn("test_square", (root / "tests" / "test_math_utils.py").read_text(encoding="utf-8"))

    def test_same_file_followup_does_not_auto_overwrite_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "math_utils.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
            context = {
                "root_path": root,
                "request": {
                    "user_goal": (
                        "[RECENT UI TURNS]\n"
                        "assistant: updated src/math_utils.py and tests/test_math_utils.py\n"
                        "[CURRENT USER REQUEST]\n"
                        "Add one more helper to the same source file."
                    ),
                    "active_user_request": "Add one more helper to the same source file.",
                },
            }

            result = write_file(
                {
                    "path": "src/math_utils.py",
                    "content": "def double(x):\n    return x * 2\n\n\ndef sign_label(value):\n    return 'zero'\n",
                },
                context,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["errors"], ["existing_file_requires_overwrite:src/math_utils.py"])
            self.assertNotIn("sign_label", (root / "src" / "math_utils.py").read_text(encoding="utf-8"))

    def test_vague_next_one_source_followup_does_not_auto_overwrite_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "math_utils.py").write_text(
                "def double(x):\n    return x * 2\n\n\ndef abs_value(x):\n    return x if x >= 0 else -x\n",
                encoding="utf-8",
            )
            context = {
                "root_path": root,
                "request": {
                    "user_goal": (
                        "[RECENT UI TURNS]\n"
                        "assistant: updated src/math_utils.py and tests/test_math_utils.py\n"
                        "[CURRENT USER REQUEST]\n"
                        "Next one is clamp. Add it to source with tests for below range, inside range, and above range."
                    ),
                    "active_user_request": (
                        "Next one is clamp. Add it to source with tests for below range, inside range, and above range."
                    ),
                },
            }

            result = write_file(
                {
                    "path": "src/math_utils.py",
                    "content": (
                        "def double(x):\n"
                        "    return x * 2\n\n\n"
                        "def abs_value(x):\n"
                        "    return x if x >= 0 else -x\n\n\n"
                        "def clamp(x, low, high):\n"
                        "    if x < low:\n"
                        "        return low\n"
                        "    if x > high:\n"
                        "        return high\n"
                        "    return x\n"
                    ),
                },
                context,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["errors"], ["existing_file_requires_overwrite:src/math_utils.py"])
            self.assertNotIn("clamp", (root / "src" / "math_utils.py").read_text(encoding="utf-8"))

    def test_test_followup_does_not_auto_overwrite_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_math_utils.py").write_text("def test_clamp_below_range():\n    pass\n", encoding="utf-8")
            context = {
                "root_path": root,
                "request": {
                    "user_goal": (
                        "[RECENT UI TURNS]\n"
                        "assistant: tests/test_math_utils.py contains clamp tests but not low == high coverage.\n"
                        "[CURRENT USER REQUEST]\n"
                        "Add that missing low equals high clamp test."
                    ),
                    "active_user_request": "Add that missing low equals high clamp test.",
                },
            }

            result = write_file(
                {
                    "path": "tests/test_math_utils.py",
                    "content": (
                        "def test_clamp_below_range():\n"
                        "    pass\n\n"
                        "def test_clamp_low_equals_high():\n"
                        "    assert clamp(5, 3, 3) == 3\n"
                    ),
                },
                context,
            )

            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["errors"], ["existing_file_requires_overwrite:tests/test_math_utils.py"])
            self.assertNotIn("test_clamp_low_equals_high", (root / "tests" / "test_math_utils.py").read_text(encoding="utf-8"))

    def test_file_management_guidance_maps_bare_read_file_alias_to_namespaced_tool(self) -> None:
        guidance = FileManagementExtension().tool_result_guidance(
            {
                "tool_id": "read_file",
                "status": "denied",
                "payload": {"errors": ["inactive_tool:read_file"]},
            }
        )

        self.assertIn("file_management.read_file", guidance)
        self.assertIn("read_file", guidance)

    def test_file_management_guidance_maps_list_directory_alias_to_tree(self) -> None:
        guidance = FileManagementExtension().tool_result_guidance(
            {
                "tool_id": "file_management.list_directory",
                "status": "denied",
                "payload": {"errors": ["inactive_tool:file_management.list_directory"]},
            }
        )

        self.assertIn("file_management.tree", guidance)
        self.assertIn("file_management.list_directory", guidance)

    def test_file_management_guidance_prefers_preserving_existing_file_over_safe_sibling_for_updates(self) -> None:
        guidance = FileManagementExtension().tool_result_guidance(
            {
                "tool_id": "file_management.write_file",
                "status": "denied",
                "payload": {
                    "path": "tests/test_text_metrics.py",
                    "suggested_path": "tests/test_text_metrics-1.py",
                    "errors": ["existing_file_requires_overwrite:tests/test_text_metrics.py"],
                },
            }
        )

        self.assertIn("file_management.edit_file", guidance)
        self.assertIn("targeted replacements", guidance)
        self.assertIn("overwrite:true", guidance)
        self.assertIn("complete rewrites", guidance)
        self.assertIn("suggested alternate path only", guidance)

    def test_file_management_guidance_recovers_copy_preserve_source_denial(self) -> None:
        guidance = FileManagementExtension().tool_result_guidance(
            {
                "tool_id": "file_management.copy_file",
                "status": "denied",
                "payload": {
                    "source": "docs/recovery_notes.md",
                    "destination": "docs/recovery_notes_copy.md",
                    "errors": ["copy_requires_preserve_source:true"],
                },
            }
        )

        self.assertIn("file_management.copy_file", guidance)
        self.assertIn("preserve_source:true", guidance)
        self.assertIn("retry", guidance.lower())

    def test_payload_ref_includes_arguments_to_avoid_semantic_collisions(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                "test.same_payload",
                "act",
                "low",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                {"type": "object", "properties": {"bytes_written": {"type": "integer"}}, "required": ["bytes_written"]},
                "test",
                "test",
            ),
            lambda _args, _context: {"status": "completed", "bytes_written": 3},
        )
        broker = ToolBroker(registry=registry, root_path=".")

        first = broker.execute("test.same_payload", {"path": "a.txt"}, active_tool_ids={"test.same_payload"})
        second = broker.execute("test.same_payload", {"path": "b.txt"}, active_tool_ids={"test.same_payload"})

        self.assertNotEqual(first["payload_ref"], second["payload_ref"])

    def test_failed_tool_results_emit_failed_event_and_reduce_into_state(self) -> None:
        captured: list[dict] = []
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
            event_sink=captured.append,
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))

        runtime._record_tool_result(
            state,
            {
                "tool_result_id": "toolres_failed",
                "tool_id": "test.tool",
                "status": "failed",
                "payload": {"errors": ["boom"]},
                "payload_ref": "",
                "evidence_refs": [],
                "arguments": {},
            },
        )

        self.assertEqual(captured[-1]["event_type"], "ToolCallFailed")
        self.assertIn("toolres_failed", state.tool_results)

    def test_tui_previous_result_uses_persisted_session_for_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.store.save(
                {
                    "status": "completed",
                    "reason": "tool_loop_completed",
                    "session_id": "sess_old",
                    "world_refs": {"world://file_management.repo_snapshot/persisted": {"summary": "snapshot"}},
                    "context_summary": {"progress": ["snapshot"]},
                },
                conversation=[],
            )

            previous = app._previous_result()

        self.assertIsInstance(previous, dict)
        self.assertEqual(previous["session_id"], "sess_old")
        self.assertIn("world://file_management.repo_snapshot/persisted", previous["world_refs"])

    def test_tui_interrupted_turn_does_not_persist_late_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = AppV22Tui(workspace=Path(tmp), dotenv_path=Path(".env"), max_turns=4, extensions=("file_management",))
            app.state.running = False
            app.state.mode = "INTERRUPTED"
            app.state.add_notice("turn interrupted")
            events: queue.Queue[tuple[str, object]] = queue.Queue()
            events.put(
                (
                    "result",
                    {
                        "status": "completed",
                        "reason": "tool_loop_completed",
                        "session_id": "sess_late",
                        "world_refs": {},
                        "context_summary": {},
                    },
                )
            )

            app._drain_events(events)

            self.assertIsNone(SessionStore(Path(tmp)).load())
            self.assertEqual(app.state.mode, "INTERRUPTED")
            self.assertIn("ignored", app.state.notice)

    def test_read_prompt_selects_read_tool_and_drops_stale_file_read_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output.md").write_text("hello", encoding="utf-8")
            provider = _CaptureProvider()
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=1)

            runtime.continue_run(
                {
                    "session_id": "sess_old",
                    "world_refs": {},
                    "context_summary": {
                        "blockers": [
                            "file.read reported error: inactive_tool:file.read",
                            "file.read request was denied for argument keys ['path']; treat that denial as evidence.",
                        ]
                    },
                },
                "[CURRENT USER REQUEST]\nread that output.md",
                active_user_request="read that output.md",
            )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(provider.prompt["state"]["context_summary"]["blockers"], [])

    def test_pi_style_file_tools_remain_available_for_vague_followup(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/ok": {
                        "kind": "file_management.read_file",
                        "arguments": {"path": "note.txt"},
                        "payload": {"path": "note.txt", "content": "note"},
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {"progress": ["file_management.read_file: file_management.read_file result"]},
            },
            "use those notes as one final version and remove the old draft",
            active_user_request="use those notes as one final version and remove the old draft",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.write_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.delete_file", provider.prompt["selection"]["selected_tools"])

    def test_code_evidence_remains_visible_for_short_continuation_followup(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/facebook": {
                        "kind": "file_management.read_file",
                        "freshness": "stable",
                        "arguments": {"path": "src/agents/facebook_surfer.py"},
                        "payload": {
                            "path": "src/agents/facebook_surfer.py",
                            "content": "class FacebookSurferAgent:\n    pass\n",
                        },
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {
                    "progress": ["file_management.read_file: file_management.read_file result"],
                    "evidence_refs": ["world://file_management.read_file/facebook"],
                },
            },
            "and ?",
            active_user_request="and ?",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("world://file_management.read_file/facebook", provider.prompt["world"]["world_refs"])

    def test_short_continuation_without_code_evidence_keeps_tools_but_no_refs(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {},
                "context_summary": {"progress": []},
            },
            "and ?",
            active_user_request="and ?",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(provider.prompt["world"]["world_refs"], {})
        self.assertEqual(provider.prompt["state"]["context_summary"]["evidence_refs"], [])

    def test_hermes_ui_hot_tail_keeps_file_tools_available_for_vague_repo_followup(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.tree/src": {
                        "kind": "file_management.tree",
                        "freshness": "turn",
                        "arguments": {"path": "src"},
                        "payload": {"entries": ["agents/", "  planner.py", "  reflection.py"]},
                        "summary": "file_management.tree result",
                    },
                    "world://file_management.find_files/agents": {
                        "kind": "file_management.find_files",
                        "freshness": "turn",
                        "arguments": {"path": "src/agents", "patterns": ["*.py"]},
                        "payload": {"matches": ["src/agents/planner.py", "src/agents/reflection.py"]},
                        "summary": "file_management.find_files result",
                    },
                },
                "context_summary": {
                    "progress": [
                        "file_management.tree: file_management.tree result",
                        "file_management.find_files: file_management.find_files result",
                    ],
                    "evidence_refs": [
                        "world://file_management.tree/src",
                        "world://file_management.find_files/agents",
                    ],
                },
            },
            (
                "[UI SESSION SUMMARY - REFERENCE ONLY]\n"
                "User is analyzing the repository under src/agents.\n"
                "--- END UI SESSION SUMMARY ---\n"
                "[RECENT UI TURNS]\n"
                "user: list dir under src\n"
                "assistant: src contains agents/, tools/, storage/\n"
                "user: show me full list\n"
                "assistant: src/agents contains planner.py, reflection.py, facebook_surfer.py\n\n"
                "assistant: Unable to analyze src/agents/planner.py because no useful read tools are available.\n\n"
                "[CURRENT USER REQUEST]\n"
                "analyze the planner one. give me full software desing patterns"
            ),
            active_user_request="analyze the planner one. give me full software desing patterns",
            ui_context={
                "conversation_summary": "User is analyzing the repository under src/agents.",
                "metrics": {"compaction_count": 2, "hot_lines": 6},
            },
        )

        self.assertIsNotNone(provider.prompt)
        self.assertEqual(
            provider.prompt["agent"]["request"],
            "analyze the planner one. give me full software desing patterns",
        )
        self.assertEqual(
            provider.prompt["agent"]["reference_request_context"],
            "analyze the planner one. give me full software desing patterns",
        )
        self.assertNotIn("no useful read tools", provider.prompt["agent"]["reference_request_context"])
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.find_files", provider.prompt["selection"]["selected_tools"])

    def test_referential_line_count_followup_keeps_prior_code_evidence_visible(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
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
            "how many lines in that",
            active_user_request="how many lines in that",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("world://file_management.read_file/facebook", provider.prompt["world"]["world_refs"])

    def test_retry_followup_keeps_prior_code_search_context_active(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/facebook": {
                        "kind": "file_management.read_file",
                        "freshness": "stable",
                        "arguments": {"path": "src/agents/facebook_surfer.py"},
                        "payload": {
                            "path": "src/agents/facebook_surfer.py",
                            "content": "class FacebookSurferAgent:\n    pass\n",
                        },
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {
                    "progress": ["file_management.read_file: file_management.read_file result"],
                    "evidence_refs": ["world://file_management.read_file/facebook"],
                },
            },
            "retry",
            active_user_request="retry",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])

    def test_referential_line_count_without_code_evidence_keeps_tools_but_no_refs(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {},
                "context_summary": {"progress": []},
            },
            "how many lines in that",
            active_user_request="how many lines in that",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.code_search", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(provider.prompt["world"]["world_refs"], {})
        self.assertEqual(provider.prompt["state"]["context_summary"]["evidence_refs"], [])

    def test_scan_codebase_prompt_selects_repo_snapshot_tool(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run("scan the codebase", active_user_request="scan the codebase")

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.repo_snapshot", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.find_files", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.search_text", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_many", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.tree", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.grep", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_range", provider.prompt["selection"]["selected_tools"])

    def test_repo_analysis_prompt_has_pi_style_read_only_file_tools_without_alias_guessing(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run("analyze the repo", active_user_request="analyze the repo")

        self.assertIsNotNone(provider.prompt)
        selected_tools = provider.prompt["selection"]["selected_tools"]
        self.assertIn("file_management.tree", selected_tools)
        self.assertIn("file_management.find_files", selected_tools)
        self.assertIn("file_management.read_file", selected_tools)
        self.assertIn("file_management.grep", selected_tools)
        self.assertNotIn("observe", selected_tools)
        self.assertNotIn("read_file", selected_tools)

    def test_greeting_with_fixes_does_not_activate_mutation_tools_or_action_guidance(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        result = runtime.run("hi after fixes", active_user_request="hi after fixes")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["assistant_message"], "captured")
        self.assertNotIn("file_management.write_file", provider.prompt["selection"]["selected_tools"])
        self.assertNotIn("file_management.edit_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(result["turn_feedback"], [])

    def test_noop_tool_call_is_repaired_without_terminal_failure(self) -> None:
        provider = _SequenceProvider(
            [
                {"kind": "tool_call", "payload": {"tool_id": "none", "arguments": {}}},
                {"kind": "finalize", "payload": {"message": "Hi! How can I help you with the codebase?"}},
            ]
        )
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=2)

        result = runtime.run("hi after fixes", active_user_request="hi after fixes")

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["assistant_message"], "Hi! How can I help you with the codebase?")
        self.assertTrue(any("No-op tool_call" in feedback for feedback in result["turn_feedback"]))

    def test_code_file_explanation_prompt_selects_file_read_tools(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run("explain me planner.py; full explanation", active_user_request="explain me planner.py; full explanation")

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.grep", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_range", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_many", provider.prompt["selection"]["selected_tools"])

    def test_add_helper_prompt_selects_file_mutation_tools(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run(
            "Add a new triple(value: int) -> int helper to src/math_utils.py. Preserve double.",
            active_user_request="Add a new triple(value: int) -> int helper to src/math_utils.py. Preserve double.",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.file_mutation", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.write_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])

    def test_fix_bug_prompt_selects_file_mutation_tools(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run(
            "Fix the discount bug in src/cart.py while preserving subtotal behavior.",
            active_user_request="Fix the discount bug in src/cart.py while preserving subtotal behavior.",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.file_mutation", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.write_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])

    def test_update_test_prompt_selects_file_mutation_tools(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run(
            "Update tests/test_math_utils.py with a test for triple(4) == 12.",
            active_user_request="Update tests/test_math_utils.py with a test for triple(4) == 12.",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.file_mutation", provider.prompt["selection"]["selected_skills"])
        self.assertIn("file_management.write_file", provider.prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])

    def test_provider_prompt_requires_full_multistep_completion_before_finalizing(self) -> None:
        prompt = _appv22_decision_prompt(
            {
                "agent": {"request": "write the report and remove the obsolete draft"},
                "state": {"context_summary": {"blockers": []}, "turn_feedback": []},
                "selection": {"selected_tools": ["file_management.write_file", "file_management.delete_file"]},
                "world": {"world_refs": {}},
            }
        )

        self.assertIn("fully satisfied", prompt)
        self.assertIn("before finalizing", prompt)

    def test_provider_prompt_forbids_meta_tool_intent_as_finalize(self) -> None:
        prompt = _appv22_decision_prompt(
            {
                "agent": {"request": "no, only src/agents list"},
                "state": {"context_summary": {"blockers": []}, "turn_feedback": []},
                "selection": {"selected_tools": ["file_management.tree"]},
                "world": {"world_refs": {}},
            }
        )

        self.assertIn("finalize must answer the user", prompt)
        self.assertIn("Requesting", prompt)
        self.assertIn("emit tool_call", prompt)

    def test_provider_prompt_surfaces_latest_tool_results_as_hot_context(self) -> None:
        prompt = _appv22_decision_prompt(
            {
                "state": {
                    "context_summary": {"blockers": []},
                    "turn_feedback": [],
                    "latest_tool_results": [
                        {
                            "tool_id": "file_management.repo_snapshot",
                            "status": "completed",
                            "payload": {"files": ["alpha.txt"], "directories": []},
                        }
                    ],
                }
            }
        )

        self.assertIn("LATEST TOOL RESULTS - HOT PI-STYLE CONTEXT", prompt)
        self.assertIn("alpha.txt", prompt)
        self.assertIn("kind must be finalize", prompt)

    def test_provider_prompt_guides_vague_followups_and_missing_file_recovery(self) -> None:
        prompt = _appv22_decision_prompt(
            {
                "agent": {"request": "next one is pii_redaction.py"},
                "state": {
                    "context_summary": {"blockers": []},
                    "turn_feedback": [
                        "file_management.read_file reported error: missing_file:src/pii_redaction.py"
                    ],
                    "latest_tool_results": [
                        {
                            "tool_id": "file_management.read_file",
                            "status": "failed",
                            "payload": {"errors": ["missing_file:src/pii_redaction.py"]},
                        }
                    ],
                },
                "selection": {
                    "selected_tools": ["file_management.find_files", "file_management.read_file"],
                },
            }
        )

        self.assertIn("For vague follow-ups such as 'next one is X'", prompt)
        self.assertIn("preserve the previous task shape", prompt)
        self.assertIn("bare filename", prompt)
        self.assertIn("selected discovery tools before selected exact-read tools", prompt)
        self.assertIn("missing_file", prompt)
        self.assertIn("do not finalize with a tool-unavailable answer", prompt)

    def test_file_management_skill_instructions_cover_bare_filename_followups(self) -> None:
        instructions = " ".join(CODE_SEARCH_SKILL.instructions)

        self.assertIn("bare filename", instructions)
        self.assertIn("next one is", instructions)
        self.assertIn("file_management.find_files before file_management.read_file", instructions)

    def test_runtime_emits_context_window_and_prompt_token_metrics(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        result = runtime.run("hi", active_user_request="hi")

        usage = result.get("usage")
        self.assertIsInstance(usage, dict)
        context = usage.get("context")
        self.assertIsInstance(context, dict)
        self.assertEqual(context["model_calls"], 1)
        self.assertEqual(context["context_window_chars"], 120000)
        self.assertGreater(context["total_prompt_estimated_tokens"], 0)
        self.assertEqual(len(context["model_call_contexts"]), 1)
        self.assertGreater(context["model_call_contexts"][0]["message_count"], 0)

    def test_provider_prompt_does_not_embed_internal_messages_lane(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.run("hi", active_user_request="hi")

        self.assertIsNotNone(provider.prompt)
        self.assertNotIn("messages", provider.prompt)

    def test_non_tool_prompt_keeps_pi_tools_without_hydrating_old_file_evidence(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/old": {
                        "kind": "file_management.read_file",
                        "arguments": {"path": "note.txt"},
                        "payload": {"path": "note.txt", "content": "old file evidence"},
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {
                    "progress": ["file_management.read_file: file_management.read_file result"],
                    "evidence_refs": ["world://file_management.read_file/old"],
                },
            },
            "hi",
            active_user_request="hi",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertIn("file_management.read_file", provider.prompt["selection"]["selected_tools"])
        self.assertEqual(provider.prompt["world"]["world_refs"], {})
        self.assertEqual(provider.prompt["state"]["context_summary"]["evidence_refs"], [])
        self.assertEqual(provider.prompt["state"]["context_summary"]["progress"], [])

    def test_duplicate_observe_reexecutes_like_safe_pi_tool_call(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {"tool_id": "file_management.repo_snapshot", "arguments": {}},
                },
                {
                    "kind": "tool_call",
                    "payload": {"tool_id": "file_management.repo_snapshot", "arguments": {}},
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.txt").write_text("alpha", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)

            result = runtime.run("list files", active_user_request="list files")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertFalse(any("duplicate observe tool call suppressed" in item for item in result["turn_feedback"]))
        self.assertEqual(
            [item["tool_id"] for item in result["tool_results"]],
            ["file_management.repo_snapshot", "file_management.repo_snapshot"],
        )

    def test_repeated_denied_tool_call_is_recorded_like_pi_tool_result(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {"tool_id": "file_management.read_file", "arguments": {"path": "../outside.txt"}},
                },
                {
                    "kind": "tool_call",
                    "payload": {"tool_id": "file_management.read_file", "arguments": {"path": "../outside.txt"}},
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)

            result = runtime.run("read outside file twice", active_user_request="read outside file twice")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertEqual(
            [item["tool_id"] for item in result["tool_results"]],
            ["file_management.read_file", "file_management.read_file"],
        )
        self.assertEqual([item["status"] for item in result["tool_results"]], ["denied", "denied"])

    def test_copy_file_preserve_source_denial_recovers_with_corrected_arguments(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.copy_file",
                        "arguments": {
                            "source": "docs/recovery_notes.md",
                            "destination": "docs/recovery_notes_copy.md",
                        },
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.copy_file",
                        "arguments": {
                            "source": "docs/recovery_notes.md",
                            "destination": "docs/recovery_notes_copy.md",
                            "preserve_source": True,
                        },
                    },
                },
                {
                    "kind": "finalize",
                    "payload": {"message": "Copied recovery notes."},
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "recovery_notes.md").write_text("# Recovery Notes\n", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.run(
                "copy docs/recovery_notes.md to docs/recovery_notes_copy.md",
                active_user_request="copy docs/recovery_notes.md to docs/recovery_notes_copy.md",
            )
            copied = (root / "docs" / "recovery_notes_copy.md").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(copied, "# Recovery Notes\n")
        self.assertEqual([item["status"] for item in result["tool_results"]], ["denied", "completed"])
        self.assertGreaterEqual(len(provider.prompts), 2)
        second_prompt_text = str(provider.prompts[1]["state"])
        self.assertIn("preserve_source:true", second_prompt_text)
        self.assertIn("corrected arguments", second_prompt_text)
        self.assertNotIn("without retrying the same denied call", second_prompt_text)

    def test_action_request_cannot_finalize_after_only_observe_evidence(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "tests/test_math_utils.py"},
                    },
                },
                {
                    "kind": "finalize",
                    "payload": {
                        "message": (
                            "tests/test_math_utils.py:\n"
                            "from src.math_utils import double\n\n"
                            "def test_double():\n"
                            "    assert double(3) == 6"
                        )
                    },
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_math_utils.py").write_text(
                "from src.math_utils import double\n\n"
                "def test_double():\n"
                "    assert double(3) == 6\n",
                encoding="utf-8",
            )
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)

            result = runtime.run(
                "Update tests/test_math_utils.py with a test for triple(4) == 12.",
                active_user_request="Update tests/test_math_utils.py with a test for triple(4) == 12.",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertIn("file_management.read_file", [item["tool_id"] for item in result["tool_results"]])
        self.assertTrue(
            any("action tool" in feedback and "finalize" in feedback for feedback in result["turn_feedback"]),
            result["turn_feedback"],
        )

    def test_analysis_only_request_can_finalize_after_observe_evidence_even_with_action_tools_selected(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "README.md"},
                    },
                },
                {
                    "kind": "finalize",
                    "payload": {"message": "README inspected; no writes performed."},
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("hello", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=4)

            result = runtime.run(
                "Inspect README.md and explain what it says. Do not write files.",
                active_user_request="Inspect README.md and explain what it says. Do not write files.",
            )

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["assistant_message"], "README inspected; no writes performed.")
        self.assertEqual(provider.kinds, ["tool_call", "finalize"])

    def test_no_edit_analysis_request_does_not_trigger_existing_file_edit_guidance(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "src/agents/planner.py"},
                    },
                },
                {
                    "kind": "finalize",
                    "payload": {"message": "PlanningAgent uses a facade-style planner around retrieval and LLM calls."},
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "agents").mkdir(parents=True)
            (root / "src" / "agents" / "planner.py").write_text(
                "class PlanningAgent:\n"
                "    def create_plan(self):\n"
                "        return 'plan'\n",
                encoding="utf-8",
            )
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.run(
                "analyze planner.py design patterns no edit",
                active_user_request="analyze planner.py design patterns no edit",
            )

        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(
            result["assistant_message"],
            "PlanningAgent uses a facade-style planner around retrieval and LLM calls.",
        )
        self.assertFalse(any("edit_file" in feedback for feedback in result["turn_feedback"]))

    def test_action_request_cannot_pause_complete_from_stale_action_evidence(self) -> None:
        provider = _SequenceProvider([{"kind": "pause", "payload": {"pause_type": "tool_blocked"}}])
        previous_result = {
            "session_id": "sess_old",
            "world_refs": {
                "world://file_management.write_file/old": {
                    "kind": "file_management.write_file",
                    "arguments": {"path": "src/math_utils.py", "content": "old", "overwrite": True},
                    "payload": {"path": "src/math_utils.py", "bytes_written": 3},
                    "summary": "file_management.write_file result",
                    "request_id": "req_old",
                    "run_id": "run_old",
                    "freshness": "stable",
                }
            },
            "context_summary": {
                "progress": ["file_management.write_file: file_management.write_file result"],
                "evidence_refs": ["world://file_management.write_file/old"],
            },
        }
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        result = runtime.continue_run(
            previous_result,
            "Update tests/test_math_utils.py with a test for triple(4) == 12.",
            active_user_request="Update tests/test_math_utils.py with a test for triple(4) == 12.",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertTrue(any("Pause is premature" in feedback for feedback in result["turn_feedback"]))

    def test_action_request_exhaustion_reports_missing_current_action_evidence(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "src/math_utils.py"},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "src/math_utils.py"},
                    },
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "math_utils.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)

            result = runtime.run(
                "Add negate(x) to src/math_utils.py.",
                active_user_request="Add negate(x) to src/math_utils.py.",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertTrue(any("current action evidence" in feedback for feedback in result["turn_feedback"]))
        self.assertIn("current action evidence", result["assistant_message"])

    def test_read_only_followup_after_stale_write_guidance_does_not_report_mutation_failure(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "tests/test_math_utils.py"},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "tests/test_math_utils.py"},
                    },
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_math_utils.py").write_text(
                "from src.math_utils import double\n\n"
                "def test_double():\n"
                "    assert double(2) == 4\n",
                encoding="utf-8",
            )
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)
            previous_result = {
                "status": "failed",
                "reason": "max_turns_exceeded",
                "session_id": "sess_test",
                "world_refs": {},
                "context_summary": {
                    "blockers": [
                        "The latest file mutation request has no completed write evidence; the next decision must be a tool_call to file_management.write_file before finalizing.",
                        "Finalization guidance names selected tool file_management.write_file; the next decision should call file_management.write_file before finalizing.",
                    ]
                },
            }

            result = runtime.continue_run(
                previous_result,
                "Read only the tests file and tell me whether all helpers now have at least one test. Do not edit.",
                active_user_request=(
                    "Read only the tests file and tell me whether all helpers now have at least one test. Do not edit."
                ),
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertFalse(any("current action evidence" in feedback for feedback in result["turn_feedback"]))
        self.assertNotIn("workspace change", result.get("assistant_message", ""))

    def test_repeated_observe_in_action_request_guides_to_action_without_reexecuting(self) -> None:
        updated_source = (
            "def word_count(text):\n"
            "    return len(text.split())\n\n\n"
            "def character_count(text):\n"
            "    return len(text)\n"
        )
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "src/text_metrics.py"},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "src/text_metrics.py"},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.write_file",
                        "arguments": {"path": "src/text_metrics.py", "content": updated_source, "overwrite": True},
                    },
                },
                {"kind": "finalize", "payload": {"message": "character_count added"}},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "text_metrics.py").write_text(
                "def word_count(text):\n    return len(text.split())\n",
                encoding="utf-8",
            )
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=4)

            result = runtime.run(
                "Add character_count(text) to src/text_metrics.py.",
                active_user_request="Add character_count(text) to src/text_metrics.py.",
            )

            written = (root / "src" / "text_metrics.py").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertIn("def character_count", written)
        self.assertEqual(
            [item["tool_id"] for item in result["tool_results"]],
            ["file_management.read_file", "file_management.read_file", "file_management.write_file"],
        )
        self.assertFalse(any("Repeated observe evidence" in feedback for feedback in result["turn_feedback"]))

    def test_action_request_exhaustion_reports_partial_current_action_evidence(self) -> None:
        updated_source = "def double(x):\n    return x * 2\n\n\ndef negate(x):\n    return -x\n"
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.write_file",
                        "arguments": {"path": "src/math_utils.py", "content": updated_source, "overwrite": True},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "tests/test_math_utils.py"},
                    },
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "math_utils.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
            (root / "tests" / "test_math_utils.py").write_text("from src.math_utils import double\n", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=2)

            result = runtime.run(
                "Add negate(x) to src/math_utils.py and update tests/test_math_utils.py for negate.",
                active_user_request="Add negate(x) to src/math_utils.py and update tests/test_math_utils.py for negate.",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "max_turns_exceeded")
        self.assertTrue(any("partial current action evidence" in feedback for feedback in result["turn_feedback"]))
        self.assertIn("partial current action evidence", result["assistant_message"])
        self.assertTrue(
            any(ref.get("arguments", {}).get("path") == "src/math_utils.py" for ref in result["world_refs"].values())
        )

    def test_stale_duplicate_action_evidence_does_not_complete_new_mutation_request(self) -> None:
        old_content = (
            "def double(x):\n"
            "    return x * 2\n\n"
            "def triple(x):\n"
            "    return x * 3\n"
        )
        new_content = old_content + "\n\ndef negate(x):\n    return -x\n"
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.write_file",
                        "arguments": {"path": "src/math_utils.py", "content": old_content, "overwrite": True},
                    },
                },
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.write_file",
                        "arguments": {"path": "src/math_utils.py", "content": new_content, "overwrite": True},
                    },
                },
                {"kind": "finalize", "payload": {"assistant_message": "negate added"}},
            ]
        )
        previous_result = {
            "session_id": "sess_old",
            "world_refs": {
                "world://file_management.write_file/old": {
                    "kind": "file_management.write_file",
                    "arguments": {"path": "src/math_utils.py", "content": old_content, "overwrite": True},
                    "payload": {"path": "src/math_utils.py", "bytes_written": len(old_content)},
                    "summary": "file_management.write_file result",
                    "request_id": "req_old",
                    "run_id": "run_old",
                    "freshness": "stable",
                }
            },
            "context_summary": {
                "progress": ["file_management.write_file: file_management.write_file result"],
                "evidence_refs": ["world://file_management.write_file/old"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "math_utils.py").write_text(old_content, encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.continue_run(
                previous_result,
                "Add negate(x) to math_utils and add pytest coverage for positive, negative, and zero values.",
                active_user_request="Add negate(x) to math_utils and add pytest coverage for positive, negative, and zero values.",
            )

            written = (root / "src" / "math_utils.py").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertIn("def negate", written)
        self.assertEqual(
            [item["tool_id"] for item in result["tool_results"]],
            ["file_management.write_file", "file_management.write_file"],
        )
        self.assertFalse(any("stale action evidence" in feedback for feedback in result["turn_feedback"]))
        self.assertFalse(
            any(
                "Duplicate completed tool call suppressed" in progress
                for progress in result["context_summary"].get("progress", [])
            )
        )

    def test_action_request_pause_repairs_into_selected_action_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "math_utils.py").write_text(
                "def double(value: int) -> int:\n"
                "    return value * 2\n\n"
                "def triple(value: int) -> int:\n"
                "    return value * 3\n",
                encoding="utf-8",
            )
            updated = (
                "def double(value: int) -> int:\n"
                "    return value * 2\n\n"
                "def triple(value: int) -> int:\n"
                "    return value * 3\n\n"
                "def square(value: int) -> int:\n"
                "    return value * value\n"
            )
            provider = _SequenceProvider(
                [
                    {"kind": "pause", "payload": {"pause_type": "tool_blocked"}},
                    {
                        "kind": "tool_call",
                        "payload": {
                            "tool_id": "file_management.write_file",
                            "arguments": {"path": "src/math_utils.py", "content": updated, "overwrite": True},
                        },
                    },
                    {"kind": "finalize", "payload": {"message": "Added square."}},
                ]
            )
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.run(
                "Add a square(value: int) -> int helper to src/math_utils.py. Preserve the existing helpers.",
                active_user_request=(
                    "Add a square(value: int) -> int helper to src/math_utils.py. Preserve the existing helpers."
                ),
            )
            final_content = (root / "src" / "math_utils.py").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(provider.kinds, ["pause", "tool_call", "finalize"])
        self.assertIn("def square(value: int) -> int:", final_content)
        self.assertTrue(any("pause" in feedback.lower() for feedback in result["turn_feedback"]))

    def test_context_harness_promotes_latest_tool_results_as_hot_lane(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.tool_results["toolres_000001"] = {
            "tool_result_id": "toolres_000001",
            "tool_id": "file_management.repo_snapshot",
            "status": "completed",
            "arguments": {},
            "payload": {
                "files": ["alpha.txt"],
                "directories": [],
                "text_previews": {},
            },
            "evidence_refs": ["world://file_management.repo_snapshot/current"],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        latest = packet.provider_prompt["state"]["latest_tool_results"]
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["tool_id"], "file_management.repo_snapshot")
        self.assertEqual(latest[0]["payload"]["files"], ["alpha.txt"])

    def test_context_harness_promotes_transformed_tool_result_model_view(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.tool_results["toolres_000001"] = {
            "tool_result_id": "toolres_000001",
            "tool_id": "file_management.repo_snapshot",
            "status": "completed",
            "arguments": {},
            "payload": {"files": ["alpha.txt"], "directories": ["docs"], "text_previews": {}},
            "model_view": "Directories: docs\nFiles: alpha.txt",
            "user_message": "Directories: docs\nFiles: alpha.txt",
            "evidence_refs": ["world://file_management.repo_snapshot/current"],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        latest = packet.provider_prompt["state"]["latest_tool_results"]
        self.assertIn("Directories: docs", latest[0]["model_view"])
        self.assertIn("Files: alpha.txt", latest[0]["model_view"])

    def test_observe_fallback_uses_transformed_user_message_not_raw_json(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services)
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.tool_results["toolres_000001"] = {
            "tool_result_id": "toolres_000001",
            "tool_id": "file_management.repo_snapshot",
            "status": "completed",
            "arguments": {},
            "payload": {"files": ["alpha.txt"], "directories": ["docs"], "text_previews": {}},
            "user_message": "Directories: docs\nFiles: alpha.txt",
            "evidence_refs": ["world://file_management.repo_snapshot/current"],
        }

        resolved = services.extension_registry.resolve_active(state)
        packet = services.context_harness.prepare_turn(state, resolved)

        latest = packet.provider_prompt["state"]["latest_tool_results"]
        self.assertEqual(latest[-1]["payload"]["files"], ["alpha.txt"])
        self.assertEqual(latest[-1]["payload"]["directories"], ["docs"])

    def test_repeated_observe_turn_preserves_pi_read_tool_surface(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.tool_results["toolres_000001"] = {
            "tool_result_id": "toolres_000001",
            "tool_id": "file_management.repo_snapshot",
            "status": "completed",
            "arguments": {},
            "payload": {"files": ["alpha.txt"], "directories": [], "text_previews": {}},
            "evidence_refs": ["world://file_management.repo_snapshot/current"],
        }
        state.tool_results["toolres_000002"] = {
            "tool_result_id": "toolres_000002",
            "tool_id": "file_management.repo_snapshot",
            "status": "completed",
            "arguments": {},
            "payload": {"files": ["alpha.txt"], "directories": [], "text_previews": {}},
            "evidence_refs": ["world://file_management.repo_snapshot/current2"],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        self.assertIn("file_management.tree", packet.provider_prompt["selection"]["selected_tools"])
        self.assertIn("file_management.repo_snapshot", packet.provider_prompt["selection"]["selected_tools"])
        self.assertIn("file_management.find_files", packet.provider_prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", packet.provider_prompt["selection"]["selected_tools"])
        self.assertEqual(packet.provider_prompt["tools"], packet.provider_prompt["selection"]["selected_tools"])
        self.assertEqual(packet.provider_prompt["state"]["latest_tool_results"][-1]["payload"]["files"], ["alpha.txt"])

    def test_repeated_observe_does_not_close_action_tool_surface(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState(
            "sess_test",
            "run_test",
            RequestEnvelope(
                "req_test",
                "Update tests/test_math_utils.py with a test for triple(4) == 12.",
                ".",
                active_user_request="Update tests/test_math_utils.py with a test for triple(4) == 12.",
            ),
        )
        for index in range(2):
            state.tool_results[f"toolres_{index + 1:06d}"] = {
                "tool_result_id": f"toolres_{index + 1:06d}",
                "tool_id": "file_management.read_file",
                "status": "completed",
                "arguments": {"path": "tests/test_math_utils.py"},
                "payload": {"path": "tests/test_math_utils.py", "content": "from src.math_utils import double\n"},
                "evidence_refs": [f"world://file_management.read_file/current{index}"],
            }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        self.assertIn("file_management.file_mutation", packet.provider_prompt["selection"]["selected_skills"])
        self.assertIn("file_management.write_file", packet.provider_prompt["selection"]["selected_tools"])
        self.assertIn("file_management.read_file", packet.provider_prompt["selection"]["selected_tools"])

    def test_context_harness_hides_prior_request_action_refs_for_new_mutation_prompt(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState(
            "sess_test",
            "run_new",
            RequestEnvelope(
                "req_new",
                "Add negate(x) to src/math_utils.py and update tests.",
                ".",
                active_user_request="Add negate(x) to src/math_utils.py and update tests.",
            ),
        )
        state.world_refs = {
            "world://file_management.write_file/old": {
                "kind": "file_management.write_file",
                "arguments": {"path": "src/math_utils.py", "content": "def square(x):\n    return x * x\n"},
                "summary": "file_management.write_file result",
                "payload": {"path": "src/math_utils.py", "bytes_written": 32},
                "request_id": "req_old",
                "run_id": "run_old",
                "freshness": "stable",
                "mutation_seq": 1,
            },
            "world://file_management.read_file/current": {
                "kind": "file_management.read_file",
                "arguments": {"path": "src/math_utils.py"},
                "summary": "file_management.read_file result",
                "payload": {"path": "src/math_utils.py", "content": "def square(x):\n    return x * x\n"},
                "request_id": "req_old",
                "run_id": "run_old",
                "freshness": "stable",
                "mutation_seq": 1,
            },
        }
        state.mutation_seq = 1
        state.context_summary = {
            "progress": [
                "file_management.write_file: file_management.write_file result",
                "file_management.read_file: file_management.read_file result",
            ],
            "evidence_refs": [
                "world://file_management.write_file/old",
                "world://file_management.read_file/current",
            ],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        self.assertIn("file_management.write_file", packet.provider_prompt["selection"]["selected_tools"])
        self.assertNotIn("world://file_management.write_file/old", packet.provider_prompt["world"]["world_refs"])
        self.assertIn("world://file_management.read_file/current", packet.provider_prompt["world"]["world_refs"])
        summary = packet.provider_prompt["state"]["context_summary"]
        self.assertNotIn("world://file_management.write_file/old", summary["evidence_refs"])
        self.assertIn("world://file_management.read_file/current", summary["evidence_refs"])
        self.assertNotIn("file_management.write_file: file_management.write_file result", summary["progress"])
        self.assertIn("file_management.read_file: file_management.read_file result", summary["progress"])

    def test_context_harness_exposes_safe_action_refs_for_read_only_followup(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState(
            "sess_test",
            "run_new",
            RequestEnvelope(
                "req_new",
                "show me the file you wrote",
                ".",
                active_user_request="show me the file you wrote",
            ),
        )
        state.world_refs = {
            "world://file_management.write_file/old": {
                "kind": "file_management.write_file",
                "arguments": {"path": "src/math_utils.py", "content": "def double(x):\n    return x * 2\n"},
                "summary": "file_management.write_file result",
                "payload": {"path": "src/math_utils.py", "bytes_written": 32},
                "request_id": "req_old",
                "run_id": "run_old",
                "freshness": "stable",
                "mutation_seq": 1,
            }
        }
        state.mutation_seq = 1
        state.context_summary = {
            "progress": ["file_management.write_file: file_management.write_file result"],
            "evidence_refs": ["world://file_management.write_file/old"],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        self.assertIn("file_management.read_file", packet.provider_prompt["selection"]["selected_tools"])
        self.assertNotIn("world://file_management.write_file/old", packet.provider_prompt["world"]["world_refs"])
        action_refs = packet.provider_prompt["state"]["action_refs"]
        self.assertEqual(action_refs[0]["ref_id"], "world://file_management.write_file/old")
        self.assertEqual(action_refs[0]["kind"], "file_management.write_file")
        self.assertEqual(action_refs[0]["paths"], ["src/math_utils.py"])
        self.assertNotIn("content", action_refs[0])

    def test_context_harness_labels_move_action_refs_for_compacted_summaries(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState(
            "sess_test",
            "run_new",
            RequestEnvelope(
                "req_new",
                "summarize all modified files",
                ".",
                active_user_request="summarize all modified files",
            ),
        )
        state.world_refs = {
            "world://file_management.move_file/old": {
                "kind": "file_management.move_file",
                "arguments": {
                    "source": "docs/math_utils_notes.md",
                    "destination": "docs/math_helpers.md",
                },
                "summary": "file_management.move_file result",
                "payload": {
                    "source": "docs/math_utils_notes.md",
                    "destination": "docs/math_helpers.md",
                },
                "request_id": "req_old",
                "run_id": "run_old",
                "freshness": "stable",
                "mutation_seq": 1,
            }
        }
        state.mutation_seq = 1
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        action_refs = packet.provider_prompt["state"]["action_refs"]
        self.assertEqual(action_refs[0]["kind"], "file_management.move_file")
        self.assertEqual(action_refs[0]["paths"], ["docs/math_helpers.md"])
        self.assertEqual(action_refs[0]["source"], "docs/math_utils_notes.md")
        self.assertEqual(action_refs[0]["destination"], "docs/math_helpers.md")
        self.assertEqual(action_refs[0]["current_paths"], ["docs/math_helpers.md"])
        self.assertEqual(action_refs[0]["obsolete_paths"], ["docs/math_utils_notes.md"])

    def test_continue_run_resolves_blockers_from_existing_world_refs_before_prompt(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/ok": {
                        "kind": "file_management.read_file",
                        "summary": "file_management.read_file result",
                    }
                },
                "context_summary": {
                    "blockers": [
                        "file_management.read_file reported error: missing_file:cat.txt",
                        "file_management.read_file request was failed for argument keys ['path']; treat that failure as evidence.",
                    ],
                    "progress": ["file_management.read_file: file_management.read_file result"],
                },
            },
            "read note.txt",
            active_user_request="read note.txt",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertEqual(provider.prompt["state"]["context_summary"]["blockers"], [])
        self.assertIn(
            "file_management.read_file: prior failed/denied tool risk resolved by later successful result",
            provider.prompt["state"]["context_summary"]["progress"],
        )

    def test_successful_tool_result_resolves_prior_same_tool_blockers(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))
        state.context_summary = {
            "blockers": [
                "file_management.read_file reported error: missing_file:cat.txt",
                "file_management.read_file request was failed for argument keys ['path']; treat that failure as evidence.",
                "approval required: other_tool still_active",
            ],
            "progress": [],
        }

        runtime._record_tool_result(
            state,
            {
                "tool_result_id": "toolres_read",
                "tool_id": "file_management.read_file",
                "status": "completed",
                "payload": {"content": "note", "bytes_read": 4, "path": "note.txt"},
                "payload_ref": "world://file_management.read_file/test",
                "evidence_refs": ["world://file_management.read_file/test"],
                "arguments": {"path": "note.txt"},
            },
        )

        self.assertEqual(state.context_summary["blockers"], ["approval required: other_tool still_active"])
        self.assertIn(
            "file_management.read_file: prior failed/denied tool risk resolved by later successful result",
            state.context_summary["progress"],
        )

    def test_payloadless_observe_world_ref_does_not_suppress_rehydration(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services_with_registry(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.world_refs = {
            "world://file_management.repo_snapshot/payloadless": {
                "kind": "file_management.repo_snapshot",
                "arguments": {},
                "summary": "file_management.repo_snapshot result",
            }
        }

        self.assertFalse(
            runtime._world_ref_has_usable_payload(
                state,
                None,
                state.world_refs["world://file_management.repo_snapshot/payloadless"],
            )
        )

    def test_persisted_repo_snapshot_does_not_suppress_fresh_list_request(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=services,
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "list files", "."))
        state.world_refs = {
            "world://file_management.repo_snapshot/stale": {
                "kind": "file_management.repo_snapshot",
                "arguments": {},
                "summary": "file_management.repo_snapshot result",
                "payload": {"files": ["stale.txt"], "directories": []},
                "freshness": "turn",
                "request_id": "old_request",
                "run_id": "old_run",
            }
        }

        self.assertFalse(runtime._world_ref_fresh_for_tool(state, state.world_refs["world://file_management.repo_snapshot/stale"]))

    def test_read_file_evidence_is_stale_after_workspace_mutation(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services)
        state = AgentState(
            "sess_test",
            "run_test",
            RequestEnvelope(
                "req_test",
                "Please inspect tests/test_math_utils.py and tell me what behavior it now covers.",
                ".",
                active_user_request="Please inspect tests/test_math_utils.py and tell me what behavior it now covers.",
            ),
        )
        state.mutation_seq = 1
        state.world_refs = {
            "world://file_management.read_file/stale": {
                "kind": "file_management.read_file",
                "arguments": {"path": "tests/test_math_utils.py"},
                "summary": "file_management.read_file result",
                "payload": {
                    "path": "tests/test_math_utils.py",
                    "content": "from src.math_utils import double\n",
                    "line_count": 1,
                },
                "mutation_seq": 0,
            }
        }
        resolved = services.extension_registry.resolve_active(state)
        state.active_extension_ids = list(resolved.extension_ids)

        self.assertFalse(runtime._observation_contract_satisfied(state, resolved))

    def test_continue_run_restores_mutation_sequence_from_persisted_world_refs(self) -> None:
        provider = _CaptureProvider()
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=1)

        runtime.continue_run(
            {
                "session_id": "sess_old",
                "world_refs": {
                    "world://file_management.read_file/before": {
                        "kind": "file_management.read_file",
                        "arguments": {"path": "src/math_utils.py"},
                        "payload": {"path": "src/math_utils.py", "content": "def double(value):\n    return value * 2\n"},
                        "summary": "file_management.read_file result",
                        "mutation_seq": 0,
                    },
                    "world://file_management.write_file/after": {
                        "kind": "file_management.write_file",
                        "arguments": {"path": "src/math_utils.py"},
                        "payload": {"path": "src/math_utils.py", "bytes_written": 90},
                        "summary": "file_management.write_file result",
                        "mutation_seq": 1,
                    },
                },
                "context_summary": {
                    "progress": [
                        "file_management.read_file: file_management.read_file result",
                        "file_management.write_file: file_management.write_file result",
                    ],
                    "evidence_refs": [
                        "world://file_management.read_file/before",
                        "world://file_management.write_file/after",
                    ],
                },
            },
            "how many lines are in that file now",
            active_user_request="how many lines are in that file now",
        )

        self.assertIsNotNone(provider.prompt)
        self.assertNotIn("world://file_management.read_file/before", provider.prompt["world"]["world_refs"])

    def test_clipped_read_file_evidence_without_line_count_does_not_satisfy_line_count_followup(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services)
        state = AgentState(
            "sess_test",
            "run_test",
            RequestEnvelope("req_test", "how many lines in that", ".", active_user_request="how many lines in that"),
        )
        state.world_refs = {
            "world://file_management.read_file/clipped": {
                "kind": "file_management.read_file",
                "arguments": {"path": "src/agents/facebook_surfer.py"},
                "summary": "file_management.read_file result",
                "payload": {
                    "path": "src/agents/facebook_surfer.py",
                    "content": "x" * 12000,
                    "content_truncated_by_session": True,
                },
            }
        }
        resolved = services.extension_registry.resolve_active(state)
        state.active_extension_ids = list(resolved.extension_ids)

        self.assertIn("file_management.code_search", [card.skill_id for card in resolved.skill_cards])
        self.assertFalse(runtime._observation_contract_satisfied(state, resolved))

    def test_clipped_read_file_evidence_with_line_count_satisfies_line_count_followup(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services)
        state = AgentState(
            "sess_test",
            "run_test",
            RequestEnvelope("req_test", "how many lines in that", ".", active_user_request="how many lines in that"),
        )
        state.world_refs = {
            "world://file_management.read_file/clipped": {
                "kind": "file_management.read_file",
                "arguments": {"path": "src/agents/facebook_surfer.py"},
                "summary": "file_management.read_file result",
                "payload": {
                    "path": "src/agents/facebook_surfer.py",
                    "content": "x" * 12000,
                    "content_truncated_by_session": True,
                    "line_count": 828,
                },
            }
        }
        resolved = services.extension_registry.resolve_active(state)
        state.active_extension_ids = list(resolved.extension_ids)

        self.assertTrue(runtime._observation_contract_satisfied(state, resolved))

    def test_turn_scoped_world_ref_is_not_fresh_across_requests(self) -> None:
        registry = ToolRegistry()
        definition = ToolDefinition(
            "test.observe",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            "test",
            "test",
            freshness="turn",
        )
        state = AgentState("sess_test", "run_new", RequestEnvelope("req_new", "test", "."))
        self.assertFalse(
            is_world_ref_fresh(
                state,
                {"kind": "test.observe", "request_id": "req_old", "run_id": "run_old"},
                definition,
            )
        )
        registry.register(definition, lambda _args, _context: {"status": "completed"})

    def test_stable_world_ref_is_fresh_without_turn_metadata(self) -> None:
        definition = ToolDefinition(
            "test.stable",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            "test",
            "test",
        )
        state = AgentState("sess_test", "run_new", RequestEnvelope("req_new", "test", "."))

        self.assertTrue(is_world_ref_fresh(state, {"kind": "test.stable"}, definition))

    def test_context_harness_filters_stale_mutable_refs_from_model_context(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState("sess_test", "run_new", RequestEnvelope("req_new", "read note.txt", "."))
        state.world_refs = {
            "world://file_management.repo_snapshot/stale": {
                "kind": "file_management.repo_snapshot",
                "arguments": {},
                "summary": "file_management.repo_snapshot result",
                "payload": {"files": ["old.txt"], "directories": []},
                "freshness": "turn",
                "request_id": "req_old",
                "run_id": "run_old",
                "mutation_seq": 0,
            },
            "world://file_management.read_file/stable": {
                "kind": "file_management.read_file",
                "arguments": {"path": "note.txt"},
                "summary": "file_management.read_file result",
                "payload": {"path": "note.txt", "content": "hello"},
            },
        }
        state.context_summary = {
            "progress": [
                "file_management.repo_snapshot: file_management.repo_snapshot result",
                "file_management.read_file: file_management.read_file result",
            ],
            "evidence_refs": [
                "world://file_management.repo_snapshot/stale",
                "world://file_management.read_file/stable",
            ],
        }
        resolved = services.extension_registry.resolve_active(state)

        packet = services.context_harness.prepare_turn(state, resolved)

        self.assertNotIn("world://file_management.repo_snapshot/stale", packet.provider_prompt["world"]["world_refs"])
        self.assertIn("world://file_management.read_file/stable", packet.provider_prompt["world"]["world_refs"])
        summary = packet.provider_prompt["state"]["context_summary"]
        self.assertNotIn("world://file_management.repo_snapshot/stale", summary["evidence_refs"])
        self.assertIn("world://file_management.read_file/stable", summary["evidence_refs"])

    def test_context_harness_compacts_durable_memory_lane_without_dropping_evidence_ledger(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )
        state = AgentState("sess_test", "run_new", RequestEnvelope("req_new", "continue", "."))
        state.context_summary = {
            "progress": [f"progress item {index} " + ("x" * 800) for index in range(80)],
            "blockers": [f"blocker {index} " + ("y" * 800) for index in range(20)],
            "evidence_refs": [f"world://file_management.read_file/{index}" for index in range(120)],
        }

        result = services.context_harness.compact_if_needed(state)

        self.assertTrue(result.compacted)
        self.assertLess(result.after_chars, result.before_chars)
        self.assertEqual(len(state.context_summary["progress"]), 16)
        self.assertEqual(len(state.context_summary["blockers"]), 16)
        self.assertEqual(len(state.context_summary["evidence_refs"]), 96)
        self.assertIn("world://file_management.read_file/119", state.context_summary["evidence_refs"])

    def test_repo_snapshot_uses_content_addressed_world_ref_not_latest(self) -> None:
        services = create_appv22_services(
            root_path=Path("."),
            provider=_CaptureProvider(),
            extensions=[FileManagementExtension()],
        )

        result = services.broker.execute(
            "file_management.repo_snapshot",
            {},
            active_tool_ids={"file_management.repo_snapshot"},
        )

        self.assertEqual(result["status"], "completed")
        self.assertIsInstance(result.get("payload_ref"), str)
        self.assertTrue(result["payload_ref"].startswith("world://file_management.repo_snapshot/"))
        self.assertTrue(result["payload_ref"].endswith("/latest"))

    def test_malformed_tool_call_guidance_is_turn_feedback_not_persisted_blocker(self) -> None:
        runtime = AppV22AgentRuntime(
            root_path=Path("."),
            services=_unused_services(),
        )
        state = AgentState("sess_test", "run_test", RequestEnvelope("req_test", "test", "."))
        state.active_tool_ids = ["file_management.read_file"]
        decision = type("Decision", (), {"payload": {}, "kind": "tool_call"})()
        resolved = type("Resolved", (), {"tool_ids": ("file_management.read_file",)})()

        runtime._handle_tool_call(state, decision, resolved)

        self.assertEqual(state.context_summary.get("blockers", []), [])
        self.assertIn(
            "Malformed tool_call decision was missing payload.tool_id; treated as turn-local provider repair feedback. Continue from selected tools or existing evidence.",
            state.turn_feedback,
        )

    def test_inactive_directory_observe_denial_names_selected_file_tools(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "observe_directory",
                        "arguments": {"path": "src"},
                    },
                },
                {"kind": "finalize", "payload": {"assistant_message": "continued from prior evidence"}},
            ]
        )
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=2)

        result = runtime.run("what's inside src", active_user_request="what's inside src")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "unresolved_tool_feedback")
        self.assertTrue(any("observe_directory" in feedback for feedback in result["turn_feedback"]))
        feedback_text = str(result["context_summary"]) + str(result["turn_feedback"])
        self.assertIn("file_management.tree", feedback_text)
        self.assertIn("file_management.repo_snapshot", feedback_text)

    def test_expected_missing_file_read_can_finalize_without_recovery_tool(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "plan/appv22-live-scope/missing.md"},
                    },
                },
                {"kind": "finalize", "payload": {"message": "plan/appv22-live-scope/missing.md is missing."}},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plan" / "appv22-live-scope").mkdir(parents=True)
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.run(
                "Try to read plan/appv22-live-scope/missing.md, then tell me it is missing if it does not exist. Do not create it.",
                active_user_request=(
                    "Try to read plan/appv22-live-scope/missing.md, then tell me it is missing if it does not exist. Do not create it."
                ),
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["assistant_message"], "plan/appv22-live-scope/missing.md is missing.")
        self.assertTrue(any(item["status"] == "failed" for item in result["tool_results"]))

    def test_expected_protected_read_denial_can_finalize_without_recovery_tool(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "file_management.read_file",
                        "arguments": {"path": "secrets/prod.env"},
                    },
                },
                {"kind": "finalize", "payload": {"message": "Access to secrets/prod.env is denied."}},
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "secrets").mkdir()
            (root / "secrets" / "prod.env").write_text("TOKEN=secret", encoding="utf-8")
            services = create_appv22_services(
                root_path=root,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            runtime = AppV22AgentRuntime(root_path=root, services=services, max_turns=3)

            result = runtime.run(
                "Try to read secrets/prod.env and tell me if access is denied. Do not work around it and do not edit.",
                active_user_request=(
                    "Try to read secrets/prod.env and tell me if access is denied. Do not work around it and do not edit."
                ),
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["assistant_message"], "Access to secrets/prod.env is denied.")
        self.assertTrue(any(item["status"] == "denied" for item in result["tool_results"]))

    def test_inactive_tool_denial_does_not_emit_duplicate_context_summary_updates(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "observe_directory",
                        "arguments": {"path": "src"},
                    },
                },
                {"kind": "finalize", "payload": {"assistant_message": "continued from prior evidence"}},
            ]
        )
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=2)

        result = runtime.run("what's inside src", active_user_request="what's inside src")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "unresolved_tool_feedback")
        self.assertTrue(any("observe_directory" in feedback for feedback in result["turn_feedback"]))

    def test_inactive_tool_denial_cannot_complete_without_later_successful_evidence(self) -> None:
        provider = _SequenceProvider(
            [
                {
                    "kind": "tool_call",
                    "payload": {
                        "tool_id": "observe_directory",
                        "arguments": {"path": "src"},
                    },
                },
                {"kind": "finalize", "payload": {"assistant_message": "I cannot inspect src."}},
            ]
        )
        services = create_appv22_services(
            root_path=Path("."),
            provider=provider,
            extensions=[FileManagementExtension()],
        )
        runtime = AppV22AgentRuntime(root_path=Path("."), services=services, max_turns=2)

        result = runtime.run("what's inside src", active_user_request="what's inside src")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "unresolved_tool_feedback")
        self.assertTrue(any(item["status"] == "denied" for item in result["tool_results"]))
        self.assertFalse(any(event["event_type"] == "RunCompleted" for event in result["events"]))


def _unused_services() -> AppV22Services:
    return AppV22Services(
        root_path=Path("."),
        provider=object(),
        extension_registry=object(),
        tool_registry=object(),
        broker=object(),
        context_selector=object(),
        prompt_builder=object(),
        gateway_guard=object(),
        compressor=object(),
    )


def _unused_services_with_registry() -> AppV22Services:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            "test",
            "test",
        ),
        lambda _args, _context: {"status": "completed"},
    )
    return AppV22Services(
        root_path=Path("."),
        provider=object(),
        extension_registry=object(),
        tool_registry=registry,
        broker=object(),
        context_selector=object(),
        prompt_builder=object(),
        gateway_guard=object(),
        compressor=object(),
    )


class _CaptureProvider:
    def __init__(self) -> None:
        self.prompt = None

    def decide(self, prompt):
        self.prompt = prompt
        return {"kind": "finalize", "payload": {"assistant_message": "captured"}}


class _SequenceProvider:
    def __init__(self, decisions: list[dict]) -> None:
        self.decisions = list(decisions)
        self.kinds: list[str] = []
        self.prompts: list[dict] = []

    def decide(self, prompt):
        from appv22.runtime.decisions import RuntimeDecision

        self.prompts.append(prompt)
        decision = self.decisions.pop(0)
        self.kinds.append(decision["kind"])
        return RuntimeDecision(
            kind=decision["kind"],
            reason=decision.get("reason", "test_decision"),
            payload=decision.get("payload", {}),
        )


if __name__ == "__main__":
    unittest.main()
