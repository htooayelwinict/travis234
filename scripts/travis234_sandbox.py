#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


TRAVIS234_ROOT = Path(__file__).resolve().parents[1]
if str(TRAVIS234_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAVIS234_ROOT))


def _maybe_reexec_project_python() -> None:
    if os.getenv("TRAVIS234_NO_VENV_REEXEC") == "1":
        return
    venv_python = TRAVIS234_ROOT.parent / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_maybe_reexec_project_python()

from travis.sandbox_launcher import resolve_sandbox_config, run_sandbox  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Travis234 inside a whole-app Docker sandbox")
    parser.add_argument("--cwd", default=".", help="Host workspace directory to mount as /workspace")
    parser.add_argument("--image", default=None, help="Docker image to use; defaults to TRAVIS234_SANDBOX_IMAGE or the release image")
    parser.add_argument("--agent-home", default=None, help="Host directory for isolated Travis234 sandbox state")
    parser.add_argument(
        "--agents-file",
        action="append",
        default=[],
        help="Copy an explicit AGENTS.md/CLAUDE.md-style instruction file into the sandbox",
    )
    parser.add_argument(
        "--with-skills",
        action="append",
        default=[],
        help="Copy an explicit skill file or directory into sandbox $HOME/agent/skills",
    )
    parser.add_argument("--no-user-skills", action="store_true", help="Do not copy host ~/.travis234/agent/skills into the sandbox")
    parser.add_argument("--no-network", action="store_true", help="Disable container network access")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the Travis234 Docker image before running")
    parser.add_argument("--dry-run", action="store_true", help="Print docker command without running it")
    args, extra = parser.parse_known_args(argv)
    config = resolve_sandbox_config(
        workspace=args.cwd,
        app_root=TRAVIS234_ROOT,
        agent_home=args.agent_home,
        image=args.image,
        extra_args=extra,
        network=not args.no_network,
        base_dir=os.environ.get("INIT_CWD"),
        agents_files=args.agents_file,
        skills_paths=args.with_skills,
        import_user_skills=not args.no_user_skills,
    )
    try:
        return run_sandbox(config, dry_run=args.dry_run, rebuild=args.rebuild)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
