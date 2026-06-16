from __future__ import annotations

from appv22.extensions.base import ObservationContract, SkillCard

FILE_MANAGEMENT_SKILL = SkillCard(
    skill_id="file_management.model_authored_files",
    extension_id="file_management",
    triggers=(
        "workspace",
        "record",
        "leave",
        "useful",
        "next person",
        "capture",
        "decision",
        "durable file",
        "file",
        "checklist",
        "notes",
        "runbook",
        "stub",
        "create",
        "document",
        "handoff",
    ),
    modes=("START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY"),
    summary="Safely observe workspace files, create useful model-authored records, and verify durable artifacts.",
    planner_id="file_management.model_authored_file_planner",
    mutation_policy_id="file_management.safe_file_mutations",
    mutation_executor_id="file_management.file_mutation_executor",
    verifier_id="file_management.manifest_verifier",
    tool_ids=("file_management.repo_snapshot", "file_management.read_file"),
    artifact_schema_ids=(),
    instructions=(
        "Act as a Pi-style coding agent for file creation/documentation work: observe the repo/file map before planning or creating files.",
        "Use repo_snapshot as durable evidence; cite world://file_management.repo_snapshot/latest instead of replaying broad scans after compaction.",
        "For creation tasks, produce a model-authored proposed_artifact with a safe relative path and useful content.",
        "When repo_snapshot.payload.text_previews contains notes or context, preserve the concrete domain terms from those previews in the created artifact.",
        "Do not invent cleanup, move files, or write manifests unless the model plan explicitly provides those operations.",
        "If a plan lacks proposed_artifact or mutation_intent.operations, the runtime must reject it and ask for a repaired executable plan.",
    ),
    observation_contract=ObservationContract(
        evidence_refs=("world://file_management.repo_snapshot/latest",),
        evidence_kinds=("file_management.repo_snapshot",),
        preferred_tool_id="file_management.repo_snapshot",
    ),
)
