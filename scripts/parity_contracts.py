#!/usr/bin/env python3
"""Machine-readable Pi and Hermes behavioral contract manifests."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
VALID_STATUSES = frozenset({"parity", "divergence"})


@dataclass(frozen=True)
class ContractEntry:
    contract_id: str
    source: str
    category: str
    evidence: str
    status: str = "parity"
    reason: str = ""
    safety_evidence: str = ""


def _pi(
    name: str,
    category: str,
    evidence: str,
    *,
    status: str = "parity",
    reason: str = "",
    safety_evidence: str = "",
) -> ContractEntry:
    return ContractEntry(
        contract_id=f"pi.{category}.{name}",
        source="pi",
        category=category,
        evidence=evidence,
        status=status,
        reason=reason,
        safety_evidence=safety_evidence,
    )


def _hermes(name: str, evidence: str) -> ContractEntry:
    return ContractEntry(
        contract_id=f"hermes.compaction.{name}",
        source="hermes",
        category="compaction",
        evidence=evidence,
    )


_EXTENSION_EVIDENCE = (
    "tests/test_extension_event_parity.py::"
    "test_extension_runner_declares_all_pinned_pi_events"
)

_PI_EXTENSION_EVENTS = (
    "project_trust",
    "resources_discover",
    "session_start",
    "session_info_changed",
    "session_before_switch",
    "session_before_fork",
    "session_before_compact",
    "session_compact",
    "session_shutdown",
    "session_before_tree",
    "session_tree",
    "context",
    "before_provider_request",
    "before_provider_headers",
    "after_provider_response",
    "before_agent_start",
    "agent_start",
    "agent_end",
    "agent_settled",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "model_select",
    "thinking_level_select",
    "tool_call",
    "tool_result",
    "user_bash",
    "input",
)


PI_CONTRACTS = (
    _pi("text_turn_event_order", "loop", "tests/test_agent_loop.py::test_single_text_turn_event_sequence"),
    _pi("tool_turn_continuation", "loop", "tests/test_agent_loop.py::test_tool_call_turn_executes_and_continues"),
    _pi(
        "raw_tool_arguments",
        "loop",
        "tests/test_agent_loop.py::test_tool_call_history_keeps_raw_arguments_after_execution_before_next_model_call",
    ),
    _pi(
        "abort_during_tool",
        "loop",
        "tests/test_agent_loop.py::test_agent_loop_stops_after_signal_aborted_during_tool_execution",
    ),
    _pi(
        "duplicate_tool_calls_execute",
        "loop",
        "tests/test_agent_loop.py::test_duplicate_tool_calls_in_same_assistant_turn_execute_like_travis234",
    ),
    _pi(
        "truncated_tool_calls_fail_closed",
        "loop",
        "tests/test_agent_loop.py::test_truncated_assistant_tool_calls_fail_without_execution_like_travis234",
    ),
    _pi(
        "batch_termination",
        "loop",
        "tests/test_agent_loop.py::test_after_tool_call_terminate_uses_travis234_batch_semantics",
    ),
    _pi(
        "parallel_result_source_order",
        "loop",
        "tests/test_agent_loop.py::test_parallel_tool_end_events_follow_completion_order_while_results_keep_source_order",
    ),
    _pi(
        "parallel_callbacks_on_coordinator",
        "loop",
        "tests/test_agent_loop.py::test_parallel_tool_execution_end_events_emit_from_loop_thread",
    ),
    _pi(
        "bounded_parallelism",
        "loop",
        "tests/test_agent_loop.py::test_parallel_tools_are_bounded_and_callbacks_stay_on_coordinator_thread",
        status="divergence",
        reason="Travis preserves a bounded worker pool instead of inheriting unbounded host concurrency.",
        safety_evidence="tests/test_agent_loop.py::test_parallel_tools_are_bounded_and_callbacks_stay_on_coordinator_thread",
    ),
    _pi(
        "immediate_outcome_hook_boundary",
        "loop",
        "tests/test_agent_loop.py::test_immediate_tool_outcomes_bypass_after_hook",
    ),
    _pi(
        "invoked_failure_after_hook_once",
        "loop",
        "tests/test_agent_loop.py::test_invoked_tool_failure_runs_after_hook_once",
    ),
    *(
        _pi(event, "extension_event", _EXTENSION_EVIDENCE)
        for event in _PI_EXTENSION_EVENTS
    ),
    _pi(
        "yaml_frontmatter",
        "resource",
        "tests/test_resource_runtime_parity.py::test_yaml_frontmatter_supports_pi_metadata_shapes",
    ),
    _pi(
        "ignore_discovery",
        "resource",
        "tests/test_resource_runtime_parity.py::test_resource_discovery_merges_ignore_files_but_explicit_file_wins",
    ),
    _pi(
        "prompt_arguments",
        "resource",
        "tests/test_resource_runtime_parity.py::test_prompt_expansion_supports_shell_quoting_and_positional_arguments",
    ),
    _pi(
        "prompt_reload",
        "resource",
        "tests/test_resource_runtime_parity.py::test_prompt_template_expands_before_provider_and_refreshes_after_reload",
    ),
    _pi(
        "skill_commands",
        "resource",
        "tests/test_resource_runtime_parity.py::test_skill_command_injects_selected_skill_only_when_enabled",
    ),
    _pi(
        "theme_reload",
        "resource",
        "tests/test_resource_runtime_parity.py::test_theme_registry_preserves_or_falls_back_across_reload",
    ),
    _pi("source_kinds", "package", "tests/test_package_manager.py::test_package_source_kinds"),
    _pi(
        "project_trust",
        "package",
        "tests/test_package_manager.py::test_project_package_mutations_require_resolved_trust",
        status="divergence",
        reason="Project package mutation fails closed until Travis project trust is resolved.",
        safety_evidence="tests/test_project_trust.py::test_no_ui_unknown_project_fails_closed",
    ),
    _pi(
        "atomic_install",
        "package",
        "tests/test_package_manager.py::test_local_install_is_atomic_persisted_and_resolved",
    ),
    _pi(
        "transactional_reinstall",
        "package",
        "tests/test_package_manager.py::test_failed_reinstall_preserves_previous_package",
    ),
    _pi(
        "credential_isolation",
        "package",
        "tests/test_package_manager.py::test_package_subprocesses_strip_runtime_credentials",
    ),
    _pi(
        "no_implicit_install",
        "package",
        "tests/test_package_manager.py::test_configured_missing_package_is_diagnostic_not_auto_install",
    ),
    _pi("print_mode", "cli", "tests/test_automation_modes.py::test_print_mode_outputs_only_final_text"),
    _pi("json_mode", "cli", "tests/test_automation_modes.py::test_json_mode_emits_ordered_machine_events"),
    _pi("rpc_mode", "cli", "tests/test_rpc_mode.py::test_rpc_prompt_correlates_events_and_result"),
    _pi(
        "mode_owner_equivalence",
        "cli",
        "tests/test_automation_modes.py::test_print_json_rpc_and_tui_share_the_same_final_session_result",
    ),
    _pi(
        "tool_resource_controls",
        "cli",
        "tests/test_cli_runtime_controls.py::test_cli_forwards_repeatable_tool_resource_and_offline_controls",
    ),
    _pi(
        "extension_flags",
        "cli",
        "tests/test_cli_extension_flags.py::test_cli_parses_typed_extension_flags_once_and_preserves_prompt",
    ),
    _pi(
        "no_tools",
        "cli",
        "tests/test_cli_runtime_controls.py::test_cli_no_tools_disables_all_tools_by_default",
    ),
    _pi(
        "offline_startup",
        "cli",
        "tests/test_cli_runtime_controls.py::test_offline_models_skip_catalog_and_oauth_refresh",
    ),
    _pi(
        "file_expansion",
        "cli",
        "tests/test_input_expansion.py::test_expands_unquoted_and_quoted_files_but_preserves_escaped_at",
    ),
    _pi(
        "image_inputs",
        "cli",
        "tests/test_input_expansion.py::test_inline_and_explicit_images_become_content_blocks",
    ),
    _pi(
        "noninteractive_trust",
        "cli",
        "tests/test_project_trust.py::test_no_ui_unknown_project_fails_closed",
        status="divergence",
        reason="Non-interactive startup does not execute unknown project resources without a policy decision.",
        safety_evidence="tests/test_project_trust.py::test_project_resource_requires_trust",
    ),
    _pi("tree", "session", "tests/test_session_parity.py::test_session_tree_reports_stable_depth_first_structure_and_active_branch"),
    _pi("fork", "session", "tests/test_session_parity.py::test_fork_preserves_non_label_ids_and_recreates_resolved_labels"),
    _pi("clone", "session", "tests/test_session_parity.py::test_runtime_clone_forks_at_current_leaf_without_mutating_source"),
    _pi("rename", "session", "tests/test_session_parity.py::test_rename_session_updates_jsonl_events_and_catalog_index"),
    _pi("import", "session", "tests/test_session_parity.py::test_import_validates_before_copy_and_avoids_name_collisions"),
    _pi(
        "serialized_commands",
        "session",
        "tests/test_session_commands.py::test_session_commands_execute_in_submission_order_on_one_owner_thread",
    ),
    _pi(
        "async_models",
        "sdk",
        "tests/test_models_runtime.py::test_async_models_refreshes_and_finds_inside_a_running_loop",
    ),
    _pi(
        "agent_harness",
        "sdk",
        "tests/test_agent_harness.py::test_agent_harness_composes_existing_owners_inside_async_context",
        status="divergence",
        reason="The public SDK is a Pythonic async facade, not a TypeScript signature clone.",
        safety_evidence="tests/test_agent_harness.py::test_agent_harness_delegates_session_tree_clone_and_rename",
    ),
    _pi(
        "stream_proxy",
        "sdk",
        "tests/test_stream_proxy.py::test_stream_proxy_preserves_order_and_supports_replace_and_suppress",
    ),
    _pi(
        "optional_images",
        "sdk",
        "tests/test_ai_images.py::test_openrouter_image_adapter_parses_data_and_url_outputs_without_live_network",
    ),
)


HERMES_CONTRACTS = (
    _hermes("threshold_bands", "tests/test_compaction_policy.py::test_hermes_threshold_bands"),
    _hermes(
        "small_window_floor_fallback",
        "tests/test_compaction_policy.py::test_below_64k_route_uses_reachable_small_window_fallback",
    ),
    _hermes("full_request_accounting", "tests/test_context_estimate.py::test_full_request_estimate_reports_components"),
    _hermes(
        "prompt_only_provider_usage",
        "tests/test_context_estimate.py::test_provider_prompt_usage_excludes_output_and_reports_confidence",
    ),
    _hermes(
        "replay_tail_fields",
        "tests/test_context_estimate.py::test_assistant_replay_fields_increase_estimate",
    ),
    _hermes("tail_budget", "tests/test_compaction.py::test_tail_budget_counts_images_with_travis_fixed_estimate"),
    _hermes("protected_head_decay", "tests/test_compaction.py::test_protected_head_decays_after_a_previous_summary_exists"),
    _hermes(
        "boundary_stripping",
        "tests/test_compaction.py::test_compress_removes_orphaned_tool_result_from_tail",
    ),
    _hermes(
        "cooldown",
        "tests/test_compaction_timing.py::test_automatic_compaction_does_not_rewrite_during_summary_cooldown",
    ),
    _hermes(
        "fallback_wording",
        "tests/test_compaction.py::test_empty_main_summary_uses_deterministic_handoff_instead_of_blank_summary",
    ),
    _hermes(
        "auxiliary_capacity",
        "tests/test_compaction_policy.py::test_smaller_aux_model_lowers_trigger_before_overflow",
    ),
)


def validate_contracts(
    contracts: Iterable[ContractEntry],
    *,
    root: Path = ROOT,
    include_safety_evidence: bool = False,
) -> tuple[str, ...]:
    errors: list[str] = []
    seen: set[str] = set()
    parsed: dict[Path, frozenset[str]] = {}
    for entry in contracts:
        if entry.contract_id in seen:
            errors.append(f"duplicate contract id: {entry.contract_id}")
        seen.add(entry.contract_id)
        if entry.status not in VALID_STATUSES:
            errors.append(f"{entry.contract_id}: invalid status {entry.status}")
        if entry.status == "divergence" and (not entry.reason or not entry.safety_evidence):
            errors.append(f"{entry.contract_id}: divergence lacks reason or safety evidence")
        references = [entry.evidence]
        if include_safety_evidence and entry.safety_evidence:
            references.append(entry.safety_evidence)
        for reference in references:
            error = _validate_evidence(reference, root=root, parsed=parsed)
            if error:
                errors.append(f"{entry.contract_id}: {error}")
    return tuple(errors)


def _validate_evidence(
    reference: str,
    *,
    root: Path,
    parsed: dict[Path, frozenset[str]],
) -> str | None:
    path_text, separator, test_name = reference.partition("::")
    if not separator or not test_name.startswith("test_"):
        return f"invalid evidence reference: {reference}"
    path = root / path_text
    if not path.is_file() or path.suffix != ".py":
        return f"evidence file is missing: {path_text}"
    if path not in parsed:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed[path] = frozenset(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
    if test_name not in parsed[path]:
        return f"evidence test is missing: {reference}"
    return None


def build_parity_report(
    *,
    pi_contracts: Sequence[ContractEntry] = PI_CONTRACTS,
    hermes_contracts: Sequence[ContractEntry] = HERMES_CONTRACTS,
    root: Path = ROOT,
) -> dict[str, object]:
    contracts = {"pi": tuple(pi_contracts), "hermes": tuple(hermes_contracts)}
    summary: dict[str, dict[str, int]] = {}
    serialized: list[dict[str, str]] = []
    for source, entries in contracts.items():
        invalid_ids = {
            error.partition(":")[0]
            for error in validate_contracts(entries, root=root, include_safety_evidence=True)
        }
        summary[source] = {
            "total": len(entries),
            "parity": sum(entry.status == "parity" for entry in entries),
            "divergence": sum(entry.status == "divergence" for entry in entries),
            "invalid": len(invalid_ids),
        }
        serialized.extend(asdict(entry) for entry in entries)
    return {
        "schema_version": 1,
        "summary": summary,
        "contracts": serialized,
    }


__all__ = [
    "ContractEntry",
    "HERMES_CONTRACTS",
    "PI_CONTRACTS",
    "build_parity_report",
    "validate_contracts",
]
