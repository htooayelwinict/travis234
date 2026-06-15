"""Probe AppV2.1 runtime context compaction."""

from __future__ import annotations

from appv21_probe_common import seed_repo, write_report

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class CompactingProvider:
    provider_id = "compacting"

    def __init__(self) -> None:
        self.calls = 0
        self.compacted = False

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        self.calls += 1
        if self.calls <= 8:
            return RuntimeDecision(kind="tool_call", reason="Read README repeatedly.", payload={"tool_name": "read_file", "arguments": {"path": "README.md"}})
        if not self.compacted:
            self.compacted = True
            return RuntimeDecision(kind="compact", reason="Compact after verification evidence.")
        return RuntimeDecision(kind="finalize", reason="Verified no-op.", payload={"explicit_noop": True})


def main() -> int:
    repo = seed_repo("live_appv21_context_compaction_repo")
    services = create_appv21_runtime_services(root_path=repo, provider=CompactingProvider())
    result = AppV21AgentRuntime(root_path=repo, services=services, max_turns=12).run("Exercise compaction.")
    write_report("live-appv21-context-compaction-probe.json", result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
