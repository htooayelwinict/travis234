import json
import re
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
                "input_type": "docker_concept_question",
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


class FakeConfiguredPlannerClient:
    def __init__(self, **config: Any) -> None:
        self.config = config

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        matches = re.findall(r'"request_id":\s*"([^"]+)"', prompt)
        request_id = matches[-1] if matches else "req_001"
        responses = {
            "draft_plan": {
                "plan_id": f"plan_{request_id}_direct_support",
                "request_id": request_id,
                "planner": "direct_support_planner",
                "objective": "Answer the user's question directly.",
                "strategy": "phase_aware_direct_support",
                "execution_pattern": "finalize",
                "global_invariants": ["no_tools", "no_file_access", "answer_from_user_input_only"],
                "steps": [
                    {
                        "step_id": "direct_support_response",
                        "worker_type": "direct_worker",
                        "phase": "FINALIZE",
                        "mode": "summarize_only",
                        "task_id": "direct_support",
                        "instruction": "Known facts: User asks what Docker is. Unknowns: none. Do now: answer directly. Do not do: do not use tools. Output: direct_guidance.",
                        "input_artifacts": [],
                        "output_artifacts": ["direct_guidance"],
                        "max_tool_calls": 0,
                        "max_model_calls": 1,
                        "permissions": {
                            "read_files": False,
                            "write_files": False,
                            "run_commands": False,
                            "web_research": False,
                        },
                    }
                ],
                "budget": {"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
                "success_criteria": ["User receives a direct answer."],
                "metadata": {},
            }
        }
        return json.dumps(responses[stage])


def test_compiled_graph_invocation(monkeypatch) -> None:
    monkeypatch.setenv("DECOMPRESSOR_LLM_ENABLED", "true")
    monkeypatch.setenv("DECOMPRESSOR_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DECOMPRESSOR_LLM_MODEL", "test-model")
    monkeypatch.setenv("PLANNER_LLM_ENABLED", "true")
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("PLANNER_LLM_MODEL", "test-model")
    monkeypatch.setenv("WORKER_LLM_ENABLED", "false")
    graph = build_graph(client_factory=FakeConfiguredClient, planner_client_factory=FakeConfiguredPlannerClient)

    state = graph.invoke({"user_input": "what is docker", "errors": []})

    assert "envelope" in state
    assert "plan" in state
    assert "result" in state
    assert state["result"]["status"] == "completed"
    assert state["envelope"]["metadata"]["decompressor_mode"] == "llm_prompt_chain"


def test_graph_registers_required_node_keys_when_exposed(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_LLM_ENABLED", "false")
    monkeypatch.setenv("WORKER_LLM_ENABLED", "false")
    graph = build_graph(decompressor_runtime=object())

    nodes = getattr(graph, "nodes", None)
    if isinstance(nodes, dict):
        assert "decompressor_node" in nodes
        assert "planner_node" in nodes
        assert "worker_kernel_node" in nodes


def test_compiled_graph_can_use_worker_llm_runtime(monkeypatch) -> None:
    class FakeWorkerClient:
        def __init__(self, **config: Any) -> None:
            self.config = config

        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            return json.dumps(
                {
                    "final_result": {
                        "status": "completed",
                        "summary": "worker answered",
                        "artifacts": [{"id": "direct_guidance", "content": "Docker packages apps."}],
                    }
                }
            )

    monkeypatch.setenv("DECOMPRESSOR_LLM_ENABLED", "true")
    monkeypatch.setenv("DECOMPRESSOR_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DECOMPRESSOR_LLM_MODEL", "test-model")
    monkeypatch.setenv("PLANNER_LLM_ENABLED", "true")
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("PLANNER_LLM_MODEL", "test-model")
    monkeypatch.setenv("WORKER_LLM_ENABLED", "true")
    monkeypatch.setenv("WORKER_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WORKER_LLM_MODEL", "test-model")

    graph = build_graph(
        client_factory=FakeConfiguredClient,
        planner_client_factory=FakeConfiguredPlannerClient,
        worker_client_factory=FakeWorkerClient,
    )

    state = graph.invoke({"user_input": "what is docker", "errors": []})

    assert state["result"]["status"] == "completed"
    assert state["result"]["metadata"]["worker_results"][0]["metadata"]["worker_type"] == "direct_worker"
