import json
from typing import Any

from app.graph import build_graph


class FakeConfiguredClient:
    def __init__(self, **config: Any) -> None:
        self.config = config

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        responses = {
            "decompress_request": {
                "normalized_input": "what is docker",
                "user_goal": "Answer the user's question.",
                "input_type": "question",
                "intents": ["question.answer"],
                "domains": ["infra"],
                "risks": [],
                "artifacts": [],
                "context_needed": [],
                "constraints": [],
                "complexity_hint": "low",
                "confidence": 0.9,
                "ambiguity": [],
                "assumptions": [],
            },
        }
        return json.dumps(responses[stage])


def test_compiled_graph_invocation(monkeypatch) -> None:
    monkeypatch.setenv("DECOMPRESSOR_LLM_ENABLED", "true")
    monkeypatch.setenv("DECOMPRESSOR_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DECOMPRESSOR_LLM_MODEL", "test-model")
    graph = build_graph(client_factory=FakeConfiguredClient)

    state = graph.invoke({"user_input": "what is docker", "errors": []})

    assert "envelope" in state
    assert "plan" in state
    assert "result" in state
    assert state["result"]["status"] == "completed"
    assert state["envelope"]["metadata"]["decompressor_mode"] == "llm_prompt_chain"


def test_graph_registers_required_node_keys_when_exposed() -> None:
    graph = build_graph(decompressor_runtime=object())

    nodes = getattr(graph, "nodes", None)
    if isinstance(nodes, dict):
        assert "decompressor_node" in nodes
        assert "planner_node" in nodes
        assert "worker_kernel_node" in nodes
