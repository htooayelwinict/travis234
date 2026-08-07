# Additive MCP CLI Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bare `--mcp` flag that enables the optional MCP proxy alongside Travis234's otherwise active tools while retaining MCP-only and generic allowlist workflows.

**Architecture:** Keep MCP installation and transport behavior in the optional adapter. Resolve the boolean in `travis/cli.py`, carry one additive startup tool name through `CodingApp`, and let `AgentSession` append registered and allowed additive names to its existing initial selection.

**Tech Stack:** Python 3.13, argparse, Travis234 extension/session tooling, pytest, npm, uv build, Twine, Docker, and Minimax M3.

## Global Constraints

- Product and CLI remain `Travis234` and `travis234`; Python import package remains `travis`.
- Preserve user state under `~/.travis234` and keep credentials out of tracked files and command output.
- Do not alter agent-loop ordering, iteration budgeting, bounded parallel execution, compaction, provider payloads, or MCP transports.
- Keep the MCP SDK and adapter optional and separately distributed.
- Preserve `--tools mcp`; bare `--mcp` is a presence flag and accepts no boolean value.
- Add failing regression tests before implementation.
- Never stage, alter, or remove the two protected user-owned untracked documents.
- Do not push or publish without a later explicit GitOps request; keep GitHub CLI on `htooayelwinict`.

## File map

- `travis/cli.py`: parse, resolve, validate, and explain `--mcp`.
- `travis/app.py`: forward additive startup tools to every created session.
- `travis/coding_agent/agent_session.py`: append deduplicated additive names to initial active tools.
- `tests/test_cli_runtime_controls.py`: CLI combinations, errors, help, and real app activation.
- `tests/test_coding_policy_and_extensions.py`: session-level additive-selection boundary.
- `README.md` and `packages/travis234-mcp-adapter/README.md`: canonical commands and semantics.
- `packages/travis234-mcp-adapter/tests/test_distribution.py`: packaged documentation assertions.
- `docs/verification/main-additive-mcp-flag-five-prompt-tui.md`: exact Minimax M3 evidence.

---

### Task 1: Lock the CLI contract with failing tests

**Files:**
- Modify: `tests/test_cli_runtime_controls.py`

**Interfaces:**
- Consumes: `cli.main(argv: list[str]) -> int` and the existing `CodingApp(**kwargs)` test seam.
- Produces: expectations for `additional_active_tool_names`, `allowed_tool_names`, errors, and help text.

- [ ] **Step 1: Add a fake-app capture helper**

```python
def _capture_cli_tool_options(monkeypatch, *, known_tools):
    captured: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session = SimpleNamespace(
                get_known_tool_names=lambda: list(known_tools)
            )

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "run_print_mode", lambda *_args: 0)
    return captured
```

- [ ] **Step 2: Add failing selection tests**

```python
@pytest.mark.parametrize(
    ("flags", "expected_allowed"),
    [
        (["--mcp"], None),
        (["--no-tools", "--mcp"], ["mcp"]),
        (["--tools", "read,bash", "--mcp"], ["read", "bash", "mcp"]),
        (["--tools", "mcp", "--mcp"], ["mcp"]),
    ],
)
def test_cli_mcp_flag_resolves_additive_tool_selection(
    monkeypatch, tmp_path, flags, expected_allowed
):
    captured = _capture_cli_tool_options(
        monkeypatch, known_tools=["read", "bash", "mcp"]
    )
    assert cli.main([
        "--cwd", str(tmp_path), "--no-session", "--mode", "print",
        *flags, "inspect",
    ]) == 0
    assert captured["allowed_tool_names"] == expected_allowed
    assert captured["additional_active_tool_names"] == ["mcp"]
```

- [ ] **Step 3: Add failing errors and help tests**

Add `test_cli_mcp_flag_explains_missing_adapter`, asserting the error contains `travis234 install travis234-mcp-adapter`; `test_cli_rejects_mcp_flag_excluded_mcp`, asserting `--mcp cannot be combined with --exclude-tools mcp`; and `test_cli_help_describes_additive_mcp_flag`, asserting help contains `--mcp` and `Add MCP to the otherwise active tool set`.

- [ ] **Step 4: Run the tests and verify RED**

```bash
uv run pytest tests/test_cli_runtime_controls.py \
  -k 'mcp_flag or help_describes_additive_mcp' -q
```

Expected: failures because `--mcp` and `additional_active_tool_names` do not exist.

- [ ] **Step 5: Commit the regression tests**

```bash
git add tests/test_cli_runtime_controls.py
git commit -m "test: define additive MCP CLI behavior"
```

---

### Task 2: Implement additive startup selection

**Files:**
- Modify: `travis/cli.py`
- Modify: `travis/app.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `tests/test_coding_policy_and_extensions.py`
- Test: `tests/test_cli_runtime_controls.py`

**Interfaces:**
- Consumes: parsed `args.mcp: bool`, current allowlist controls, and registered tool names.
- Produces: `CodingApp(additional_active_tool_names: list[str] | None = None)` and `AgentSession(additional_active_tool_names: list[str] | None = None)`.

- [ ] **Step 1: Add a failing session-level regression**

Create a tool definition named `mcp`, construct `AgentSession` with `tool_definitions=[definition]`, `active_tool_names=[]`, and `additional_active_tool_names=["mcp", "mcp"]`, and assert the active list is `["mcp"]`. Add a real `CodingApp` regression using `_write_extension_tool(extension, "mcp")` and assert default tools remain ordered with one trailing `mcp`.

- [ ] **Step 2: Run the boundary tests and verify RED**

```bash
uv run pytest \
  tests/test_coding_policy_and_extensions.py::test_session_additional_active_tools_extend_selection_once \
  tests/test_cli_runtime_controls.py::test_coding_app_adds_mcp_to_default_active_tools -q
```

Expected: `TypeError` for the missing parameter.

- [ ] **Step 3: Parse and resolve `--mcp`**

Add to `travis/cli.py`:

```python
parser.add_argument(
    "--mcp",
    action="store_true",
    help="Add MCP to the otherwise active tool set; requires travis234-mcp-adapter",
)
```

Resolve without expanding implicit defaults:

```python
if args.mcp and "mcp" in excluded_tool_names:
    parser.error("--mcp cannot be combined with --exclude-tools mcp")
if args.mcp and "mcp" not in selected_tool_names:
    selected_tool_names.append("mcp")
allowed_tool_names = (
    selected_tool_names
    if args.tools is not None or args.mcp and args.no_tools
    else [] if args.no_tools else None
)
additional_active_tool_names = ["mcp"] if args.mcp else None
```

Pass the additive list to `CodingApp`. When `args.mcp` is true and `mcp` is unknown, call:

```python
parser.error(
    "MCP tool is unavailable; install it with: "
    "travis234 install travis234-mcp-adapter"
)
```

- [ ] **Step 4: Carry additive names through `CodingApp`**

Add `additional_active_tool_names: list[str] | None = None`, copy it with `self._additional_active_tool_names = list(additional_active_tool_names or [])`, and pass it to every `AgentSession` created by `_build_session`.

- [ ] **Step 5: Append names in `AgentSession`**

After the existing `initial_active_tool_names` expression and before `set_active_tools_by_name`, add:

```python
for name in additional_active_tool_names or []:
    if name not in initial_active_tool_names:
        initial_active_tool_names.append(name)
```

Do not bypass `_is_allowed_tool`; existing allowlist and denylist filtering remains authoritative.

- [ ] **Step 6: Run focused tests and verify GREEN**

```bash
uv run pytest tests/test_cli_runtime_controls.py \
  tests/test_coding_policy_and_extensions.py -q
```

- [ ] **Step 7: Commit implementation and tests**

```bash
git add travis/cli.py travis/app.py travis/coding_agent/agent_session.py \
  tests/test_cli_runtime_controls.py tests/test_coding_policy_and_extensions.py
git commit -m "feat: add additive MCP CLI flag"
```

---

### Task 3: Update operator documentation

**Files:**
- Modify: `README.md`
- Modify: `packages/travis234-mcp-adapter/README.md`
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py`

**Interfaces:**
- Consumes: Task 2 CLI contract.
- Produces: primary additive, MCP-only, and advanced explicit-subset examples.

- [ ] **Step 1: Add failing packaged-documentation assertions**

```python
assert "travis234 --cwd . --mcp" in readme
assert "travis234 --cwd . --no-tools --mcp" in readme
assert "--tools read,bash,process,edit,write,mcp" not in readme
```

- [ ] **Step 2: Run the assertion and verify RED**

```bash
uv run --project packages/travis234-mcp-adapter pytest \
  packages/travis234-mcp-adapter/tests/test_distribution.py -q
```

- [ ] **Step 3: Rewrite both launch sections**

Use these canonical examples:

```bash
# Default Travis234 tools plus MCP
travis234 --cwd . --mcp

# MCP only
travis234 --cwd . --no-tools --mcp

# Advanced explicit subset plus MCP
travis234 --cwd . --tools read,bash --mcp
```

State that the flag is process-scoped, additive, requires the adapter, and does not alter MCP configuration. Retain one sentence that `--tools mcp` remains supported.

- [ ] **Step 4: Verify and commit docs**

```bash
uv run --project packages/travis234-mcp-adapter pytest \
  packages/travis234-mcp-adapter/tests/test_distribution.py -q
uv run pytest tests/test_cli_runtime_controls.py -q
git diff --check
git add README.md packages/travis234-mcp-adapter/README.md \
  packages/travis234-mcp-adapter/tests/test_distribution.py
git commit -m "docs: simplify MCP activation"
```

---

### Task 4: Full verification and five-prompt TUI evidence

**Files:**
- Create: `docs/verification/main-additive-mcp-flag-five-prompt-tui.md`

**Interfaces:**
- Consumes: Tasks 1–3, root `.env`, public MCP servers, and Minimax M3.
- Produces: reproducible evidence without publishing.

- [ ] **Step 1: Run complete test suites**

```bash
uv run pytest -q
uv run --project packages/travis234-mcp-adapter pytest \
  packages/travis234-mcp-adapter/tests -q
npm test --prefix packages/travis234-cli
```

Record exact pass counts and elapsed times.

- [ ] **Step 2: Build and validate packages**

```bash
uv build
uv build --project packages/travis234-mcp-adapter --out-dir dist/mcp-adapter
uvx --from twine twine check dist/*.whl dist/*.tar.gz \
  dist/mcp-adapter/*.whl dist/mcp-adapter/*.tar.gz
npm pack --prefix packages/travis234-cli --dry-run
```

- [ ] **Step 3: Run container smoke**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:additive-mcp-flag .
python3 evals/container_smoke.py --image travis234:additive-mcp-flag
```

- [ ] **Step 4: Run one continuous five-prompt TUI session**

Build/install the current wheel, load the existing root `.env` without printing values, select Minimax M3, and launch with `--mcp`, event trace, and conversation log. Verify in order:

1. A default read/search tool inspects a harmless repository fact.
2. The `mcp` proxy calls public Context7.
3. The `mcp` proxy calls the configured public filesystem server and compares with a default file tool.
4. A harmless default bash/process operation proves MCP did not replace core tools.
5. A final audit confirms both default and MCP calls, followed by clean exit and server/process cleanup.

Write exact commands with secrets omitted, prompt text, model/provider resolution, tool-call evidence, cleanup, and pass/fail results to the evidence file.

- [ ] **Step 5: Commit evidence and audit**

```bash
git add docs/verification/main-additive-mcp-flag-five-prompt-tui.md
git commit -m "test: verify additive MCP TUI workflow"
git diff --check
git status --short --branch
gh api user --jq .login
```

Expected: only the two protected untracked documents remain, commits remain local pending GitOps approval, and GitHub identity is `htooayelwinict`.
