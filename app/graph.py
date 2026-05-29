"""LangGraph assembly for Phase 1 runtime architecture."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime
from app.schemas import Envelope, Plan, Result, RuntimeState
from app.worker_kernel.runtime import WorkerKernelRuntime


def build_graph(*, decompressor_runtime=None, client_factory=None):
    if decompressor_runtime is None:
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        decompressor_runtime = DecompressorRuntime.from_env(**client_options)
    planner_runtime = PlannerRuntime()
    worker_kernel_runtime = WorkerKernelRuntime()

    def decompressor_node(state: RuntimeState) -> RuntimeState:
        user_input = state.get("user_input", "")
        envelope = decompressor_runtime.run(user_input)
        return {
            "envelope": envelope.model_dump(),
            "errors": state.get("errors", []),
        }

    def planner_node(state: RuntimeState) -> RuntimeState:
        envelope = Envelope.model_validate(state["envelope"])
        plan = planner_runtime.run(envelope)
        return {
            "plan": plan.model_dump(),
            "errors": state.get("errors", []),
        }

    def worker_kernel_node(state: RuntimeState) -> RuntimeState:
        plan = Plan.model_validate(state["plan"])
        result = worker_kernel_runtime.run(plan)
        return {
            "result": result.model_dump(),
            "errors": state.get("errors", []),
        }

    graph = StateGraph(RuntimeState)
    graph.add_node("decompressor_node", decompressor_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("worker_kernel_node", worker_kernel_node)

    graph.add_edge(START, "decompressor_node")
    graph.add_edge("decompressor_node", "planner_node")
    graph.add_edge("planner_node", "worker_kernel_node")
    graph.add_edge("worker_kernel_node", END)

    return graph.compile()
