import json
from pathlib import Path
from typing import Any

import pytest

from app.schemas import ArtifactPayload, Plan, Task
from app.worker_kernel.agentic import (
    AgenticWorkerGroupRunner,
    WorkerInstanceTemplate,
    WorkerLLMController,
)
from app.worker_kernel.env_config import build_worker_model_client, load_worker_runtime_config
from app.worker_kernel.runtime import WorkerKernelRuntime
from app.worker_kernel.tools import ToolPermissionError, WorkerToolConfig, WorkerToolbox
from app.worker_kernel.workers.agentic_templates import get_agentic_worker_templates


def _task(
    *,
    worker_type: str = "repo_worker",
    expected_outputs: list[str] | None = None,
    permissions: dict[str, Any] | None = None,
    max_tool_calls: int = 3,
    max_model_calls: int = 2,
) -> Task:
    return Task(
        task_id="task_1",
        run_id="run_1",
        step_id="step_1",
        worker_type=worker_type,
        instruction="complete scoped worker task",
        expected_outputs=expected_outputs or ["final_artifact"],
        max_tool_calls=max_tool_calls,
        max_model_calls=max_model_calls,
        permissions=permissions
        or {
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    )


class FakeConfiguredClient:
    configs: list[dict[str, Any]] = []

    def __init__(self, **config: Any) -> None:
        type(self).configs.append(config)


class QueueClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.stages: list[str] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.stages.append(stage)
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


def test_worker_env_config_builds_openrouter_compatible_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_LLM_ENABLED", raising=False)
    monkeypatch.delenv("WORKER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("WORKER_LLM_MODEL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "WORKER_LLM_ENABLED=true",
                "WORKER_LLM_API_KEY=test-key",
                "WORKER_LLM_MODEL=test-model",
                "WORKER_LLM_BASE_URL=https://worker.example/v1",
                "WORKER_LLM_PROVIDER_SORT=latency",
                "WORKER_MAX_PARALLEL_INSTANCES=2",
                "WORKER_TOOL_TIMEOUT_SECONDS=7",
                "WORKER_MAX_FILE_BYTES=1234",
            ]
        ),
        encoding="utf-8",
    )

    config = load_worker_runtime_config(dotenv)
    client = build_worker_model_client(dotenv, client_factory=FakeConfiguredClient)

    assert client is not None
    assert config.llm_enabled is True
    assert config.max_parallel_instances == 2
    assert config.tool_timeout_seconds == 7
    assert config.max_file_bytes == 1234
    assert FakeConfiguredClient.configs[-1]["api_key"] == "test-key"
    assert FakeConfiguredClient.configs[-1]["model"] == "test-model"
    assert FakeConfiguredClient.configs[-1]["base_url"] == "https://worker.example/v1"
    assert FakeConfiguredClient.configs[-1]["provider_sort"] == "latency"


def test_worker_from_env_falls_back_to_stub_registry_when_disabled(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("WORKER_LLM_ENABLED=false\n", encoding="utf-8")
    runtime = WorkerKernelRuntime.from_env(str(dotenv))

    result = runtime.run(
        Plan.model_validate(
            {
            "plan_id": "plan_direct",
            "request_id": "req_direct",
            "planner": "test",
            "objective": "answer",
            "strategy": "direct",
            "steps": [
                {
                    "step_id": "answer",
                    "worker_type": "direct_worker",
                    "instruction": "answer",
                    "output_artifacts": ["direct_answer"],
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
            }
        )
    )

    assert result.status == "completed"


def test_repo_worker_agentic_templates_are_split_by_role() -> None:
    templates = get_agentic_worker_templates()["repo_worker"]

    assert [template.name for template in templates] == ["repo_locator", "repo_reader", "repo_summarizer"]
    assert templates[0].allowed_tools
    assert "read_file" in templates[1].allowed_tools
    assert templates[2].allowed_tools == ()
    assert "Every artifact must be an object" in templates[2].system_prompt


def test_toolbox_enforces_read_write_command_and_web_permissions(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("hello worker", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    no_permissions = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        }
    )

    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="read_file", arguments={"path": "sample.txt"})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="write_file", arguments={"path": "x.txt", "content": "x"})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="run_readonly_command", arguments={"command": "rg hello ."})
    with pytest.raises(ToolPermissionError):
        toolbox.execute(task=no_permissions, tool_name="web_search", arguments={"query": "hello"})

    read_task = _task()
    assert toolbox.execute(task=read_task, tool_name="read_file", arguments={"path": "sample.txt"})["content"] == "hello worker"

    command_task = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        }
    )
    version = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": ["python", "-m", "pytest", "--version"]},
    )
    missing = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": ["python", "-m", "pytest", "does_not_exist.py"]},
    )
    pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "PYTHONPATH=. pytest --version"},
    )
    assert version["returncode"] == 0
    assert missing["returncode"] != 0
    assert pythonpath["returncode"] == 0
    assert pythonpath["env"] == {"PYTHONPATH": "."}

    with pytest.raises(ToolPermissionError):
        toolbox.execute(
            task=command_task,
            tool_name="run_readonly_command",
            arguments={"command": "OPENAI_API_KEY=x pytest --version"},
        )


def test_toolbox_excludes_repo_noise_from_discovery(tmp_path: Path) -> None:
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "blob").write_text("noise", encoding="utf-8")
    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    (tmp_path / "src" / "__pycache__" / "module.pyc").write_text("noise", encoding="utf-8")
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task()

    listed = toolbox.execute(task=task, tool_name="list_dir", arguments={"path": "."})
    searched = toolbox.execute(task=task, tool_name="file_search", arguments={"path": ".", "pattern": "**/*"})

    assert ".git" not in {entry["name"] for entry in listed["entries"]}
    assert "src/checkout.py" in searched["matches"]
    assert not any(".git" in match or "__pycache__" in match for match in searched["matches"])


def test_toolbox_extracts_nested_write_scope_paths_from_artifacts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "checkout.py"
    target.write_text('key = "charge:{order_id}:retry:{retry_count}"\n', encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        }
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={
                        "evidence": [
                            {
                                "file": "src/checkout.py",
                                "change_type": "modify_string_formatting",
                            }
                        ],
                        "notes": "Only mutate the scoped source file.",
                    },
                )
            ]
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="replace_in_file",
        arguments={
            "path": "src/checkout.py",
            "old": "retry:{retry_count}",
            "new": "stable",
        },
    )

    assert result["replacements"] == 1
    assert "stable" in target.read_text(encoding="utf-8")


def test_agentic_worker_group_fanout_and_artifact_handoff(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "sources found",
                    "artifacts": [{"id": "source_links", "content": ["https://example.test/a"]}],
                }
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "citations formatted",
                    "artifacts": [{"id": "final_artifact", "content": "cited result"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="web_research_worker",
        templates=[
            WorkerInstanceTemplate(name="source_discovery", role="find sources"),
            WorkerInstanceTemplate(name="citation_formatter", role="format citations"),
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="web_research_worker",
            expected_outputs=["final_artifact"],
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            },
            max_model_calls=2,
        )
    )

    assert result.status == "completed"
    assert "source_links" in client.prompts[1]
    assert {artifact.id for artifact in result.artifacts} >= {"source_links", "final_artifact"}
    assert len(result.metadata["worker_group_results"]) == 2


def test_worker_llm_controller_normalizes_common_tool_call_aliases() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {"name": "list_dir", "args": {"path": "."}},
                    {"name": "text_search", "arguments": {"pattern": "idempotency"}},
                ]
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.tool_calls[0].tool_name == "list_dir"
    assert decision.tool_calls[0].arguments == {"path": "."}
    assert decision.tool_calls[1].tool_name == "text_search"


def test_worker_llm_controller_normalizes_root_level_tool_call() -> None:
    client = QueueClient([{"name": "list_dir", "arguments": {"path": "."}}])

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].tool_name == "list_dir"


def test_worker_llm_controller_normalizes_openai_function_tool_call() -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\": \"src/checkout.py\"}",
                        },
                    }
                ]
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.tool_calls[0].tool_name == "read_file"
    assert decision.tool_calls[0].arguments == {"path": "src/checkout.py"}


def test_worker_llm_controller_normalizes_root_level_final_status() -> None:
    client = QueueClient(
        [
            {
                "status": "needs_replan",
                "reason": "Discovery did not produce target files.",
                "missing_artifacts": ["target_files", "test_command"],
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.status == "needs_replan"
    assert decision.final_result.summary == "Discovery did not produce target files."
    assert decision.final_result.issues[0].issue_type == "plan_failure"
    assert decision.final_result.issues[0].metadata["missing_artifacts"] == ["target_files", "test_command"]


def test_worker_llm_controller_converts_final_result_fields_to_artifacts() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "success",
                    "summary": "Discovery complete.",
                    "repo_inventory": ["README.md", "src/checkout.py"],
                    "candidate_retry_locations": ["src/checkout.py::build_charge_headers"],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.status == "completed"
    artifact_ids = {artifact.id for artifact in decision.final_result.artifacts}
    assert {"repo_inventory", "candidate_retry_locations"} <= artifact_ids


def test_worker_llm_controller_normalizes_bare_artifact_ids_as_placeholders() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Declared outputs.",
                    "artifacts": ["repo_inventory"],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="repo_worker_repo_locator", prompt="{}")

    assert decision.final_result is not None
    artifact = decision.final_result.artifacts[0]
    assert artifact.id == "repo_inventory"
    assert artifact.content is None
    assert artifact.metadata["worker_returned_bare_artifact_id"] is True


def test_worker_llm_controller_normalizes_string_issues() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "needs_replan",
                    "summary": "Need source evidence.",
                    "issues": ["Missing source code content for src/checkout.py."],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    assert decision.final_result.issues[0].issue_type == "plan_failure"
    assert decision.final_result.issues[0].message == "Missing source code content for src/checkout.py."


def test_research_worker_template_can_use_readonly_tools_when_permitted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "name": "read_file",
                        "arguments": {"path": "src/checkout.py"},
                    }
                ],
                "final_result": {
                    "status": "completed",
                    "summary": "This same-turn final result must wait for the next model turn.",
                    "artifacts": [{"id": "ignored_same_turn_artifact", "content": "ignored"}],
                },
            },
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Read source.",
                    "artifacts": [{"id": "final_artifact", "content": "source read"}],
                },
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="research_worker",
        templates=[
            WorkerInstanceTemplate(
                name="context_synthesizer",
                role="synthesize",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="research_worker", max_tool_calls=1, max_model_calls=2))

    assert result.status == "completed"
    assert result.usage["model_calls"] == 2
    assert any(artifact.id == "final_artifact" for artifact in result.artifacts)
    assert all(artifact.id != "ignored_same_turn_artifact" for artifact in result.artifacts)


def test_agentic_prompt_uses_worker_system_prompt_and_function_tool_specs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Located repo context.",
                    "artifacts": [{"id": "final_artifact", "content": "repo context"}],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                system_prompt="You are the repository discovery worker.",
                allowed_tools=("list_dir", "read_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=1))
    payload = json.loads(client.prompts[0])

    assert result.status == "completed"
    assert payload["instance"]["system_prompt"] == "You are the repository discovery worker."
    assert payload["available_tools"][0]["type"] == "function"
    assert payload["available_tools"][0]["function"]["name"] == "list_dir"


def test_agentic_prompt_hides_tools_after_tool_budget_is_spent(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Finalized from observations.",
                    "artifacts": [{"id": "final_artifact", "content": "done"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                system_prompt="You are the repository discovery worker.",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=2))
    second_prompt = json.loads(client.prompts[1])

    assert result.status == "completed"
    assert second_prompt["runtime_budget"]["remaining_tool_calls"] == 0
    assert second_prompt["available_tools"] == []


def test_agentic_group_does_not_count_bare_artifact_ids_as_completed_outputs(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Only declared artifact names.",
                    "artifacts": ["final_artifact"],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[WorkerInstanceTemplate(name="repo_locator", role="locate files")],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=0, max_model_calls=1))

    assert result.status == "needs_replan"
    assert result.metadata["issues"][0]["code"] == "missing_expected_artifacts"
    assert result.metadata["issues"][0]["metadata"]["missing_artifacts"] == ["final_artifact"]


def test_tool_observation_without_final_model_budget_requests_replan(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout(): pass\n", encoding="utf-8")
    client = QueueClient([{"tool_calls": [{"name": "list_dir", "arguments": {"path": "."}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_locator",
                role="locate files",
                allowed_tools=("list_dir",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(max_tool_calls=1, max_model_calls=1))

    assert result.status == "needs_replan"
    assert result.metadata["issues"][0]["code"] == "model_budget_exhausted_before_final_result"
    assert any(artifact.kind == "tool_observation_summary" for artifact in result.artifacts)


def test_agentic_worker_rejects_disallowed_tool_as_retryable_instance_failure(tmp_path: Path) -> None:
    client = QueueClient([{"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "missing.txt"}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="direct_worker",
        templates=[WorkerInstanceTemplate(name="direct_responder", role="answer", allowed_tools=())],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="direct_worker",
            permissions={
                "read_files": True,
                "write_files": False,
                "run_commands": False,
                "web_research": False,
            },
        )
    )

    assert result.status == "failed"
    assert result.metadata["issues"][0]["issue_type"] == "instance_failure"
    assert result.metadata["issues"][0]["retryable"] is True


def test_agentic_web_search_without_provider_requests_replan(tmp_path: Path) -> None:
    client = QueueClient([{"tool_calls": [{"tool_name": "web_search", "arguments": {"query": "worker runtime"}}]}])
    group = AgenticWorkerGroupRunner(
        worker_type="web_research_worker",
        templates=[
            WorkerInstanceTemplate(
                name="source_discovery",
                role="find sources",
                allowed_tools=("web_search",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(
        _task(
            worker_type="web_research_worker",
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            },
            max_tool_calls=1,
            max_model_calls=1,
        )
    )

    assert result.status == "needs_replan"
    assert result.metadata["issues"][0]["issue_type"] == "plan_failure"
    assert result.metadata["issues"][0]["code"] == "tool_unavailable"
