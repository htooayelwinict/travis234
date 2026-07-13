# Travis234 Rebrand and Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every runtime-facing legacy name with the Travis234/`travis` naming contract and produce installable Python, npm, and container entry points with correct persistent paths.

**Architecture:** Move the authoritative runtime to a focused repository root, keep `travis` as the import package, and expose one `travis234` product command. Use Python distribution metadata and package resources directly; use one npm launcher as the owner of Docker, AGENTS, skill, and sandbox-home setup.

**Tech Stack:** Python 3.13, Setuptools, pytest, Node.js test runner, npm, Docker CLI, GitHub Actions.

## Global Constraints

- Repository/product/distribution/image/config root is `travis234`; Python import package and container user are `travis`.
- The only command is `travis234`; the only application environment prefix is `TRAVIS234_`.
- Persistent state is `~/.travis234/agent`; sessions are under its `sessions/` directory.
- The sandbox uses `/travis-home`, with agent state at `/travis-home/agent`.
- No compatibility command, import, environment variable, path fallback, or automatic state migration is retained.
- Pi/Hermes product labels and porting comments are forbidden outside `LICENSE`, `NOTICE.md`, and historical `docs/superpowers` records.
- `.env` and credentials must remain ignored, unprinted, unpackaged, and unmounted.
- The agent-loop and compaction algorithms are red zones; this plan permits import/name movement only.
- Preserve version `2.3.1`; rebranding is not an unrequested semantic-version bump.

---

### Task 1: Encode the hard-cutover brand contract

**Files:**
- Create: `tests/test_brand_contract.py`
- Create: `tests/test_distribution_contract.py`
- Modify later in this task: `.gitignore`

**Interfaces:**
- Consumes: repository root from `Path(__file__).parents[1]`.
- Produces: `FORBIDDEN_RUNTIME_PATTERNS`, `ALLOWED_ATTRIBUTION_FILES`, and path/metadata assertions used as the mechanical rename acceptance gate.

- [ ] **Step 1: Write the failing repository contract**

```python
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
ALLOWED_ATTRIBUTION_FILES = {ROOT / "LICENSE", ROOT / "NOTICE.md"}
SKIPPED_TREES = {ROOT / ".git", ROOT / ".worktrees", ROOT / "docs" / "superpowers"}
FORBIDDEN_RUNTIME_PATTERNS = (
    re.compile(r"appv(?:2|21|22|23|231)", re.IGNORECASE),
    re.compile(r"\bpi(?:-style)?\b", re.IGNORECASE),
    re.compile(r"\bhermes(?:-style| agent)?\b", re.IGNORECASE),
    re.compile(r"(?:^|/)\.pi(?:/|$)", re.IGNORECASE),
)


def _runtime_text_files() -> list[Path]:
    suffixes = {".py", ".js", ".json", ".md", ".toml", ".yml", ".yaml"}
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path in ALLOWED_ATTRIBUTION_FILES:
            continue
        if any(tree == path or tree in path.parents for tree in SKIPPED_TREES):
            continue
        files.append(path)
    return files


def test_focused_repository_layout() -> None:
    assert (ROOT / "travis" / "__init__.py").is_file()
    assert not (ROOT / "appV2.3.1").exists()
    assert not (ROOT / "appv231").exists()


def test_runtime_text_has_no_former_product_labels() -> None:
    failures: list[str] = []
    for path in _runtime_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_RUNTIME_PATTERNS:
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert failures == []


def test_only_travis234_state_contract_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "~/.travis234/agent/AGENTS.md" in readme
    assert "~/.travis234/agent/skills/" in readme
    assert "~/.travis234/agent/sessions/" in readme
    assert "/travis-home/agent/sessions/" in readme
```

- [ ] **Step 2: Write the failing distribution contract**

```python
from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_python_distribution_names_only_travis234() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "travis234"
    assert project["scripts"] == {"travis234": "travis.cli:main"}


def test_npm_distribution_names_only_travis234() -> None:
    import json

    package = json.loads((ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8"))
    assert package["name"] == "@htooayelwinict/travis234"
    assert package["bin"] == {"travis234": "bin/travis234.js"}
```

- [ ] **Step 3: Run the tests to prove the old layout is red**

Run: `.venv/bin/python -m pytest tests/test_brand_contract.py tests/test_distribution_contract.py -q`

Expected: FAIL because `tests/`, root `pyproject.toml`, `travis/`, and the Travis234 package metadata do not exist yet.

- [ ] **Step 4: Protect build and live-test artifacts**

Add these exact lines to `.gitignore` if they are not already present:

```gitignore
.travis234/
artifacts/
*.egg-info/
```

- [ ] **Step 5: Commit the red contract**

```bash
git add .gitignore tests/test_brand_contract.py tests/test_distribution_contract.py
git commit -m "test: define Travis234 hard-cutover contract"
```

### Task 2: Move the focused source tree and rename imports

**Files:**
- Move: `appV2.3.1/appv231/` to `travis/`
- Move: `appV2.3.1/tests/` to `tests/`
- Move: `appV2.3.1/evals/` to `evals/`
- Move: `appV2.3.1/scripts/` to `scripts/`
- Move: `appV2.3.1/pyproject.toml` to `pyproject.toml`
- Move: `appV2.3.1/package.json` to `package.json`
- Move: `appV2.3.1/Dockerfile.appv231` to `Dockerfile`
- Move: `appV2.3.1/{README.md,LICENSE,NOTICE.md,.dockerignore}` to repository root
- Move: `travis/ai/providers/appv2_env.py` to `travis/ai/providers/travis_env.py`
- Move: `scripts/appv231_tui.py` to `scripts/travis234_tui.py`
- Move: `scripts/appv231_sandbox.py` to `scripts/travis234_sandbox.py`
- Rename tests containing former product/version names to behavior-owner names.

**Interfaces:**
- Consumes: the contract tests from Task 1.
- Produces: import root `travis`, console target `travis.cli:main`, and root-focused source/test layout.

- [ ] **Step 1: Move tracked paths without changing behavior**

Run these mechanical moves:

```bash
git mv appV2.3.1/appv231 travis
git mv appV2.3.1/tests tests-baseline
git mv appV2.3.1/evals evals
git mv appV2.3.1/scripts scripts
git mv appV2.3.1/pyproject.toml pyproject.toml
git mv appV2.3.1/package.json package.json
git mv appV2.3.1/Dockerfile.appv231 Dockerfile
git mv appV2.3.1/README.md README.md
git mv appV2.3.1/LICENSE LICENSE
git mv appV2.3.1/NOTICE.md NOTICE.md
git mv appV2.3.1/.dockerignore .dockerignore
git mv travis/ai/providers/appv2_env.py travis/ai/providers/travis_env.py
git mv scripts/appv231_tui.py scripts/travis234_tui.py
git mv scripts/appv231_sandbox.py scripts/travis234_sandbox.py
```

Move the baseline tests into the already-created `tests/` directory:

```bash
git mv tests-baseline/* tests/
rmdir tests-baseline appV2.3.1
```

- [ ] **Step 2: Apply the deterministic identifier mapping**

For tracked text files outside attribution/design history, apply this mapping in order:

```text
APPV231_       -> TRAVIS234_
APPV2_         -> TRAVIS234_
appv231        -> travis
Appv231        -> Travis
APPV231        -> TRAVIS
appV2.3.1      -> repository root wording or travis234
appv2_env      -> travis_env
```

Imports must end in this exact form:

```python
from travis.cli import main
from travis.ai.providers.travis_env import TravisProvider
```

Do not replace legal attribution in `LICENSE`, `NOTICE.md`, or the committed design/plan history.

- [ ] **Step 3: Rename behavior-owned tests**

```bash
git mv tests/test_ai_appv2_env_provider.py tests/test_ai_travis_env_provider.py
git mv tests/test_no_appv21_coupling.py tests/test_no_legacy_source_coupling.py
git mv tests/test_process_v2_regressions.py tests/test_process_regressions.py
```

- [ ] **Step 4: Reword former product labels by behavior**

Examples of required rewrites:

```python
# Before: "Hermes dual-pass context compaction."
# After:
"""Deterministic pruning followed by model-assisted context compaction."""

# Before: "Pi-style auth storage"
# After:
"""Credential storage for coding-agent services."""
```

Runtime provider identifiers such as `provider="hermes"`, HTML keys such as
`pi-share:*`, temp prefixes such as `pi-bash`, and user directories such as
`.pi/agent` become `travis`/`travis234` equivalents. Wire-format migrations are
covered by explicit serialization tests before old keys are removed.

- [ ] **Step 5: Run import compilation and the brand scan**

Run: `.venv/bin/python -m compileall -q travis tests evals scripts`

Expected: PASS.

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_brand_contract.py -q`

Expected: still FAIL only on metadata/launcher/README paths owned by later tasks; no failure may point into `travis/`, `tests/`, `evals/`, or `scripts/`.

- [ ] **Step 6: Run red-zone characterization tests after import movement**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_compaction.py tests/test_compaction_timing.py -q`

Expected: PASS with the same collected/passed counts as the baseline for these files.

- [ ] **Step 7: Commit the focused layout**

```bash
git add -A
git commit -m "refactor: move runtime to Travis234 layout"
```

### Task 3: Replace source-tree metadata inference with installed metadata

**Files:**
- Modify: `travis/coding_agent/config.py`
- Modify: `travis/coding_agent/__init__.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `pyproject.toml`
- Create: `tests/test_installed_metadata.py`
- Modify: `tests/test_coding_agent.py` by removing superseded compatibility assertions.

**Interfaces:**
- Produces: `APP_NAME: str`, `APP_TITLE: str`, `VERSION: str`, `CONFIG_DIR_NAME: str`, `ENV_AGENT_DIR: str`, `ENV_SESSION_DIR: str`, `package_resource(name: str) -> Traversable`, `existing_context_resource_paths() -> tuple[str, ...]`.
- Removes: package-json search and camelCase config aliases.

- [ ] **Step 1: Write failing source and wheel metadata tests**

```python
from __future__ import annotations

import json
import subprocess
import sys
import venv
from pathlib import Path


def test_source_metadata_is_authoritative() -> None:
    from travis.coding_agent import config

    assert config.APP_NAME == "travis234"
    assert config.APP_TITLE == "Travis234"
    assert config.CONFIG_DIR_NAME == ".travis234"
    assert config.VERSION == "2.3.1"
    assert config.ENV_AGENT_DIR == "TRAVIS234_CODING_AGENT_DIR"
    assert config.ENV_SESSION_DIR == "TRAVIS234_CODING_AGENT_SESSION_DIR"
    assert all(Path(path).exists() for path in config.existing_context_resource_paths())


def test_built_wheel_reports_real_metadata_and_entry_point(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    dist = tmp_path / "dist"
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)], cwd=root, check=True)
    wheel = next(dist.glob("travis234-2.3.1-*.whl"))
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
    probe = subprocess.run(
        [str(python), "-c", "from importlib.metadata import version; from travis.coding_agent.config import VERSION; import json; print(json.dumps([version('travis234'), VERSION]))"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(probe.stdout) == ["2.3.1", "2.3.1"]
```

- [ ] **Step 2: Run the tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_installed_metadata.py -q`

Expected: FAIL because config still searches for `package.json`, reports old metadata, and exposes nonexistent paths.

- [ ] **Step 3: Implement metadata/resource ownership**

Replace source-tree inference with this contract:

```python
from importlib import metadata, resources
from importlib.abc import Traversable
from pathlib import Path

DIST_NAME = "travis234"
APP_NAME = "travis234"
APP_TITLE = "Travis234"
CONFIG_DIR_NAME = ".travis234"
ENV_AGENT_DIR = "TRAVIS234_CODING_AGENT_DIR"
ENV_SESSION_DIR = "TRAVIS234_CODING_AGENT_SESSION_DIR"

try:
    VERSION = metadata.version(DIST_NAME)
except metadata.PackageNotFoundError:
    VERSION = "2.3.1"


def package_resource(name: str) -> Traversable:
    return resources.files("travis").joinpath(name)


def existing_context_resource_paths() -> tuple[str, ...]:
    candidates = (package_resource("resources/README.md"), package_resource("resources/docs"), package_resource("resources/examples"))
    return tuple(str(item) for item in candidates if item.is_file() or item.is_dir())


def get_agent_dir() -> str:
    configured = os.environ.get(ENV_AGENT_DIR)
    return os.path.expanduser(configured) if configured else str(Path.home() / CONFIG_DIR_NAME / "agent")
```

`AgentSession` consumes `existing_context_resource_paths()` and never appends a
nonexistent resource. Remove all camelCase exports and unsupported source-layout
getters from `travis/coding_agent/__init__.py`.

- [ ] **Step 4: Declare exact distribution metadata**

`pyproject.toml` must contain:

```toml
[project]
name = "travis234"
version = "2.3.1"
description = "Terminal coding agent with persistent sessions and bounded tool execution."
readme = "README.md"
license = "MIT"
requires-python = ">=3.13,<3.14"

[project.scripts]
travis234 = "travis.cli:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["travis*"]

[tool.setuptools.package-data]
"travis.coding_agent" = ["export_html_assets/vendor/*.js"]
"travis" = ["resources/**/*"]
```

- [ ] **Step 5: Run source and installed tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_installed_metadata.py tests/test_coding_agent.py -q`

Expected: PASS.

- [ ] **Step 6: Commit metadata repair**

```bash
git add pyproject.toml travis/coding_agent/config.py travis/coding_agent/__init__.py travis/coding_agent/agent_session.py travis/resources tests/test_installed_metadata.py tests/test_coding_agent.py
git commit -m "fix: use installed Travis234 metadata and resources"
```

### Task 4: Create the canonical npm launcher and bundled instructions

**Files:**
- Create: `packages/travis234-cli/bin/travis234.js`
- Create: `packages/travis234-cli/package.json`
- Create: `packages/travis234-cli/README.md`
- Create: `packages/travis234-cli/agents/AGENTS.md`
- Create: `packages/travis234-cli/skills/subagent-delegation/SKILL.md`
- Create: `packages/travis234-cli/skills/web-search/SKILL.md`
- Create: `packages/travis234-cli/test/travis234-cli.test.js`
- Modify: `travis/sandbox_launcher.py` to delegate shared naming/configuration rather than duplicate host seeding.
- Modify: `scripts/travis234_sandbox.py`
- Modify: `package.json`

**Interfaces:**
- Produces Node exports: `parseArgs`, `buildDockerCommand`, `buildPullCommand`, `prepareSandboxImports`, `shouldAutoPull`, `main`.
- Produces paths: host `~/.travis234/agent/{AGENTS.md,skills}`, sandbox home `~/.travis234/sandbox-home`, container `/travis-home/agent/{AGENTS.md,skills,sessions}`.

- [ ] **Step 1: Write failing launcher tests**

```javascript
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { buildDockerCommand, parseArgs, prepareSandboxImports } from "../bin/travis234.js";

test("uses only Travis234 names and persistent paths", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-home-"));
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-workspace-"));
  const config = parseArgs(["--cwd", workspace], { homeDir: home });
  assert.equal(config.agentHome, path.join(home, ".travis234", "sandbox-home"));
  const command = buildDockerCommand(config, { pid: 7, uid: 1000, gid: 1000 });
  assert.ok(command.includes(`${config.agentHome}:/travis-home:rw`));
  assert.ok(command.includes("TRAVIS234_CODING_AGENT_DIR=/travis-home/agent"));
  assert.ok(command.includes("ghcr.io/htooayelwinict/travis234:production"));
  assert.equal(command.some((part) => /appv|hermes|(?:^|-)pi(?:-|$)/i.test(part)), false);
});

test("seeds AGENTS and skills only under the Travis234 state root", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "travis234-seed-"));
  const config = parseArgs(["--cwd", home], { homeDir: home });
  prepareSandboxImports(config, { homeDir: home });
  assert.ok(fs.existsSync(path.join(home, ".travis234", "agent", "AGENTS.md")));
  assert.ok(fs.existsSync(path.join(home, ".travis234", "agent", "skills", "subagent-delegation", "SKILL.md")));
  assert.equal(fs.existsSync(path.join(home, ".agents")), false);
});
```

- [ ] **Step 2: Run npm tests to verify red**

Run: `node --test packages/travis234-cli/test/travis234-cli.test.js`

Expected: FAIL because the canonical launcher does not exist.

- [ ] **Step 3: Implement the launcher constants and Docker boundary**

The launcher starts with these exact constants:

```javascript
const DEFAULT_IMAGE = "ghcr.io/htooayelwinict/travis234:production";
const PUBLIC_IMAGE_PREFIX = "ghcr.io/htooayelwinict/travis234:";
const CONTAINER_WORKSPACE = "/workspace";
const CONTAINER_HOME = "/travis-home";
const APP_CONFIG_DIR = ".travis234";
const APP_AGENT_DIR = "agent";
const IMPORTED_AGENTS_MARKER = "<!-- travis234-sandbox-imported-agents -->";
```

`buildDockerCommand()` must include:

```javascript
[
  "docker", "run", "--rm", "-it",
  "--cap-drop", "ALL",
  "--security-opt", "no-new-privileges",
  "--pids-limit", "512",
  "--user", `${uid}:${gid}`,
  "-v", `${config.workspace}:/workspace:rw`,
  "-v", `${config.agentHome}:/travis-home:rw`,
  "-e", "HOME=/travis-home",
  "-e", "TRAVIS234_CODING_AGENT_DIR=/travis-home/agent",
  "-e", "TRAVIS234_SANDBOX=1",
  config.image, "--cwd", "/workspace",
]
```

It never forwards `.env`, provider secrets, Docker credentials, or paths outside
the selected workspace and state root. Default seeding copies only missing
bundled assets into `~/.travis234/agent`; existing files win.

- [ ] **Step 4: Declare the public npm package**

```json
{
  "name": "@htooayelwinict/travis234",
  "version": "2.3.1",
  "description": "Docker launcher for the Travis234 terminal coding agent.",
  "type": "module",
  "bin": {"travis234": "bin/travis234.js"},
  "files": ["bin/travis234.js", "agents/AGENTS.md", "skills/**/*.md", "README.md"],
  "scripts": {
    "test": "node --test test/travis234-cli.test.js",
    "pack:dry-run": "npm pack --dry-run"
  }
}
```

- [ ] **Step 5: Make Python development sandbox code delegate naming**

`travis/sandbox_launcher.py` may build a local image and invoke the canonical
Node launcher, but must not contain independent AGENTS/skills seeding or pull-cache
logic. `scripts/travis234_sandbox.py` uses only `TRAVIS234_*` variables.

- [ ] **Step 6: Run launcher and sandbox tests green**

Run: `npm --prefix packages/travis234-cli test`

Expected: PASS.

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_sandbox_launcher.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the canonical launcher**

```bash
git add packages/travis234-cli package.json travis/sandbox_launcher.py scripts/travis234_sandbox.py tests/test_sandbox_launcher.py
git commit -m "feat: add canonical Travis234 sandbox launcher"
```

### Task 5: Build secure development and release images

**Files:**
- Modify: `Dockerfile`
- Create: `Dockerfile.release`
- Create: `.github/workflows/travis234-release-image.yml`
- Modify: `tests/test_release_workflow.py`
- Modify: `evals/container_smoke.py`

**Interfaces:**
- Produces image entry point `travis234`, user `travis`, home `/travis-home`, image `ghcr.io/htooayelwinict/travis234:production`.

- [ ] **Step 1: Write failing release-contract tests**

```python
def test_release_image_uses_travis_identity_without_unrestricted_sudo() -> None:
    root = Path(__file__).parents[1]
    text = (root / "Dockerfile.release").read_text(encoding="utf-8")
    assert "useradd --create-home --home-dir /travis-home" in text
    assert 'ENTRYPOINT ["travis234"]' in text
    assert "NOPASSWD: ALL" not in text
    assert "APPV" not in text.upper()


def test_release_workflow_targets_public_travis234_image() -> None:
    workflow = _workflow()
    assert workflow["env"]["IMAGE_NAME"] == "ghcr.io/htooayelwinict/travis234"
    assert set(workflow["jobs"]["build-and-push"]["needs"]) == {"test", "image-smoke"}
```

- [ ] **Step 2: Run release tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_release_workflow.py -q`

Expected: FAIL because `Dockerfile.release` and the Travis234 workflow do not exist.

- [ ] **Step 3: Implement image identity and privilege boundaries**

Both Dockerfiles set:

```dockerfile
ENV PYTHONUNBUFFERED=1 \
    HOME=/travis-home \
    TRAVIS234_CODING_AGENT_DIR=/travis-home/agent \
    TRAVIS234_NO_VENV_REEXEC=1

RUN useradd --create-home --home-dir /travis-home --shell /bin/bash travis \
    && mkdir -p /workspace /travis-home/agent \
    && chown -R travis:travis /workspace /travis-home

USER travis
WORKDIR /workspace
ENTRYPOINT ["travis234"]
```

The release image contains no unrestricted passwordless sudo rule. If package
installation is retained in the development image, its sudoers entry is limited
to the exact package-manager executables already covered by the local baseline.

- [ ] **Step 4: Implement gated release workflow**

The workflow runs full Python tests and a no-cache image smoke before the push
job. Registry login appears only in the push job. The smoke job uses
`load: true`, `push: false`, and `no-cache: true`.

- [ ] **Step 5: Run release contract green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_release_workflow.py tests/test_eval_harness.py -q`

Expected: PASS.

- [ ] **Step 6: Commit image/release ownership**

```bash
git add Dockerfile Dockerfile.release .github/workflows/travis234-release-image.yml tests/test_release_workflow.py evals/container_smoke.py
git commit -m "build: define Travis234 release image"
```

### Task 6: Complete user documentation and packaging acceptance

**Files:**
- Rewrite: `README.md`
- Rewrite: `NOTICE.md` while retaining exact copyright holders.
- Modify: `LICENSE` contributor identity only; retain all copyright and MIT terms.
- Rewrite: `packages/travis234-cli/README.md`
- Create: `.env.example` with names and non-secret placeholders only.
- Modify: `tests/test_brand_contract.py`

**Interfaces:**
- Produces documented install/run/state/session/skill/AGENTS contracts and sanitized configuration template.

- [ ] **Step 1: Add documentation assertions**

Extend `tests/test_brand_contract.py`:

```python
def test_readme_documents_public_install_and_state() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    required = (
        "npx --yes @htooayelwinict/travis234@latest --cwd .",
        "travis234 --cwd .",
        "ghcr.io/htooayelwinict/travis234:production",
        "~/.travis234/agent/AGENTS.md",
        "~/.travis234/agent/skills/",
        "~/.travis234/agent/sessions/",
    )
    assert all(item in text for item in required)


def test_env_example_contains_names_not_credentials() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TRAVIS234_WORKER_LLM_PROVIDER=" in text
    assert "TRAVIS234_WORKER_LLM_MODEL=" in text
    assert not re.search(r"(?:sk-|AIza|ghp_)[A-Za-z0-9_-]+", text)
```

- [ ] **Step 2: Run documentation tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_brand_contract.py -q`

Expected: FAIL on old README content and missing `.env.example`.

- [ ] **Step 3: Rewrite user documentation**

Document only the final name/path contracts, actual CLI arguments, actual
launcher behavior, and actual session semantics. `NOTICE.md` states third-party
MIT attribution without using those names as product labels. Do not claim a test
count or release artifact until the corresponding command has run.

- [ ] **Step 4: Run complete rebrand checks**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_brand_contract.py tests/test_distribution_contract.py tests/test_installed_metadata.py tests/test_sandbox_launcher.py tests/test_release_workflow.py -q`

Expected: PASS.

Run: `npm --prefix packages/travis234-cli test && npm --prefix packages/travis234-cli run pack:dry-run`

Expected: PASS; tarball includes only declared launcher, instructions, skills, and README files.

Run: `.venv/bin/python -m build`

Expected: PASS and create `travis234-2.3.1` wheel/sdist artifacts without `.env`.

- [ ] **Step 5: Inspect package contents for secret/legacy leaks**

Run: `.venv/bin/python -m zipfile -l dist/travis234-2.3.1-py3-none-any.whl`

Expected: no `.env`, old import tree, old command, or nonexistent source-layout assets.

- [ ] **Step 6: Commit rebrand acceptance**

```bash
git add README.md NOTICE.md LICENSE .env.example packages/travis234-cli/README.md tests/test_brand_contract.py
git commit -m "docs: complete Travis234 hard cutover"
```
