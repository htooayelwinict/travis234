from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from evals.optional_tool_conformance import run_optional_tool_conformance


EXPECTED_CHECKS = {
    "extension_loaded",
    "expected_tools_registered",
    "explicit_effects",
    "untrusted_project_suppressed",
    "policy_denial",
    "policy_approval",
    "cancellation_propagated",
    "bounded_output",
    "artifact_spill",
    "sanitized_effects",
    "secret_absent",
    "shutdown_cleanup",
    "forbidden_imports_absent",
    "root_dependencies_optional",
}


def _write_extension(
    path: Path,
    *,
    effects: str = "frozenset({'read', 'write', 'execute', 'network'})",
    execute_body: str | None = None,
    imports: str = "",
    top_level: str = "",
) -> Path:
    body = execute_body or """
        if signal is not None and signal.aborted:
            raise asyncio.CancelledError
        return AgentToolResult(
            content=[TextContent(text="bounded optional output")],
            details={
                "artifactId": "artifact-11111111111111111111111111111111",
                "sourceBytes": 60000,
            },
        )
    """
    indented_body = textwrap.indent(textwrap.dedent(body).strip(), "        ")
    source = f"""from __future__ import annotations
import asyncio
{imports}
from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition

{top_level}

def extension(travis):
    async def execute(_tool_call_id, args, signal=None, _on_update=None, _ctx=None):
{indented_body}

    for name in ("browser_fixture", "computer_fixture"):
        travis.register_tool(ToolDefinition(
            name=name,
            label=name,
            description="Synthetic optional integration probe",
            parameters={{"type": "object", "additionalProperties": True}},
            execute=execute,
            effects={effects},
            policy_context=lambda _args: {{"action": "conformance", "target": "fixture"}},
        ))

    async def shutdown(_event, _ctx):
        return None

    travis.on("session_shutdown", shutdown)
"""
    path.write_text(source, encoding="utf-8")
    return path


def test_compliant_optional_integration_passes_every_contract(tmp_path: Path) -> None:
    del tmp_path
    extension = Path(__file__).parents[1] / "evals" / "optional_integration_fixture.py"

    report = run_optional_tool_conformance(
        extension,
        ("browser_fixture", "computer_fixture"),
    )

    assert report.passed is True
    assert {check.code for check in report.checks} == EXPECTED_CHECKS
    assert report.failure_codes == ()


@pytest.mark.parametrize(
    ("variant", "expected_code"),
    [
        ("missing_effects", "explicit_effects"),
        ("ignored_cancellation", "cancellation_propagated"),
        ("unbounded_output", "bounded_output"),
        ("leaked_secret", "secret_absent"),
        ("child_process", "shutdown_cleanup"),
        ("forbidden_import", "forbidden_imports_absent"),
    ],
)
def test_broken_optional_integrations_report_stable_failure_codes(
    tmp_path: Path,
    variant: str,
    expected_code: str,
) -> None:
    options: dict[str, str] = {}
    if variant == "missing_effects":
        options["effects"] = "frozenset()"
    elif variant == "ignored_cancellation":
        options["execute_body"] = """
            return AgentToolResult(
                content=[TextContent(text="ignored cancellation")],
                details={"artifactId": "artifact-" + "1" * 32, "sourceBytes": 60000},
            )
        """
    elif variant == "unbounded_output":
        options["execute_body"] = """
            if signal is not None and signal.aborted:
                raise asyncio.CancelledError
            return AgentToolResult(content=[TextContent(text="x" * 60000)], details={})
        """
    elif variant == "leaked_secret":
        options["execute_body"] = """
            if signal is not None and signal.aborted:
                raise asyncio.CancelledError
            return AgentToolResult(
                content=[TextContent(text=str(args.get("secret")))],
                details={"artifactId": "artifact-" + "1" * 32, "sourceBytes": 60000},
            )
        """
    elif variant == "child_process":
        options["imports"] = "import subprocess\nimport sys"
        options["top_level"] = (
            "LEAKED_CHILD = subprocess.Popen("
            "[sys.executable, '-c', 'import time; time.sleep(60)'])"
        )
    elif variant == "forbidden_import":
        options["imports"] = "import travis.agent.agent_loop\nimport travis.tui"
    extension = _write_extension(tmp_path / f"{variant}.py", **options)

    report = run_optional_tool_conformance(
        extension,
        ("browser_fixture", "computer_fixture"),
    )

    assert report.passed is False
    assert expected_code in report.failure_codes
