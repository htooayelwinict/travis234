# Package Command Prompt Tokenization Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implemented and verified

**Goal:** Prevent ordinary TUI prompts with unmatched shell-style quotes from being consumed by package-command parsing while preserving package command syntax and errors.

**Architecture:** Gate `_run_package_command()` with an exact first-token membership check before invoking `shlex.split()`. Normal conversation bypasses shell parsing; the existing package-command parser and manager flow remain unchanged after the gate.

**Tech Stack:** Python 3.13, `shlex`, pytest, real installed-entry-point PTY acceptance, npm launcher tests, Python package build, Docker release smoke.

## Global Constraints

- Do not run any Git command or Git-backed operation.
- Do not spawn subagents.
- Modify only `travis/tui/interactive_extensions.py`, `tests/test_tui_commands_and_extensions.py`, and this task's approved documentation.
- Preserve malformed recognized package-command errors and quoted package-source behavior.
- Do not modify Agent, provider, context, compaction, session, tool, extension, or package-manager behavior outside package-command recognition.
- Add the failing regression before production code.
- Use the real installed `travis234` console entry point for the final reproduction.

---

## File Responsibility Map

- `travis/tui/interactive_extensions.py`: recognize the four package commands before shell tokenization.
- `tests/test_tui_commands_and_extensions.py`: regress ordinary apostrophes and characterize malformed real package commands.
- `docs/superpowers/specs/2026-07-15-package-command-prompt-tokenization-guard-design.md`: approved behavioral contract and final verification status.
- `docs/superpowers/plans/2026-07-15-package-command-prompt-tokenization-guard.md`: execution checklist and final status.

### Task 1: Red-green package-command recognition guard

**Files:**
- Modify: `tests/test_tui_commands_and_extensions.py`
- Modify: `travis/tui/interactive_extensions.py:382-390`

**Interfaces:**
- Consumes: `InteractiveMode._run_package_command(prompt: str) -> bool`
- Produces: unchanged method signature; `False` for ordinary prompts and `True` for recognized package commands, including malformed recognized commands.

- [x] **Step 1: Add the ordinary-prompt regression and malformed-command characterization**

Add two focused tests next to the existing package-command tests:

```python
def test_interactive_package_parser_ignores_ordinary_prompt_with_apostrophe(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        before = strip_ansi("\n".join(mode.history.render(500)))
        assert mode._run_package_command("Report README.md's exact byte count") is False
        assert mode._run_package_command("/packages-extra README.md's") is False
        after = strip_ansi("\n".join(mode.history.render(500)))
        assert after == before
        assert "Invalid package command" not in after
    finally:
        mode.footer_data_provider.dispose()
        app.close()


def test_interactive_package_parser_reports_malformed_recognized_command(tmp_path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        enable_tui=True,
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    mode = InteractiveMode(app, input_fn=lambda prompt: "/exit")

    try:
        assert mode._run_package_command("/install 'unterminated") is True
        history = strip_ansi("\n".join(mode.history.render(500)))
        assert "Invalid package command: No closing quotation" in history
    finally:
        mode.footer_data_provider.dispose()
        app.close()
```

- [x] **Step 2: Run the new tests and verify the regression is red for the observed reason**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_tui_commands_and_extensions.py \
  -k 'package_parser' -vv
```

Expected: the ordinary-prompt test fails because `_run_package_command()` returns `True`; the malformed recognized-command characterization passes.

- [x] **Step 3: Implement the exact-token gate before `shlex.split()`**

Add the command set near the imports:

```python
_PACKAGE_COMMANDS = frozenset({"/install", "/remove", "/update", "/packages"})
```

Change the method entry to:

```python
def _run_package_command(self, prompt: str) -> bool:
    prompt_parts = prompt.split(maxsplit=1)
    first_token = prompt_parts[0] if prompt_parts else ""
    if first_token not in _PACKAGE_COMMANDS:
        return False
    try:
        parts = shlex.split(prompt)
    except ValueError as error:
        self.history.add(StatusLine(f"Invalid package command: {error}", kind="error"))
        return True
```

Retain the existing package action, argument, confirmation, mutation, and reload code unchanged.

- [x] **Step 4: Run the red tests green**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_tui_commands_and_extensions.py \
  -k 'package_parser' -vv
```

Expected: both tests pass.

- [x] **Step 5: Run all package-command tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_tui_commands_and_extensions.py \
  -k 'package' -vv
```

Expected: all selected tests pass, including quoted package installation/removal and cancellation.

- [x] **Step 6: Review checkpoint**

Confirm the production edit is confined to the early recognition gate, exact command names do not accept suffixes such as `/packages-extra`, and no package-manager or dispatcher behavior changed.

### Task 2: Repository and installed-entry-point verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-15-package-command-prompt-tokenization-guard-design.md`
- Modify: `docs/superpowers/plans/2026-07-15-package-command-prompt-tokenization-guard.md`

**Interfaces:**
- Consumes: fixed `InteractiveMode._run_package_command(prompt: str) -> bool`
- Produces: fresh verification evidence and an installed-entry-point reproduction proving the prompt reaches MiMo Pro.

- [x] **Step 1: Run the complete command/extension file**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tui_commands_and_extensions.py -q
```

Expected: all tests pass.

- [x] **Step 2: Run the broader TUI suite**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_tui_*.py tests/tui -q
```

Expected: all tests pass.

- [x] **Step 3: Run the full Python repository suite**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Expected: all tests pass with zero failures.

- [x] **Step 4: Run launcher and package gates**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
.venv/bin/python -m build
```

Expected: npm tests pass, dry-run package contents pass, and wheel/sdist build succeeds.

- [x] **Step 5: Run release-container smoke verification**

```bash
docker build -f Dockerfile.release -t travis234:package-command-guard .
.venv/bin/python evals/container_smoke.py --image travis234:package-command-guard
```

Expected: image build and smoke check exit zero.

- [x] **Step 6: Run the real installed-entry-point apostrophe reproduction**

Build/install the current wheel in an isolated `/tmp` environment, start `travis234` in a real PTY with isolated state and `.env`, select `openrouter/xiaomi/mimo-v2.5-pro`, and submit:

```text
The filename README.md's apostrophe is ordinary prose. Reply with exactly this token and nothing else: APOSTROPHE-OK
```

Expected: no `Invalid package command` line, one provider `turn_start`/`turn_end` with status `ok`, exact `APOSTROPHE-OK` response, clean shutdown, and no owned process remaining.

- [x] **Step 7: Record completion without Git**

Mark the spec and plan `Implemented and verified`, record exact test counts, scan for placeholders and unchecked steps, and perform no add/commit/push operation.

## Verification Record

- Red phase: ordinary apostrophe prompt failed because `_run_package_command()` returned `True`; malformed recognized command passed its characterization.
- Green phase: 2 focused parser tests passed; all 4 package-focused tests passed.
- Command/extension file: 69 passed.
- Broader TUI suite: 335 passed.
- Full Python suite: 1,614 passed.
- npm launcher: 20 passed; npm pack dry-run contained exactly 5 files.
- Python build: wheel and sdist built successfully.
- Release container: image build and smoke check exited zero.
- Installed-wheel PTY: OpenRouter MiMo-V2.5-Pro returned exact `APOSTROPHE-OK`; trace recorded one successful turn and clean shutdown; no process remained.
- Git operations: none performed.
