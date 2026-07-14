from __future__ import annotations

import shlex
import sys
from pathlib import Path

from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.app import CodingApp
from tests.test_coding_persistence_and_compaction import (
    test_agent_session_manual_compaction_persists_managed_process_ledger as _compaction_ledger,
)
from tests.test_coding_policy_and_extensions import (
    test_concurrent_external_steering_is_delivered_once_with_distinct_ids as _concurrent_steering,
)
from tests.test_process_context import (
    test_provider_context_is_not_displaced_by_managed_process_state as _provider_context_ordering,
)
from tests.test_process_output import (
    test_live_spool_budget_is_shared_and_released_exactly_once as _shared_spool_budget,
)
from tests.test_process_service import (
    owner,
    test_active_limit_is_per_owner_scope_with_global_ceiling as _owner_scope_quota,
    test_process_output_limit_fails_only_producer_and_preserves_prefix as _output_limit,
    test_spool_failure_stops_process_and_publishes_failed as _spool_failure,
    test_terminal_poll_falls_back_to_durable_completion_after_memory_eviction as _terminal_recovery,
)
from tests.test_process_tools import (
    managed_tools,
    test_process_wait_collapses_large_output_to_bounded_borrowed_artifact as _large_artifact,
    test_process_wait_uses_terminal_wait_streams_updates_and_is_sequential as _sequential_wait,
)
from tests.test_tui_runtime_compaction_and_models import (
    test_interactive_mode_serializes_bang_bash_after_streaming_turn as _bang_during_turn,
)
from tests.test_tui_terminal_and_input import (
    test_ctrl_c_interrupts_focused_user_command_without_aborting_agent as _focused_ctrl_c,
)


def setup_function() -> None:
    reset_api_providers()


def test_required_chatty_job_uses_one_wait_result(tmp_path: Path) -> None:
    source = (
        "import time; "
        "[(print(i, flush=True), time.sleep(.002)) for i in range(120)]; "
        "time.sleep(.2)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    process_actions: list[str] = []

    def provider(model, context):
        results = [message for message in context.messages if message.role == "toolResult"]
        if not results:
            return tool_call_response_events(
                model,
                "bash",
                {"command": command, "yield_time_ms": 0},
            )
        latest = results[-1]
        if latest.tool_name == "bash":
            process_actions.append("wait")
            return tool_call_response_events(
                model,
                "process",
                {
                    "action": "wait",
                    "session_id": latest.details["sessionId"],
                    "cursor": latest.details["nextCursor"],
                    "wait_time_ms": 60_000,
                },
            )
        return text_response_events(model, "complete")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        agent_dir=str(tmp_path / "agent"),
    )
    try:
        app.run_turn("run the chatty job and wait for its result")

        tool_results = [message for message in app.messages if message.role == "toolResult"]
        assert [message.tool_name for message in tool_results] == ["bash", "process"]
        assert process_actions == ["wait"]
        assert tool_results[-1].details["status"] == "exited"
        assert "119" in tool_results[-1].content[0].text
    finally:
        app.close()


def test_terminal_output_recovers_after_live_ttl_and_new_app(tmp_path: Path, owner) -> None:
    _terminal_recovery(tmp_path, owner)


def test_managed_process_state_does_not_displace_provider_context(tmp_path: Path) -> None:
    _provider_context_ordering(tmp_path)


def test_compaction_round_trip_preserves_live_process_ledger(tmp_path: Path) -> None:
    _compaction_ledger(tmp_path)


def test_bang_completes_while_turn_waits(tmp_path: Path) -> None:
    _bang_during_turn(tmp_path)


def test_single_ctrl_c_routes_to_focused_operation(tmp_path: Path, monkeypatch) -> None:
    _focused_ctrl_c(tmp_path, monkeypatch)


def test_duplicate_concurrent_steering_messages_both_arrive(tmp_path: Path) -> None:
    _concurrent_steering(tmp_path)


def test_spool_failure_is_failed_not_exited(tmp_path: Path, owner, monkeypatch) -> None:
    _spool_failure(tmp_path, owner, monkeypatch)


def test_live_spool_budget_is_bounded_and_not_a_timeout(tmp_path: Path, owner) -> None:
    _output_limit(tmp_path, owner)
    _shared_spool_budget(tmp_path)


def test_owner_scope_quota_does_not_starve_other_workspace(tmp_path: Path) -> None:
    _owner_scope_quota(tmp_path)


def test_two_megabyte_detached_output_has_durable_artifact(tmp_path: Path) -> None:
    _large_artifact(tmp_path)


def test_batched_process_controls_execute_sequentially(managed_tools) -> None:
    _sequential_wait(managed_tools)
