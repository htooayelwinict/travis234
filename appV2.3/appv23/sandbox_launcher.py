"""Docker launcher helpers for whole-app appv23 sandboxing.

The launcher is intentionally outside the model/tool runtime. It constrains what
host paths the entire app process can see by running an appv23-only image and
mounting only the selected workspace plus an isolated app home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


DEFAULT_IMAGE = "appv23:local"
CONTAINER_WORKSPACE = "/workspace"
CONTAINER_AGENT_HOME = "/agent-home"

_PROVIDER_ENV_PREFIXES = (
    "OPENAI_",
    "OPENROUTER_",
    "ANTHROPIC_",
    "GEMINI_",
    "GOOGLE_",
    "APPV2_WORKER_LLM_",
)

_STRIP_FLAGS_WITH_VALUE = {"--cwd", "--dotenv"}
_STRIP_FLAGS_EXACT = set[str]()


@dataclass(frozen=True)
class SandboxConfig:
    workspace: Path
    app_root: Path
    agent_home: Path
    image: str = DEFAULT_IMAGE
    extra_args: list[str] = field(default_factory=list)
    network: bool = True
    name: str | None = None

    container_workspace: str = CONTAINER_WORKSPACE
    container_agent_home: str = CONTAINER_AGENT_HOME


def resolve_host_path(path: str | Path, *, base_dir: str | Path | None = None) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute() and base_dir:
        resolved = Path(base_dir).expanduser() / resolved
    return resolved.resolve()


def resolve_sandbox_config(
    *,
    workspace: str | Path,
    app_root: str | Path,
    agent_home: str | Path | None = None,
    image: str | None = None,
    extra_args: Sequence[str] | None = None,
    network: bool = True,
    name: str | None = None,
    base_dir: str | Path | None = None,
) -> SandboxConfig:
    resolved_workspace = resolve_host_path(workspace, base_dir=base_dir)
    resolved_app_root = Path(app_root).expanduser().resolve()
    resolved_agent_home = Path(
        agent_home
        or os.environ.get("APPV23_SANDBOX_HOME")
        or (Path.home() / ".appv23" / "sandbox-home")
    ).expanduser().resolve()
    return SandboxConfig(
        workspace=resolved_workspace,
        app_root=resolved_app_root,
        agent_home=resolved_agent_home,
        image=image or os.environ.get("APPV23_SANDBOX_IMAGE") or DEFAULT_IMAGE,
        extra_args=list(extra_args or []),
        network=network,
        name=name,
    )


def sanitize_app_args(args: Sequence[str]) -> list[str]:
    """Remove host-only app args before forwarding into the container.

    Sandbox mode owns the workspace mount and does not forward dotenv files. Users
    configure credentials through /login inside the isolated app home.
    """
    sanitized: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in _STRIP_FLAGS_EXACT:
            continue
        if arg in _STRIP_FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if any(arg.startswith(f"{flag}=") for flag in _STRIP_FLAGS_WITH_VALUE):
            continue
        sanitized.append(arg)
    return sanitized


def build_image_command(config: SandboxConfig) -> list[str]:
    return [
        "docker",
        "build",
        "-f",
        str(config.app_root / "Dockerfile.appv23"),
        "-t",
        config.image,
        str(config.app_root),
    ]


def build_docker_command(config: SandboxConfig) -> list[str]:
    agent_home = config.agent_home.resolve()
    app_args = sanitize_app_args(config.extra_args)
    name = config.name or f"appv23-sandbox-{os.getpid()}"
    command = [
        "docker",
        "run",
        "--rm",
        "-it",
        "--name",
        name,
        "--workdir",
        config.container_workspace,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "512",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v",
        f"{config.workspace.resolve()}:{config.container_workspace}:rw",
        "-v",
        f"{agent_home}:{config.container_agent_home}:rw",
        "-e",
        f"HOME={config.container_agent_home}",
        "-e",
        f"PI_CODING_AGENT_DIR={config.container_agent_home}/agent",
        "-e",
        "APPV23_SANDBOX=1",
        "-e",
        "APPV23_NO_VENV_REEXEC=1",
        "-e",
        "PYTHONUNBUFFERED=1",
    ]
    if not config.network:
        command.append("--network=none")
    command.extend([config.image, "--cwd", config.container_workspace, *app_args])
    return command


def assert_safe_environment(config: SandboxConfig) -> None:
    if not config.workspace.exists():
        raise ValueError(f"workspace does not exist: {config.workspace}")
    if not config.workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {config.workspace}")
    if not config.app_root.exists():
        raise ValueError(f"app root does not exist: {config.app_root}")
    if not config.app_root.is_dir():
        raise ValueError(f"app root is not a directory: {config.app_root}")
    config.agent_home.mkdir(parents=True, exist_ok=True)


def ensure_image(config: SandboxConfig, *, rebuild: bool = False, dry_run: bool = False) -> int:
    if not rebuild:
        inspect = subprocess.run(
            ["docker", "image", "inspect", config.image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if inspect.returncode == 0:
            return 0
    command = build_image_command(config)
    if dry_run:
        print(" ".join(command))
        return 0
    return subprocess.call(command)


def provider_env_names_present() -> list[str]:
    return sorted(
        key for key in os.environ if any(key.startswith(prefix) for prefix in _PROVIDER_ENV_PREFIXES)
    )


def run_sandbox(config: SandboxConfig, *, dry_run: bool = False, rebuild: bool = False) -> int:
    assert_safe_environment(config)
    command = build_docker_command(config)
    if dry_run:
        ensure_image(config, rebuild=rebuild, dry_run=True)
        print(" ".join(command))
        return 0
    if provider_env_names_present():
        print(
            "appv23 sandbox: provider environment variables are intentionally not forwarded; "
            "use /login inside the sandbox.",
            file=sys.stderr,
        )
    build_exit = ensure_image(config, rebuild=rebuild)
    if build_exit != 0:
        return build_exit
    try:
        return subprocess.call(command)
    except FileNotFoundError:
        print("Error: docker command not found. Install Docker or run appv23 without sandbox mode.", file=sys.stderr)
        return 127
