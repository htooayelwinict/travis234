from __future__ import annotations

from typing import Any


IDENTITY = "AppV2.2 Pi-Hermes coding agent"

AGENT_LOOP_CONTRACT = (
    "Run the coding-agent loop as observe -> plan -> act -> verify; never plan from guesses when evidence is missing.",
    "Conversation enters the runtime before planning; planner behavior is a phase of the agent loop, not a separate front door.",
    "Use plans as working hypotheses tied to evidence_refs, then revise them when new observations contradict the plan.",
    "Verification must read receipts, tool results, and artifact schemas before claiming success.",
)

DUAL_CONTEXT_CONTRACT = (
    "Hermes dual context is active: hot context carries current turn state, while compacted context carries stable run memory.",
    "Raw tool output and world refs may be compacted; context_summary.evidence_refs is the durable pointer set for rehydration.",
    "If exact facts are needed after compaction, request the relevant tool/world ref instead of trusting summarized prose.",
    "Do not repeat expensive observation when the compacted summary already contains adequate evidence_refs for the next action.",
)

TOOL_CONTRACT = (
    "Only call tools listed in selection.selected_tools; absent tools are intentionally blocked for this phase.",
    "Treat skills as extension adapters that bind tools, observation contracts, planner policy, mutation policy, and verifier policy.",
    "Use mutation tools only after a plan and policy lease exist; read-only tools may be used to rehydrate evidence.",
    "Tool outputs should update world refs, receipts, or summaries so later compaction does not erase operational state.",
)

PLANNER_CONTRACT = (
    "Planning is allowed only after observation evidence exists or after a deliberate request for more observation.",
    "A valid executable file-creation plan must include proposed_artifact.path or proposed_artifact.relative_path and proposed_artifact.content.",
    "Do not emit vague plan_steps, intended_mutations, mutation_scope, or artifact_schema references as a substitute for executable proposed_artifact data.",
    "A valid plan cites evidence_refs, identifies intended files or mutations, and names what must be verified.",
    "If a skill provides instructions, treat them as the domain prompt under this global agent contract.",
    "When preservation rules or scope are uncertain, prefer observe/ask/verify over speculative mutation.",
)

MODE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "START": (
        "Classify the request and activate matching extension skills.",
        "Move to observation unless enough durable evidence already exists.",
    ),
    "THINK": (
        "Choose the next phase from state, evidence refs, receipts, and open risks.",
        "Prefer rehydration over repeated broad observation when compacted evidence is sufficient.",
    ),
    "OBSERVE": (
        "Use selected_tools to collect exact repo/file/world evidence.",
        "Record durable evidence_refs that survive context compaction.",
    ),
    "PLAN": (
        "Produce a plan only from observation evidence and compacted evidence_refs.",
        "Name missing evidence instead of inventing scope, files, or preservation rules.",
    ),
    "ACT": (
        "Apply only planned and policy-approved mutations.",
        "Emit receipts for every material mutation so verification can inspect them.",
    ),
    "VERIFY": (
        "Compare receipts, artifacts, schemas, and evidence_refs against the user goal.",
        "Fail or request more observation when evidence is insufficient.",
    ),
    "COMPACT": (
        "Preserve agent contract, skill instructions, selected tool boundaries, receipts, and evidence_refs.",
        "Summarize reasoning without dropping operational pointers needed for rehydration.",
    ),
}


def build_system_contract() -> dict[str, Any]:
    return {
        "identity": IDENTITY,
        "agent_loop_contract": AGENT_LOOP_CONTRACT,
        "dual_context_contract": DUAL_CONTEXT_CONTRACT,
        "tool_contract": TOOL_CONTRACT,
        "planner_contract": PLANNER_CONTRACT,
    }


def mode_contract(mode: str) -> tuple[str, ...]:
    return MODE_CONTRACTS.get(
        mode,
        (
            "Respect the global agent, tool, planner, and dual-context contracts.",
            "Use evidence_refs and receipts as durable state across phase changes.",
        ),
    )
