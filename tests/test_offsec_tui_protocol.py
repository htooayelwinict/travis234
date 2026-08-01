from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/offsec/tui-test-protocol.md"


def test_protocol_defines_exactly_seven_executable_scenarios() -> None:
    text = PROTOCOL.read_text(encoding="utf-8")
    headings = re.findall(r"^## Scenario ([1-7]):", text, flags=re.MULTILINE)
    assert headings == list("1234567")
    for section in re.split(r"^## Scenario [1-7]:", text, flags=re.MULTILINE)[1:]:
        assert "### Setup" in section
        assert "### Exact prompt" in section
        assert "### Expected tools/events" in section
        assert "### Pass criteria" in section
        assert "### Cleanup" in section


def test_protocol_covers_single_agent_terminal_and_compaction_contracts() -> None:
    text = PROTOCOL.read_text(encoding="utf-8")
    for required in (
        "Travis234 OffSec",
        "--target local-ctf-fixture",
        "bash",
        "process",
        "spawn_subagent",
        "exactly three",
        "tmux",
        "/compact",
        "--continue",
    ):
        assert required in text
