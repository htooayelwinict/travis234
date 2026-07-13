from __future__ import annotations

from pathlib import Path

import pytest

from travis.coding_agent.policies.bash_classification import (
    BashMutationClass,
    classify_bash_mutation,
)
from travis.coding_agent.policies.tool_guardrails import (
    _semantic_bash_read_key,
    _tool_call_may_change_state,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("echo hi>file", BashMutationClass.MUTATING),
        ("cat <<EOF >out.txt\nx\nEOF", BashMutationClass.MUTATING),
        ("sed -i s/a/b/ file", BashMutationClass.MUTATING),
        ("sed -i.bak s/a/b/ file", BashMutationClass.MUTATING),
        ("sed -n 'w output.txt' input.txt", BashMutationClass.MUTATING),
        ("perl -" + "p" + "i -e s/a/b/ file", BashMutationClass.MUTATING),
        ("sort -o output.txt input.txt", BashMutationClass.MUTATING),
        ('awk \'BEGIN { print "x" > "out.txt" }\'', BashMutationClass.MUTATING),
        ("date --set 2026-07-13", BashMutationClass.MUTATING),
        (
            'python -c "from pathlib import Path; Path(\'x\').write_text(\'y\')"',
            BashMutationClass.MUTATING,
        ),
        ('python -c "open(\'x\', \'w\').write(\'y\')"', BashMutationClass.MUTATING),
        ("git checkout -- file", BashMutationClass.MUTATING),
        ("git restore file", BashMutationClass.MUTATING),
        ("/bin/rm file", BashMutationClass.MUTATING),
        ("python -m pip install requests", BashMutationClass.MUTATING),
        ("printf '%s\\n' hi | sed -n 1p", BashMutationClass.READ_ONLY),
        ("git status --short", BashMutationClass.READ_ONLY),
        ("cat < input.txt", BashMutationClass.READ_ONLY),
        ("ls -la src 2>&1", BashMutationClass.READ_ONLY),
        ("echo 'a>b'", BashMutationClass.READ_ONLY),
        ("cd src && rg TODO .", BashMutationClass.READ_ONLY),
        ("make test", BashMutationClass.UNKNOWN),
        ("python script.py", BashMutationClass.UNKNOWN),
        ("$(dynamic_command)", BashMutationClass.UNKNOWN),
        ("unterminated '", BashMutationClass.UNKNOWN),
    ],
)
def test_classify_bash_mutation_matrix(command: str, expected: BashMutationClass) -> None:
    hint = classify_bash_mutation(command)

    assert hint.classification is expected
    assert hint.reason


def test_unknown_bash_is_conservative_for_progress_bookkeeping_only() -> None:
    assert _tool_call_may_change_state("bash", {"command": "make test"}) is True
    assert _semantic_bash_read_key("make test") is None
    assert _tool_call_may_change_state("bash", {"command": "git status --short"}) is False


def test_bash_classifier_is_not_imported_by_authorization_or_execution_boundaries() -> None:
    policies = Path(__file__).parents[1] / "travis" / "coding_agent" / "policies"
    forbidden_consumers = (
        policies / "package_consent.py",
        policies / "pipeline.py",
        policies / "types.py",
        Path(__file__).parents[1] / "travis" / "coding_agent" / "capabilities.py",
        Path(__file__).parents[1] / "travis" / "coding_agent" / "execution_backend.py",
    )

    for path in forbidden_consumers:
        assert "bash_classification" not in path.read_text(encoding="utf-8"), path
