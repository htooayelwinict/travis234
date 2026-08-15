from __future__ import annotations

from pathlib import Path

from tests._support_coding_agent import faux_model
from travis.coding_agent import AgentSession
from travis.coding_agent.processes import ProcessOwner, ProcessSessionService


EXPECTED = {
    "read": {"read"},
    "ls": {"read"},
    "find": {"read"},
    "grep": {"read"},
    "edit": {"write"},
    "write": {"write"},
    "bash": {"read", "write", "execute", "network"},
    "process": {"read", "write", "execute", "network"},
    "tmux": {"read", "write", "execute", "network"},
}

SUBAGENT_EXPECTED = {
    "spawn_subagent": {"read", "write", "execute", "network"},
    "wait_subagent": {"read"},
    "list_subagents": {"read"},
    "get_subagent_result": {"read"},
    "expand_subagent_result": {"read"},
    "cancel_subagent": {"execute"},
}


def _session(tmp_path: Path) -> AgentSession:
    service = ProcessSessionService(directory=tmp_path / "processes")
    owner = ProcessOwner("policy-inventory", str(tmp_path), "agent")
    return AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=owner,
    )


def test_builtin_tool_effect_inventory_is_exact(tmp_path: Path) -> None:
    session = _session(tmp_path)

    actual = {
        name: set(session.get_tool_definition(name).effects)
        for name in EXPECTED
    }

    assert actual == EXPECTED


def test_subagent_tool_effect_inventory_is_exact(tmp_path: Path) -> None:
    session = _session(tmp_path)

    actual = {
        name: set(session.get_tool_definition(name).effects)
        for name in SUBAGENT_EXPECTED
    }

    assert actual == SUBAGENT_EXPECTED


def test_builtin_policy_context_is_useful_without_raw_path_or_command(tmp_path: Path) -> None:
    session = _session(tmp_path)
    read = session.get_tool_definition("read")
    bash = session.get_tool_definition("bash")
    secret = "credential-value-never-render"
    absolute_target = tmp_path / "src" / "module.py"
    command = f"curl https://example.invalid/?token={secret}"

    read_context = read.policy_context({"path": str(absolute_target)})
    bash_context = bash.policy_context({"command": command})

    assert read_context == {"action": "read", "target": "src/module.py"}
    assert bash_context["action"] == "execute"
    assert bash_context["executable"] == "curl"
    assert len(bash_context["commandFingerprint"]) == 16
    rendered = repr((read_context, bash_context))
    assert secret not in rendered
    assert command not in rendered
    assert str(tmp_path) not in rendered


def test_spawn_policy_context_omits_goal(tmp_path: Path) -> None:
    session = _session(tmp_path)
    spawn = session.get_tool_definition("spawn_subagent")
    secret_goal = "analyze credential-value-never-render"

    context = spawn.policy_context(
        {"role": "reviewer", "backend": "internal", "goal": secret_goal}
    )

    assert context == {
        "action": "spawn",
        "role": "reviewer",
        "backend": "internal",
    }
    assert secret_goal not in repr(context)
