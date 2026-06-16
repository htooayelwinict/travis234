from __future__ import annotations

from pathlib import Path
import queue
import tempfile
import unittest

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.file_management.tools import mkdir, repo_snapshot, write_file
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

    def test_non_tool_prompt_does_not_select_file_tools_or_hydrate_old_file_evidence(self) -> None:
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
        self.assertEqual(provider.prompt["selection"]["selected_tools"], [])
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
        self.assertEqual(provider.kinds, ["tool_call", "tool_call"])

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

    def test_repeated_observe_turn_closes_tool_surface_without_domain_classifier(self) -> None:
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

        self.assertEqual(packet.provider_prompt["selection"]["selected_tools"], [])
        self.assertEqual(packet.provider_prompt["tools"], [])
        self.assertEqual(packet.provider_prompt["state"]["latest_tool_results"][-1]["payload"]["files"], ["alpha.txt"])

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
            "list files",
            active_user_request="list files",
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

        exists = runtime._tool_call_evidence_already_exists(state, "file_management.repo_snapshot", {})

        self.assertFalse(exists)

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

        exists = runtime._tool_call_evidence_already_exists(state, "file_management.repo_snapshot", {})

        self.assertFalse(exists)

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
        state = AgentState("sess_test", "run_new", RequestEnvelope("req_new", "list files", "."))
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
        self.assertFalse(result["payload_ref"].endswith("/latest"))

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

        self.assertEqual(state.context_summary["blockers"], [])
        self.assertIn(
            "Malformed tool_call decision was missing payload.tool_id; treated as turn-local provider repair feedback. Continue from selected tools or existing evidence.",
            state.turn_feedback,
        )


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

    def decide(self, _prompt):
        from appv22.runtime.decisions import RuntimeDecision

        decision = self.decisions.pop(0)
        self.kinds.append(decision["kind"])
        return RuntimeDecision(
            kind=decision["kind"],
            reason=decision.get("reason", "test_decision"),
            payload=decision.get("payload", {}),
        )


if __name__ == "__main__":
    unittest.main()
