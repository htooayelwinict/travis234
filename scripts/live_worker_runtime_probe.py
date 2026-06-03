"""Run a live decompressor -> planner -> worker probe against a mock repo."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime
from app.worker_kernel.runtime import WorkerKernelRuntime


DEFAULT_PROMPT = (
    "In the repo rooted at live_worker_mock_repo, inspect the payment retry bug. "
    "Read the code and tests, fix the issue so retries for the same order reuse the same idempotency key, "
    "keep the change minimal, and verify with the repo tests. Summarize what changed and any remaining risk."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="live_worker_mock_repo")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--worker-model", default="qwen/qwen3.7-max")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--out-dir", default="plan")
    parser.add_argument("--max-parallel-instances", default="3")
    parser.add_argument("--worker-timeout", default="90")
    parser.add_argument("--tool-timeout", default="20")
    parser.add_argument("--max-tokens", default="2400")
    args = parser.parse_args()

    workspace = Path.cwd()
    repo_path = (workspace / args.repo).resolve()
    out_dir = (workspace / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"live-worker-{_slug(args.worker_model)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    _ensure_mock_repo(repo_path)
    _configure_worker_env(args)

    baseline = _run_pytest(repo_path, "before")
    decompressor = DecompressorRuntime.from_env(args.dotenv)
    planner = PlannerRuntime.from_env(args.dotenv, fallback_on_error=False)
    worker = WorkerKernelRuntime.from_env(
        args.dotenv,
        planner_runtime=planner,
        root_path=str(repo_path),
        fallback_to_stub_workers=False,
    )

    envelope = decompressor.run(args.prompt)
    plan = planner.run(envelope)
    result = worker.run(plan, envelope=envelope)
    after = _run_pytest(repo_path, "after")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "prompt": args.prompt,
        "repo_path": str(repo_path),
        "worker_env": _worker_env_snapshot(),
        "baseline_pytest": baseline,
        "envelope": envelope.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "after_pytest": after,
        "git_status": _run(["git", "status", "--short"], cwd=repo_path),
        "final_files": {
            "src/checkout.py": (repo_path / "src" / "checkout.py").read_text(encoding="utf-8"),
            "tests/test_checkout.py": (repo_path / "tests" / "test_checkout.py").read_text(encoding="utf-8"),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"OUTPUT_PATH={out_path}")
    print(
        json.dumps(
            {
                "baseline_returncode": baseline["returncode"],
                "result_status": result.status,
                "after_returncode": after["returncode"],
                "step_count": len(plan.steps),
            },
            sort_keys=True,
        )
    )
    return 0


def _configure_worker_env(args: argparse.Namespace) -> None:
    os.environ["WORKER_LLM_ENABLED"] = "true"
    os.environ["WORKER_LLM_MODEL"] = args.worker_model
    os.environ["WORKER_LLM_PROVIDER_SORT"] = "latency"
    os.environ["WORKER_LLM_TIMEOUT_SECONDS"] = args.worker_timeout
    os.environ["WORKER_LLM_TEMPERATURE"] = "0"
    os.environ["WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["WORKER_LLM_MAX_TOKENS"] = args.max_tokens
    os.environ["WORKER_MAX_PARALLEL_INSTANCES"] = args.max_parallel_instances
    os.environ["WORKER_TOOL_TIMEOUT_SECONDS"] = args.tool_timeout
    os.environ["WORKER_MAX_FILE_BYTES"] = "200000"


def _worker_env_snapshot() -> dict[str, str | None]:
    keys = [
        "WORKER_LLM_ENABLED",
        "WORKER_LLM_MODEL",
        "WORKER_LLM_PROVIDER_SORT",
        "WORKER_LLM_TIMEOUT_SECONDS",
        "WORKER_LLM_MAX_TOKENS",
        "WORKER_MAX_PARALLEL_INSTANCES",
        "WORKER_TOOL_TIMEOUT_SECONDS",
    ]
    return {key: os.environ.get(key) for key in keys}


def _ensure_mock_repo(repo_path: Path) -> None:
    (repo_path / "src").mkdir(parents=True, exist_ok=True)
    (repo_path / "tests").mkdir(parents=True, exist_ok=True)
    _remove_generated_runtime_dirs(repo_path)
    (repo_path / "README.md").write_text(
        "# Live Worker Mock Repo\n\nThis mock repo simulates a payment retry idempotency bug.\n",
        encoding="utf-8",
    )
    (repo_path / "src" / "__init__.py").write_text(
        '"""Mock payment package for live worker testing."""\n',
        encoding="utf-8",
    )
    (repo_path / "src" / "checkout.py").write_text(
        '''"""Simple payment retry helpers for worker-runtime live testing."""

from __future__ import annotations


def build_charge_headers(order_id: str, retry_count: int) -> dict[str, str]:
    """Return headers for a payment charge request.

    Current behavior is intentionally wrong for retries: the idempotency key
    changes when `retry_count` changes, which makes retries look like fresh
    charges to an upstream processor.
    """

    return {
        "Idempotency-Key": f"charge:{order_id}:retry:{retry_count}",
        "X-Retry-Count": str(retry_count),
    }
''',
        encoding="utf-8",
    )
    (repo_path / "tests" / "test_checkout.py").write_text(
        '''from src.checkout import build_charge_headers


def test_first_attempt_keeps_retry_count_header() -> None:
    headers = build_charge_headers("order-123", 0)
    assert headers["X-Retry-Count"] == "0"


def test_retries_reuse_same_idempotency_key() -> None:
    first = build_charge_headers("order-123", 0)
    retry = build_charge_headers("order-123", 1)

    assert retry["Idempotency-Key"] == first["Idempotency-Key"]
''',
        encoding="utf-8",
    )
    if not (repo_path / ".git").exists():
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True, text=True)


def _remove_generated_runtime_dirs(repo_path: Path) -> None:
    for relative in ["src/__pycache__", "tests/__pycache__", ".pytest_cache"]:
        path = repo_path / relative
        if path.exists():
            shutil.rmtree(path)


def _run_pytest(repo_path: Path, label: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "label": label,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _run(command: list[str], *, cwd: Path) -> dict[str, object]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


if __name__ == "__main__":
    raise SystemExit(main())
