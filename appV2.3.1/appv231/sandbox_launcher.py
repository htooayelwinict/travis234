"""Docker launcher helpers for whole-app appv231 sandboxing.

The launcher is intentionally outside the model/tool runtime. It constrains what
host paths the entire app process can see by running an appv231-only image and
mounting only the selected workspace plus an isolated app home.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence


DEFAULT_IMAGE = "appv231:local"
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
_IMPORTED_AGENTS_MARKER = "<!-- appv231-sandbox-imported-agents -->"
_SKIP_IMPORT_NAMES = {
    ".DS_Store",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class SandboxConfig:
    workspace: Path
    app_root: Path
    agent_home: Path
    image: str = DEFAULT_IMAGE
    extra_args: list[str] = field(default_factory=list)
    network: bool = True
    name: str | None = None
    agents_files: list[Path] = field(default_factory=list)
    skills_paths: list[Path] = field(default_factory=list)
    import_user_skills: bool = True

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
    agents_files: Sequence[str | Path] | None = None,
    skills_paths: Sequence[str | Path] | None = None,
    import_user_skills: bool = True,
) -> SandboxConfig:
    resolved_workspace = resolve_host_path(workspace, base_dir=base_dir)
    resolved_app_root = Path(app_root).expanduser().resolve()
    resolved_agent_home = Path(
        agent_home
        or os.environ.get("APPV231_SANDBOX_HOME")
        or (Path.home() / ".appv231" / "sandbox-home")
    ).expanduser().resolve()
    return SandboxConfig(
        workspace=resolved_workspace,
        app_root=resolved_app_root,
        agent_home=resolved_agent_home,
        image=image or os.environ.get("APPV231_SANDBOX_IMAGE") or DEFAULT_IMAGE,
        extra_args=list(extra_args or []),
        network=network,
        name=name,
        agents_files=[resolve_host_path(path, base_dir=base_dir) for path in agents_files or []],
        skills_paths=[resolve_host_path(path, base_dir=base_dir) for path in skills_paths or []],
        import_user_skills=import_user_skills,
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
        str(config.app_root / "Dockerfile.appv231"),
        "-t",
        config.image,
        str(config.app_root),
    ]


def build_docker_command(config: SandboxConfig) -> list[str]:
    agent_home = config.agent_home.resolve()
    app_args = sanitize_app_args(config.extra_args)
    name = config.name or f"appv231-sandbox-{os.getpid()}"
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
        "APPV231_SANDBOX=1",
        "-e",
        "APPV231_NO_VENV_REEXEC=1",
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


def prepare_sandbox_imports(config: SandboxConfig) -> None:
    _prepare_agents_files(config)
    _prepare_skills(config)


def _prepare_agents_files(config: SandboxConfig) -> None:
    target = config.agent_home / "agent" / "AGENTS.md"
    sources = _sandbox_agents_sources(config)
    if not sources:
        if target.exists() and target.read_text(encoding="utf-8", errors="ignore").startswith(_IMPORTED_AGENTS_MARKER):
            target.unlink()
        return

    parts = [
        _IMPORTED_AGENTS_MARKER,
        "# Imported appv231 sandbox instructions",
        "",
        "These instructions were copied into the sandbox from host ~/.agents/AGENTS.md and explicit --agents-file arguments.",
        "",
    ]
    for source in sources:
        if not source.exists():
            raise ValueError(f"agents file does not exist: {source}")
        if not source.is_file():
            raise ValueError(f"agents file is not a file: {source}")
        parts.extend(
            [
                f"## Source: {source}",
                "",
                source.read_text(encoding="utf-8"),
                "",
            ]
        )

    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_text("\n".join(parts), encoding="utf-8")
    os.chmod(target, 0o600)


def _sandbox_agents_sources(config: SandboxConfig) -> list[Path]:
    sources: list[Path] = []
    user_agents_file = Path.home() / ".agents" / "AGENTS.md"
    if user_agents_file.exists():
        sources.append(user_agents_file.resolve())
    sources.extend(path.resolve() for path in config.agents_files)
    deduped: list[Path] = []
    seen: set[str] = set()
    for source in sources:
        key = str(source)
        if key not in seen:
            deduped.append(source)
            seen.add(key)
    return deduped


def _prepare_skills(config: SandboxConfig) -> None:
    sources = _sandbox_skill_sources(config)
    if not sources:
        return

    target_root = config.agent_home / ".agents" / "skills"
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, mode=0o700, exist_ok=True)

    imported: list[dict[str, str]] = []
    for source in sources:
        if not source.exists():
            raise ValueError(f"skills path does not exist: {source}")
        if source.is_file():
            if source.suffix != ".md":
                raise ValueError(f"skills file must be markdown: {source}")
            target = target_root / source.name
            _copy_file_safe(source, target)
            imported.append({"source": str(source), "target": str(target)})
            continue
        if not source.is_dir():
            raise ValueError(f"skills path is not a file or directory: {source}")
        if (source / "SKILL.md").is_file():
            target = target_root / source.name
            _copy_tree_safe(source, target)
            imported.append({"source": str(source), "target": str(target)})
            continue
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            if _should_skip_import(child):
                continue
            target = target_root / child.name
            if child.is_dir():
                _copy_tree_safe(child, target)
                imported.append({"source": str(child), "target": str(target)})
            elif child.is_file() and child.suffix == ".md":
                _copy_file_safe(child, target)
                imported.append({"source": str(child), "target": str(target)})

    manifest = {
        "source": "appv231-sandbox",
        "skills": imported,
    }
    (target_root / ".appv231-import-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _sandbox_skill_sources(config: SandboxConfig) -> list[Path]:
    sources: list[Path] = []
    for bundled_skills in (config.app_root / "skills", config.app_root.parent / "skills"):
        if bundled_skills.exists():
            sources.append(bundled_skills.resolve())
    if config.import_user_skills:
        user_skills = Path.home() / ".agents" / "skills"
        if user_skills.exists():
            sources.append(user_skills.resolve())
    sources.extend(path.resolve() for path in config.skills_paths)
    deduped: list[Path] = []
    seen: set[str] = set()
    for source in sources:
        key = str(source)
        if key not in seen:
            deduped.append(source)
            seen.add(key)
    return deduped


def _copy_tree_safe(source: Path, target: Path) -> None:
    if _should_skip_import(source):
        return
    if source.is_symlink():
        return
    if source.is_file():
        _copy_file_safe(source, target)
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        if _should_skip_import(child):
            continue
        child_target = target / child.name
        if child.is_dir():
            _copy_tree_safe(child, child_target)
        elif child.is_file():
            _copy_file_safe(child, child_target)


def _copy_file_safe(source: Path, target: Path) -> None:
    if _should_skip_import(source) or source.is_symlink():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _should_skip_import(path: Path) -> bool:
    name = path.name
    return name in _SKIP_IMPORT_NAMES or name.startswith(".env")


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
    prepare_sandbox_imports(config)
    if provider_env_names_present():
        print(
            "appv231 sandbox: provider environment variables are intentionally not forwarded; "
            "use /login inside the sandbox.",
            file=sys.stderr,
        )
    build_exit = ensure_image(config, rebuild=rebuild)
    if build_exit != 0:
        return build_exit
    try:
        return subprocess.call(command)
    except FileNotFoundError:
        print("Error: docker command not found. Install Docker or run appv231 without sandbox mode.", file=sys.stderr)
        return 127
