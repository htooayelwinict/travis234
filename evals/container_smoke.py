from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def run_container_smoke(image: str) -> None:
    _run(["docker", "run", "--rm", "--entrypoint", "id", image, "-un"], expected="travis")
    _run(["docker", "run", "--rm", "--entrypoint", "travis", image, "--help"])
    _run(["docker", "run", "--rm", "--entrypoint", "node", image, "--version"])
    _run(["docker", "run", "--rm", "--entrypoint", "npm", image, "--version"])
    shell_env_script = (
        "import json,shutil;"
        "from travis.coding_agent.tools.bash import get_shell_env;"
        "env=get_shell_env();"
        "print(json.dumps({name:shutil.which(name,path=env['PATH']) for name in "
        "('python','node','npm','npx')}));"
        "import pytest"
    )
    shell_env = json.loads(
        _run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", shell_env_script])
    )
    missing = [name for name, executable in shell_env.items() if executable is None]
    if missing:
        raise RuntimeError(f"coding-agent shell PATH is missing: {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="travis-container-smoke-") as temporary:
        workspace = Path(temporary)
        prepare_npm_workspace(workspace)
        _run(
            [
                "docker", "run", "--rm", "--entrypoint", "sh",
                "-v", f"{workspace}:/workspace", "-w", "/workspace", image,
                "-lc",
                (
                    "npm install --ignore-scripts --no-audit --no-fund is-number; "
                    "status=$?; chmod -R a+rwX /workspace; exit $status"
                ),
            ]
        )
        if not (workspace / "node_modules" / "is-number").is_dir():
            raise RuntimeError("container npm smoke did not create node_modules/is-number")
    script = (
        "from travis.ai.providers.faux import create_faux_provider,faux_model,text_response_events;"
        "from travis.ai.stream import register_api_provider;"
        "from travis.app import CodingApp;"
        "from travis.tui.interactive_mode import InteractiveMode;"
        "from travis.tui.terminal import FakeTerminal;"
        "register_api_provider(create_faux_provider(lambda m,c:text_response_events(m,'smoke ok')));"
        "a=CodingApp(cwd='/workspace',model=faux_model(),terminal=FakeTerminal(),enable_tui=True,"
        "summarizer=lambda p:'## Summary\\nsmoke');"
        "i=iter(['smoke task','/compact','/exit']);"
        "raise SystemExit(InteractiveMode(a,input_fn=lambda p:next(i)).run())"
    )
    _run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", script])


def prepare_npm_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o777)
    package_json = workspace / "package.json"
    package_json.write_text(
        json.dumps({"name": "smoke", "version": "1.0.0", "private": True}) + "\n",
        encoding="utf-8",
    )
    package_json.chmod(0o666)


def _run(command: list[str], expected: str | None = None) -> str:
    completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(f"container smoke failed ({completed.returncode}): {completed.stderr[-2000:]}")
    output = completed.stdout.strip()
    if expected is not None and output != expected:
        raise RuntimeError(f"expected {expected!r}, received {output!r}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    run_container_smoke(args.image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
