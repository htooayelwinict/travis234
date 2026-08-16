"""Executable contract checks for optional browser and computer integrations."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Iterable, Mapping

import psutil

from travis.agent.async_utils import resolve, run_sync
from travis.agent.types import AbortSignal, AgentToolResult
from travis.coding_agent.policy import ApprovalResponse
from travis.coding_agent.policy.engine import ToolPolicyEngine
from travis.coding_agent.policy.types import TOOL_EFFECT_ORDER, ToolPolicySettings
from travis.coding_agent.resource_loader import DefaultResourceLoader


_INLINE_BYTES = 50 * 1024
_INLINE_LINES = 2_000
_ARTIFACT_ID = re.compile(r"artifact-[0-9a-f]{32}\Z")
_PROBE_SECRET = "optional-conformance-secret-never-render"
_FORBIDDEN_EXTENSION_IMPORTS = (
    "travis.agent.agent_loop",
    "travis.tui",
)
_OPTIONAL_ROOT_IMPORTS = frozenset(
    {"playwright", "selenium", "pyautogui", "Quartz", "AppKit"}
)


@dataclass(frozen=True)
class ConformanceCheck:
    code: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ConformanceReport:
    extension_path: str
    expected_tools: tuple[str, ...]
    checks: tuple[ConformanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failure_codes(self) -> tuple[str, ...]:
        return tuple(check.code for check in self.checks if not check.passed)


def run_optional_tool_conformance(
    extension_path: str | Path,
    expected_tools: Iterable[str],
) -> ConformanceReport:
    source = Path(extension_path).expanduser().resolve()
    names = tuple(dict.fromkeys(str(name) for name in expected_tools))
    if not source.is_file() or source.suffix != ".py":
        raise ValueError("extension_path must be an existing Python file")
    if not names or any(not name.strip() for name in names):
        raise ValueError("expected_tools must contain non-empty names")

    checks: list[ConformanceCheck] = []
    checks.append(
        _check(
            "forbidden_imports_absent",
            not _forbidden_extension_imports(source),
            "extension imports stay outside the generic loop and TUI",
        )
    )
    checks.append(
        _check(
            "root_dependencies_optional",
            not _optional_root_imports(),
            "core imports no optional browser or desktop runtime",
        )
    )

    before_children = _child_processes()
    trusted_runtime = None
    trusted_loader = None
    results: list[AgentToolResult] = []
    policy_records: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="travis234-optional-conformance-") as raw:
            root = Path(raw)
            project = root / "project"
            agent_dir = root / "agent"
            extension_dir = project / ".travis234" / "extensions"
            extension_dir.mkdir(parents=True)
            agent_dir.mkdir()
            project_extension = extension_dir / "integration.py"
            shutil.copy2(source, project_extension)

            untrusted_loader = DefaultResourceLoader(
                cwd=str(project),
                agent_dir=str(agent_dir),
                project_trusted=False,
            )
            untrusted_loader.complete_reload({"projectTrustOverride": False})
            untrusted_result = untrusted_loader.get_extensions()
            untrusted_runtime = untrusted_result["runtime"]
            suppressed = (
                not untrusted_runtime.get_all_registered_tools()
                and not untrusted_result.get("extensions")
                and not untrusted_result.get("errors")
            )
            checks.append(
                _check(
                    "untrusted_project_suppressed",
                    suppressed,
                    "project extension does not load before trust",
                )
            )
            untrusted_runtime.dispose()

            trusted_loader = DefaultResourceLoader(
                cwd=str(project),
                agent_dir=str(agent_dir),
                project_trusted=True,
            )
            trusted_loader.complete_reload({"projectTrustOverride": True})
            extension_result = trusted_loader.get_extensions()
            trusted_runtime = extension_result["runtime"]
            errors = extension_result.get("errors", [])
            checks.append(
                _check(
                    "extension_loaded",
                    not errors,
                    "trusted extension loads without diagnostics",
                )
            )

            registrations = {
                item.definition.name: item.definition
                for item in trusted_runtime.get_all_registered_tools()
            }
            selected = [registrations[name] for name in names if name in registrations]
            checks.append(
                _check(
                    "expected_tools_registered",
                    len(selected) == len(names),
                    "all expected optional tools register exactly once",
                )
            )
            checks.append(
                _check(
                    "explicit_effects",
                    bool(selected) and all(definition.effects for definition in selected),
                    "every optional tool declares security effects",
                )
            )

            denial_ok, approval_ok, sanitized_ok, policy_records = _policy_checks(selected)
            checks.append(
                _check(
                    "policy_denial",
                    denial_ok,
                    "enforce mode denies approval-required tools without a broker",
                )
            )
            checks.append(
                _check(
                    "policy_approval",
                    approval_ok,
                    "an explicit one-shot approval allows the declared effects",
                )
            )
            checks.append(
                _check(
                    "sanitized_effects",
                    sanitized_ok,
                    "policy records contain effects but no raw arguments",
                )
            )

            cancellation_ok = bool(selected) and all(
                _cancellation_propagates(definition) for definition in selected
            )
            checks.append(
                _check(
                    "cancellation_propagated",
                    cancellation_ok,
                    "pre-aborted execution cancels without producing a result",
                )
            )

            for definition in selected:
                result = _execute_probe(definition)
                if result is not None:
                    results.append(result)
            bounded = len(results) == len(selected) and all(
                _result_is_bounded(result) for result in results
            )
            artifact = len(results) == len(selected) and all(
                _oversized_result_has_artifact(result) for result in results
            )
            rendered = repr((results, policy_records))
            checks.append(
                _check("bounded_output", bounded, "model-visible output stays within bounds")
            )
            checks.append(
                _check(
                    "artifact_spill",
                    artifact,
                    "oversized source output is represented by an artifact ID",
                )
            )
            checks.append(
                _check(
                    "secret_absent",
                    _PROBE_SECRET not in rendered,
                    "probe secrets stay out of results and policy records",
                )
            )

            has_shutdown = trusted_runtime.has_handlers("session_shutdown")
            if has_shutdown:
                run_sync(
                    trusted_runtime.async_emit(
                        {"type": "session_shutdown", "reason": "conformance"}
                    )
                )
            trusted_runtime.dispose()
            trusted_runtime = None
            time.sleep(0.05)
            leaked = _new_live_children(before_children)
            checks.append(
                _check(
                    "shutdown_cleanup",
                    has_shutdown and not leaked,
                    "session shutdown leaves no integration-owned child",
                )
            )
            _terminate_processes(leaked)
    except Exception:
        _append_missing_runtime_checks(checks)
    finally:
        if trusted_runtime is not None:
            try:
                if trusted_runtime.has_handlers("session_shutdown"):
                    run_sync(
                        trusted_runtime.async_emit(
                            {"type": "session_shutdown", "reason": "conformance-error"}
                        )
                    )
            except Exception:
                pass
            trusted_runtime.dispose()
        leaked = _new_live_children(before_children)
        _terminate_processes(leaked)

    by_code = {check.code: check for check in checks}
    ordered = tuple(by_code[code] for code in _CHECK_ORDER)
    return ConformanceReport(str(source), names, ordered)


_CHECK_ORDER = (
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
)


def _check(code: str, passed: bool, message: str) -> ConformanceCheck:
    return ConformanceCheck(code, bool(passed), message)


class _ApproveOnce:
    async def request(self, _request, _signal):
        return ApprovalResponse(scope="once")


def _policy_checks(definitions) -> tuple[bool, bool, bool, list[dict[str, object]]]:
    denied = ToolPolicyEngine(
        ToolPolicySettings(mode="enforce", auto_allow_effects=frozenset({"read"}))
    )
    approved = ToolPolicyEngine(
        ToolPolicySettings(mode="enforce", auto_allow_effects=frozenset({"read"})),
        broker=_ApproveOnce(),
    )
    denial_ok = bool(definitions)
    approval_ok = bool(definitions)
    records: list[dict[str, object]] = []
    arguments = {"action": "conformance", "secret": _PROBE_SECRET}
    for definition in definitions:
        denial = run_sync(denied.authorize(definition, arguments))
        approval = run_sync(approved.authorize(definition, arguments))
        denial_ok = denial_ok and not denial.allow
        approval_ok = approval_ok and approval.allow
        records.append(
            {
                "tool": approval.tool_name,
                "effects": [
                    effect for effect in TOOL_EFFECT_ORDER if effect in approval.effects
                ],
                "reasonCode": approval.reason_code,
            }
        )
    return denial_ok, approval_ok, _PROBE_SECRET not in repr(records), records


def _cancellation_propagates(definition) -> bool:
    signal = AbortSignal()
    signal.abort()
    try:
        run_sync(_invoke(definition, signal))
    except asyncio.CancelledError:
        return True
    except Exception:
        return False
    return False


def _execute_probe(definition) -> AgentToolResult | None:
    try:
        result = run_sync(_invoke(definition, None))
    except BaseException:
        return None
    return result if isinstance(result, AgentToolResult) else None


async def _invoke(definition, signal: AbortSignal | None):
    value = definition.execute(
        "optional-conformance",
        {"action": "conformance", "secret": _PROBE_SECRET},
        signal,
        None,
        None,
    )
    return await resolve(value)


def _result_is_bounded(result: AgentToolResult) -> bool:
    text = "\n".join(
        str(getattr(block, "text", ""))
        for block in result.content
        if hasattr(block, "text")
    )
    return len(text.encode("utf-8")) <= _INLINE_BYTES and len(text.splitlines()) <= _INLINE_LINES


def _oversized_result_has_artifact(result: AgentToolResult) -> bool:
    details = result.details if isinstance(result.details, Mapping) else {}
    source_bytes = details.get("sourceBytes")
    if not isinstance(source_bytes, int) or source_bytes <= _INLINE_BYTES:
        return True
    artifact_id = details.get("artifactId")
    return isinstance(artifact_id, str) and _ARTIFACT_ID.fullmatch(artifact_id) is not None


def _forbidden_extension_imports(path: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return ("unreadable",)
    imports = _imports(tree)
    return tuple(
        name
        for name in imports
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_EXTENSION_IMPORTS)
    )


def _optional_root_imports() -> tuple[str, ...]:
    root = Path(__file__).resolve().parents[1] / "travis"
    found: set[str] = set()
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for name in _imports(tree):
            root_name = name.split(".", 1)[0]
            if root_name in _OPTIONAL_ROOT_IMPORTS:
                found.add(root_name)
    return tuple(sorted(found))


def _imports(tree: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return tuple(names)


def _child_processes() -> dict[int, psutil.Process]:
    return {process.pid: process for process in psutil.Process().children(recursive=True)}


def _new_live_children(before: Mapping[int, psutil.Process]) -> list[psutil.Process]:
    return [
        process
        for pid, process in _child_processes().items()
        if pid not in before and process.is_running()
    ]


def _terminate_processes(processes: Iterable[psutil.Process]) -> None:
    items = list(processes)
    for process in items:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _gone, alive = psutil.wait_procs(items, timeout=0.5)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _append_missing_runtime_checks(checks: list[ConformanceCheck]) -> None:
    existing = {check.code for check in checks}
    for code in _CHECK_ORDER:
        if code not in existing:
            checks.append(_check(code, False, "conformance runtime check did not complete"))


__all__ = [
    "ConformanceCheck",
    "ConformanceReport",
    "run_optional_tool_conformance",
]
