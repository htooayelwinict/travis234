import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.schemas import ArtifactPayload, MutationScope, Plan, PlanStep, Task, resolve_mutation_scope_proposal
from app.runtime_matrix import RuntimeMatrixLogger
from app.worker_kernel.compiler import TaskCompiler
from app.worker_kernel.agentic import (
    AgenticWorkerGroupRunner,
    WorkerInstanceTemplate,
    WorkerLLMController,
    _normalize_worker_decision,
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
                "WORKER_WEB_SEARCH_PROVIDER=duckduckgo",
                "WORKER_WEB_SEARCH_API_KEY=brave-key",
                "WORKER_WEB_SEARCH_MAX_RESULTS=7",
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
    assert config.web_search_provider == "duckduckgo"
    assert config.web_search_api_key == "brave-key"
    assert config.web_search_max_results == 7
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


def test_filesystem_worker_template_exposes_scoped_file_tools() -> None:
    templates = get_agentic_worker_templates()["filesystem_worker"]

    assert [template.name for template in templates] == ["filesystem_operator"]
    assert "write_many_files" in templates[0].allowed_tools
    assert "move_file" in templates[0].allowed_tools
    assert "delete_file" in templates[0].allowed_tools
    assert "runtime_capabilities" in templates[0].allowed_tools
    assert "shell chaining" in templates[0].system_prompt
    assert "hatchling" in templates[0].system_prompt
    assert 'packages = ["app"]' in templates[0].system_prompt


def test_worker_decision_normalizes_implementation_failure_issue_type() -> None:
    normalized = _normalize_worker_decision(
        {
            "final_result": {
                "status": "failed",
                "summary": "Generated package failed verification.",
                "issues": [
                    {
                        "issue_type": "implementation_failure",
                        "code": "pytest_failed",
                        "message": "pytest failed after scaffold",
                    }
                ],
            }
        }
    )

    assert normalized["final_result"]["issues"][0]["issue_type"] == "instance_failure"


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
    env_pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "env PYTHONPATH=. pytest --version"},
    )
    sh_pythonpath = toolbox.execute(
        task=command_task,
        tool_name="run_readonly_command",
        arguments={"command": "sh -c 'PYTHONPATH=. pytest --version'"},
    )
    assert version["returncode"] == 0
    assert missing["returncode"] != 0
    assert pythonpath["returncode"] == 0
    assert pythonpath["command"][1:3] == ["-m", "pytest"]
    assert pythonpath["env"] == {"PYTHONPATH": "."}
    assert env_pythonpath["returncode"] == 0
    assert env_pythonpath["command"][1:3] == ["-m", "pytest"]
    assert env_pythonpath["env"] == {"PYTHONPATH": "."}
    assert sh_pythonpath["returncode"] == 0
    assert sh_pythonpath["command"][1:3] == ["-m", "pytest"]
    assert sh_pythonpath["env"] == {"PYTHONPATH": "."}

    with pytest.raises(ToolPermissionError):
        toolbox.execute(
            task=command_task,
            tool_name="run_readonly_command",
            arguments={"command": "OPENAI_API_KEY=x pytest --version"},
        )
    with pytest.raises(ToolPermissionError):
        toolbox.execute(
            task=command_task,
            tool_name="run_readonly_command",
            arguments={"command": "sh -c 'pytest --version && echo unsafe'"},
        )


def test_toolbox_high_level_worker_tools_are_permission_gated_and_scoped(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text(
        "from src.app import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, text=True)
    target.write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")

    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    read_task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["src/app.py"], "reason": "app logic", "max_files": 1},
                )
            ]
        }
    )
    command_task = read_task.model_copy(
        update={
            "permissions": read_task.permissions.model_copy(update={"run_commands": True})
        }
    )

    snapshot = toolbox.execute(task=read_task, tool_name="repo_snapshot", arguments={"path": "."})
    many = toolbox.execute(
        task=read_task,
        tool_name="read_many_files",
        arguments={"paths": ["src/app.py", "tests/test_app.py"]},
    )
    diff = toolbox.execute(task=read_task, tool_name="diff_summary", arguments={"path": "src/app.py"})
    scope = toolbox.execute(task=read_task, tool_name="mutation_scope_check", arguments={})
    tests = toolbox.execute(
        task=command_task,
        tool_name="run_focused_tests",
        arguments={"paths": "tests/test_app.py"},
    )

    assert "src/app.py" in snapshot["files"]
    assert "tests/test_app.py" in snapshot["test_candidates"]
    assert [file["path"] for file in many["files"]] == ["src/app.py", "tests/test_app.py"]
    assert diff["changed_files"] == ["src/app.py"]
    assert scope["passed"] is True
    assert scope["in_scope"] == ["src/app.py"]
    assert tests["returncode"] == 0
    assert tests["env"] == {"PYTHONPATH": "."}


def test_toolbox_web_search_uses_configured_duckduckgo_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b'''
                <html>
                  <a class="result__a" href="/l/?uddg=https%3A%2F%2Fstripe.com%2Fdocs%2Fpayments%2Fpayment-intents">Stripe docs</a>
                  <a class="result__snippet">Use idempotency keys for retry safety.</a>
                  <a class="result__a" href="https://example.com/retry">Retry guide</a>
                  <a class="result__snippet">Backoff reduces duplicate pressure.</a>
                </html>
            '''

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    toolbox = WorkerToolbox(
        WorkerToolConfig(
            root_path=tmp_path,
            web_search_provider="duckduckgo",
            web_search_max_results=2,
        )
    )
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_search",
        arguments={"query": "payment idempotency retry"},
    )

    assert "payment+idempotency+retry" in captured["url"]
    assert captured["timeout"] == 15.0
    assert result["provider"] == "duckduckgo"
    assert len(result["results"]) == 2
    assert result["results"][0]["url"] == "https://stripe.com/docs/payments/payment-intents"
    assert "idempotency keys" in result["results"][0]["snippet"]


def test_toolbox_web_search_uses_brave_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return json.dumps(
                {
                    "query": {"original": "payment idempotency retry"},
                    "web": {
                        "results": [
                            {
                                "title": "Stripe idempotency docs",
                                "url": "https://stripe.com/docs/idempotency",
                                "description": "Use idempotency keys for retries.",
                                "extra_snippets": ["Retry requests can safely use the same key."],
                                "profile": {"name": "Stripe"},
                                "age": "2 weeks ago",
                                "language": "en",
                            }
                        ]
                    },
                }
            ).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    toolbox = WorkerToolbox(
        WorkerToolConfig(
            root_path=tmp_path,
            web_search_provider="brave",
            web_search_api_key="test-brave-key",
            web_search_max_results=3,
        )
    )
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_search",
        arguments={"query": "payment idempotency retry"},
    )

    assert "api.search.brave.com/res/v1/web/search" in captured["url"]
    assert "payment+idempotency+retry" in captured["url"]
    assert captured["headers"]["X-subscription-token"] == "test-brave-key"
    assert captured["timeout"] == 15.0
    assert result["provider"] == "brave"
    assert result["results"][0]["title"] == "Stripe idempotency docs"
    assert result["results"][0]["snippets"] == [
        "Use idempotency keys for retries.",
        "Retry requests can safely use the same key.",
    ]


def test_toolbox_web_fetch_extracts_readable_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHeaders:
        def get(self, key: str, default: str = "") -> str:
            return "text/html; charset=utf-8" if key == "Content-Type" else default

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def geturl(self) -> str:
            return "https://example.com/final"

        def read(self, limit: int) -> bytes:
            return b"""
                <html>
                  <head>
                    <title>Retry Safety</title>
                    <meta name="description" content="Payment retry notes">
                    <script>secret()</script>
                  </head>
                  <body>
                    <h1>Payment retries</h1>
                    <p>Use stable idempotency keys.</p>
                    <a href="https://example.com/source">Source page</a>
                  </body>
                </html>
            """

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    result = toolbox.execute(
        task=_task(
            permissions={
                "read_files": False,
                "write_files": False,
                "run_commands": False,
                "web_research": True,
            }
        ),
        tool_name="web_fetch",
        arguments={"url": "https://example.com/retry"},
    )

    assert result["final_url"] == "https://example.com/final"
    assert result["title"] == "Retry Safety"
    assert result["description"] == "Payment retry notes"
    assert "Use stable idempotency keys." in result["content"]
    assert "secret()" not in result["content"]
    assert result["links"] == [{"url": "https://example.com/source", "text": "Source page"}]


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


def test_toolbox_treats_root_basename_as_mounted_repo_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task()

    snapshot = toolbox.execute(task=task, tool_name="repo_snapshot", arguments={"path": tmp_path.name})
    read = toolbox.execute(
        task=task,
        tool_name="read_file",
        arguments={"path": f"{tmp_path.name}/src/app.py"},
    )

    assert snapshot["path"] == "."
    assert snapshot["is_empty"] is False
    assert "src/app.py" in snapshot["files"]
    assert read["path"] == "src/app.py"


def test_toolbox_runtime_capabilities_is_structured_command_tool(tmp_path: Path) -> None:
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        }
    )

    result = toolbox.execute(task=task, tool_name="runtime_capabilities", arguments={})

    assert result["preferred_local_stack"] == "python"
    assert result["capabilities"]["python"]["available"] is True
    assert result["capabilities"]["pytest"]["command"][1:3] == ["-m", "pytest"]


def test_toolbox_batch_write_move_and_delete_are_scoped(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "staging").mkdir()
    (tmp_path / "staging" / "draft.md").write_text("draft", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["docs", "staging/draft.md"],
        }
    )

    written = toolbox.execute(
        task=task,
        tool_name="write_many_files",
        arguments={
            "files": [
                {"path": "docs/a.md", "content": "# A\n"},
                {"path": "docs/b.md", "content": "# B\n"},
            ]
        },
    )
    moved = toolbox.execute(
        task=task,
        tool_name="move_file",
        arguments={"source": "staging/draft.md", "destination": "docs/draft.md"},
    )
    deleted = toolbox.execute(task=task, tool_name="delete_file", arguments={"path": "docs/b.md"})

    assert written["count"] == 2
    assert moved["destination"] == "docs/draft.md"
    assert deleted["deleted"] is True
    assert (tmp_path / "docs" / "a.md").read_text(encoding="utf-8") == "# A\n"
    assert not (tmp_path / "staging" / "draft.md").exists()
    with pytest.raises(ToolPermissionError, match="outside allowed scope"):
        toolbox.execute(
            task=task,
            tool_name="write_many_files",
            arguments={"files": [{"path": "src/app.py", "content": "bad"}]},
        )
    assert not (tmp_path / "src" / "app.py").exists()


def test_toolbox_reports_empty_repo_snapshot(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    snapshot = toolbox.execute(task=_task(), tool_name="repo_snapshot", arguments={"path": tmp_path.name})

    assert snapshot["path"] == "."
    assert snapshot["is_empty"] is True
    assert snapshot["files"] == []
    assert snapshot["directories"] == []


def test_diff_summary_and_scope_check_include_untracked_new_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": False,
            "run_commands": False,
            "web_research": False,
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["src/app.py"], "reason": "new file", "max_files": 1},
                )
            ]
        }
    )

    diff = toolbox.execute(task=task, tool_name="diff_summary", arguments={"path": "src/app.py"})
    scope = toolbox.execute(task=task, tool_name="mutation_scope_check", arguments={})

    assert diff["changed_files"] == ["src/app.py"]
    assert "+++ src/app.py" in diff["diff"]
    assert scope["passed"] is True
    assert scope["in_scope"] == ["src/app.py"]


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


def test_mutation_scope_accepts_structured_target_paths() -> None:
    scope = MutationScope.model_validate(
        {
            "target_paths": ["src/fulfillment/events.py"],
            "test_paths": ["tests/test_webhook.py"],
            "forbidden_paths": ["src/fulfillment/secrets.py"],
            "reason": "only webhook idempotency code should change",
            "max_files": 2,
        }
    )

    assert scope.target_paths == ["src/fulfillment/events.py"]
    assert scope.test_paths == ["tests/test_webhook.py"]
    assert scope.write_scope_paths == ["src/fulfillment/events.py"]


def test_mutation_scope_normalizes_forbidden_globs() -> None:
    scope = MutationScope.model_validate(
        {
            "target_paths": ["src/fulfillment/events.py"],
            "forbidden_paths": ["**/*.py"],
            "reason": "source code should remain untouched",
            "max_files": 1,
        }
    )

    assert scope.forbidden_paths == []
    assert scope.forbidden_globs == ["**/*.py"]


def test_mutation_scope_accepts_legacy_file_label() -> None:
    scope = MutationScope.model_validate(
        {
            "evidence": [
                "File: src/fulfillment/events.py",
                "Insertion point: after the ignored event branch.",
            ],
            "notes": "Strictly limited to `src/fulfillment/events.py`.",
        }
    )

    assert scope.target_paths == ["src/fulfillment/events.py"]


def test_mutation_scope_rejects_escaping_path() -> None:
    with pytest.raises(ValueError, match="invalid repo-relative path"):
        MutationScope.model_validate(
            {
                "target_paths": ["../secret.py"],
                "reason": "bad scope",
            }
        )


def test_mutation_scope_rejects_too_many_files() -> None:
    with pytest.raises(ValueError, match="exceeding max_files"):
        MutationScope.model_validate(
            {
                "target_paths": ["src/a.py", "src/b.py", "src/c.py"],
                "reason": "too broad",
                "max_files": 2,
            }
        )


def test_mutation_scope_extracts_move_destinations_and_skips_manifest_noise() -> None:
    scope = MutationScope.model_validate(
        {
            "moves": [
                {"source": "notes/drafts/task_notes.md", "destination": "docs/task_notes.md"},
                {"source": "tmp/tmp_report.md", "destination": "docs/tmp_report.md"},
            ],
            "manifest_target": "docs/workspace_manifest.json",
            "excluded": [
                {"file": "notes/raw/old_blob.txt", "reason": "not markdown"},
                "misc/legacy.txt",
            ],
            "missing_sources": ["misc"],
        }
    )

    assert scope.target_paths == [
        "docs/task_notes.md",
        "docs/tmp_report.md",
        "docs/workspace_manifest.json",
    ]
    assert scope.max_files == 3


def test_mutation_scope_extracts_operation_paths_for_greenfield_scaffold() -> None:
    scope = MutationScope.model_validate(
        {
            "operations": [
                {"action": "create", "path": "pyproject.toml"},
                {"action": "create", "path": ".dockerignore"},
                {"action": "create", "path": "calculator/main.py"},
                {"action": "create", "path": "tests/test_api.py"},
            ]
        }
    )

    assert scope.target_paths == ["pyproject.toml", ".dockerignore", "calculator/main.py", "tests/test_api.py"]
    assert scope.max_files == 4


def test_mutation_scope_resolver_marks_proposal_source() -> None:
    scope = resolve_mutation_scope_proposal(
        {"target_paths": [".gitignore", ".prettierrc", "app/main.py"]},
        source_artifact_id="mutation_scope",
    )

    assert scope.target_paths == [".gitignore", ".prettierrc", "app/main.py"]
    assert scope.metadata["resolver"] == "mutation_scope_proposal_v1"
    assert scope.metadata["source_artifact_id"] == "mutation_scope"


def test_toolbox_rejects_write_outside_approved_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    allowed = tmp_path / "src" / "allowed.py"
    denied = tmp_path / "src" / "denied.py"
    allowed.write_text("value = 'old'\n", encoding="utf-8")
    denied.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/allowed.py"],
        }
    )

    with pytest.raises(ToolPermissionError, match="outside allowed scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/denied.py", "old": "old", "new": "new"},
        )


def test_toolbox_rejects_forbidden_subpath(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "secret.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src"],
        },
    ).model_copy(
        update={
            "metadata": {
                "write_scope": {
                    "target_paths": ["src"],
                    "forbidden_paths": ["src/secret.py"],
                    "reason": "directory scope with explicit exclusion",
                    "max_files": 5,
                }
            }
        }
    )

    with pytest.raises(ToolPermissionError, match="forbidden scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/secret.py", "old": "old", "new": "new"},
        )


def test_toolbox_rejects_forbidden_glob(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/app.py"],
        },
    ).model_copy(
        update={
            "metadata": {
                "write_scope": {
                    "target_paths": ["src/app.py"],
                    "forbidden_paths": ["**/*.py"],
                    "reason": "glob exclusion",
                    "max_files": 1,
                }
            }
        }
    )

    with pytest.raises(ToolPermissionError, match="forbidden scope"):
        toolbox.execute(
            task=task,
            tool_name="replace_in_file",
            arguments={"path": "src/app.py", "old": "old", "new": "new"},
        )


def test_toolbox_normalizes_root_basename_in_write_scope(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": [f"{tmp_path.name}/src/app.py"],
        }
    )

    result = toolbox.execute(
        task=task,
        tool_name="replace_in_file",
        arguments={"path": f"{tmp_path.name}/src/app.py", "old": "old", "new": "new"},
    )

    assert result["path"] == "src/app.py"
    assert "new" in target.read_text(encoding="utf-8")


def test_toolbox_rejects_invalid_strict_scope_artifact_without_fallback(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 'old'\n", encoding="utf-8")
    toolbox = WorkerToolbox(WorkerToolConfig(root_path=tmp_path))
    task = _task(
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    ).model_copy(
        update={
            "input_artifacts": [
                ArtifactPayload(
                    id="mutation_scope",
                    content={"target_paths": ["../secret.py"], "reason": "bad scope"},
                )
            ]
        }
    )

    with pytest.raises(ToolPermissionError, match="invalid write scope artifact mutation_scope"):
        toolbox.validate_write_scope(task)


def test_task_compiler_merge_scope_ceiling_tracks_merged_paths() -> None:
    step = PlanStep(
        step_id="mutate",
        worker_type="code_worker",
        phase="MUTATE",
        mode="bounded_mutation",
        instruction="apply scoped edit",
        input_artifacts=["mutation_scope"],
        output_artifacts=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/b.py"],
            "write_paths_from_artifacts": ["mutation_scope"],
        },
    )
    artifact_store = {
        "mutation_scope": ArtifactPayload(
            id="mutation_scope",
            content={"target_paths": ["src/a.py"], "reason": "one file", "max_files": 1},
        )
    }

    task = TaskCompiler().compile("run", step, artifact_store)

    assert task.permissions.write_paths == ["src/b.py", "src/a.py"]
    assert task.metadata["write_scope"]["max_files"] == 2
    assert task.metadata["write_scope"]["metadata"]["resolver"] == "mutation_scope_proposal_v1"
    assert task.metadata["write_scope"]["metadata"]["source_artifact_ids"] == ["mutation_scope"]


def test_agentic_group_blocks_missing_write_scope_before_model_call(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "should not be called",
                    "artifacts": [{"id": "change_summary", "content": "bad"}],
                }
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("replace_in_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
        },
    )

    result = runner.run(task)

    assert result.status == "blocked"
    assert result.metadata["issue_code"] == "invalid_write_scope"
    assert client.prompts == []


def test_code_worker_synthesizes_mutation_artifacts_after_write_budget_exhaustion(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "checkout.py"
    target.write_text("value = 'old'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True, text=True)
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "tool_calls": [
                    {
                        "tool_name": "replace_in_file",
                        "arguments": {"path": "src/checkout.py", "old": "old", "new": "new"},
                    }
                ]
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("read_file", "replace_in_file", "git_diff"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/checkout.py"],
        },
        max_tool_calls=2,
        max_model_calls=2,
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert "new" in target.read_text(encoding="utf-8")
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"change_summary", "rollback_patch", "patch_diff"} <= artifact_ids
    assert result.metadata["fallback"] == "mutation_observation_synthesis"


def test_filesystem_worker_synthesizes_mutation_artifacts_after_batch_write_budget_exhaustion(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "write_many_files",
                        "arguments": {
                            "files": [
                                {"path": "src/calculator.py", "content": "def add(a, b):\n    return a + b\n"},
                                {
                                    "path": "tests/test_calculator.py",
                                    "content": "from src.calculator import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                                },
                                {"path": "README.md", "content": "# Calculator API\n"},
                            ]
                        },
                    }
                ]
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="filesystem_worker",
        templates=[
            WorkerInstanceTemplate(
                name="filesystem_operator",
                role="scaffold",
                system_prompt="scaffold",
                allowed_tools=("write_many_files", "diff_summary"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="filesystem_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src", "tests", "README.md"],
        },
        max_tool_calls=1,
        max_model_calls=1,
    )

    result = runner.run(task)

    assert result.status == "completed"
    assert (tmp_path / "src" / "calculator.py").exists()
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"change_summary", "rollback_patch", "patch_diff"} <= artifact_ids
    patch_diff = next(artifact for artifact in result.artifacts if artifact.id == "patch_diff")
    assert "src/calculator.py" in patch_diff.content["diff"]
    assert result.metadata["fallback"] == "mutation_observation_synthesis"


def test_code_worker_rejects_completed_mutation_without_write_observation(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("value = 'old'\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/checkout.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "Patch applied.",
                    "artifacts": [
                        {"id": "change_summary", "content": "claimed change"},
                        {"id": "rollback_patch", "content": "claimed rollback"},
                    ],
                }
            },
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="code_worker",
        templates=[
            WorkerInstanceTemplate(
                name="code_agent",
                role="mutate",
                system_prompt="mutate",
                allowed_tools=("read_file", "replace_in_file"),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="code_worker",
        expected_outputs=["change_summary", "rollback_patch"],
        permissions={
            "read_files": True,
            "write_files": True,
            "run_commands": False,
            "web_research": False,
            "write_paths": ["src/checkout.py"],
        },
        max_tool_calls=1,
        max_model_calls=2,
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["issue_code"] == "mutation_completed_without_write"
    assert result.metadata["retryable"] is True
    assert "old" in (tmp_path / "src" / "checkout.py").read_text(encoding="utf-8")


def test_verify_worker_synthesizes_failed_verification_after_model_budget_exhaustion(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "tool_calls": [
                    {
                        "tool_name": "run_readonly_command",
                        "arguments": {"command": ["python", "-m", "pytest", "missing_test.py"]},
                    }
                ]
            }
        ]
    )
    runner = AgenticWorkerGroupRunner(
        worker_type="verify_worker",
        templates=[
            WorkerInstanceTemplate(
                name="verification_runner",
                role="verify",
                system_prompt="verify",
                allowed_tools=("run_readonly_command",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    task = _task(
        worker_type="verify_worker",
        expected_outputs=["test_results", "verification_results"],
        permissions={
            "read_files": False,
            "write_files": False,
            "run_commands": True,
            "web_research": False,
        },
        max_tool_calls=1,
        max_model_calls=1,
    )

    result = runner.run(task)

    assert result.status == "failed"
    assert result.metadata["fallback"] == "verification_observation_synthesis"
    artifact_ids = {artifact.id for artifact in result.artifacts}
    assert {"test_results", "verification_results"} <= artifact_ids


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
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
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


def test_agentic_worker_group_skips_later_instances_when_outputs_are_done(tmp_path: Path) -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "completed",
                    "summary": "done early",
                    "artifacts": [{"id": "final_artifact", "content": "complete"}],
                }
            }
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(name="repo_locator", role="locate"),
            WorkerInstanceTemplate(name="repo_reader", role="read"),
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )

    result = group.run(_task(worker_type="repo_worker", expected_outputs=["final_artifact"], max_model_calls=2))

    assert result.status == "completed"
    assert len(client.prompts) == 1
    assert len(result.metadata["worker_group_results"]) == 1
    assert result.metadata["skipped_worker_instances"] == ["repo_reader"]


def test_agentic_worker_group_records_model_and_tool_matrix_rows(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    client = QueueClient(
        [
            {"tool_calls": [{"tool_name": "read_file", "arguments": {"path": "src/app.py"}}]},
            {
                "final_result": {
                    "status": "completed",
                    "summary": "read app",
                    "artifacts": [{"id": "final_artifact", "content": "value = 1"}],
                }
            },
        ]
    )
    group = AgenticWorkerGroupRunner(
        worker_type="repo_worker",
        templates=[
            WorkerInstanceTemplate(
                name="repo_reader",
                role="read",
                allowed_tools=("read_file",),
            )
        ],
        controller=WorkerLLMController(client),
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path)),
    )
    trace = RuntimeMatrixLogger()

    result = group.run(_task(worker_type="repo_worker", max_tool_calls=1, max_model_calls=2), trace=trace)
    events = [row["event"] for row in trace.snapshot()["rows"]]

    assert result.status == "completed"
    assert "worker_group_started" in events
    assert "worker_instance_started" in events
    assert events.count("worker_model_call_started") == 2
    assert "worker_tool_call_started" in events
    assert "worker_tool_call_completed" in events
    assert "worker_group_completed" in events


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


def test_worker_llm_controller_normalizes_type_and_detail_issue_fields() -> None:
    client = QueueClient(
        [
            {
                "final_result": {
                    "status": "needs_replan",
                    "summary": "Need more source evidence.",
                    "issues": [
                        {
                            "type": "plan_failure",
                            "code": "missing_source_contents",
                            "detail": "Cannot analyze root_cause without file contents.",
                            "artifact": "candidate_paths",
                        }
                    ],
                }
            }
        ]
    )

    decision = WorkerLLMController(client).decide(stage="research_worker_context_synthesizer", prompt="{}")

    assert decision.final_result is not None
    issue = decision.final_result.issues[0]
    assert issue.issue_type == "plan_failure"
    assert issue.code == "missing_source_contents"
    assert issue.message == "Cannot analyze root_cause without file contents."
    assert issue.metadata["artifact"] == "candidate_paths"


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
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
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


def test_tool_observation_without_final_model_budget_is_kernel_budget_issue(tmp_path: Path) -> None:
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

    assert result.status == "budget_exceeded"
    assert result.metadata["issues"][0]["issue_type"] == "instance_failure"
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


def test_agentic_web_search_without_provider_is_kernel_blocked(tmp_path: Path) -> None:
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
        toolbox=WorkerToolbox(WorkerToolConfig(root_path=tmp_path, web_search_provider="disabled")),
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

    assert result.status == "blocked"
    assert result.metadata["issues"][0]["issue_type"] == "kernel_failure"
    assert result.metadata["issues"][0]["code"] == "tool_unavailable"
