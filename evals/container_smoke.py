from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


CONSOLE_ENTRYPOINT = "travis234"


def run_container_smoke(image: str) -> None:
    _run(["docker", "run", "--rm", "--entrypoint", "id", image, "-un"], expected="travis")
    _run(["docker", "run", "--rm", "--entrypoint", CONSOLE_ENTRYPOINT, image, "--help"])
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
        mounted = f"{workspace}:/workspace"
        installed_modes = _run_mounted_python(
            image,
            Path(__file__).with_name("installed_modes_smoke.py"),
            mounted,
            ["--workspace", "/workspace/installed-modes"],
        )
        if set(json.loads(installed_modes).values()) != {"installed smoke"}:
            raise RuntimeError("container print/JSON/RPC smoke results differ")
        untrusted = _run_mounted_python(
            image,
            Path(__file__).with_name("untrusted_repository_smoke.py"),
            mounted,
            ["--workspace", "/workspace/untrusted"],
        )
        untrusted_result = json.loads(untrusted)
        if (
            untrusted_result.get("extension_executed") is not False
            or untrusted_result.get("global_extension_loaded") is not True
            or untrusted_result.get("session_completed") is not True
        ):
            raise RuntimeError(f"container trust smoke failed: {untrusted_result}")
        qualification = _run_mounted_python(
            image,
            Path(__file__).with_name("container_qualification.py"),
            mounted,
            ["--workspace", "/workspace/qualification", "--require-container"],
        )
        if json.loads(qualification).get("passed") is not True:
            raise RuntimeError("container runtime qualification did not pass")
        _run(
            [
                "docker", "run", "--rm", "--entrypoint", "sh",
                "-v", mounted, "-w", "/workspace", image,
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
        "from travis.app import CodingApp;"
        "from travis.coding_agent.auth_storage import AuthStorage;"
        "from travis.coding_agent.model_registry import ModelRegistry;"
        "from travis.tui.interactive_mode import InteractiveMode;"
        "from travis.tui.terminal import FakeTerminal;"
        "r=ModelRegistry.in_memory(AuthStorage.in_memory());"
        "r.runtime.clear_providers();"
        "r.runtime.set_provider(create_faux_provider(lambda m,c:text_response_events(m,'smoke ok')));"
        "a=CodingApp(cwd='/workspace',model=faux_model(),terminal=FakeTerminal(),enable_tui=True,"
        "model_registry=r,summarizer=lambda p:'## Summary\\nsmoke');"
        "i=iter(['smoke task','/compact','/exit']);"
        "raise SystemExit(InteractiveMode(a,input_fn=lambda p:next(i)).run())"
    )
    _run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", script])


def _run_mounted_python(
    image: str,
    script: Path,
    workspace_mount: str,
    arguments: list[str],
) -> str:
    if not script.is_file():
        raise RuntimeError(f"container smoke helper is missing: {script}")
    container_script = f"/tmp/{script.name}"
    return _run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "-v",
            workspace_mount,
            "-v",
            f"{script.resolve()}:{container_script}:ro",
            image,
            container_script,
            *arguments,
        ]
    )


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
