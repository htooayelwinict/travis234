# Packaged Skills and Extension Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an authoritative installed extension reference, native read-only defaults for the two existing built-in skills, and complete removal of obsolete Hypa support in Travis234 2.3.3.

**Architecture:** Keep extension authoring knowledge in the already advertised installed documentation tree, not a new skill. Add one packaged-skills path to resource discovery after all existing skill sources so first-wins collision behavior preserves user overrides; package the same skill bytes in Python and npm; delete Hypa's isolated resource/CLI/documentation surface.

**Tech Stack:** Python 3.13, importlib.resources, pytest, setuptools package data, Markdown resources, Node 20/npm, Docker Buildx, GitHub Actions, PyPI trusted token upload.

## Global Constraints

- Do not modify the agent loop, tool coordinator, session persistence, JSONL/session replacement ordering, context-envelope construction, compaction, provider request translation, authentication, iteration budgets, or bounded parallel execution.
- Add and witness a failing regression before each production behavior change.
- Packaged skills are read-only defaults; never copy or overwrite files under `~/.travis234`.
- Existing skill sources win name collisions; packaged defaults are appended last.
- Do not add an extension-authoring skill.
- Exclude the unrelated untracked `appv231/` tree from every Git and release operation.
- Do not expose credentials in tracked files, commands, or output.
- Publish only after all source, npm, package, installed-wheel, parity, hygiene, and container gates pass.

---

### Task 1: Lock the Python packaged-skill contract

**Files:**
- Modify: `tests/test_installed_metadata.py`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `travis/coding_agent/config.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Create: `travis/resources/skills/subagent-delegation/SKILL.md`
- Create: `travis/resources/skills/web-search/SKILL.md`

**Interfaces:**
- Produces: `get_packaged_skills_path() -> str` resolving the installed `travis/resources/skills` directory.
- Consumes: existing `load_skills()` first-wins collision semantics and `DefaultResourceLoader.no_skills`.

- [ ] **Step 1: Add failing installed-resource and mirror tests**

Add assertions that `get_packaged_skills_path()` exists, contains exactly the two expected `SKILL.md` files, and that each resource file is byte-identical to its npm counterpart.

```python
def test_packaged_builtin_skills_exist_and_match_npm_distribution() -> None:
    from travis.coding_agent.config import get_packaged_skills_path

    skills_root = Path(get_packaged_skills_path())
    expected = {"subagent-delegation", "web-search"}
    assert {path.parent.name for path in skills_root.glob("*/SKILL.md")} == expected
    for name in expected:
        assert (skills_root / name / "SKILL.md").read_bytes() == (
            ROOT / "packages" / "travis234-cli" / "skills" / name / "SKILL.md"
        ).read_bytes()
```

- [ ] **Step 2: Add failing loader behavior tests**

Create loaders with isolated agent directories and assert that both defaults load, skill bodies stay out of the system prompt, `no_skills=True` omits the packaged defaults, and a same-name global user skill wins.

```python
loader = DefaultResourceLoader(cwd=str(tmp_path), agent_dir=str(agent_dir))
loader.reload({"projectTrustOverride": False})
skills = {skill.name: skill for skill in loader.get_skills()["skills"]}
assert set(skills) >= {"subagent-delegation", "web-search"}
skill_prompt = format_skills_for_prompt(list(skills.values()))
assert "subagent-delegation" in skill_prompt
assert "# Subagent Delegation" not in skill_prompt
```

- [ ] **Step 3: Run the focused tests and witness the intended failures**

Run:

```bash
uv run pytest -q tests/test_installed_metadata.py tests/test_distribution_contract.py tests/test_coding_resources_and_services.py -k 'packaged or builtin or skill'
```

Expected: failures identify the missing packaged skills path/files and absent default discovery.

- [ ] **Step 4: Add the packaged resources and minimal discovery path**

Implement:

```python
def get_packaged_skills_path() -> str:
    return _packaged_resource_path("skills")
```

Append `get_packaged_skills_path()` after existing resolved and additional skill paths only when `no_skills` is false. Copy the two existing npm `SKILL.md` files byte-for-byte into the new resource directories.

- [ ] **Step 5: Run the focused tests to green**

Run the command from Step 3. Expected: all selected tests pass and user override assertions select the user file.

### Task 2: Remove Hypa without leaving a dangling surface

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_distribution_contract.py`
- Delete: `tests/test_hypa_extension.py`
- Modify: `travis/cli.py`
- Delete: `travis/resources/extensions/hypa/__init__.py`
- Delete: `travis/resources/extensions/hypa/hypa_tools.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `packages/travis234-cli/README.md`

**Interfaces:**
- Removes: `--install-extension hypa` and the private first-party resource-copy helper.
- Preserves: global/project/explicit extension discovery and managed resource-package commands.

- [ ] **Step 1: Replace Hypa-positive tests with failing absence contracts**

Assert that CLI help does not contain `--install-extension`, packaged resources contain no Hypa directory, Python package data no longer declares `resources/extensions/**/*.py`, and both public READMEs contain no case-insensitive `hypa` reference.

```python
def test_obsolete_hypa_surface_is_absent() -> None:
    assert "--install-extension" not in cli._build_parser(include_prompt=True).format_help()
    assert not (ROOT / "travis/resources/extensions/hypa").exists()
    assert "hypa" not in (ROOT / "README.md").read_text(encoding="utf-8").lower()
```

- [ ] **Step 2: Run the absence contracts and witness failure**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_distribution_contract.py -k 'extension or hypa or readme'
```

Expected: failures point only to the still-present Hypa source, option, metadata, and documentation.

- [ ] **Step 3: Delete Hypa and its installer path**

Remove the resource directory, Hypa test module, CLI option, `_copy_extension_resources()`, `_install_first_party_extension()`, the associated `importlib.resources`/`shutil`/`tempfile` imports, and the `args.install_extension` branches. Remove Hypa instructions from both READMEs and remove the unused Python resource-extension package-data glob.

- [ ] **Step 4: Run the focused tests to green**

Run the Step 2 command plus `uv run pytest -q tests/test_extension_loading_and_reload.py tests/test_package_manager.py`. Expected: Hypa absence and general extension/package loading both pass.

### Task 3: Expand the installed extension-authoring reference

**Files:**
- Modify: `travis/resources/docs/extensions.md`
- Modify: `tests/test_pi_behavioral_parity.py`

**Interfaces:**
- Produces: one installed, agent-readable, Python-native extension reference.
- Consumes: public behavior from `extensions.py`, `extension_host.py`, resource loading, and executable parity tests.

- [ ] **Step 1: Add failing documentation-contract assertions**

Require headings and exact API markers covering discovery/trust, anatomy, commands, tools, shortcuts, flags, events, contexts, UI modes, messages, subagents/processes, create-check-reload-repair workflow, packaging, diagnostics, context cost, and Pi divergences. Require the guide to state that no extension-authoring skill is necessary because the installed guide is directly advertised.

```python
guide = Path("travis/resources/docs/extensions.md").read_text(encoding="utf-8")
for heading in (
    "## Create an extension with the agent",
    "## Extension module anatomy",
    "## Commands, flags, and shortcuts",
    "## Tools and providers",
    "## Events",
    "## Context API",
    "## UI API",
    "## Diagnose and repair",
    "## Packages",
    "## Intentional Pi divergences",
):
    assert heading in guide
for marker in (
    "register_command", "register_tool", "register_flag", "register_shortcut",
    "register_provider", "register_message_renderer", "send_user_message",
    "spawn_subagent", "ctx.has_ui", "python -m py_compile", "/reload",
):
    assert marker in guide
assert "No extension-authoring skill is required" in guide
```

- [ ] **Step 2: Run the guide contract and witness failure**

Run:

```bash
uv run pytest -q tests/test_pi_behavioral_parity.py -k extension_guide
```

Expected: missing-section assertions fail against the current concise guide.

- [ ] **Step 3: Rewrite the guide from verified runtime surfaces**

Document complete executable examples for a command, a typed tool, an event transformation/block, a shortcut/status action, and an agent-created extension repair loop. Include the 33 event names, command/event context properties and actions, async behavior, stale-generation rule, trust boundary, non-interactive fallbacks, user-message delivery rules, package manifests, diagnostic workflow, and precise unsupported surfaces.

- [ ] **Step 4: Run the guide and extension-runtime tests to green**

Run:

```bash
uv run pytest -q tests/test_pi_behavioral_parity.py tests/test_extension_host_runtime.py tests/test_extension_loading_and_reload.py tests/test_extension_event_parity.py
```

Expected: guide contracts and runtime evidence all pass.

### Task 4: Align version 2.3.3 and qualify distributions

**Files:**
- Modify: `pyproject.toml`
- Modify: `travis/coding_agent/config.py`
- Modify: `packages/travis234-cli/package.json`
- Modify: `README.md`
- Modify: `docs/verification/full-suite.md`

**Interfaces:**
- Produces: aligned Python/npm version `2.3.3` and release evidence.

- [ ] **Step 1: Add/update the version alignment assertion and witness failure**

Require Python metadata, source fallback, npm metadata, and README badge to equal `2.3.3`, then run the focused distribution/brand tests and observe the existing `2.3.2` mismatch.

```python
metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
npm = json.loads((ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8"))
config_source = (ROOT / "travis/coding_agent/config.py").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
assert metadata["project"]["version"] == "2.3.3"
assert npm["version"] == "2.3.3"
assert 'VERSION = "2.3.3"' in config_source
assert "Version 2.3.3" in readme
```

- [ ] **Step 2: Update all product version surfaces to 2.3.3**

Change only the four declared version locations. Update verification documentation after gates produce fresh counts and artifact names.

- [ ] **Step 3: Run all source and package gates**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
uv build
uv run twine check dist/travis234-2.3.3*
uv run python scripts/check_repository_hygiene.py
uv run python scripts/verify_acceptance.py --parity-json
python -m compileall -q travis
```

Expected: zero failures, publishable Python/npm packages, zero invalid parity contracts, and no hygiene violations.

- [ ] **Step 4: Verify clean-wheel and container behavior**

Install the built wheel into an isolated environment, assert both skill resources and extension guide are present, run a faux print/JSON smoke, then build without cache and smoke the release image:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:2.3.3-release-smoke .
uv run python -m evals.container_smoke --image travis234:2.3.3-release-smoke
```

Expected: installed wheel and unprivileged container pass; Hypa is absent and both packaged skills are available.

### Task 5: Commit, publish, and verify release artifacts

**Files:**
- Git scope: all intended Travis234 tracked/untracked files except `appv231/`
- Publish: PyPI `travis234==2.3.3`
- Publish: npm `@htooayelwinict/travis234@2.3.3`
- Publish: GHCR `ghcr.io/htooayelwinict/travis234:2.3.3` and `:production`

**Interfaces:**
- Consumes: verified source tree and credentials from the authorized local environment/GitHub Actions.
- Produces: commit on `main` and verified public package/image artifacts.

- [ ] **Step 1: Audit the exact Git scope**

Run `git diff --check`, inspect `git status --short`, confirm protected-path diff is empty, and explicitly exclude `appv231/`. Confirm no credential-like file is staged.

- [ ] **Step 2: Commit and push main**

Create one intentional release commit for the complete approved Travis234 source set and push `main` to `origin` only after local gates pass.

- [ ] **Step 3: Publish Python and npm artifacts**

Upload only `dist/travis234-2.3.3-py3-none-any.whl` and `dist/travis234-2.3.3.tar.gz` using the PyPI token without printing it. Run `npm publish --access public` from `packages/travis234-cli`; npm publication is required because packaged documentation/version changed and cross-distribution releases stay aligned.

- [ ] **Step 4: Dispatch and monitor the gated GHCR workflow**

Dispatch `.github/workflows/travis234-release-image.yml` with `ref=main` and `image_tag=2.3.3`. Wait for its test, no-cache image-smoke, and multi-architecture build-and-push jobs to pass.

- [ ] **Step 5: Verify public artifacts**

Query PyPI/npm metadata for exact version `2.3.3`, install/run each public package in isolated temporary state, inspect the GHCR production manifest, and verify it resolves to the workflow-produced multi-platform image. Report exact artifact versions, commit SHA, workflow result, and any remaining noncritical observations.
