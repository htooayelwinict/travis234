from __future__ import annotations

from appv22.runtime.decisions import RuntimeDecision


class DeterministicAppV22Provider:
    provider_id = "deterministic-appv22"

    def decide(self, prompt: dict) -> RuntimeDecision:
        if not prompt["world"]["world_refs"]:
            tool_ids = prompt.get("selection", {}).get("selected_tools") or prompt.get("tools", [])
            if not tool_ids:
                return RuntimeDecision("pause", "no prompt-visible tool available")
            return RuntimeDecision(
                "tool_call",
                "observe first",
                {"tool_id": tool_ids[0], "arguments": {}},
            )
        if not prompt["state"]["runtime_plan"]:
            evidence_refs = list(prompt["world"]["world_refs"])
            return RuntimeDecision("plan", "plan from observed context", evidence_refs=evidence_refs)
        if not prompt["state"]["mutation_receipts"]:
            return RuntimeDecision(
                "mutation_intent",
                "apply extension plan",
                prompt["state"]["runtime_plan"]["mutation_intent"],
                ["plan://accepted/latest"],
            )
        return RuntimeDecision("finalize", "verify and finish")
