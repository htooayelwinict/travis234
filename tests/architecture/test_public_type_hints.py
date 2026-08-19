from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_export_type_hints_resolve_in_clean_interpreter() -> None:
    script = textwrap.dedent(
        """
        import importlib
        import inspect
        import typing

        module_names = (
            "travis",
            "travis.coding_agent",
            "travis.coding_agent.agent_session_services",
            "travis.coding_agent.session_contracts",
            "travis.tui.interactive_contracts",
            "travis.ai.providers",
        )
        failures = []
        for module_name in module_names:
            exported = importlib.import_module(module_name)
            names = getattr(exported, "__all__", ())
            for name in names:
                value = getattr(exported, name)
                if not (inspect.isfunction(value) or inspect.isclass(value)):
                    continue
                targets = [(name, value)]
                if inspect.isclass(value):
                    for member_name, member in inspect.getmembers_static(value):
                        if member_name != "__init__" and member_name.startswith("_"):
                            continue
                        if isinstance(member, (classmethod, staticmethod)):
                            member = member.__func__
                        elif isinstance(member, property):
                            member = member.fget
                        if inspect.isfunction(member):
                            targets.append((f"{name}.{member_name}", member))
                for qualified_name, target in targets:
                    defining_module = inspect.getmodule(target)
                    if defining_module is None:
                        continue
                    try:
                        typing.get_type_hints(
                            target,
                            globalns=vars(defining_module),
                            localns=vars(defining_module),
                        )
                    except Exception as error:
                        failures.append(
                            f"{module_name}.{qualified_name} "
                            f"({defining_module.__name__}.{target.__qualname__}): "
                            f"{type(error).__name__}: {error}"
                        )
        if failures:
            print("\\n".join(failures))
            raise SystemExit(1)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
