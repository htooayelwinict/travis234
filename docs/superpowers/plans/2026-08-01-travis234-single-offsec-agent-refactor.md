# Travis234 Single OffSec Agent Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `offsec-agent` from clean `main` as one Travis234 OffSec product with an OffSec-native prompt, optional target context, writable tool-capable subagents, and a first-class tmux tool while preserving the proven agent loop, compaction layers, and provider runtime.

**Architecture:** Archive the existing dual-profile prototype, reset only `offsec-agent` to the current local `main`, and cherry-pick this design and plan onto that clean base. Specialize the existing `CodingApp`/`AgentSession` composition through product metadata, prompt inputs, built-in tools, and child-session wiring; do not create a second loop, profile router, manifest runtime, or policy engine.

**Tech Stack:** Python 3.13, pytest, Travis234 `AgentSession`/tool definitions/process service, tmux CLI, Node 20/npm launcher, Kali rolling, Docker/GHCR, `uv build`.

## Global Constraints

- Work only in `/Users/htooayelwin/lewis/travis234-offsec` on branch `offsec-agent`; never reset, merge, commit on, or push `main` while executing this plan.
- Preserve the current prototype as `archive/offsec-agent-v1-20260801` plus a named stash containing all tracked and untracked dirty files before resetting the branch.
- Product identity is `Travis234 OffSec`; Python distribution is `travis234-offsec`; npm package is `@htooayelwinict/travis234-offsec`; executable remains `travis234`; Python import package remains `travis`; state remains exclusively under `~/.travis234`.
- There is one default OffSec agent. Do not add `--profile`, `--agent-profile`, engagement manifests, systemd containment, worker users, CTFd adapters, fixture adapters, case boards, or a second runtime loop.
- `--target` is optional, repeatable operator context. It must not create routes, firewall rules, manifests, verifier adapters, or alternate working directories.
- Default internal-child tools are exactly `read`, `grep`, `find`, `ls`, `bash`, `process`, `edit`, `write`, and `tmux`; child sandbox metadata defaults to `workspace_write`; children never receive subagent tools.
- Retain maximum three active children, depth one, maximum three model-spawned children per turn, duplicate-spawn suppression, timeout/cancellation, bounded result summaries, and paged result expansion.
- Use `bash` for finite commands, `bash` plus `process` for current-session interactive PTYs, and `tmux` for listeners, reverse connections, OOB callbacks, relays, servers, long waits, or work that must survive turns.
- tmux sessions use `travis234-{12-character workspace SHA-256 prefix}-{validated logical name}`, direct argument vectors, bounded output, explicit stop, and no automatic installation or privilege request.
- Do not modify any file under `travis/agent/`, `travis/compaction/`, or `travis/ai/providers/`.
- Preserve core loop ordering, iteration budgeting, bounded parallel execution, session persistence, extensions, skills, TUI behavior, and provider-neutral model selection.
- Add a failing regression before every behavior change and commit after each independently passing task.
- Do not publish to PyPI, npm, or GHCR and do not merge into `main` as part of this implementation plan.

---

## File and responsibility map

### New files

- `travis/coding_agent/tools/tmux.py`: schema, namespace, validation, direct-argv tmux execution, and `ToolDefinition`/`AgentTool` factories.
- `tests/test_tmux_tool.py`: fake-runner unit coverage and optional real tmux smoke.
- `tests/test_offsec_product_contract.py`: single-product identity, forbidden legacy surface, and red-zone assertions.
- `tests/test_offsec_tui_protocol.py`: structural test for the seven-scenario manual TUI protocol.
- `docs/offsec/tui-test-protocol.md`: exact installed-entrypoint qualification procedure and seven prompts.
- `docs/offsec/manual.md`: host-native and Kali/container operating manual with bash/process/tmux guidance.

### Modified files

- `pyproject.toml`, `package.json`, `packages/travis234-cli/package.json`: specialized distribution identity while preserving version and executable.
- `travis/cli.py`: OffSec CLI description, repeatable target argument, target validation, and propagation.
- `travis/app.py`: immutable target storage and propagation to every app-created session.
- `travis/coding_agent/agent_session.py`: target state accepted by the composed runtime.
- `travis/coding_agent/agent_session_services.py`: SDK factory propagation of `targets`/`target` without adding a separate SDK runtime.
- `travis/coding_agent/system_prompt.py`: full OffSec operating contract, target rendering, and capability-derived tool strategy.
- `travis/coding_agent/session_tooling.py`: target prompt composition, tmux options, and default tmux activation.
- `travis/coding_agent/session_extensions.py`: target-preserving prompt option snapshots.
- `travis/coding_agent/subagents.py`: shared OffSec child catalog, workspace-write default, prompt contract, and Codex backend alignment.
- `travis/coding_agent/session_types.py`: writable child defaults and model-facing delegation description.
- `travis/coding_agent/session_subagents.py`: removal of mutation rejection, child-specific process ownership, process-service sharing, inherited targets, and changed-file reporting.
- `travis/coding_agent/subagent_trace.py`: exact successful `edit`/`write` path metadata for result packs without exposing full tool arguments.
- `travis/coding_agent/tools/__init__.py`: tmux registry, factory options, and tool bundles.
- `skills/subagent-delegation/SKILL.md` and `packages/travis234-cli/skills/subagent-delegation/SKILL.md`: remove read-only contradictions and document disjoint writable ownership plus terminal strategy.
- `Dockerfile`, `Dockerfile.release`: Kali rolling, Python virtual environment, Node 20, tmux, and baseline CTF utilities.
- `packages/travis234-cli/bin/travis234.js`: specialized GHCR image.
- `packages/travis234-cli/test/travis234-cli.test.js`: npm identity, image, Kali tool, tmux, and package assertions.
- `.github/workflows/travis234-release-image.yml`: specialized image/ref naming and unchanged test-before-push gates.
- `evals/container_smoke.py`, `evals/container_qualification.py`: installed OffSec identity, tmux action, baseline-tool, and process-cleanup checks.
- `README.md`, `packages/travis234-cli/README.md`: beginner-first one-command usage and distribution contract.
- Existing focused test modules: adapt assertions from coding/read-only defaults to OffSec/workspace-write defaults without weakening unrelated runtime coverage.

### Explicitly untouched files

- `travis/agent/**`
- `travis/compaction/**`
- `travis/ai/providers/**`

---

### Task 0: Archive the prototype and rebuild the branch from clean main

**Files:**
- Preserve through archive ref and stash: every currently committed and dirty file on `offsec-agent`
- Reapply after reset: `docs/superpowers/specs/2026-08-01-travis234-single-offsec-agent-refactor-design.md`
- Reapply after reset: `docs/superpowers/plans/2026-08-01-travis234-single-offsec-agent-refactor.md`

**Interfaces:**
- Consumes: current `offsec-agent` HEAD containing the committed design and this committed plan; current local `main` ref.
- Produces: a recoverable prototype archive and an `offsec-agent` tree equal to `main` except for the two approved documents.

- [ ] **Step 1: Record authoritative refs and dirty paths**

Run:

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse main
git status --short
git diff --name-only main...HEAD
```

Expected: branch is `offsec-agent`; the dirty beginner experiment is visible; `main` is not checked out here.

- [ ] **Step 2: Create the committed-history archive**

Run:

```bash
git show-ref --verify --quiet refs/heads/archive/offsec-agent-v1-20260801 && {
  printf '%s\n' 'archive/offsec-agent-v1-20260801 already exists; verify it before continuing'
  exit 1
}
git branch archive/offsec-agent-v1-20260801 HEAD
git show --no-patch --oneline archive/offsec-agent-v1-20260801
```

Expected: the archive ref points at the pre-reset branch HEAD.

- [ ] **Step 3: Stash every dirty tracked and untracked prototype file**

Run:

```bash
git stash push --include-untracked -m 'offsec-agent-v1-dirty-2026-08-01'
git stash list --format='%gd %s' | rg 'offsec-agent-v1-dirty-2026-08-01'
git status --short
```

Expected: one matching stash exists and the worktree is clean. Do not pop this stash onto the rebuilt branch.

- [ ] **Step 4: Capture document commits, reset only this branch, and reapply the documents**

Run:

```bash
design_commit=$(git rev-parse 95b1169^{commit})
plan_commit=$(git rev-parse HEAD^{commit})
main_commit=$(git rev-parse main^{commit})
git reset --hard "$main_commit"
git cherry-pick "$design_commit" "$plan_commit"
```

Expected: reset and cherry-picks succeed without moving `main`.

- [ ] **Step 5: Prove the baseline and red zones**

Run:

```bash
git diff --name-only main...HEAD
git diff --exit-code main -- travis/agent travis/compaction travis/ai/providers
test ! -e travis/offsec
test -z "$(git ls-files 'tests/offsec/**')"
git status --short
```

Expected: only the approved design and plan differ from `main`; red-zone diff is empty; `travis/offsec` and old `tests/offsec` are absent; worktree is clean.

---

### Task 1: Establish the single Travis234 OffSec product identity

**Files:**
- Create: `tests/test_offsec_product_contract.py`
- Modify: `pyproject.toml:5-29`
- Modify: `package.json:1-10`
- Modify: `packages/travis234-cli/package.json:1-25`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js:22-26`
- Modify: `travis/cli.py:1,318-336`
- Modify: `evals/container_smoke.py:37-55,153-164`

**Interfaces:**
- Consumes: clean main-based source tree from Task 0.
- Produces: Python distribution `travis234-offsec`, npm distribution `@htooayelwinict/travis234-offsec`, executable `travis234`, and core CLI help with no profile/manifest surface.

- [ ] **Step 1: Write the failing product-contract tests**

Add:

```python
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from travis import cli


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_identity_is_single_offsec_product() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    npm = json.loads(
        (ROOT / "packages/travis234-cli/package.json").read_text(encoding="utf-8")
    )

    assert project["name"] == "travis234-offsec"
    assert project["scripts"] == {"travis234": "travis.cli:main"}
    assert npm["name"] == "@htooayelwinict/travis234-offsec"
    assert npm["bin"] == {"travis234": "bin/travis234.js"}


def test_core_cli_is_offsec_native_without_legacy_profiles() -> None:
    help_text = cli._build_parser(include_prompt=True).format_help()

    assert "Travis234 OffSec" in help_text
    for forbidden in (
        "--profile",
        "--agent-profile",
        "--engagement",
        "--challenge",
        "--ctfd-url",
        "--ctf-fixture-root",
        "--offsec-worker-user",
    ):
        assert forbidden not in help_text


def test_removed_dual_profile_tree_does_not_return() -> None:
    assert not (ROOT / "travis/offsec").exists()
    assert not (ROOT / "tests/offsec").exists()
```

- [ ] **Step 2: Run the tests and confirm the identity assertion fails**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_offsec_product_contract.py
```

Expected: failure reports `travis234` instead of `travis234-offsec` and coding-agent CLI wording.

- [ ] **Step 3: Change only the public identity surfaces**

Set these exact values:

```toml
# pyproject.toml
[project]
name = "travis234-offsec"
version = "2.3.5"
description = "Terminal OffSec agent with persistent sessions, tool-capable subagents, and long-lived terminal workflows."

[project.scripts]
travis234 = "travis.cli:main"
```

```json
// package.json
{
  "name": "travis234-offsec-workspace",
  "version": "2.3.5",
  "private": true
}
```

```json
// packages/travis234-cli/package.json
{
  "name": "@htooayelwinict/travis234-offsec",
  "version": "2.3.5",
  "description": "Docker launcher for the Travis234 OffSec terminal agent.",
  "bin": {"travis234": "bin/travis234.js"}
}
```

Change the CLI module description and parser description to `Travis234 OffSec terminal agent`. In `evals/container_smoke.py`, rename the synthetic extension flag from `profile` to `smoke-channel` and assert `--smoke-channel` so release smoke output cannot be mistaken for a core profile switch.

Change the first npm launcher test to assert:

```javascript
assert.equal(packageJson.name, "@htooayelwinict/travis234-offsec");
assert.deepEqual(packageJson.bin, { travis234: "bin/travis234.js" });
```

- [ ] **Step 4: Run focused identity and existing CLI tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_offsec_product_contract.py \
  tests/test_cli.py
npm --prefix packages/travis234-cli test
```

Expected: Python and npm tests pass; image expectations still match unchanged main-based Docker/launcher files until Task 10.

- [ ] **Step 5: Commit the identity cutover**

```bash
git add pyproject.toml package.json packages/travis234-cli/package.json travis/cli.py \
  packages/travis234-cli/test/travis234-cli.test.js evals/container_smoke.py \
  tests/test_offsec_product_contract.py
git commit -m "refactor(offsec): establish single product identity"
```

---

### Task 2: Add optional repeatable target context across CLI, app, sessions, and SDK

**Files:**
- Modify: `travis/cli.py:41-58,318-461,690-757`
- Modify: `travis/app.py:94-127,128-180,234-315`
- Modify: `travis/coding_agent/agent_session.py:125-167,168-245,390-420`
- Modify: `travis/coding_agent/agent_session_services.py:125-239`
- Modify: `travis/coding_agent/system_prompt.py:14-24,32-91,137-144`
- Modify: `travis/coding_agent/session_tooling.py:225-246`
- Modify: `travis/coding_agent/session_extensions.py:689-709`
- Test: `tests/test_cli.py`
- Test: `tests/test_coding_resources_and_services.py`
- Test: `tests/test_app_integration.py`

**Interfaces:**
- Consumes: existing `CodingApp` and `AgentSession` creation pipelines.
- Produces: `normalize_targets(values: Sequence[str] | None) -> tuple[str, ...]`; `BuildSystemPromptOptions.targets`; `CodingApp(..., targets=...)`; `AgentSession(..., targets=...)`; CLI `--target TARGET`.

- [ ] **Step 1: Write failing target validation and propagation tests**

Add tests with these assertions:

```python
def test_cli_propagates_repeatable_targets_to_app(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    _install_session_cli_fakes(monkeypatch, captured)
    assert cli.main([
        "--cwd", str(tmp_path), "--no-session", "--plain",
        "--target", "10.10.10.10", "--target", "https://lab.local/path", "inspect",
    ]) == 0
    assert captured["app_kwargs"]["targets"] == (
        "10.10.10.10", "https://lab.local/path"
    )


def test_cli_rejects_blank_target() -> None:
    with pytest.raises(SystemExit, match="2"):
        cli._build_parser(include_prompt=True).parse_args(["--target", "   ", "inspect"])
```

Also assert `normalize_targets(None) == ()`, duplicates retain first-seen order, 33 targets fail with `at most 32 targets`, and a 2,049-character target fails with the documented 2,048-character limit.

```python
def test_agent_session_projects_targets_without_changing_cwd(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        targets=("10.10.10.10", "https://lab.local/a?x=1&y=2"),
    )
    try:
        assert "Operator-authorized targets:" in session.system_prompt
        assert "- 10.10.10.10" in session.system_prompt
        assert "https://lab.local/a?x=1&amp;y=2" in session.system_prompt
        assert f"Current working directory: {tmp_path}" in session.system_prompt
    finally:
        session.shutdown()
```

Also add an app-session switch test asserting a new/resumed app-created session retains the same `targets` tuple.

- [ ] **Step 2: Run the three focused tests and verify constructor/argument failures**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_cli.py -k target \
  tests/test_coding_resources_and_services.py -k target \
  tests/test_app_integration.py -k target
```

Expected: failures report unknown `--target`, unexpected `targets`, or missing prompt target context.

- [ ] **Step 3: Add one normalization contract in `system_prompt.py`**

Add these exact public values and behavior:

```python
from collections.abc import Sequence
from html import escape

MAX_TARGETS = 32
MAX_TARGET_LENGTH = 2048


def normalize_targets(values: Sequence[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values or ():
        if not isinstance(value, str) or not value.strip():
            raise ValueError("target must be a non-empty string")
        target = value.strip()
        if len(target) > MAX_TARGET_LENGTH:
            raise ValueError(f"target must be at most {MAX_TARGET_LENGTH} characters")
        if target not in normalized:
            normalized.append(target)
    if len(normalized) > MAX_TARGETS:
        raise ValueError(f"at most {MAX_TARGETS} targets may be supplied")
    return tuple(normalized)


def _targets_section(targets: Sequence[str]) -> str:
    if not targets:
        return ""
    rendered = "\n".join(f"- {escape(target)}" for target in targets)
    return (
        "\n\nOperator-authorized targets:\n"
        f"{rendered}\n"
        "Treat these labels as engagement context, not as proof of reachability or impact."
    )
```

Add `targets: tuple[str, ...] = ()` to `BuildSystemPromptOptions`, normalize it, and append `_targets_section(...)` before the date/cwd footer in both default and custom-prompt branches.

Render with `_targets_section(normalize_targets(options.targets))` so direct SDK/resource callers receive the same bounds as CLI callers.

- [ ] **Step 4: Add CLI parsing and normalize once at startup**

Add:

```python
def _target_arg(value: str) -> str:
    target = value.strip()
    if not target:
        raise argparse.ArgumentTypeError("target must be non-empty")
    if len(target) > MAX_TARGET_LENGTH:
        raise argparse.ArgumentTypeError(
            f"target must be at most {MAX_TARGET_LENGTH} characters"
        )
    return target
```

Register:

```python
parser.add_argument(
    "--target",
    dest="targets",
    action="append",
    type=_target_arg,
    metavar="TARGET",
    help="Operator-authorized target label; may be repeated",
)
```

In `main()`, call `normalize_targets(args.targets)` inside the existing parser-error boundary and pass the result as `targets=targets` to `CodingApp`.

- [ ] **Step 5: Propagate immutable targets through every session constructor**

Add the keyword-only parameter `targets: tuple[str, ...] | list[str] | None = None` to both `CodingApp.__init__` and `_SessionRuntime.__init__`, adjacent to their existing prompt/session configuration parameters, then assign this exact normalized value in each constructor:

```python
self.targets = normalize_targets(targets)
```

Pass `targets=self.targets` from `CodingApp._create_session()` to `AgentSession`, from `AgentSession.create_agent_session()`, and from `agent_session_services.create_agent_session_from_services()` using both `targets` and singular `target` SDK options:

```python
raw_targets = options.get("targets", options.get("target"))
if isinstance(raw_targets, str):
    raw_targets = [raw_targets]
```

Pass `targets=normalize_targets(raw_targets)` in the `AgentSession` constructor call in that factory.

Add `targets=self.targets` to both `BuildSystemPromptOptions(...)` construction sites in `session_tooling.py` and `session_extensions.py`.

- [ ] **Step 6: Run focused target tests and the SDK factory tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_cli.py -k target \
  tests/test_coding_resources_and_services.py \
  tests/test_app_integration.py -k 'target or session'
```

Expected: all selected tests pass and target context survives session replacement.

- [ ] **Step 7: Commit target context**

```bash
git add travis/cli.py travis/app.py travis/coding_agent/agent_session.py \
  travis/coding_agent/agent_session_services.py travis/coding_agent/system_prompt.py \
  travis/coding_agent/session_tooling.py travis/coding_agent/session_extensions.py \
  tests/test_cli.py tests/test_coding_resources_and_services.py tests/test_app_integration.py
git commit -m "feat(offsec): add operator target context"
```

---

### Task 3: Replace the coding-assistant prompt with the OffSec operating contract

**Files:**
- Modify: `travis/coding_agent/system_prompt.py:26-91`
- Test: `tests/test_coding_resources_and_services.py:167-245`
- Test: `tests/test_offsec_product_contract.py`

**Interfaces:**
- Consumes: `BuildSystemPromptOptions` plus targets from Task 2.
- Produces: `_OFFSEC_PREAMBLE`, `_tool_strategy(tools: Sequence[str]) -> str`, and a default prompt with no coding-assistant identity.

- [ ] **Step 1: Replace old prompt assertions with a failing OffSec contract test**

Add:

```python
def test_default_system_prompt_is_complete_offsec_contract(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd=str(tmp_path),
            selected_tools=[
                "read", "grep", "find", "ls", "bash", "process",
                "tmux", "edit", "write", "spawn_subagent",
            ],
            tool_snippets={"tmux": "Manage named long-lived tmux sessions"},
        )
    )

    for required in (
        "Travis234 OffSec",
        "operator-authorized CTFs, labs, and assessments",
        "Facts",
        "Hypotheses",
        "Failed attempts",
        "Use bash for finite commands",
        "Use bash plus process for interactive programs",
        "Use tmux for listeners, reverse connections, OOB callbacks, relays",
        "Do not claim a flag, shell, vulnerability, credential, or impact",
        "running tmux sessions",
    ):
        assert required in prompt
    assert "expert coding assistant" not in prompt
    assert len(prompt) < 16_000
```

Keep the existing custom-prompt, context-files, skills, tool-snippets, current-date, and cwd tests unchanged.

- [ ] **Step 2: Run the prompt tests and verify the identity failure**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_coding_resources_and_services.py -k system_prompt \
  tests/test_offsec_product_contract.py
```

Expected: the new contract test fails on the old coding-assistant preamble.

- [ ] **Step 3: Add the exact OffSec identity and investigation contract**

Replace `_PREAMBLE` with:

```python
_OFFSEC_PREAMBLE = """You are Travis234 OffSec, an expert OffSec agent operating inside the Travis234 agent harness for operator-authorized CTFs, labs, and assessments.

Authorization and truth:
- Treat operator-provided targets and engagement context as authoritative scope.
- Do not invent scope, credentials, findings, successful exploitation, or effects.

Investigation loop:
- Observe the environment before concluding.
- Track Facts, Hypotheses, Tests, Evidence, and Failed attempts in the conversation or workspace artifacts.
- Prefer the cheapest test that distinguishes competing hypotheses.
- Pivot when observed evidence contradicts a hypothesis.
- Treat exploit delivery and command execution as attempts until their effects are observed.

Evidence and completion:
- Preserve exact commands, relevant output, paths, and artifacts.
- Separate confirmed findings from candidates and speculation.
- Do not claim a flag, shell, vulnerability, credential, or impact without observed evidence.
- Finish with confirmed results, evidence references, failed approaches, running tmux sessions, and blockers."""
```

- [ ] **Step 4: Make terminal guidance capability-derived**

Add:

```python
def _tool_strategy(tools: Sequence[str]) -> str:
    selected = set(tools)
    lines = ["", "", "Tool strategy:"]
    if "bash" in selected:
        lines.append("- Use bash for finite commands that should finish promptly.")
    if {"bash", "process"} <= selected:
        lines.append(
            "- Use bash plus process for interactive programs that need a PTY, "
            "follow-up input, control sequences, polling, or termination during this session."
        )
    if "tmux" in selected:
        lines.append(
            "- Use tmux for listeners, reverse connections, OOB callbacks, relays, servers, "
            "long waits, and work that must survive turns; capture evidence and explicitly stop it when finished."
        )
    if selected & {"read", "grep", "find", "ls"}:
        lines.append("- Use read, grep, find, and ls for evidence gathering when available.")
    if selected & {"edit", "write"}:
        lines.append("- Use edit and write for scripts, payloads, wordlists, notes, and reports.")
    if "spawn_subagent" in selected:
        lines.append(
            "- Delegate independent objectives to subagents with disjoint file ownership; "
            "review their evidence and do not duplicate the same work in the parent."
        )
    return "\n".join(lines) if len(lines) > 3 else ""
```

Compose `_OFFSEC_PREAMBLE`, available tool snippets, `_tool_strategy(tools)`, registered prompt guidelines, documentation, appended prompt, project context, skills, targets, date, and cwd in that order. The custom-prompt branch remains an explicit complete override and still receives context, skills, targets, date, and cwd.

When `selected_tools is None`, use `['read', 'bash', 'tmux', 'edit', 'write']` as the prompt-only fallback catalog so direct `build_system_prompt()` callers receive the same default strategy as normal sessions.

- [ ] **Step 5: Run all prompt/resource tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_coding_resources_and_services.py \
  tests/test_offsec_product_contract.py
```

Expected: all tests pass; no default prompt contains `expert coding assistant`.

- [ ] **Step 6: Commit the prompt replacement**

```bash
git add travis/coding_agent/system_prompt.py \
  tests/test_coding_resources_and_services.py tests/test_offsec_product_contract.py
git commit -m "feat(offsec): replace default agent prompt"
```

---

### Task 4: Implement the first-class tmux tool with a fakeable direct-argv boundary

**Files:**
- Create: `travis/coding_agent/tools/tmux.py`
- Create: `tests/test_tmux_tool.py`

**Interfaces:**
- Consumes: `WorkspaceCapability`, `ToolDefinition`, `wrap_tool_definition`, and `AgentToolResult`.
- Produces: `TMUX_SCHEMA`; `TmuxOperations`; `create_tmux_tool_definition(cwd, operations=None, workspace=None) -> ToolDefinition`; `create_tmux_tool(cwd, operations=None, workspace=None) -> AgentTool`.

- [ ] **Step 1: Write failing fake-runner tests for all five actions**

Use an operation recorder with `which("tmux") -> "/usr/bin/tmux"` and deterministic `CompletedProcess` values. In the test, compute the expected resolved name with:

```python
digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:12]
session_name = f"travis234-{digest}-callback"
```

Then assert these exact argument vectors:

```python
[
    "/usr/bin/tmux", "has-session", "-t", session_name,
]
[
    "/usr/bin/tmux", "new-session", "-d", "-s", session_name,
    "-c", str(tmp_path.resolve()), "--", "nc -lvnp 4444",
]
[
    "/usr/bin/tmux", "send-keys", "-t", session_name,
    "-l", "--", "whoami",
]
[
    "/usr/bin/tmux", "send-keys", "-t", session_name, "Enter",
]
[
    "/usr/bin/tmux", "capture-pane", "-p", "-t", session_name,
    "-S", "-200",
]
[
    "/usr/bin/tmux", "list-sessions", "-F", "#{session_name}",
]
[
    "/usr/bin/tmux", "kill-session", "-t", session_name,
]
```

Assert `list` filters out `foreign-session`; `stop` returns success without `kill-session` when `has-session` reports absent; every result includes `action` and resolved `sessionName` where applicable.

- [ ] **Step 2: Write failing validation/error tests**

Cover this exact matrix:

```python
@pytest.mark.parametrize("name", ["", "two words", "bad:name", "../escape", "x" * 49])
def test_tmux_rejects_invalid_names(name, tmp_path):
    definition, recorder = recorded_tmux_definition(tmp_path)
    with pytest.raises(ValueError, match="tmux name must match"):
        definition.execute("call-1", {"action": "capture", "name": name})
    assert recorder.calls == []

@pytest.mark.parametrize("lines", [True, 0, -1, 2001, "200"])
def test_tmux_rejects_invalid_capture_lines(lines, tmp_path):
    definition, recorder = recorded_tmux_definition(tmp_path)
    with pytest.raises(ValueError, match="lines must be an integer from 1 to 2000"):
        definition.execute(
            "call-1", {"action": "capture", "name": "callback", "lines": lines}
        )
    assert recorder.calls == []
```

Also assert:

- unknown action and unexpected fields fail before runner invocation;
- `start` requires non-empty `name` and `command`;
- `send` requires string `input` and boolean `enter`;
- `cwd` must exist and be a directory;
- missing tmux reports `tmux executable not found; install tmux and retry`;
- duplicate start reports the resolved live session;
- send/capture on an absent session report the resolved missing session;
- capture truncates successful stdout to at most 2,000 lines and 51,200 UTF-8 bytes and reports truncation metadata in `details`;
- stderr returned from a failed tmux command is truncated to 4,000 characters and 20 lines.

- [ ] **Step 3: Run the new test module and verify import failure**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_tmux_tool.py
```

Expected: collection fails because `travis.coding_agent.tools.tmux` does not exist.

- [ ] **Step 4: Implement schema, namespace, and execution operations**

Create the module with these exact constants and types:

```python
TMUX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
TMUX_CAPTURE_LINES_DEFAULT = 200
TMUX_CAPTURE_LINES_MAX = 2000
TMUX_CAPTURE_BYTES_MAX = 50 * 1024
TMUX_ERROR_MAX_LINES = 20
TMUX_ERROR_MAX_BYTES = 4000

TMUX_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["start", "send", "capture", "list", "stop"]},
        "name": {"type": "string", "description": "Logical tmux session name"},
        "command": {"type": "string", "description": "Command for a detached new session"},
        "cwd": {"type": "string", "description": "Workspace directory for start; defaults to cwd"},
        "input": {"type": "string", "description": "Literal keys for send"},
        "enter": {"type": "boolean", "description": "Send Enter after literal input; defaults to true"},
        "lines": {"type": "integer", "minimum": 1, "maximum": 2000},
    },
    "required": ["action"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TmuxOperations:
    which: Callable[[str], str | None]
    run: Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=get_shell_env(),
    )


DEFAULT_TMUX_OPERATIONS = TmuxOperations(which=shutil.which, run=_default_run)
```

Import `get_shell_env` from `travis.coding_agent.tools.bash` and `truncate_tail`/`truncation_to_details` from `travis.coding_agent.tools.truncate`. Treat `tmux list-sessions` exit code `1` as an empty server only when stderr reports no server; propagate every other nonzero result. For capture, apply `truncate_tail(stdout, max_lines=lines, max_bytes=TMUX_CAPTURE_BYTES_MAX)` and place `truncation_to_details(...)` in `details["truncation"]` when truncated. Apply a separate tail bound of 20 lines and 4,000 bytes to failure stderr.

Resolve the namespace exactly as:

```python
def _workspace_prefix(workspace: WorkspaceCapability) -> str:
    digest = hashlib.sha256(str(workspace.root).encode("utf-8")).hexdigest()[:12]
    return f"travis234-{digest}-"


def _session_name(workspace: WorkspaceCapability, name: object) -> str:
    if not isinstance(name, str) or TMUX_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("tmux name must match [A-Za-z0-9][A-Za-z0-9_-]{0,47}")
    return f"{_workspace_prefix(workspace)}{name}"
```

Implement per-action allowed fields exactly:

```python
_ACTION_FIELDS = {
    "start": {"action", "name", "command", "cwd"},
    "send": {"action", "name", "input", "enter"},
    "capture": {"action", "name", "lines"},
    "list": {"action"},
    "stop": {"action", "name"},
}
```

Before executing, reject any key not in the selected set. Resolve `start.cwd` with `workspace.resolve(str(args.get("cwd", ".")), "execute")`, then require `is_dir()`. Pass model-provided commands only as the final single `new-session` argument; never interpolate them into a wrapper shell command.

- [ ] **Step 5: Return stable tool details and prompt guidance**

Create the definition with:

```python
return ToolDefinition(
    name="tmux",
    label="tmux",
    description=(
        "Manage named, detached tmux sessions for long-lived terminal work. "
        "Actions: start, send literal input, capture recent output, list Travis234 sessions, and stop."
    ),
    parameters=TMUX_SCHEMA,
    prompt_snippet="Manage named long-lived tmux sessions",
    prompt_guidelines=[
        "Use tmux for listeners, reverse connections, OOB callbacks, relays, servers, and work that must survive turns.",
        "Use capture to collect evidence and stop sessions explicitly when they are no longer needed.",
    ],
    execute=execute,
)
```

Return `AgentToolResult(content=[TextContent(text=message)], details=details)`. `list` details use keys `action` and `sessions`; other actions include logical `name`, resolved `sessionName`, and action-specific `cwd`, `lines`, or `alreadyAbsent`.

- [ ] **Step 6: Run tmux unit tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_tmux_tool.py
```

Expected: fake-runner action, validation, missing executable, idempotency, and bounded error tests pass.

- [ ] **Step 7: Commit the isolated tmux tool**

```bash
git add travis/coding_agent/tools/tmux.py tests/test_tmux_tool.py
git commit -m "feat(offsec): add tmux tool"
```

---

### Task 5: Register tmux as a built-in default and run a native smoke

**Files:**
- Modify: `travis/coding_agent/tools/__init__.py:8-59,62-97,113-150`
- Modify: `travis/coding_agent/session_types.py:90-107`
- Modify: `travis/coding_agent/session_tooling.py:118-187`
- Modify: `tests/test_coding_tools_and_subagents.py:1463-1482`
- Modify: `tests/test_tmux_tool.py`

**Interfaces:**
- Consumes: tmux factories from Task 4.
- Produces: `tmux` in the built-in registry and default parent catalog; process remains dynamically registered only when an app-owned process service exists.

- [ ] **Step 1: Write failing registry/default tests**

Add:

```python
def test_tmux_is_builtin_and_default(tmp_path: Path) -> None:
    assert "tmux" in all_tool_names
    assert create_tool_definition("tmux", str(tmp_path)).name == "tmux"
    assert len(create_all_tools(str(tmp_path))) == 8

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    try:
        assert session.get_active_tool_names() == ["read", "bash", "tmux", "edit", "write"]
        assert "Manage named long-lived tmux sessions" in session.system_prompt
    finally:
        session.shutdown()
```

Add a process-service variant expecting `["read", "bash", "process", "tmux", "edit", "write"]`.

- [ ] **Step 2: Run the focused registry tests and verify `tmux` is unknown**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_coding_tools_and_subagents.py -k 'tool_factory_bundles or tmux_is_builtin' \
  tests/test_process_tools.py -k system_prompt
```

Expected: tmux registry/default assertions fail.

- [ ] **Step 3: Register tmux and add workspace options**

Make these exact registry changes:

```python
ToolName = Literal["read", "bash", "tmux", "edit", "write", "grep", "find", "ls"]
_ORDERED_TOOL_NAMES = ("read", "bash", "tmux", "edit", "write", "grep", "find", "ls")
```

Import tmux factories, add them to both factory maps, and add `"tmux": {"operations", "workspace"}` to supported definition/tool options. Include tmux in `create_coding_tools()` and `create_coding_tool_definitions()`.

Set:

```python
_DEFAULT_ACTIVE_TOOL_NAMES = ["read", "bash", "tmux", "edit", "write"]
```

Add `"tmux": {"workspace": self._workspace}` to `_builtin_tool_options()`. Keep process insertion at index 2 so tmux follows process when process exists.

- [ ] **Step 4: Run registry, prompt, and process tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_tmux_tool.py \
  tests/test_coding_tools_and_subagents.py -k 'tool_factory_bundles or default or tmux' \
  tests/test_process_tools.py -k 'system_prompt or default'
```

Expected: all selected tests pass.

- [ ] **Step 5: Add and run an optional real tmux smoke**

Add a `pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")` test that uses a UUID-suffixed logical name, starts `printf 'TMUX-SMOKE-OK\\n'; sleep 0.2`, captures until the marker appears, lists the resolved session, and stops it in `finally`.

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_tmux_tool.py
```

Expected: pass when tmux exists; one explicit skip otherwise.

- [ ] **Step 6: Commit built-in registration**

```bash
git add travis/coding_agent/tools/__init__.py travis/coding_agent/session_types.py \
  travis/coding_agent/session_tooling.py tests/test_coding_tools_and_subagents.py \
  tests/test_tmux_tool.py tests/test_process_tools.py
git commit -m "feat(offsec): enable tmux by default"
```

---

### Task 6: Convert subagent contracts from read-only review to workspace-write execution

**Files:**
- Modify: `travis/coding_agent/subagents.py:25-35,60-155,434-461`
- Modify: `travis/coding_agent/session_types.py:90-191`
- Modify: `travis/coding_agent/session_subagents.py:88-144,167-267,332-352`
- Modify: `tests/test_subagents.py:294-337,575-594,965-1054`
- Modify: `tests/test_coding_tools_and_subagents.py:1484-1613`

**Interfaces:**
- Consumes: registered tmux tool and current bounded `SubagentSupervisor`.
- Produces: `OFFSEC_SUBAGENT_TOOLS`; default `SubagentTask.sandbox == "workspace_write"`; writable mutation goals; role-skill narrowing without nested spawn.

- [ ] **Step 1: Replace read-only tests with failing writable-contract tests**

Add or rewrite assertions as:

```python
def test_subagent_task_defaults_to_workspace_write_offsec_catalog(tmp_path) -> None:
    task = SubagentTask(role="enumerator", goal="write evidence/ports.md", cwd=str(tmp_path))

    assert task.sandbox == "workspace_write"
    assert task.allowed_tools == (
        "read", "grep", "find", "ls", "bash", "process", "edit", "write", "tmux"
    )
    assert "Use bash for finite commands" in task.prompt()
    assert "Use bash plus process" in task.prompt()
    assert "Use tmux for listeners" in task.prompt()
    assert "Do not assign the same file" in task.prompt()
```

Rewrite the old file-mutation rejection test to assert a `write REVIEW.md` goal spawns and completes. Keep tests proving unexpected `cwd`, `sandbox`, and `allowedTools` model arguments are rejected by the JSON schema/runtime argument checker.

- [ ] **Step 2: Run focused subagent tests and verify old defaults fail**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_subagents.py -k 'default or prompt or codex_exec_backend' \
  tests/test_coding_tools_and_subagents.py -k subagent
```

Expected: writable catalog and mutation-goal tests fail against read-only behavior.

- [ ] **Step 3: Define one shared child catalog and writable prompt**

In `subagents.py` define:

```python
OFFSEC_SUBAGENT_TOOLS = (
    "read", "grep", "find", "ls", "bash", "process", "edit", "write", "tmux"
)
```

Change `SubagentTask` defaults to:

```python
sandbox: SubagentSandbox = "workspace_write"
allowed_tools: tuple[str, ...] = OFFSEC_SUBAGENT_TOOLS
return_contract: str = (
    "Return a concise summary, confirmed findings, evidence, failed attempts, changed files, "
    "artifacts, live tmux sessions, and blockers. Mark unsupported claims as uncertain."
)
```

Add child-prompt lines:

```text
- Use bash for finite commands that should finish promptly.
- Use bash plus process for interactive PTY work that needs follow-up input during this child run.
- Use tmux for listeners, reverse connections, OOB callbacks, relays, servers, or waits that must survive this child.
- You may create and modify workspace files with edit/write.
- Do not assign or modify a file owned by another concurrently running child; report ownership conflicts.
- Do not spawn subagents; execute the bounded Goal directly.
```

- [ ] **Step 4: Remove mutation-goal and prompt-text rejection**

In `session_types.py`:

- import and assign `_DEFAULT_SUBAGENT_ALLOWED_TOOLS = OFFSEC_SUBAGENT_TOOLS`;
- assign `_SKILL_SUBAGENT_ALLOWED_TOOL_NAMES = set(OFFSEC_SUBAGENT_TOOLS)`;
- delete the mutation regexes and `_subagent_goal_requests_file_mutation()`;
- change `_SPAWN_SUBAGENT_SCHEMA.goal.description` to `Bounded OffSec objective for the child agent, including workspace artifacts when needed.`

In `session_subagents.py`:

- remove the mutation helper import;
- remove `_reject_subagent_safety_override_text()` and its call;
- remove the entire `read_only_subagent_file_mutation_goal` result branch;
- retain unexpected-argument rejection, three-per-turn budget, duplicate suppression, cancellation, and result expansion;
- default task sandbox to `workspace_write`;
- permit extension/role-skill `allowedTools` only when every name belongs to `OFFSEC_SUBAGENT_TOOLS`, contains no subagent tool, and is non-empty.

- [ ] **Step 5: Align the optional Codex backend with workspace-write tasks**

Replace the read-only-only check with:

```python
if any(name not in OFFSEC_SUBAGENT_TOOLS for name in task.allowed_tools):
    ended = _now_ms()
    error_text = "Codex backend received a tool outside the OffSec child catalog."
    return SubagentResult(
        task_id=task.id,
        backend=self.name,
        role=task.role,
        status="failed",
        summary=error_text,
        errors=[error_text],
        started_at_ms=started,
        ended_at_ms=ended,
    )
```

Keep `_SANDBOX_FLAGS[task.sandbox]`, so a default Codex child executes with `--sandbox workspace-write`. Keep model, reasoning, timeout, cancellation, JSONL parsing, raw-log, and error behavior unchanged.

- [ ] **Step 6: Run all supervisor and model-facing subagent tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py -k subagent
```

Expected: writable default tests pass; existing concurrency, depth, duplicate, timeout, cancellation, and result-bound tests remain green.

- [ ] **Step 7: Commit writable child policy**

```bash
git add travis/coding_agent/subagents.py travis/coding_agent/session_types.py \
  travis/coding_agent/session_subagents.py tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py
git commit -m "feat(offsec): enable workspace-write subagents"
```

---

### Task 7: Share managed PTYs with internal children using child-specific ownership

**Files:**
- Modify: `travis/coding_agent/session_subagents.py:424-477`
- Test: `tests/test_process_tools.py`
- Test: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Consumes: app-owned `ProcessSessionService`, parent `ProcessOwner`, targets, and `OFFSEC_SUBAGENT_TOOLS`.
- Produces: `_subagent_process_owner(task: SubagentTask) -> ProcessOwner | None`; child constructor receives shared service and unique live-process owner.

- [ ] **Step 1: Write a failing constructor-wiring regression**

Use a recording `_session_factory` and assert:

```python
def test_internal_child_inherits_process_service_targets_and_unique_owner(tmp_path) -> None:
    child_stream = create_faux_provider(
        lambda model, _context: text_response_events(model, "child complete")
    ).api.stream_simple
    service = ProcessSessionService(directory=tmp_path / "processes")
    parent_owner = ProcessOwner("app-fixed", str(tmp_path), "agent")
    parent = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        stream_fn=child_stream,
        process_service=service,
        process_owner=parent_owner,
        targets=("lab.local",),
    )
    captured: dict[str, object] = {}

    def recording_factory(**kwargs):
        captured.update(kwargs)
        return AgentSession(**kwargs)

    parent._session_factory = recording_factory
    task = parent._build_subagent_task("shell-worker", "run an interactive command")
    try:
        result = parent._run_internal_subagent(task)
        assert result.status == "completed"
        assert result.summary == "child complete"
        child_owner = captured["process_owner"]
        assert captured["process_service"] is service
        assert captured["targets"] == ("lab.local",)
        assert captured["allowed_tool_names"] == list(OFFSEC_SUBAGENT_TOOLS)
        assert child_owner != parent_owner
        assert child_owner.workspace_key == parent_owner.workspace_key
        assert child_owner.origin == "agent"
        assert child_owner.app_instance_id == f"app-fixed:subagent:{task.id}"
    finally:
        parent.shutdown()
        service.close()
```

Add explicit imports for `ProcessSessionService`, `ProcessOwner`, and `OFFSEC_SUBAGENT_TOOLS` beside the existing shared test-support import. This test deliberately exercises `_run_internal_subagent` and a real `AgentSession` child, including event subscription and shutdown.

- [ ] **Step 2: Run the regression and verify process arguments are missing**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_coding_tools_and_subagents.py -k unique_owner
```

Expected: failure shows the child constructor did not receive `process_service`, `process_owner`, or `targets`.

- [ ] **Step 3: Add deterministic child-owner derivation**

Add:

```python
def _subagent_process_owner(self, task: SubagentTask) -> ProcessOwner | None:
    if self.process_owner is None:
        return None
    return replace(
        self.process_owner,
        app_instance_id=f"{self.process_owner.app_instance_id}:subagent:{task.id}",
        origin="agent",
    )
```

Do not modify `ProcessOwner`, completion-store schema, process-service limits, or `travis/agent`.

- [ ] **Step 4: Pass app-owned services into internal child construction**

Change child creation to:

```python
child_owner = self._subagent_process_owner(task)
child = self._session_factory(
    cwd=task.cwd,
    model=self.model,
    active_tool_names=list(task.allowed_tools),
    allowed_tool_names=list(task.allowed_tools),
    thinking_level=self.thinking_level,
    stream_fn=self._stream_fn,
    targets=self.targets,
    process_service=self.process_service if child_owner is not None else None,
    process_owner=child_owner,
)
```

`child.shutdown()` must not close the shared service. App `CodingApp.close()` remains the sole service owner and continues to terminate every live managed process.

- [ ] **Step 5: Add a scripted internal-child PTY round-trip test**

Use the existing faux provider pattern from `tests/test_process_tools.py`: first return a `bash` tool call with `stdin="open"`, `tty=True`, and `yield_time_ms=0`; on the next model call read `sessionId`/`nextCursor` from the tool result and return `process` action `write` with `data="PTY-CHILD-OK\n"`; then return `process` action `wait`; finally return text. Assert child tool trace is `bash`, `process`, `process`, output contains `PTY-CHILD-OK`, and the service owner is the child-specific owner.

- [ ] **Step 6: Run process and child wiring tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_process_service.py \
  tests/test_process_tools.py \
  tests/test_coding_tools_and_subagents.py -k 'subagent or unique_owner'
```

Expected: existing process isolation/cleanup tests and new child PTY test pass.

- [ ] **Step 7: Commit child PTY wiring**

```bash
git add travis/coding_agent/session_subagents.py \
  tests/test_process_tools.py tests/test_coding_tools_and_subagents.py
git commit -m "feat(offsec): share managed PTYs with children"
```

---

### Task 8: Prove real child writes, changed-file evidence, tmux visibility, and bounded parallel work

**Files:**
- Modify: `travis/coding_agent/subagent_trace.py:400-485`
- Modify: `travis/coding_agent/session_subagents.py:424-501`
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `tests/test_subagents.py`
- Modify: `tests/test_tmux_tool.py`

**Interfaces:**
- Consumes: tool-capable internal child, tmux namespace, and existing `SubagentResult.files_changed`/`tool_trace` fields.
- Produces: successful child `edit`/`write` calls become exact workspace-relative `files_changed`; three disjoint child tasks can execute concurrently; child tmux sessions remain parent-visible.

- [ ] **Step 1: Write a failing real internal-child file mutation test**

Use a scripted faux stream with this exact tool sequence:

```python
[
    ("write", {"path": "evidence/child.txt", "content": "draft\n"}),
    ("edit", {
        "path": "evidence/child.txt",
        "edits": [{"oldText": "draft", "newText": "CHILD-EDIT-OK"}],
    }),
    ("bash", {"command": "test \"$(cat evidence/child.txt)\" = CHILD-EDIT-OK"}),
]
```

After those tool calls, return a concise evidence-backed final response. Invoke `_run_internal_subagent()` and assert:

```python
assert (tmp_path / "evidence/child.txt").read_text() == "CHILD-EDIT-OK\n"
assert result.status == "completed"
assert result.files_changed == ["evidence/child.txt"]
assert [entry["toolName"] for entry in result.tool_trace] == ["write", "edit", "bash"]
assert all(name not in task.allowed_tools for name in _SUBAGENT_TOOL_NAMES)
```

- [ ] **Step 2: Run the test and verify missing changed-file metadata**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_coding_tools_and_subagents.py -k real_internal_child
```

Expected: file/tool execution succeeds after Tasks 6-7, but `files_changed` is empty.

- [ ] **Step 3: Record only successful mutation paths in trace entries**

In `_subagent_tool_trace_listener`, when a `tool_execution_start` event has tool name `edit` or `write` and mapping arguments with a string `path`, add:

```python
"filePath": str(event.args["path"]),
```

Do not add file contents, command text, environment, or unbounded arguments. Add:

```python
def _subagent_changed_files(task: SubagentTask, tool_trace: list[dict[str, object]]) -> list[str]:
    changed: list[str] = []
    root = Path(task.cwd).resolve()
    for entry in tool_trace:
        if entry.get("status") != "ok" or entry.get("toolName") not in {"edit", "write"}:
            continue
        raw_path = entry.get("filePath")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            rendered = str(resolved.relative_to(root))
        except ValueError:
            continue
        if rendered not in changed:
            changed.append(rendered)
    return changed
```

Set the existing `SubagentResult` constructor's `files_changed` keyword to `_subagent_changed_files(task, tool_trace)`; leave every other result field unchanged.

- [ ] **Step 4: Write and run a child-to-parent tmux visibility regression**

With real tmux available, create parent and child tmux definitions for the same cwd. Start logical name `child-listener` through the child definition, then assert parent `list` includes the same resolved session and parent `capture` sees `CHILD-TMUX-OK`. Stop in `finally`. Skip only when `shutil.which("tmux")` is absent.

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_tmux_tool.py
```

Expected: pass or one explicit missing-tmux skip.

- [ ] **Step 5: Add a three-child disjoint-write concurrency test**

Register a barrier-backed internal test backend under `SubagentSupervisor(max_threads=3, max_depth=1)`. Each task writes one distinct file (`evidence/a.txt`, `evidence/b.txt`, `evidence/c.txt`) and returns that path in `files_changed`. Assert all three enter the barrier before release, all statuses are `completed`, all three files contain their task ids, and the union of result paths has size three. Keep the existing max-thread, duplicate-id, timeout, cancel, and shutdown tests.

- [ ] **Step 6: Run the full subagent/tmux integration slice**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_subagents.py \
  tests/test_tmux_tool.py \
  tests/test_coding_tools_and_subagents.py -k subagent
```

Expected: writable files, changed-file metadata, child tmux visibility, three-worker concurrency, no recursion, and bounded result behavior pass.

- [ ] **Step 7: Commit child evidence and concurrency coverage**

```bash
git add travis/coding_agent/subagent_trace.py travis/coding_agent/session_subagents.py \
  tests/test_coding_tools_and_subagents.py tests/test_subagents.py tests/test_tmux_tool.py
git commit -m "test(offsec): prove tool-capable child execution"
```

---

### Task 9: Remove bundled read-only delegation contradictions

**Files:**
- Modify: `skills/subagent-delegation/SKILL.md`
- Modify: `packages/travis234-cli/skills/subagent-delegation/SKILL.md`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js:40-63`
- Test: `tests/test_offsec_product_contract.py`

**Interfaces:**
- Consumes: runtime child catalog from Task 6.
- Produces: bundled guidance consistent with writable children, disjoint ownership, finite/interactive/long-lived terminal selection, and no nested delegation.

- [ ] **Step 1: Write failing contradiction tests**

For both skill files assert:

```python
for text in skill_texts:
    assert "workspace-write" in text
    assert "bash plus process" in text
    assert "tmux" in text
    assert "disjoint" in text
    assert "Do not let children spawn more subagents" in text
    assert "Subagents must remain read-only" not in text
    assert "parent should write" not in text
```

Make equivalent assertions in the npm package test.

- [ ] **Step 2: Run the contradiction tests and verify read-only text fails**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_offsec_product_contract.py
npm --prefix packages/travis234-cli test
```

Expected: failures identify the old read-only/parent-write instructions.

- [ ] **Step 3: Rewrite the two delegation guides to one contract**

Both files must state:

```text
- Children use a workspace-write catalog containing read, grep, find, ls, bash, process, edit, write, and tmux.
- Assign one bounded objective and disjoint file ownership to each child.
- Use bash for finite commands, bash plus process for interactive PTY work, and tmux for listeners, reverse connections, OOB callbacks, relays, servers, and cross-turn waits.
- Do not let children spawn more subagents.
- The parent reviews changed files, evidence, failed attempts, artifacts, live tmux sessions, and blockers before integrating results.
- Use expand_subagent_result for bounded child output instead of duplicating the child's investigation.
```

Remove all claims that children are read-only, cannot edit/write, or require the parent to write their artifact. Retain the three-child limit, duplicate-work avoidance, bounded results, cancellation, and explicit user-trigger behavior of the skill itself.

- [ ] **Step 4: Run Python and npm guide tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_offsec_product_contract.py
npm --prefix packages/travis234-cli test
```

Expected: guide-contract tests pass; remaining npm identity/image failures belong to Task 10.

- [ ] **Step 5: Commit bundled guidance**

```bash
git add skills/subagent-delegation/SKILL.md \
  packages/travis234-cli/skills/subagent-delegation/SKILL.md \
  packages/travis234-cli/test/travis234-cli.test.js tests/test_offsec_product_contract.py
git commit -m "docs(offsec): align delegation guidance"
```

---

### Task 10: Build the Kali runtime and specialized npm/GHCR distribution surfaces

**Files:**
- Modify: `Dockerfile`
- Modify: `Dockerfile.release`
- Modify: `packages/travis234-cli/bin/travis234.js:11-16`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`
- Modify: `.github/workflows/travis234-release-image.yml`
- Modify: `evals/container_smoke.py`
- Modify: `evals/container_qualification.py`

**Interfaces:**
- Consumes: Python package identity, tmux tool, and existing npm launcher behavior.
- Produces: Kali rolling image with Python 3.13-compatible venv, Node 20/npm/npx, tmux/baseline tools; default image `ghcr.io/htooayelwinict/travis234-offsec:production`.

- [ ] **Step 1: Write failing npm/Docker contract assertions**

Assert both Dockerfiles contain `FROM kalilinux/kali-rolling:latest`, `python3-venv`, and every package below:

```javascript
for (const packageName of [
  "bash", "ca-certificates", "curl", "file", "git", "iproute2", "jq",
  "libstdc++6", "netcat-openbsd", "nmap", "openssl", "python3", "python3-pip",
  "python3-venv", "ripgrep", "socat", "tmux",
]) {
  assert.match(dockerfile, new RegExp(`\\b${packageName}\\b`));
}
```

Assert npm name and default image use `travis234-offsec`; workflow name is `travis234-offsec release image`, manual default ref is `offsec-agent`, and image name ends `/travis234-offsec`.

- [ ] **Step 2: Run npm tests and confirm old Python-slim/image expectations fail**

Run:

```bash
npm --prefix packages/travis234-cli test
```

Expected: Kali/tmux/specialized-image tests fail.

- [ ] **Step 3: Convert development and release Dockerfiles to Kali**

Use this structure in both files:

```dockerfile
FROM node:20-bookworm-slim AS node-runtime
FROM kalilinux/kali-rolling:latest

ENV PYTHONUNBUFFERED=1 \
    TRAVIS234_NO_VENV_REEXEC=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOME=/travis-home \
    TRAVIS234_CODING_AGENT_DIR=/travis-home/agent \
    VIRTUAL_ENV=/opt/travis234-venv \
    PATH="/opt/travis234-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
```

Install the exact baseline list from Step 1. In the same `RUN` layer create the environment using the proven Kali sequence below; dereferencing the interpreter prevents the venv from resolving through Kali's externally-managed system-Python shim:

```dockerfile
python3 -m venv /opt/travis234-venv \
    && cp --dereference --remove-destination \
        /usr/bin/python3 /opt/travis234-venv/bin/python3
```

Install the project through `/opt/travis234-venv/bin/python -m pip`, copy Node 20 and global npm from the Node stage, create npm/npx symlinks, preserve user `travis`, home `/travis-home`, workspace `/workspace`, entrypoint `travis234`, and default `--cwd /workspace`.

The development image may retain sudo limited to `/usr/bin/apt-get`, `/usr/bin/apt`, and `/usr/bin/dpkg`; release image contains no sudo or `NOPASSWD` entry. Neither image installs systemd or creates `travis-offsec`.

- [ ] **Step 4: Point npm and workflow at the specialized image**

Set:

```javascript
const DEFAULT_IMAGE =
  process.env.TRAVIS234_IMAGE ||
  process.env.TRAVIS234_SANDBOX_IMAGE ||
  "ghcr.io/htooayelwinict/travis234-offsec:production";
const PUBLIC_IMAGE_PREFIX = "ghcr.io/htooayelwinict/travis234-offsec:";
```

Set workflow values:

```yaml
name: travis234-offsec release image
on:
  workflow_dispatch:
    inputs:
      ref:
        description: "Git ref to build into the image"
        required: false
        default: "offsec-agent"
env:
  IMAGE_NAME: ghcr.io/${{ github.repository_owner }}/travis234-offsec
```

Keep test and image-smoke jobs as prerequisites of build-and-push.

- [ ] **Step 5: Extend container qualification for OffSec identity and tmux**

In `evals/container_smoke.py`, audit `python`, `node`, `npm`, `npx`, `bash`, `curl`, `file`, `git`, `ip`, `jq`, `nc`, `nmap`, `openssl`, `rg`, `socat`, and `tmux` with `shutil.which`. Assert `travis234 --help` contains `Travis234 OffSec` and has no core legacy flags.

In `evals/container_qualification.py`, add `tmux_round_trip: bool` to `ContainerQualification`; start a UUID-suffixed tmux session through `create_tmux_tool_definition`, capture `CONTAINER-TMUX-OK`, list it, stop it in `finally`, and include the boolean in `passed`.

- [ ] **Step 6: Run npm tests and dry-run package creation**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: tests pass; dry run lists only declared launcher/package/skill files and reports package `@htooayelwinict/travis234-offsec`.

- [ ] **Step 7: Build and smoke the release image**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234-offsec:refactor-smoke .
python evals/container_smoke.py --image travis234-offsec:refactor-smoke
```

Expected: Kali build succeeds; user, entrypoint, Node/npm, baseline tools, tmux round trip, compaction, process cleanup, trust, and installed modes pass.

- [ ] **Step 8: Commit packaging and image changes**

```bash
git add Dockerfile Dockerfile.release packages/travis234-cli/bin/travis234.js \
  packages/travis234-cli/test/travis234-cli.test.js \
  .github/workflows/travis234-release-image.yml \
  evals/container_smoke.py evals/container_qualification.py
git commit -m "build(offsec): package Kali runtime"
```

---

### Task 11: Write the beginner manual and exact seven-scenario TUI protocol

**Files:**
- Create: `docs/offsec/manual.md`
- Create: `docs/offsec/tui-test-protocol.md`
- Create: `tests/test_offsec_tui_protocol.py`
- Modify: `README.md`
- Modify: `packages/travis234-cli/README.md`

**Interfaces:**
- Consumes: final CLI, prompt, subagent, tmux, host-native, npm, and Kali behavior.
- Produces: one-command beginner usage and a reproducible installed-entrypoint qualification record.

- [ ] **Step 1: Write a failing documentation structure test**

Create:

```python
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/offsec/tui-test-protocol.md"


def test_protocol_defines_exactly_seven_executable_scenarios() -> None:
    text = PROTOCOL.read_text(encoding="utf-8")
    headings = re.findall(r"^## Scenario ([1-7]):", text, flags=re.MULTILINE)
    assert headings == list("1234567")
    for section in re.split(r"^## Scenario [1-7]:", text, flags=re.MULTILINE)[1:]:
        assert "### Setup" in section
        assert "### Exact prompt" in section
        assert "### Expected tools/events" in section
        assert "### Pass criteria" in section
        assert "### Cleanup" in section


def test_protocol_covers_single_agent_terminal_and_compaction_contracts() -> None:
    text = PROTOCOL.read_text(encoding="utf-8")
    for required in (
        "Travis234 OffSec",
        "--target local-ctf-fixture",
        "bash",
        "process",
        "spawn_subagent",
        "exactly three",
        "tmux",
        "/compact",
        "--continue",
    ):
        assert required in text
```

- [ ] **Step 2: Run the test and verify missing files fail**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests/test_offsec_tui_protocol.py
```

Expected: failure reports missing `docs/offsec/tui-test-protocol.md`.

- [ ] **Step 3: Write the beginner manual**

Document these exact starter commands:

```bash
# Host-native source checkout
uv sync
uv run travis234 --cwd ~/agent-work --target 10.129.1.23

# Published Python distribution
uv tool install --python 3.13 travis234-offsec
travis234 --cwd ~/agent-work --target 10.129.1.23

# Optional Kali image through npm
npx @htooayelwinict/travis234-offsec --cwd ~/agent-work -- --target 10.129.1.23
```

Explain that no manifest is required; `--target` is optional context; host-native use is preferred for VPN-attached labs; Docker is optional; credentials live in normal Travis234 auth/dotenv handling; bash/process/tmux selection follows the exact contract in Global Constraints. Include tmux native inspection examples using the resolved name returned by the tool:

```bash
tmux attach -t travis234-a1b2c3d4e5f6-callback-check
tmux capture-pane -p -t travis234-a1b2c3d4e5f6-callback-check -S -200
```

State that `a1b2c3d4e5f6` is an example workspace digest and that operators must copy the exact resolved session name returned by the tool.

- [ ] **Step 4: Write the seven exact scenarios**

Use one persistent installed-entrypoint session started as:

```bash
test_root=$(mktemp -d)
mkdir -p "$test_root/workspace"
TRAVIS234_CODING_AGENT_DIR="$test_root/agent" \
travis234 --cwd "$test_root/workspace" --target local-ctf-fixture
```

The seven exact prompts are:

1. `State your operating role and operator-authorized target context. Do not run commands.`
2. `Use bash to run printf 'FINITE-RECON-OK\\n'. Report the exact command, output, and exit status.`
3. `Use a managed PTY to run python3 -u -c 'value=input("token: "); print("PTY-OK:" + value)'. Send INTERACTIVE-OK as follow-up input, wait for exit, and report the evidence.`
4. `Delegate one child to create evidence/child.txt containing draft, edit draft to CHILD-EDIT-OK, verify it with bash, and return changed-file evidence. The parent must inspect the child result pack.`
5. `Spawn exactly three parallel children with disjoint ownership: evidence/a.txt, evidence/b.txt, and evidence/c.txt. Each child writes its uppercase letter plus -OK, verifies its own file, and returns evidence. Reconcile all three results.`
6. `Use tmux to start a named session callback-check running sh -lc 'sleep 1; printf "TMUX-CALLBACK-OK\\n"; sleep 5'. List it, capture TMUX-CALLBACK-OK after the wait, report the resolved session name, then stop it and prove it is absent.`
7. Run `/compact`, then `/exit`. Restart the same state directory and workspace with `TRAVIS234_CODING_AGENT_DIR="$test_root/agent" travis234 --cwd "$test_root/workspace" --continue`, then enter: `Using the compacted and resumed session, report the target, confirmed markers from scenarios 2 through 6, changed files, failed attempts, and current tmux sessions.`

For every scenario include setup, expected tools/events, observed evidence, pass criteria, and cleanup. Require credential redaction and classify weak model choices separately from runtime defects.

- [ ] **Step 5: Rewrite both READMEs around the single-agent beginner flow**

The root README must lead with `Travis234 OffSec`, show `uv tool install travis234-offsec`, the one-command `--target` flow, writable subagents, terminal strategy, optional Kali/npx use, state path, and a link to both new docs. Remove coding-agent marketing and every obsolete manifest/profile/systemd/worker/CTFd instruction.

The npm README must name `@htooayelwinict/travis234-offsec`, show the exact npx command, explain mounts and credential handling, and link the same terminal strategy. Preserve extension, skill, session, and launcher flags that still exist.

- [ ] **Step 6: Run documentation and identity tests**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_offsec_tui_protocol.py tests/test_offsec_product_contract.py tests/test_cli.py
npm --prefix packages/travis234-cli test
```

Expected: documentation structure, identity, CLI, and launcher tests pass.

- [ ] **Step 7: Commit manuals and qualification protocol**

```bash
git add README.md packages/travis234-cli/README.md docs/offsec/manual.md \
  docs/offsec/tui-test-protocol.md tests/test_offsec_tui_protocol.py
git commit -m "docs(offsec): add operator manual and TUI protocol"
```

---

### Task 12: Complete repository, package, container, TUI, and red-zone qualification

**Files:**
- Modify only when a failing gate identifies a regression outside red zones: tests/docs/packaging/composition files already named in Tasks 1-11.
- Record sanitized manual results: `docs/offsec/qualification-results-2026-08-01.md`

**Interfaces:**
- Consumes: all implementation tasks.
- Produces: requirement-by-requirement completion evidence; no publication or merge.

- [ ] **Step 1: Run focused OffSec regressions**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q \
  tests/test_offsec_product_contract.py \
  tests/test_offsec_tui_protocol.py \
  tests/test_tmux_tool.py \
  tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_process_service.py \
  tests/test_process_tools.py \
  tests/test_coding_resources_and_services.py \
  tests/test_app_integration.py \
  tests/test_cli.py
```

Expected: all selected tests pass; native tmux tests may skip only if tmux is absent on that host.

- [ ] **Step 2: Run the complete Python repository suite**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q -p no:cacheprovider tests
```

Expected: zero failures. Diagnose any failure against its existing contract; do not change agent-loop ordering or compaction to force green.

- [ ] **Step 3: Run npm tests and package dry run**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: all Node tests pass and package name is `@htooayelwinict/travis234-offsec`.

- [ ] **Step 4: Build wheel/sdist and test the clean installed entrypoint**

Run:

```bash
qualification_root=$(mktemp -d)
uv build --out-dir "$qualification_root/dist"
python3.13 -m venv "$qualification_root/venv"
"$qualification_root/venv/bin/python" -m pip install "$qualification_root"/dist/*.whl
"$qualification_root/venv/bin/python" -m pip show travis234-offsec
"$qualification_root/venv/bin/travis234" --help
"$qualification_root/venv/bin/python" -c 'import travis; print(travis.__name__)'
```

Expected: wheel and sdist build; installed distribution is `travis234-offsec`; executable is `travis234`; import prints `travis`; help identifies `Travis234 OffSec`.

- [ ] **Step 5: Build and qualify the Kali release image**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234-offsec:refactor-final .
python evals/container_smoke.py --image travis234-offsec:refactor-final
docker run --rm --entrypoint sh travis234-offsec:refactor-final -lc '
  python --version
  node --version
  for command in bash curl file git ip jq nc nmap openssl rg socat tmux npm npx travis234; do
    command -v "$command"
  done
'
```

Expected: Python reports 3.13.x; Node reports 20.x; every command resolves; full container smoke passes.

- [ ] **Step 6: Run all seven real TUI scenarios**

Follow `docs/offsec/tui-test-protocol.md` from a clean installed entrypoint in a real PTY. Run scenarios 1-6 in one persistent session, issue `/compact` and `/exit`, then complete scenario 7 after restarting that saved session with CLI flag `--continue`. Record model/provider, exact prompt, tool sequence, evidence markers, files, tmux cleanup, compaction state, pass/fail, and failure classification in `docs/offsec/qualification-results-2026-08-01.md`; redact credentials and provider headers.

Expected: seven runtime scenarios pass. A model-quality miss is recorded and rerun with the same runtime before being classified as a product defect.

- [ ] **Step 7: Prove deletion, one-profile CLI, red-zone integrity, and clean diffs**

Run:

```bash
test ! -e travis/offsec
test -z "$(git ls-files 'tests/offsec/**')"
if uv run travis234 --help | rg -- '--profile|--agent-profile|--engagement|--challenge|--ctfd-url|--ctf-fixture-root|--offsec-worker-user'; then
  exit 1
fi
git diff --exit-code main -- travis/agent travis/compaction travis/ai/providers
git diff --check
if git grep -nE 'OPENROUTER_API_KEY=(sk-|[A-Za-z0-9]{20,})|BEGIN (RSA|OPENSSH) PRIVATE KEY' -- . \
  ':!docs/offsec/qualification-results-2026-08-01.md'; then
  exit 1
fi
git status --short
```

Expected: old architecture and core legacy flags are absent; red-zone diff is empty; whitespace check is clean; credential scan prints no real credential; status contains only the intended qualification-results file before its commit.

- [ ] **Step 8: Commit qualification evidence**

```bash
git add docs/offsec/qualification-results-2026-08-01.md
git commit -m "test(offsec): record refactor qualification"
```

- [ ] **Step 9: Produce the final requirement audit**

Run:

```bash
git log --oneline --decorate main..HEAD
git diff --stat main...HEAD
git status --short
```

The completion report must cite evidence for each acceptance criterion: clean main base, one OffSec CLI, replaced default prompt, target context, writable exact child catalog, real bash/process/edit/write/tmux child execution, named tmux lifecycle, preserved child bounds, unchanged red zones, Python/npm/package/container gates, seven TUI scenarios, no credentials, and clean worktree. Do not claim completion if any cited command is missing or failing.
