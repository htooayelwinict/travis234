# Travis234 Ghost MCP Add-on Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire `travis234-ghost-mcp` from the active Travis234 tree and default public installation path while preserving the general MCP adapter, user data, valid historical Travis234 artifacts, and an exact GitOps audit trail.

**Architecture:** Delete the isolated Ghost add-on and its active product surfaces, retain the adapter's generic package-owned server registry under neutral tests, and publish forward patch releases. Public defaults move only after immutable versioned artifacts pass clean-install and container checks; the Ghost PyPI release is yanked last, local state is preserved, and the local-only temporary branch is erased after every other gate passes.

**Tech Stack:** Python 3.13, pytest, `uv`, Twine, Node.js/npm, GitHub Actions, Docker Buildx, GHCR, PyPI, GitHub CLI, and the Travis234 package manager.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the Python import package remains `travis`.
- The repository root `/Users/htooayelwin/orca/workspaces/travis234/mcp-addons` is the only application tree used for implementation edits.
- User data remains under `~/.travis234`; never delete or migrate `/Users/htooayelwin/.travis234/ghost-mcp`.
- Keep `travis234-mcp-adapter`, additive `--mcp`, configured MCP servers, transports, output bounds, cancellation, and lifecycle behavior.
- Do not change agent-loop ordering, iteration budgets, or bounded parallel execution.
- Credentials come only from existing authenticated clients and the untracked `.env`; never print, stage, or record secret values.
- Publish `travis234` 2.4.6, `travis234-mcp-adapter` 0.1.3, `@htooayelwinict/travis234` 2.4.6, and `ghcr.io/htooayelwinict/travis234:2.4.6`.
- Do not delete Travis234 2.4.5 from PyPI, npm, or GHCR.
- Yank `travis234-ghost-mcp` 0.1.0 only after all replacement artifacts and mutable defaults pass.
- Never push `htooakalewis/mcp-addons`; push only `main` under the verified GitHub identity `htooayelwinict`.
- Do not rewrite Git history to erase previously published Ghost work; retire it through forward commits and remove only the exact temporary branch refs at the end.
- Delete the exact local and remote `htooakalewis/mcp-addons` refs only after publication, local uninstall, evidence commit, and ancestry verification complete.
- Repository guidance forbids subagents unless the user explicitly requests them; default execution is inline despite the generic worker header above.
- Do not start any task in this plan until the user separately approves execution.
- Treat the Task 4 tip as the immutable `RELEASE_SOURCE_SHA`. Later verification-only commits may descend from it, but every Python/npm artifact and both GHCR workflow runs must use that exact source SHA.
- Store the release SHA in the verification record as a `Release source SHA`
  bullet whose value is a code-formatted, 40-character lowercase hexadecimal
  commit ID. Derive the deterministic artifact root
  `/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA` from it in later tasks.

## File and Responsibility Map

- `tests/test_distribution_contract.py` owns the permanent active-tree retirement invariant and aligned release metadata checks.
- `tests/test_pyproject_dependencies.py` owns root dependency and Python metadata authority checks.
- `tests/test_eval_harness.py` stops importing the removed Ghost-only evaluation.
- `packages/travis234-mcp-adapter/tests/` retains generic package-owned MCP server coverage with neutral fixtures.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/packaged_servers.py` remains the unchanged generic packaged-server interface.
- `README.md` and `packages/travis234-mcp-adapter/README.md` describe supported Travis234 and generic MCP workflows without advertising Ghost.
- `pyproject.toml`, `uv.lock`, `package.json`, `packages/travis234-cli/package.json`, and `travis/coding_agent/config.py` own the aligned 2.4.6 root version.
- `packages/travis234-mcp-adapter/pyproject.toml`, `packages/travis234-mcp-adapter/uv.lock`, and `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py` own adapter version 0.1.3.
- `.github/workflows/travis234-release-image.yml` separates immutable GHCR version publication from mutable `production` promotion.
- `docs/verification/main-ghost-mcp-retirement.md` records bounded local, registry, state-preservation, GitOps, and branch-removal evidence.
- `packages/travis234-ghost-mcp/`, `evals/bundled_ghost_mcp_smoke.py`, and the superseded bundled-Ghost spec, plan, and verification record are removed from the active tree.

---

## Planning Record Gate (Git authorization only; not execution approval)

The current account-switch pause intentionally leaves this plan untracked.
After the user authorizes Git operations and confirms the GitHub account is back
on `htooayelwinict`, make recording this plan the sole planning-stage Git
mutation:

```bash
test "$(git branch --show-current)" = "htooakalewis/mcp-addons"
test "$(gh api user --jq .login)" = "htooayelwinict"
test -z "$(git ls-remote --heads origin \
  refs/heads/htooakalewis/mcp-addons)"
test "$(git status --porcelain)" = \
  "?? docs/superpowers/plans/2026-08-14-travis234-ghost-mcp-retirement.md"
git add docs/superpowers/plans/2026-08-14-travis234-ghost-mcp-retirement.md
git diff --cached --check
git commit -m "docs: plan Ghost MCP retirement"
test -z "$(git status --porcelain)"
```

Expected: the plan is committed locally, the worktree is clean, the active
GitHub account is exact, and the temporary branch remains absent remotely. Do
not push at this gate. Completing this gate does not authorize Task 1 or any
later execution task.

---

### Task 1: Establish the retirement regression and remove Ghost-owned product material

**Files:**
- Modify: `tests/test_distribution_contract.py:47-106`
- Modify: `tests/test_pyproject_dependencies.py:61-68`
- Modify: `tests/test_eval_harness.py:179-203`
- Modify: `README.md:480-507`
- Modify: `packages/travis234-mcp-adapter/README.md:68-83`
- Modify: `.gitignore:17-20`
- Delete: `packages/travis234-ghost-mcp/` (all 65 tracked files)
- Delete: `evals/bundled_ghost_mcp_smoke.py`
- Delete: `docs/superpowers/specs/2026-08-12-travis234-bundled-ghost-mcp-design.md`
- Delete: `docs/superpowers/plans/2026-08-12-travis234-bundled-ghost-mcp.md`
- Delete: `docs/verification/main-bundled-ghost-mcp.md`

**Interfaces:**
- Consumes: current optional add-on isolation and the existing root distribution contract.
- Produces: a Ghost-free active application tree plus a permanent regression that allows references only in the retirement contract and retirement records.

- [ ] **Step 1: Add the failing active-tree retirement contract**

Replace the positive bundled-add-on metadata/documentation tests in
`tests/test_distribution_contract.py` with this focused contract, while keeping
`test_root_distribution_excludes_optional_mcp_packages`:

```python
def test_retired_ghost_addon_has_no_active_product_surface() -> None:
    retired_package = "travis234-ghost-mcp"
    assert not (ROOT / "packages" / retired_package).exists()
    assert not (ROOT / "evals/bundled_ghost_mcp_smoke.py").exists()

    active_docs = (
        ROOT / "README.md",
        ROOT / "packages/travis234-mcp-adapter/README.md",
    )
    forbidden = (
        "travis234 install travis234-ghost-mcp",
        "ghost-os",
        "/ghost-setup",
        "/ghost-doctor",
    )
    for path in active_docs:
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden), path
```

Rename the dependency test to
`test_root_distribution_keeps_optional_mcp_packages_out_of_core` and retain
both negative dependency assertions.

- [ ] **Step 2: Run the regression and capture the required red result**

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py::test_retired_ghost_addon_has_no_active_product_surface
```

Expected: FAIL because `packages/travis234-ghost-mcp` and the active
installation guidance still exist. Record only the assertion summary for the
later verification document.

- [ ] **Step 3: Remove the exact Ghost-owned files and obsolete positive tests**

Use `apply_patch` to delete every tracked file returned by this bounded
inventory, then verify the count falls from 65 to zero:

```bash
git ls-files -- packages/travis234-ghost-mcp
git ls-files -- packages/travis234-ghost-mcp | wc -l
```

Use the same patch to delete the exact evaluation and superseded records listed
in this task. Remove the two bundled-Ghost tests from
`tests/test_eval_harness.py`, remove the bundled-Ghost section from `README.md`,
and remove the Ghost-specific sentences from the adapter README while retaining
this generic contract:

```markdown
## Trusted packaged servers

An installed trusted Travis234 extension may register an executable it ships
through the adapter's package-owned server API. A packaged descriptor is
immutable, must name an executable inside its package root, and wins an
exact-name collision with file configuration while status reports the shadowed
entry.

Packaged-server registration is an in-process extension interface, not a user
configuration format. Configure ordinary stdio and HTTP servers through the
MCP configuration files below. Installing an extension remains an
executable-code trust decision.
```

Remove `.build/` from `.gitignore` after confirming no remaining tracked
component uses a Swift build directory.

- [ ] **Step 4: Run the focused retirement and harness tests green**

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  tests/test_eval_harness.py
```

Expected: PASS, with no bundled-Ghost evaluation skip or import remaining.

- [ ] **Step 5: Audit active references without flagging retirement records**

```bash
rg -n --hidden -g '!.git/**' \
  -g '!docs/superpowers/specs/2026-08-14-travis234-ghost-mcp-retirement-design.md' \
  -g '!docs/superpowers/plans/2026-08-14-travis234-ghost-mcp-retirement.md' \
  -g '!tests/test_distribution_contract.py' \
  '(?i)travis234-ghost-mcp|ghost-os|ghost_mcp|ghost-setup|ghost-doctor|bundled ghost' \
  README.md packages evals tests docs/verification
```

Expected at this stage: only neutralization work still pending inside
`packages/travis234-mcp-adapter/tests`; no package tree, evaluation, active
README guidance, or old verification record remains.

- [ ] **Step 6: Commit the focused source retirement**

```bash
git add .gitignore README.md \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  tests/test_eval_harness.py \
  packages/travis234-mcp-adapter/README.md
git add -A -- \
  packages/travis234-ghost-mcp \
  evals/bundled_ghost_mcp_smoke.py \
  docs/superpowers/specs/2026-08-12-travis234-bundled-ghost-mcp-design.md \
  docs/superpowers/plans/2026-08-12-travis234-bundled-ghost-mcp.md \
  docs/verification/main-bundled-ghost-mcp.md
git diff --cached --check
git commit -m "refactor(ghost-mcp): retire bundled add-on source"
```

### Task 2: Neutralize packaged-server coverage and release adapter 0.1.3

**Files:**
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py:1-157`
- Modify: `packages/travis234-mcp-adapter/tests/test_config.py:20-240`
- Modify: `packages/travis234-mcp-adapter/tests/test_packaged_servers.py:1-112`
- Modify: `packages/travis234-mcp-adapter/tests/test_extension.py:70-106`
- Modify: `packages/travis234-mcp-adapter/tests/test_proxy_tool.py:60-96`
- Modify: `packages/travis234-mcp-adapter/pyproject.toml:7`
- Modify: `packages/travis234-mcp-adapter/uv.lock:460-475`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py:9`

**Interfaces:**
- Consumes: `PackagedServer`, `register_packaged_server`,
  `get_packaged_servers`, and `merge_packaged_servers` from
  `travis234_mcp_adapter.packaged_servers`.
- Produces: the same generic registration API and behavior, adapter version
  0.1.3, and tests with no dependency on a retired add-on distribution.

- [ ] **Step 1: Change the wheel-version expectation first**

In `test_built_wheel_installs_and_loads_through_travis`, change the expected
metadata version to `0.1.3` and add the dependency guard:

```python
assert metadata["Version"] == "0.1.3"
requirements = metadata.get_all("Requires-Dist", [])
assert "mcp<3,>=2" in requirements
assert not any("travis234-ghost-mcp" in item for item in requirements)
```

- [ ] **Step 2: Run the adapter distribution test red**

```bash
(
  cd packages/travis234-mcp-adapter
  ../../.venv/bin/python -m pytest -q \
    tests/test_distribution.py::test_built_wheel_installs_and_loads_through_travis
)
```

Expected: FAIL because the built wheel still reports 0.1.2.

- [ ] **Step 3: Remove the obsolete cross-package build test**

Delete `GHOST_PACKAGE_ROOT`, `_build_ghost_wheel`, and
`test_adapter_and_ghost_addon_load_once_when_installed_together` from
`test_distribution.py`. Idempotent adapter loading remains covered by
`test_adapter_extension_is_idempotent_across_duplicate_distribution_paths`.

- [ ] **Step 4: Convert all generic adapter fixtures to neutral names**

Use `package-fixture` as the server name, `fixture-server` as the executable,
and `/tmp/external-fixture` as the shadowed configured command. The shared
fixture shape becomes:

```python
def _packaged_server(
    tmp_path: Path,
    name: str = "package-fixture",
) -> PackagedServer:
    root = tmp_path / "payload"
    command = root / "bin" / "fixture-server"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("binary", encoding="utf-8")
    command.chmod(0o755)
    return PackagedServer(
        name=name,
        package_root=root,
        command=command,
        args=("mcp",),
        request_timeout_ms=1_800_000,
    )
```

Rename the `ghost_descriptor` fixture to `packaged_descriptor` and update every
assertion in the five test files to `package-fixture`. Preserve the exact
shadow-status assertion using the neutral name:

```python
assert result.content[0].text == (
    "MCP adapter status\n"
    "- package-fixture: disconnected\n"
    "- ignored external configuration for packaged server: package-fixture"
)
```

- [ ] **Step 5: Bump adapter source metadata and regenerate only its project lock entry**

Set both source versions to 0.1.3:

```toml
version = "0.1.3"
```

```python
__version__ = "0.1.3"
```

Then update the lock and inspect it for dependency churn:

```bash
uv lock --offline --project packages/travis234-mcp-adapter
git diff -- packages/travis234-mcp-adapter/uv.lock
```

Expected: the editable `travis234-mcp-adapter` lock entry becomes 0.1.3;
third-party versions and hashes remain unchanged.

- [ ] **Step 6: Run the complete adapter suite and reference audit**

```bash
(
  cd packages/travis234-mcp-adapter
  ../../.venv/bin/python -m pytest -q
)
rg -n '(?i)ghost|travis234-ghost-mcp' packages/travis234-mcp-adapter
```

Expected: all adapter tests pass and the reference audit returns no matches.

- [ ] **Step 7: Commit the generic adapter release changes**

```bash
git add \
  packages/travis234-mcp-adapter/README.md \
  packages/travis234-mcp-adapter/pyproject.toml \
  packages/travis234-mcp-adapter/uv.lock \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py \
  packages/travis234-mcp-adapter/tests/test_distribution.py \
  packages/travis234-mcp-adapter/tests/test_config.py \
  packages/travis234-mcp-adapter/tests/test_packaged_servers.py \
  packages/travis234-mcp-adapter/tests/test_extension.py \
  packages/travis234-mcp-adapter/tests/test_proxy_tool.py
git diff --cached --check
git commit -m "refactor(mcp): keep packaged servers implementation-neutral"
```

### Task 3: Align Travis234 2.4.6 metadata and lock contracts

**Files:**
- Modify: `tests/test_distribution_contract.py:26-70`
- Modify: `tests/test_pyproject_dependencies.py:51-59`
- Modify: `pyproject.toml:7`
- Modify: `uv.lock:409-425`
- Modify: `package.json:3`
- Modify: `packages/travis234-cli/package.json:3`
- Modify: `travis/coding_agent/config.py:22`
- Modify: `README.md:13`

**Interfaces:**
- Consumes: existing single-version authority tests and adapter version 0.1.3
  from Task 2.
- Produces: aligned root, npm, runtime fallback, README, and lock metadata for
  Travis234 2.4.6.

- [ ] **Step 1: Update release expectations and add lock-version coverage first**

Set the expected root version to 2.4.6 and adapter version to 0.1.3. Add this
helper and lock assertions to `tests/test_distribution_contract.py`:

```python
def _locked_project_version(path: Path, name: str) -> str:
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
    project = next(item for item in lock["package"] if item["name"] == name)
    return project["version"]


def test_release_locks_match_project_metadata() -> None:
    assert _locked_project_version(ROOT / "uv.lock", "travis234") == "2.4.6"
    assert _locked_project_version(
        ROOT / "packages/travis234-mcp-adapter/uv.lock",
        "travis234-mcp-adapter",
    ) == "0.1.3"
```

- [ ] **Step 2: Run the version contracts red**

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py::test_release_versions_are_aligned \
  tests/test_distribution_contract.py::test_release_locks_match_project_metadata \
  tests/test_pyproject_dependencies.py::test_package_metadata_has_one_python_authority
```

Expected: FAIL on the remaining 2.4.5 root metadata and root lock entry.

- [ ] **Step 3: Apply the 2.4.6 source version consistently**

Change the exact version value in `pyproject.toml`, both `package.json` files,
`travis/coding_agent/config.py`, and the README badge to 2.4.6. Do not change
dependency bounds, CLI names, distribution names, or image names.

- [ ] **Step 4: Regenerate the root lock without unrelated upgrades**

```bash
uv lock --offline
git diff -- uv.lock
```

Expected: the editable `travis234` lock entry becomes 2.4.6; registry package
versions and hashes remain unchanged.

- [ ] **Step 5: Run focused Python and npm release checks**

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  tests/test_installed_metadata.py
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: all commands pass and npm reports
`@htooayelwinict/travis234@2.4.6`.

- [ ] **Step 6: Commit aligned release metadata**

```bash
git add README.md pyproject.toml uv.lock package.json \
  packages/travis234-cli/package.json travis/coding_agent/config.py \
  tests/test_distribution_contract.py tests/test_pyproject_dependencies.py
git diff --cached --check
git commit -m "chore: prepare Travis234 2.4.6 retirement release"
```

### Task 4: Separate GHCR version publication from production promotion

**Files:**
- Modify: `packages/travis234-cli/test/travis234-cli.test.js` in the GHCR workflow test
- Modify: `.github/workflows/travis234-release-image.yml:3-101`

**Interfaces:**
- Consumes: workflow inputs `ref` and `image_tag`, GHCR package write permission,
  and the current no-cache test/image jobs.
- Produces: `promote_production: boolean`; false builds only the immutable
  requested tag, true retags that existing tag as `production` without a
  rebuild.

- [ ] **Step 1: Strengthen the launcher workflow regression first**

Replace the existing GHCR workflow source test with:

```javascript
test("ghcr workflow separates version publication from production promotion", () => {
  const workflow = fs.readFileSync(
    path.resolve(packageRoot, "..", "..", ".github", "workflows", "travis234-release-image.yml"),
    "utf8",
  );

  assert.match(workflow, /^name: travis234 release image/m);
  assert.match(workflow, /promote_production:/);
  assert.match(workflow, /docker buildx imagetools create/);
  assert.equal(
    (workflow.match(
      /ref: \$\{\{ inputs\.ref \|\| github\.event\.release\.tag_name \|\| github\.ref_name \}\}/g,
    ) ?? []).length,
    3,
  );
  const buildStart = workflow.indexOf("  build-and-push:");
  const promoteStart = workflow.indexOf("  promote-production:");
  assert.notEqual(buildStart, -1);
  assert.notEqual(promoteStart, -1);
  assert.doesNotMatch(workflow.slice(buildStart, promoteStart), /:production/);
});
```

- [ ] **Step 2: Run the workflow regression red**

```bash
npm --prefix packages/travis234-cli test -- \
  --test-name-pattern="ghcr workflow separates version publication"
```

Expected: FAIL because the current build job always includes `:production` and
has no promotion-only job.

- [ ] **Step 3: Add the explicit promotion mode**

Add this workflow-dispatch input:

```yaml
      promote_production:
        description: "Retag an already published image_tag as production"
        required: false
        type: boolean
        default: false
```

Apply this condition to `test`, `image-smoke`, and `build-and-push`:

```yaml
if: ${{ github.event_name != 'workflow_dispatch' || inputs.promote_production != true }}
```

Make all three source-checkout steps use the exact requested source:

```yaml
        with:
          ref: ${{ inputs.ref || github.event.release.tag_name || github.ref_name }}
```

Make the build job publish only the exact requested tag:

```yaml
          tags: ${{ env.IMAGE_NAME }}:${{ env.IMAGE_TAG }}
```

Add a separate job that does not check out or rebuild source:

```yaml
  promote-production:
    if: ${{ github.event_name == 'workflow_dispatch' && inputs.promote_production == true }}
    runs-on: ubuntu-latest
    env:
      IMAGE_TAG: ${{ inputs.image_tag }}
    steps:
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4
      - name: Log in to GHCR
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Promote verified image to production
        run: >-
          docker buildx imagetools create
          --tag "${IMAGE_NAME}:production"
          "${IMAGE_NAME}:${IMAGE_TAG}"
```

- [ ] **Step 4: Validate YAML and run the full launcher suite**

```bash
.venv/bin/python -c \
  'from pathlib import Path; import yaml; yaml.safe_load(Path(".github/workflows/travis234-release-image.yml").read_text())'
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: YAML parses, the workflow regression passes, all launcher tests pass,
and the package dry run remains version 2.4.6.

- [ ] **Step 5: Commit the release-workflow safety gate**

```bash
git add .github/workflows/travis234-release-image.yml \
  packages/travis234-cli/test/travis234-cli.test.js
git diff --cached --check
git commit -m "ci: separate GHCR publication from promotion"
```

### Task 5: Qualify the exact local release tree and build artifacts

**Files:**
- Create: `docs/verification/main-ghost-mcp-retirement.md`
- Build outside Git: root 2.4.6 artifacts, adapter 0.1.3 artifacts, npm 2.4.6 tarball, and a no-cache local image.

**Interfaces:**
- Consumes: Ghost-free source, generic adapter 0.1.3, root 2.4.6, and the split GHCR workflow.
- Produces: an immutable verified `RELEASE_SOURCE_SHA`, artifact hashes, a
  deterministic retained `RETIRE_ARTIFACT_ROOT` directory for publication,
  and bounded local evidence in a documentation-only child commit.

- [ ] **Step 1: Audit repository scope and credentials before running gates**

```bash
git status --short --branch
git diff --check
git diff --stat main...HEAD
git ls-files .env '*.pem' '*.key' '*token*'
git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons
test "$(gh api user --jq .login)" = "htooayelwinict"
```

Expected: only intentional retirement commits differ from `main`; no credential
file is tracked; the remote branch query is empty; GitHub reports
`htooayelwinict`.

- [ ] **Step 2: Run focused root and complete adapter verification**

```bash
.venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_pyproject_dependencies.py \
  tests/test_eval_harness.py \
  tests/test_cli.py
(
  cd packages/travis234-mcp-adapter
  ../../.venv/bin/python -m pytest -q
)
```

Expected: all focused tests pass with no Ghost package build, skip, or process.

- [ ] **Step 3: Run the complete root Python and npm suites**

```bash
.venv/bin/python -m pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: all root Python and npm launcher tests pass.

- [ ] **Step 4: Build clean Python and npm artifacts in a bounded temporary root**

```bash
test -z "$(git status --porcelain)"
RELEASE_SOURCE_SHA="$(git rev-parse HEAD)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
test ! -e "$RETIRE_ARTIFACT_ROOT"
install -d -m 700 "$RETIRE_ARTIFACT_ROOT"
uv build --clear --out-dir "$RETIRE_ARTIFACT_ROOT/root" .
uv build --clear --out-dir "$RETIRE_ARTIFACT_ROOT/adapter" \
  packages/travis234-mcp-adapter
uvx --from twine twine check \
  "$RETIRE_ARTIFACT_ROOT"/root/* \
  "$RETIRE_ARTIFACT_ROOT"/adapter/*
mkdir "$RETIRE_ARTIFACT_ROOT/npm"
(
  cd packages/travis234-cli
  npm pack --pack-destination "$RETIRE_ARTIFACT_ROOT/npm"
)
shasum -a 256 \
  "$RETIRE_ARTIFACT_ROOT"/root/* \
  "$RETIRE_ARTIFACT_ROOT"/adapter/* \
  "$RETIRE_ARTIFACT_ROOT"/npm/*
test "$(find "$RETIRE_ARTIFACT_ROOT/root" -maxdepth 1 -type f \
  | wc -l | tr -d ' ')" = "2"
test "$(find "$RETIRE_ARTIFACT_ROOT/adapter" -maxdepth 1 -type f \
  | wc -l | tr -d ' ')" = "2"
test "$(find "$RETIRE_ARTIFACT_ROOT/npm" -maxdepth 1 -type f \
  | wc -l | tr -d ' ')" = "1"
```

Expected: exactly two root Python files, two adapter Python files, and one npm
tarball are produced with versions 2.4.6, 0.1.3, and 2.4.6 respectively. Keep
the generated path for Tasks 7 through 9.

- [ ] **Step 5: Inspect distributions and install exact local artifacts cleanly**

```bash
RELEASE_SOURCE_SHA="$(git rev-parse HEAD)"
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
test -d "$RETIRE_ARTIFACT_ROOT"
python3.13 -m venv "$RETIRE_ARTIFACT_ROOT/local-venv"
"$RETIRE_ARTIFACT_ROOT/local-venv/bin/python" -m pip install \
  "$RETIRE_ARTIFACT_ROOT"/root/travis234-2.4.6-py3-none-any.whl
"$RETIRE_ARTIFACT_ROOT/local-venv/bin/python" -m pip check
RETIRE_AGENT_DIR="$RETIRE_ARTIFACT_ROOT/local-agent"
TRAVIS234_CODING_AGENT_DIR="$RETIRE_AGENT_DIR" \
  "$RETIRE_ARTIFACT_ROOT/local-venv/bin/travis234" install \
  "travis234-mcp-adapter @ file://$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl"
TRAVIS234_CODING_AGENT_DIR="$RETIRE_AGENT_DIR" \
  "$RETIRE_ARTIFACT_ROOT/local-venv/bin/travis234" list
"$RETIRE_ARTIFACT_ROOT/local-venv/bin/travis234" --version
unzip -tq "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6-py3-none-any.whl" \
  >/dev/null
unzip -tq \
  "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl" \
  >/dev/null
tar -tzf "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6.tar.gz" >/dev/null
tar -tzf \
  "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3.tar.gz" \
  >/dev/null
tar -tzf "$RETIRE_ARTIFACT_ROOT/npm/htooayelwinict-travis234-2.4.6.tgz" \
  >/dev/null
if {
  unzip -Z1 "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6-py3-none-any.whl"
  tar -tzf "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6.tar.gz"
  unzip -Z1 \
    "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl"
  tar -tzf \
    "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3.tar.gz"
  tar -tzf "$RETIRE_ARTIFACT_ROOT/npm/htooayelwinict-travis234-2.4.6.tgz"
} | rg -i 'travis234_ghost_mcp|ghost_mcp\.py|/bin/ghost$'; then
  echo "retired Ghost payload found in a release artifact" >&2
  exit 1
fi
```

Expected: Travis234 reports 2.4.6, the package list contains only the intended
adapter installation, and neither Python wheel contains
`travis234_ghost_mcp`, `ghost_mcp.py`, or a `ghost` executable.

- [ ] **Step 6: Build and smoke-test the no-cache release container**

```bash
RELEASE_SOURCE_SHA="$(git rev-parse HEAD)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
docker build --no-cache -f Dockerfile.release \
  -t travis234:2.4.6-retirement-smoke .
.venv/bin/python evals/container_smoke.py \
  --image travis234:2.4.6-retirement-smoke
```

Expected: the unprivileged release image and complete repository container
smoke pass; the image reports Travis234 2.4.6.

- [ ] **Step 7: Perform an inline two-pass review**

First compare every changed path with the approved design and remove accidental
scope. Then review the final diff for missing retirement references, weakened
generic adapter assertions, workflow tag mistakes, credential paths, and broad
destructive commands:

```bash
git diff --check
git diff --stat main...HEAD
git diff main...HEAD -- \
  README.md tests packages/travis234-mcp-adapter .github \
  pyproject.toml uv.lock package.json travis evals docs/verification
if rg -n --hidden -g '!.git/**' \
  -g '!tests/test_distribution_contract.py' \
  -g '!tests/test_pyproject_dependencies.py' \
  -g '!docs/superpowers/specs/2026-08-14-travis234-ghost-mcp-retirement-design.md' \
  -g '!docs/superpowers/plans/2026-08-14-travis234-ghost-mcp-retirement.md' \
  -g '!docs/verification/main-ghost-mcp-retirement.md' \
  '(?i)travis234-ghost-mcp|ghost-os|ghost_mcp|ghost-setup|ghost-doctor|bundled ghost' \
  README.md packages evals tests docs/verification; then
  echo "unexpected active Ghost reference" >&2
  exit 1
fi
```

Expected: no unrelated refactor, credential, core agent-loop change, or user
state mutation appears.

- [ ] **Step 8: Write and commit bounded local verification evidence**

Create `docs/verification/main-ghost-mcp-retirement.md` with the actual date
and a Markdown bullet named `Release source SHA` whose code-formatted value is
the actual output of `git rev-parse HEAD`. Also include the red-test assertion,
focused/full test totals, artifact
filenames and SHA-256 values, npm pack result, container digest/result,
active-reference audit, and confirmation that publication, yanking, uninstall,
and branch deletion have not yet occurred. Do not include environment values,
tokens, absolute auth paths, or raw `.env` content. The evidence commit is
intentionally a documentation-only child of the already built and verified
release source.

```bash
git add docs/verification/main-ghost-mcp-retirement.md
git diff --cached --check
git commit -m "docs: record local Ghost MCP retirement gates"
```

### Task 6: Fast-forward verified source to main without publishing the temporary branch

**Files:**
- Git refs only; no new application edits.

**Interfaces:**
- Consumes: the exact locally qualified release source, its documentation-only
  evidence child, and clean main worktree
  `/Users/htooayelwin/orca/travis234`.
- Produces: `origin/main` containing the verified source plus evidence while
  the temporary branch remains local as a recovery reference.

- [ ] **Step 1: Recheck both worktrees and remote identity**

```bash
git status --short --branch
git -C /Users/htooayelwin/orca/travis234 status --short --branch
test "$(gh api user --jq .login)" = "htooayelwinict"
git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
test "$RELEASE_SOURCE_SHA" = "$(git rev-parse HEAD^)"
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
test -d "$RETIRE_ARTIFACT_ROOT"
```

Expected: both worktrees are clean, GitHub reports `htooayelwinict`, and the
remote temporary branch query is empty. Stop without integration if either
worktree has unrelated changes.

- [ ] **Step 2: Fetch and prove main has not moved unexpectedly**

```bash
git -C /Users/htooayelwin/orca/travis234 fetch origin
git -C /Users/htooayelwin/orca/travis234 rev-parse main
git -C /Users/htooayelwin/orca/travis234 rev-parse origin/main
```

Expected: the two SHAs match. If they differ, run the exact non-rewriting
integration below in this worktree, abort and report any conflict, then rerun
Task 5 before restarting this task:

```bash
git merge --no-edit origin/main
```

- [ ] **Step 3: Fast-forward local main to the candidate**

```bash
git -C /Users/htooayelwin/orca/travis234 merge --ff-only \
  htooakalewis/mcp-addons
git -C /Users/htooayelwin/orca/travis234 \
  merge-base --is-ancestor htooakalewis/mcp-addons main
```

Expected: fast-forward succeeds and the ancestry check exits zero.

- [ ] **Step 4: Repeat source qualification from integrated main**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
(
  cd /Users/htooayelwin/orca/travis234
  uv run python -m pytest -q
  npm --prefix packages/travis234-cli test
  npm --prefix packages/travis234-cli run pack:dry-run
  (
    cd packages/travis234-mcp-adapter
    uv run pytest -q
  )
)
```

Expected: the integrated main tree repeats the full Python, adapter, and npm
results with no application change from the exact release source. Prove that
the sole descendant change is the verification record:

```bash
test "$(git -C /Users/htooayelwin/orca/travis234 diff --name-only \
  "$RELEASE_SOURCE_SHA" main)" = \
  "docs/verification/main-ghost-mcp-retirement.md"
```

- [ ] **Step 5: Push only main and verify the exact remote SHA**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
MAIN_EVIDENCE_SHA="$(git -C /Users/htooayelwin/orca/travis234 rev-parse main)"
git -C /Users/htooayelwin/orca/travis234 push origin main:main
git -C /Users/htooayelwin/orca/travis234 fetch origin
test "$MAIN_EVIDENCE_SHA" = \
  "$(git -C /Users/htooayelwin/orca/travis234 rev-parse origin/main)"
git -C /Users/htooayelwin/orca/travis234 \
  merge-base --is-ancestor "$RELEASE_SOURCE_SHA" origin/main
git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons
```

Expected: `origin/main` equals the evidence commit, contains the immutable
`RELEASE_SOURCE_SHA`, and the temporary branch still has no remote ref.

### Task 7: Publish and verify exact PyPI replacement releases

**Files:**
- Publish: `travis234==2.4.6`
- Publish: `travis234-mcp-adapter==0.1.3`
- Read only: `.env` key `PYPI_API_TOKEN`

**Interfaces:**
- Consumes: Task 5's exact checked artifacts and Task 6's
  `RELEASE_SOURCE_SHA` on `origin/main`.
- Produces: clean public Python replacements; Ghost remains unyanked throughout
  this task.

- [ ] **Step 1: Confirm versions are unused and Ghost is still available**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
test -f "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6-py3-none-any.whl"
test -f "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6.tar.gz"
test -f "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl"
test -f "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3.tar.gz"
git merge-base --is-ancestor "$RELEASE_SOURCE_SHA" origin/main
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  https://pypi.org/pypi/travis234/2.4.6/json)" = "404"
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  https://pypi.org/pypi/travis234-mcp-adapter/0.1.3/json)" = "404"
curl -fsSL https://pypi.org/pypi/travis234-ghost-mcp/0.1.0/json \
  | jq -e '[.urls[].yanked] | all(. == false)'
```

Expected: both replacement versions are unused and both Ghost files are not
yanked.

- [ ] **Step 2: Load only the PyPI token into the current shell and upload exact artifacts**

Disable shell tracing, source the untracked file without printing it, pass the
token only through Twine environment variables, and immediately unset it:

```bash
set +x
PYPI_API_TOKEN="$(
  .venv/bin/python -c \
    'from travis.ai.env_config import load_dotenv_values; value = load_dotenv_values(".env").get("PYPI_API_TOKEN", ""); assert value; print(value, end="")'
)"
test -n "${PYPI_API_TOKEN:-}"
trap 'unset PYPI_API_TOKEN' EXIT INT TERM
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
TWINE_USERNAME=__token__ TWINE_PASSWORD="$PYPI_API_TOKEN" \
  uvx --from twine twine upload \
  "$RETIRE_ARTIFACT_ROOT"/root/travis234-2.4.6-py3-none-any.whl \
  "$RETIRE_ARTIFACT_ROOT"/root/travis234-2.4.6.tar.gz \
  "$RETIRE_ARTIFACT_ROOT"/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl \
  "$RETIRE_ARTIFACT_ROOT"/adapter/travis234_mcp_adapter-0.1.3.tar.gz
unset PYPI_API_TOKEN
trap - EXIT INT TERM
```

Expected: Twine uploads exactly four checked files and prints no credential.

- [ ] **Step 3: Verify public metadata and clean Python 3.13 installation**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
for RETIRE_ATTEMPT in {1..30}; do
  if curl -fsS -o /dev/null \
      https://pypi.org/pypi/travis234/2.4.6/json \
    && curl -fsS -o /dev/null \
      https://pypi.org/pypi/travis234-mcp-adapter/0.1.3/json; then
    break
  fi
  sleep 2
done
ROOT_WHEEL_SHA="$(shasum -a 256 \
  "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6-py3-none-any.whl" | awk '{print $1}')"
ROOT_SDIST_SHA="$(shasum -a 256 \
  "$RETIRE_ARTIFACT_ROOT/root/travis234-2.4.6.tar.gz" | awk '{print $1}')"
ADAPTER_WHEEL_SHA="$(shasum -a 256 \
  "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3-py3-none-any.whl" | awk '{print $1}')"
ADAPTER_SDIST_SHA="$(shasum -a 256 \
  "$RETIRE_ARTIFACT_ROOT/adapter/travis234_mcp_adapter-0.1.3.tar.gz" | awk '{print $1}')"
curl -fsSL https://pypi.org/pypi/travis234/2.4.6/json \
  | jq -e --arg wheel "$ROOT_WHEEL_SHA" --arg sdist "$ROOT_SDIST_SHA" \
    '.info.version == "2.4.6"
     and any(.urls[]; .filename == "travis234-2.4.6-py3-none-any.whl" and .digests.sha256 == $wheel)
     and any(.urls[]; .filename == "travis234-2.4.6.tar.gz" and .digests.sha256 == $sdist)'
curl -fsSL https://pypi.org/pypi/travis234-mcp-adapter/0.1.3/json \
  | jq -e --arg wheel "$ADAPTER_WHEEL_SHA" --arg sdist "$ADAPTER_SDIST_SHA" \
    '.info.version == "0.1.3"
     and any(.urls[]; .filename == "travis234_mcp_adapter-0.1.3-py3-none-any.whl" and .digests.sha256 == $wheel)
     and any(.urls[]; .filename == "travis234_mcp_adapter-0.1.3.tar.gz" and .digests.sha256 == $sdist)'
RETIRE_PUBLIC_PYTHON="$(mktemp -d /tmp/travis234-public-pypi.XXXXXX)"
python3.13 -m venv "$RETIRE_PUBLIC_PYTHON/venv"
"$RETIRE_PUBLIC_PYTHON/venv/bin/python" -m pip install \
  'travis234==2.4.6'
"$RETIRE_PUBLIC_PYTHON/venv/bin/python" -m pip check
TRAVIS234_CODING_AGENT_DIR="$RETIRE_PUBLIC_PYTHON/agent" \
  "$RETIRE_PUBLIC_PYTHON/venv/bin/travis234" install \
  'travis234-mcp-adapter==0.1.3'
TRAVIS234_CODING_AGENT_DIR="$RETIRE_PUBLIC_PYTHON/agent" \
  "$RETIRE_PUBLIC_PYTHON/venv/bin/travis234" list
"$RETIRE_PUBLIC_PYTHON/venv/bin/travis234" --version
```

Expected: clean public installation reports Travis234 2.4.6 and adapter 0.1.3,
with no Ghost package installed.

- [ ] **Step 4: Stop on any partial publication defect**

If either upload or public install fails, leave Ghost 0.1.0 unyanked and do not
overwrite an uploaded file. Inventory the public filenames and hashes first.
If all expected files landed with matching hashes, treat the upload as complete
and rerun only the public verification. If a distribution is incomplete or has
a mismatched file, advance only that affected distribution to its next unused
patch version, update its metadata/contracts/lock and cross-references, rebuild
and requalify every affected artifact, amend this plan's release matrix, and
obtain fresh execution approval before publishing again. Do not proceed to
GHCR or npm defaults while any Python replacement is incomplete.

### Task 8: Publish and verify the immutable GHCR 2.4.6 image

**Files:**
- Publish: `ghcr.io/htooayelwinict/travis234:2.4.6`
- Keep unchanged: `ghcr.io/htooayelwinict/travis234:production`

**Interfaces:**
- Consumes: the split workflow on `origin/main` and `RELEASE_SOURCE_SHA`.
- Produces: a verified multi-platform `CANDIDATE_IMAGE_DIGEST` while retaining
  the previous `PRODUCTION_IMAGE_DIGEST`.

- [ ] **Step 1: Prove tag preconditions, dispatch version-only publication, and watch its exact run**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "${#RELEASE_SOURCE_SHA}" -eq 40
test "$(gh api user --jq .login)" = "htooayelwinict"
WORKFLOW_HEAD_SHA="$(gh api \
  repos/htooayelwinict/travis234/commits/main --jq .sha)"
git merge-base --is-ancestor "$RELEASE_SOURCE_SHA" "$WORKFLOW_HEAD_SHA"
EXPECTED_PRODUCTION_DIGEST="$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:2.4.5 \
  | awk '/^Digest:/ {print $2; exit}'
)"
CURRENT_PRODUCTION_DIGEST="$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:production \
  | awk '/^Digest:/ {print $2; exit}'
)"
test -n "$EXPECTED_PRODUCTION_DIGEST"
test "$CURRENT_PRODUCTION_DIGEST" = "$EXPECTED_PRODUCTION_DIGEST"
if docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:2.4.6 >/dev/null 2>&1; then
  echo "GHCR tag 2.4.6 is already in use" >&2
  exit 1
fi
GHCR_PRE_BUILD_RUN_ID="$(
  gh run list --repo htooayelwinict/travis234 \
    --workflow travis234-release-image.yml \
    --event workflow_dispatch --commit "$WORKFLOW_HEAD_SHA" --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty'
)"
gh workflow run travis234-release-image.yml \
  --repo htooayelwinict/travis234 \
  --ref main \
  -f ref="$RELEASE_SOURCE_SHA" \
  -f image_tag=2.4.6 \
  -f promote_production=false
GHCR_BUILD_RUN_ID=""
for RETIRE_ATTEMPT in {1..30}; do
  GHCR_BUILD_RUN_ID="$(
    gh run list --repo htooayelwinict/travis234 \
      --workflow travis234-release-image.yml \
      --event workflow_dispatch --commit "$WORKFLOW_HEAD_SHA" \
      --limit 1 --json databaseId --jq '.[0].databaseId // empty'
  )"
  if test -n "$GHCR_BUILD_RUN_ID" \
    && test "$GHCR_BUILD_RUN_ID" != "$GHCR_PRE_BUILD_RUN_ID"; then
    break
  fi
  sleep 2
done
test -n "$GHCR_BUILD_RUN_ID"
test "$GHCR_BUILD_RUN_ID" != "$GHCR_PRE_BUILD_RUN_ID"
gh run watch "$GHCR_BUILD_RUN_ID" \
  --repo htooayelwinict/travis234 --exit-status
test "$(gh run view "$GHCR_BUILD_RUN_ID" \
  --repo htooayelwinict/travis234 --json headSha --jq .headSha)" = \
  "$WORKFLOW_HEAD_SHA"
gh run view "$GHCR_BUILD_RUN_ID" \
  --repo htooayelwinict/travis234 \
  --json event,headSha,jobs,name \
  --jq '{name,event,headSha,jobs:[.jobs[] | {name,conclusion}]}'
```

Expected: the run's `headSha` is `WORKFLOW_HEAD_SHA`; test, no-cache image
smoke, and multi-platform build-and-push jobs pass; the promotion-only job is
skipped. Record `GHCR_BUILD_RUN_ID` for the verification document.

- [ ] **Step 2: Verify the public candidate platforms and unchanged production tag**

```bash
CANDIDATE_IMAGE_DIGEST="$(
  docker buildx imagetools inspect ghcr.io/htooayelwinict/travis234:2.4.6 \
  | awk '/^Digest:/ {print $2; exit}'
)"
test -n "$CANDIDATE_IMAGE_DIGEST"
EXPECTED_PRODUCTION_DIGEST="$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:2.4.5 \
  | awk '/^Digest:/ {print $2; exit}'
)"
test "$EXPECTED_PRODUCTION_DIGEST" = "$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:production \
  | awk '/^Digest:/ {print $2; exit}'
)"
docker buildx imagetools inspect \
  ghcr.io/htooayelwinict/travis234:2.4.6 --raw \
  | jq -e \
    '[.manifests[]?.platform | "\(.os)/\(.architecture)"] as $platforms
     | ($platforms | index("linux/amd64")) != null
       and ($platforms | index("linux/arm64")) != null'
docker pull ghcr.io/htooayelwinict/travis234:2.4.6
.venv/bin/python evals/container_smoke.py \
  --image ghcr.io/htooayelwinict/travis234:2.4.6
```

Expected: the candidate has a non-empty multi-platform digest containing
linux/amd64 and linux/arm64, production still resolves to the 2.4.5 digest,
and the pulled public candidate passes the container smoke.

If the workflow or public smoke fails after tag 2.4.6 appears, never overwrite
that tag. Leave `production` unchanged and Ghost unyanked, capture the run and
digest evidence, and prepare a newly approved forward patch release.

### Task 9: Publish npm 2.4.6, then promote npm and GHCR defaults

**Files:**
- Publish: `@htooayelwinict/travis234@2.4.6`
- Move: npm `latest` to 2.4.6
- Move: GHCR `production` to the existing 2.4.6 manifest

**Interfaces:**
- Consumes: checked npm tarball, public PyPI replacements,
  `CANDIDATE_IMAGE_DIGEST`, and the promotion-only GHCR workflow path.
- Produces: verified npm and GHCR public defaults at 2.4.6.

- [ ] **Step 1: Run npm authentication and unused-version preflight**

```bash
test "$(npm whoami)" = "htooayelwinict"
test "$(npm view @htooayelwinict/travis234@2.4.6 version \
  --json 2>/dev/null || true)" != '"2.4.6"'
```

Expected: npm identifies the authorized maintainer and 2.4.6 is unused. If npm
requests browser authorization or a one-time code during publication, pause for
the user to complete that prompt without copying the credential into chat or
logs.

- [ ] **Step 2: Publish under a non-default candidate tag**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
RETIRE_ARTIFACT_ROOT="/tmp/travis234-retirement-2.4.6-$RELEASE_SOURCE_SHA"
NPM_TARBALL="$RETIRE_ARTIFACT_ROOT/npm/htooayelwinict-travis234-2.4.6.tgz"
test -f "$NPM_TARBALL"
NPM_LOCAL_INTEGRITY="sha512-$(
  openssl dgst -sha512 -binary "$NPM_TARBALL" | openssl base64 -A
)"
npm publish "$NPM_TARBALL" --access public --tag retirement-candidate
for RETIRE_ATTEMPT in {1..30}; do
  if test "$(npm view @htooayelwinict/travis234@2.4.6 version \
      --json 2>/dev/null || true)" = '"2.4.6"'; then
    break
  fi
  sleep 2
done
test "$(npm view @htooayelwinict/travis234@2.4.6 dist.integrity)" = \
  "$NPM_LOCAL_INTEGRITY"
npm view @htooayelwinict/travis234@2.4.6 \
  name version dist.integrity dist.tarball --json
npx --yes --package @htooayelwinict/travis234@2.4.6 \
  travis234 --help
```

Expected: the exact public package reports 2.4.6 and its executable help runs
without touching Docker or host Travis234 state.

If publication returns an error, inventory the public version and integrity
before retrying. If 2.4.6 exists with the expected integrity, continue only
with verification; if it is absent, the same exact tarball may be retried after
authentication is corrected; if it exists with any mismatch, do not overwrite
or move `latest`, and obtain approval for a forward patch recovery.

- [ ] **Step 3: Move npm latest and verify a default install**

```bash
npm dist-tag add @htooayelwinict/travis234@2.4.6 latest
test "$(npm view @htooayelwinict/travis234 dist-tags.latest)" = "2.4.6"
npx --yes --package @htooayelwinict/travis234@latest \
  travis234 --help
npm dist-tag rm @htooayelwinict/travis234 retirement-candidate
```

Expected: `latest` resolves to 2.4.6, default executable help passes, and the
temporary candidate tag is removed.

- [ ] **Step 4: Dispatch promotion of the already verified GHCR tag**

```bash
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "$(gh api user --jq .login)" = "htooayelwinict"
WORKFLOW_HEAD_SHA="$(gh api \
  repos/htooayelwinict/travis234/commits/main --jq .sha)"
git merge-base --is-ancestor "$RELEASE_SOURCE_SHA" "$WORKFLOW_HEAD_SHA"
GHCR_PRE_PROMOTE_RUN_ID="$(
  gh run list --repo htooayelwinict/travis234 \
    --workflow travis234-release-image.yml \
    --event workflow_dispatch --commit "$WORKFLOW_HEAD_SHA" --limit 1 \
    --json databaseId --jq '.[0].databaseId // empty'
)"
gh workflow run travis234-release-image.yml \
  --repo htooayelwinict/travis234 \
  --ref main \
  -f ref="$RELEASE_SOURCE_SHA" \
  -f image_tag=2.4.6 \
  -f promote_production=true
GHCR_PROMOTE_RUN_ID=""
for RETIRE_ATTEMPT in {1..30}; do
  GHCR_PROMOTE_RUN_ID="$(
    gh run list --repo htooayelwinict/travis234 \
      --workflow travis234-release-image.yml \
      --event workflow_dispatch --commit "$WORKFLOW_HEAD_SHA" --limit 1 \
      --json databaseId --jq '.[0].databaseId // empty'
  )"
  if test -n "$GHCR_PROMOTE_RUN_ID" \
    && test "$GHCR_PROMOTE_RUN_ID" != "$GHCR_PRE_PROMOTE_RUN_ID"; then
    break
  fi
  sleep 2
done
test -n "$GHCR_PROMOTE_RUN_ID"
test "$GHCR_PROMOTE_RUN_ID" != "$GHCR_PRE_PROMOTE_RUN_ID"
gh run watch "$GHCR_PROMOTE_RUN_ID" \
  --repo htooayelwinict/travis234 --exit-status
test "$(gh run view "$GHCR_PROMOTE_RUN_ID" \
  --repo htooayelwinict/travis234 --json headSha --jq .headSha)" = \
  "$WORKFLOW_HEAD_SHA"
gh run view "$GHCR_PROMOTE_RUN_ID" \
  --repo htooayelwinict/travis234 \
  --json event,headSha,jobs,name \
  --jq '{name,event,headSha,jobs:[.jobs[] | {name,conclusion}]}'
```

Expected: the run's `headSha` is `WORKFLOW_HEAD_SHA`; only the promotion job
runs and succeeds; source tests, image smoke, and build-and-push are skipped, so
no image rebuild occurs. Record `GHCR_PROMOTE_RUN_ID` for the verification
document.

- [ ] **Step 5: Verify both public defaults and rerun the remote container smoke**

```bash
CANDIDATE_IMAGE_DIGEST="$(
  docker buildx imagetools inspect ghcr.io/htooayelwinict/travis234:2.4.6 \
  | awk '/^Digest:/ {print $2; exit}'
)"
test -n "$CANDIDATE_IMAGE_DIGEST"
test "$CANDIDATE_IMAGE_DIGEST" = "$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:production \
  | awk '/^Digest:/ {print $2; exit}'
)"
docker pull ghcr.io/htooayelwinict/travis234:production
.venv/bin/python evals/container_smoke.py \
  --image ghcr.io/htooayelwinict/travis234:production
```

Expected: `2.4.6` and `production` have the same manifest digest and the public
default image passes.

### Task 10: Yank Ghost 0.1.0 on PyPI and prove default resolution rejects it

**Files:**
- Registry mutation: yank the complete `travis234-ghost-mcp` 0.1.0 release.
- Preserve: project ownership, release files, and exact-pin availability.

**Interfaces:**
- Consumes: verified PyPI, npm, and GHCR replacements/defaults.
- Produces: a reversible PyPI retirement with reason
  `Retired; use Travis234's standard MCP adapter for supported MCP servers.`

- [ ] **Step 1: Recheck every replacement default before opening PyPI management**

```bash
test "$(gh api user --jq .login)" = "htooayelwinict"
curl -fsSL https://pypi.org/pypi/travis234/json \
  | jq -e '.info.version == "2.4.6"'
curl -fsSL https://pypi.org/pypi/travis234-mcp-adapter/json \
  | jq -e '.info.version == "0.1.3"'
test "$(npm view @htooayelwinict/travis234 dist-tags.latest)" = "2.4.6"
test -z "$(npm view travis234-ghost-mcp version 2>/dev/null || true)"
test -z "$(npm view @htooayelwinict/travis234-ghost-mcp version \
  2>/dev/null || true)"
GHCR_PACKAGE_NAMES="$(gh api --paginate \
  'users/htooayelwinict/packages?package_type=container&per_page=100' \
  --jq '.[].name')"
if printf '%s\n' "$GHCR_PACKAGE_NAMES" | rg -i 'ghost'; then
  echo "unexpected Ghost container package exists" >&2
  exit 1
fi
CANDIDATE_IMAGE_DIGEST="$(
  docker buildx imagetools inspect ghcr.io/htooayelwinict/travis234:2.4.6 \
  | awk '/^Digest:/ {print $2; exit}'
)"
test -n "$CANDIDATE_IMAGE_DIGEST"
test "$CANDIDATE_IMAGE_DIGEST" = "$(
  docker buildx imagetools inspect \
    ghcr.io/htooayelwinict/travis234:production \
  | awk '/^Digest:/ {print $2; exit}'
)"
```

Expected: all replacements are still correct, and the previously observed
absence of any Ghost npm or GHCR package is reconfirmed. Any mismatch stops
this task.

- [ ] **Step 2: Yank exactly release 0.1.0 through the PyPI project UI**

Open
`https://pypi.org/manage/project/travis234-ghost-mcp/releases/` in the
authenticated browser, select **Options → Yank** for 0.1.0, enter exactly:

```text
Retired; use Travis234's standard MCP adapter for supported MCP servers.
```

Confirm Yank. Do not select Delete release, Delete project, or any file-level
delete action. If authentication or 2FA is requested, let the user complete it
without exposing credentials.

- [ ] **Step 3: Verify every file is publicly yanked with the bounded reason**

```bash
RETIRE_YANK_REASON="Retired; use Travis234's standard MCP adapter for supported MCP servers."
curl -fsSL https://pypi.org/pypi/travis234-ghost-mcp/0.1.0/json \
  | jq -e --arg reason "$RETIRE_YANK_REASON" \
    '[.urls[] | select(.yanked == true and .yanked_reason == $reason)] | length == 2'
curl -fsSL \
  -H 'Accept: application/vnd.pypi.simple.v1+json' \
  https://pypi.org/simple/travis234-ghost-mcp/ \
  | jq -e --arg reason "$RETIRE_YANK_REASON" \
    '.meta["api-version"]
     and ([.files[] | select(.yanked == $reason)] | length == 2)
     and ([.files[] | select(.yanked == false)] | length == 0)'
```

Expected: both the wheel and source distribution are yanked with the exact
reason in JSON and Simple API metadata.

- [ ] **Step 4: Prove an ordinary unpinned acquisition does not select 0.1.0**

```bash
RETIRE_YANK_CHECK="$(mktemp -d /tmp/travis234-yank-check.XXXXXX)"
if python3.13 -m pip download --no-deps \
  --dest "$RETIRE_YANK_CHECK" travis234-ghost-mcp; then
  echo "unexpectedly resolved yanked Ghost release" >&2
  exit 1
fi
test -z "$(find "$RETIRE_YANK_CHECK" -type f -print -quit)"
```

Expected: pip reports the yanked version as ignored, exits nonzero, and
downloads nothing. Do not delete or install the exact pinned release.

### Task 11: Uninstall the local add-on, finalize evidence, and update main

**Files:**
- Modify: `docs/verification/main-ghost-mcp-retirement.md`
- Local package mutation: remove `travis234-ghost-mcp` only.
- Preserve: `/Users/htooayelwin/.travis234/ghost-mcp`.

**Interfaces:**
- Consumes: all verified public registry state and the locally installed
  Travis234 package inventory.
- Produces: no installed Ghost add-on, preserved state, a final evidence commit,
  and `origin/main` containing that evidence.

- [ ] **Step 1: Resolve the exact installed package before mutation**

```bash
travis234 list
```

Expected: `travis234-ghost-mcp (0.1.0)` and
`travis234-mcp-adapter` are listed. Record only package names, versions, scopes,
and install paths; do not inspect state contents.

- [ ] **Step 2: Ensure no package-owned Ghost process remains**

Resolve the Ghost install path from `travis234 list`, confirm it is beneath
`/Users/htooayelwin/.travis234/agent/packages/`, and query only matching PIDs:

```bash
pgrep -f '/Users/htooayelwin/.travis234/agent/packages/travis234-ghost-mcp-.*/travis234_ghost_mcp/bin/ghost' || true
```

Expected: no PID. If an exact package-owned PID exists, stop this task and ask
the user to close the active Travis234 session, then repeat the same exact-path
query. Do not kill, match, or inspect broad `ghost` or Python process names.

- [ ] **Step 3: Remove only the add-on and preserve state**

```bash
if test -d /Users/htooayelwin/.travis234/ghost-mcp; then
  RETIRE_GHOST_STATE_WAS_PRESENT=1
elif test -e /Users/htooayelwin/.travis234/ghost-mcp; then
  echo "unexpected non-directory Ghost state path" >&2
  exit 1
else
  RETIRE_GHOST_STATE_WAS_PRESENT=0
fi
travis234 remove travis234-ghost-mcp
travis234 list
if test "$RETIRE_GHOST_STATE_WAS_PRESENT" -eq 1; then
  test -d /Users/htooayelwin/.travis234/ghost-mcp
else
  test ! -e /Users/htooayelwin/.travis234/ghost-mcp
fi
```

Expected: Ghost is absent, the general adapter remains, and the state path has
exactly the same presence/absence as before uninstall. Do not create, enumerate,
migrate, or delete its contents.

- [ ] **Step 4: Complete the verification record with actual public and local evidence**

Append exact PyPI versions/yank reason, npm version and dist-tag, GHCR candidate
and production digest equality, workflow run IDs/results, `RELEASE_SOURCE_SHA`,
local uninstall result, retained-state existence, and remote-branch absence.
Replace the earlier statements that publication, yanking, and uninstall had not
occurred. Leave one explicit statement that local branch deletion is the sole
remaining gate; leave no other stale or contradictory status text.

- [ ] **Step 5: Commit the final evidence on the still-local temporary branch**

```bash
git add docs/verification/main-ghost-mcp-retirement.md
git diff --cached --check
git commit -m "docs: verify Ghost MCP retirement"
```

- [ ] **Step 6: Fast-forward main to the evidence commit and push only main**

```bash
test "$(gh api user --jq .login)" = "htooayelwinict"
git -C /Users/htooayelwin/orca/travis234 fetch origin
test "$(git -C /Users/htooayelwin/orca/travis234 rev-parse main)" = \
  "$(git -C /Users/htooayelwin/orca/travis234 rev-parse origin/main)"
git -C /Users/htooayelwin/orca/travis234 merge --ff-only \
  htooakalewis/mcp-addons
git -C /Users/htooayelwin/orca/travis234 push origin main:main
git -C /Users/htooayelwin/orca/travis234 fetch origin
test "$(git rev-parse htooakalewis/mcp-addons)" = \
  "$(git -C /Users/htooayelwin/orca/travis234 rev-parse origin/main)"
git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons
```

Expected: final evidence is on `origin/main`, the exact SHAs match, and the
remote temporary branch remains absent.

### Task 12: Delete the temporary branch locally and prove it cannot remain remotely

**Files:**
- Modify after deletion: `docs/verification/main-ghost-mcp-retirement.md`
- Git refs: remove only `htooakalewis/mcp-addons`; branch deletion is the final
  destructive step.

**Interfaces:**
- Consumes: clean final `origin/main`, a fully merged local temporary branch,
  completed public retirement, and completed local uninstall.
- Produces: detached linked worktree at `origin/main`, no local temporary
  branch, no remote temporary branch, and committed proof on final `main`.

- [ ] **Step 1: Prove cleanup preconditions**

```bash
git status --short
git -C /Users/htooayelwin/orca/travis234 status --short
test "$(gh api user --jq .login)" = "htooayelwinict"
git -C /Users/htooayelwin/orca/travis234 fetch origin
test "$(git -C /Users/htooayelwin/orca/travis234 rev-parse main)" = \
  "$(git -C /Users/htooayelwin/orca/travis234 rev-parse origin/main)"
git -C /Users/htooayelwin/orca/travis234 \
  merge-base --is-ancestor htooakalewis/mcp-addons main
```

Expected: both worktrees are clean, main equals origin/main, and ancestry exits
zero. Do not delete the branch if any precondition fails.

- [ ] **Step 2: Remove the exact remote ref only if it unexpectedly exists**

```bash
if test -n "$(git ls-remote --heads origin \
    refs/heads/htooakalewis/mcp-addons)"; then
  git push origin --delete htooakalewis/mcp-addons
fi
git ls-remote --heads origin refs/heads/htooakalewis/mcp-addons
```

Expected: final query is empty. The command targets no other remote ref.

- [ ] **Step 3: Detach this linked worktree and delete the exact local branch**

```bash
git switch --detach origin/main
git -C /Users/htooayelwin/orca/travis234 branch -d \
  htooakalewis/mcp-addons
```

Expected: the linked worktree is detached at the final `origin/main` commit and
safe `-d` deletion succeeds because ancestry was proven. Do not use `-D`.

- [ ] **Step 4: Prove branch absence and commit that final evidence from detached HEAD**

```bash
test -z "$(git branch --list htooakalewis/mcp-addons)"
test -z "$(git ls-remote --heads origin \
  refs/heads/htooakalewis/mcp-addons)"
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
```

Update the verification record's sole pending line with the actual empty local
and remote branch results and the detached base SHA, then commit from detached
HEAD without creating another branch:

```bash
git add docs/verification/main-ghost-mcp-retirement.md
git diff --cached --check
git commit -m "docs: record temporary branch removal"
BRANCH_PROOF_SHA="$(git rev-parse HEAD)"
```

Expected: the evidence commit has the previous `origin/main` as its parent and
contains no application or release artifact change.

- [ ] **Step 5: Fast-forward remote and local main to the detached evidence commit**

```bash
test "$(gh api user --jq .login)" = "htooayelwinict"
git push origin HEAD:main
git fetch origin
test "$BRANCH_PROOF_SHA" = "$(git rev-parse origin/main)"
git -C /Users/htooayelwin/orca/travis234 fetch origin
git -C /Users/htooayelwin/orca/travis234 merge --ff-only origin/main
```

Expected: only `main` is pushed; local main and origin/main now include the
branch-removal proof.

- [ ] **Step 6: Run the final branch, registry, and worktree proof**

```bash
test -z "$(git branch --list htooakalewis/mcp-addons)"
test -z "$(git ls-remote --heads origin \
  refs/heads/htooakalewis/mcp-addons)"
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test "$(git -C /Users/htooayelwin/orca/travis234 rev-parse main)" = \
  "$(git rev-parse origin/main)"
RELEASE_SOURCE_SHA="$(sed -nE \
  's/^- Release source SHA: `([0-9a-f]{40})`$/\1/p' \
  docs/verification/main-ghost-mcp-retirement.md)"
test "$(git diff --name-only "$RELEASE_SOURCE_SHA" origin/main)" = \
  "docs/verification/main-ghost-mcp-retirement.md"
git status --short --branch
git -C /Users/htooayelwin/orca/travis234 status --short --branch
```

Expected: no local or remote temporary branch, both worktrees clean, this
worktree detached at final origin/main, and the main worktree on the same
commit. Only after this proof may the Ghost retirement be reported complete.
