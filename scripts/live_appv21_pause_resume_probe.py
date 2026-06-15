"""Probe AppV2.1 HITL pause and resume lineage."""

from __future__ import annotations

from appv21_probe_common import seed_repo, write_report

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class PauseThenFinalizeProvider:
    provider_id = "pause-resume"

    def __init__(self) -> None:
        self.decisions = [
            RuntimeDecision(kind="observe", reason="Observe before high-risk mutation intent."),
            RuntimeDecision(kind="plan", reason="Enter mutation phase.", evidence_refs=["world://repo_snapshot/latest"]),
            RuntimeDecision(
                kind="mutation_intent",
                reason="Overwrite high-risk env file.",
                payload={"operation_batch_id": "probe-risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            ),
            RuntimeDecision(kind="finalize", reason="Approved mutation.", payload={"explicit_noop": True}),
        ]

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        if self.decisions:
            return self.decisions.pop(0)
        return RuntimeDecision(kind="finalize", reason="Done.", payload={"explicit_noop": True})


def main() -> int:
    repo = seed_repo("live_appv21_pause_resume_repo")
    (repo / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    runtime = AppV21AgentRuntime(root_path=repo, services=create_appv21_runtime_services(root_path=repo, provider=PauseThenFinalizeProvider()))
    paused = runtime.run("Overwrite the high-risk env file.")
    result = runtime.resume(paused["pause_id"], {"approval": "approve:probe-risky"})
    write_report("live-appv21-pause-resume-probe.json", result)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
