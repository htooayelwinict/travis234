"""Credential-free runtime qualification used by the release-container smoke."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import UserMessage
from travis.app import CodingApp
from travis.coding_agent.execution_backend import TrustedLocalBackend
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner


_CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TRAVIS_COMPRESSION_API_KEY",
    "TRAVIS234_COMPRESSION_LLM_API_KEY",
    "TRAVIS234_WORKER_LLM_API_KEY",
)


@dataclass(frozen=True)
class ContainerQualification:
    user: str
    home: str
    credential_env_absent: bool
    manual_compaction: bool
    automatic_compaction: bool
    managed_process_reaped: bool

    @property
    def passed(self) -> bool:
        return (
            self.credential_env_absent
            and self.manual_compaction
            and self.automatic_compaction
            and self.managed_process_reaped
        )


def run_container_qualification(
    workspace: str | Path,
    *,
    require_container: bool,
) -> ContainerQualification:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manual, automatic = _exercise_compaction(root)
    result = ContainerQualification(
        user=getpass.getuser(),
        home=str(Path.home()),
        credential_env_absent=not any(name in os.environ for name in _CREDENTIAL_NAMES),
        manual_compaction=manual,
        automatic_compaction=automatic,
        managed_process_reaped=_exercise_process_cleanup(root),
    )
    if require_container and (result.user != "travis" or result.home != "/travis-home"):
        raise RuntimeError(
            f"release container identity mismatch: user={result.user!r}, home={result.home!r}"
        )
    if not result.passed:
        raise RuntimeError(f"release container qualification failed: {asdict(result)}")
    return result


def _exercise_compaction(root: Path) -> tuple[bool, bool]:
    def make_app(name: str) -> CodingApp:
        workspace = root / name
        workspace.mkdir(parents=True, exist_ok=True)
        registry = ModelRegistry.in_memory()
        registry.runtime.clear_providers()
        registry.runtime.set_provider(
            create_faux_provider(
                lambda model, _context: text_response_events(model, "qualification ok")
            )
        )
        model = faux_model()
        model.context_window = 32_000
        model.max_tokens = 4_096
        return CodingApp(
            cwd=str(workspace),
            model=model,
            context_length=model.context_window,
            summarizer=lambda _prompt: "## Goal\nPreserve the qualification handoff.",
            enable_tui=False,
            project_trust_override=False,
            model_registry=registry,
        )

    def seed(app: CodingApp) -> int:
        messages = [
            UserMessage(content=f"qualification history {index} " + ("x" * 2_000))
            for index in range(72)
        ]
        app.session.agent.state.messages.extend(messages)
        return len(messages)

    manual_app = make_app("manual-compaction")
    try:
        before = seed(manual_app)
        manual_app.session.compact(focus="container qualification")
        manual = manual_app.compressor.compression_count > 0 and len(manual_app.messages) < before
    finally:
        manual_app.close()

    automatic_app = make_app("automatic-compaction")
    try:
        seed(automatic_app)
        automatic_app.run_turn("finish the qualification")
        automatic = automatic_app.compressor.compression_count > 0
    finally:
        automatic_app.close()
    return manual, automatic


def _exercise_process_cleanup(root: Path) -> bool:
    process_root = root / "managed-process"
    process_root.mkdir(parents=True, exist_ok=True)
    pid_path = process_root / "child.pid"
    service = ProcessSessionService(
        directory=process_root / "state",
        termination_grace_seconds=0.2,
        drain_timeout_seconds=0.2,
    )
    owner = ProcessOwner("container-qualification", str(process_root), "agent")
    request = ProcessLaunchRequest(
        command=f"printf '%s' $$ > {shlex.quote(str(pid_path))}; exec /bin/sleep 30",
        cwd=str(process_root),
        env={"PATH": "/usr/bin:/bin"},
        shell_path="/bin/bash",
    )
    backend = TrustedLocalBackend()
    try:
        service.start(
            owner,
            request,
            lambda launch: create_local_process_transport(launch, backend),
            yield_time_ms=100,
        )
        deadline = time.monotonic() + 2
        while not pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not pid_path.is_file():
            return False
        pid = int(pid_path.read_text(encoding="utf-8"))
    finally:
        service.close()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--require-container", action="store_true")
    args = parser.parse_args(argv)
    result = run_container_qualification(
        args.workspace,
        require_container=args.require_container,
    )
    print(json.dumps({**asdict(result), "passed": result.passed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
