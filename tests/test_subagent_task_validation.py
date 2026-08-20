from __future__ import annotations

from pathlib import Path

from travis.coding_agent.subagents import SubagentTask


def test_subagent_task_normalizes_runtime_and_typed_contract_fields(tmp_path: Path) -> None:
    task = SubagentTask(
        id="task-1",
        role="reviewer",
        goal="inspect",
        cwd=str(tmp_path),
        backend="internal",
        reasoning=" HIGH ",
        allowed_tools=["read", "find"],
        allowed_effects=("network", "read", "network"),
        model_role="reviewer",
        role_definition_name="coordination-planner",
        result_schema={"type": "object"},
        artifact_policy="declared",
    )

    assert task.reasoning == "high"
    assert task.allowed_tools == ("read", "find")
    assert task.allowed_effects == ("read", "network")
    assert task.model_role == "reviewer"
    assert task.role_definition_name == "coordination-planner"
    assert task.result_schema == {"type": "object"}
    assert task.artifact_policy == "declared"
