# OffSec Subagent Availability and Parallel Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate long-session editor typing stalls while preserving native terminal scrollback, and make existing on-demand subagent tools discoverable for normal parallel-delegation requests—without changing the agent runtime.

**Architecture:** The TUI retains its complete logical render buffer and native terminal scrollback behavior. Add a narrowly-scoped editor input fast path that re-renders only the configured live tail (editor, below-editor widgets, status, and footer) when its visual line count is stable; all structural, resize, overlay, image, and wrapped-editor changes retain the existing full render path. Separately, keep the six subagent tools out of the default catalog. Add one static delegation instruction to the system prompt and broaden the existing pre-turn intent gate, which temporarily adds the tools only for an explicitly parallel or separate request.

**Tech Stack:** Python 3.13, pytest, Travis234 built-in tool registry, provider-independent system-prompt generation.

## Confirmed TUI Root Cause

Fresh OffSec-only profiling on 2026-08-04 reproduced the typing regression with cached `Text` history and a focused `Input`: 751 visual lines took 12.8 ms median per keypress; 3,001 visual lines took 51.0 ms median per keypress. A four-key profile at 3,001 lines spent 0.765 s in `TUI._do_render`; `truncate_to_width` accounted for 0.702 s across 12,008 calls, while `_diff_render` consumed 0.027 s. The root cause is therefore complete-transcript width processing on the input path, not terminal I/O, agent-loop work, or compaction state.

The native-scrollback contract is also explicit in `test_tui_diff_render_keeps_complete_history_and_addresses_only_visible_tail`: full logical history must remain in `previous_lines` while terminal writes address only the visible tail. The design below preserves that contract by caching the old complete prefix and replacing only a stable live suffix for ordinary keyboard edits. It deliberately does **not** introduce a tail-only history renderer.

## Global Constraints

- Work only on the `offsec-agent` branch/worktree; do not alter `main`.
- Do not modify `travis/coding_agent/agent_loop.py`, compaction code, turn ordering, iteration budgets, `ToolCoordinator`, or `SubagentSupervisor` concurrency limits.
- Preserve the TUI's complete `previous_lines` buffer, native terminal scrollback, complete-history render contract, image behavior, resize behavior, and overlay behavior.
- Do not add a scheduler, background loop, new tool, model selector, or changes to child task execution.
- Preserve the existing `spawn_subagent(wait=True)` execution semantics and all tool schemas.
- The change exposes a capability; it must not auto-spawn subagents or override an explicit user request not to use them.
- No GitOps for this work until the operator explicitly approves it: do not commit, push, open a PR, publish, or build a release image.
- Add failing regression coverage before production code, then run focused and repository-level verification.

---

## File Structure

- Modify: `travis/coding_agent/session_types.py`
  - Keep the stable default active-tool list unchanged. Extend only the existing intent terms that activate the already-registered `_SUBAGENT_TOOL_NAMES` for one turn.
- Modify: `travis/coding_agent/system_prompt.py`
  - Add a concise static delegation cue so the model knows the capability exists when it becomes available.
- Modify: `travis/coding_agent/session_subagents.py`
  - Own `spawn_subagent` model guidance. Add concise delegation instructions only to that tool definition.
- Modify: `tests/test_coding_tools_and_subagents.py`
  - Replace the old opt-in-default assertion with regression tests that inspect active tools and the generated system prompt.
- Modify: `travis/tui/tui.py`
  - Own the optional input-tail snapshot and fast render path; keep the existing full renderer as the fallback and source of truth.
- Modify: `travis/tui/interactive_view.py`
  - Register the existing editor-side tail components with the TUI after those components are attached in their existing order.
- Modify: `tests/test_tui_commands_and_extensions.py`
  - Extend the existing native-scrollback regression.
- Modify: `tests/test_tui_terminal_and_input.py`
  - Add input-fast-path and wrap-fallback coverage near terminal input behavior.

No new production files, dependencies, CLI flags, npm changes, Docker changes, or documentation changes are required.

## Task 1: Lock down the native-scrollback-safe input fast path

**Files:**
- Modify: `tests/test_tui_commands_and_extensions.py:2373-2400`

**Interfaces:**
- Consumes: `TUI.set_input_tail_components(components: list[Component]) -> None`, added by Task 2; `TUI._handle_terminal_input(data: str) -> None`; existing `FakeTerminal`, `Text`, and `Input` components.
- Produces: regression coverage proving that ordinary editor typing does not render retained history, while full history and native scrollback remain intact for structural changes.

- [ ] **Step 1: Add a failing no-history-rerender regression**

  Add a small local test component that counts `render()` calls, then use it in a long transcript:

  ```python
  class CountingText(Text):
      def __init__(self, text: str) -> None:
          super().__init__(text)
          self.render_calls = 0

      def render(self, width: int) -> list[str]:
          self.render_calls += 1
          return super().render(width)


  def test_tui_editor_input_fast_path_does_not_rerender_retained_history() -> None:
      terminal = FakeTerminal(columns=80, rows=24)
      tui = TUI(terminal)
      history = [CountingText(f"history {index}") for index in range(1_000)]
      for item in history:
          tui.add(item)
      editor = Input(prompt="> ")
      tui.add(editor)
      tui.set_input_tail_components([editor])
      tui.set_focus(editor)
      tui.request_render(force=True)
      for item in history:
          item.render_calls = 0

      tui._handle_terminal_input("x")

      assert sum(item.render_calls for item in history) == 0
      assert tui.previous_lines[0] == "history 0"
      assert tui.previous_lines[-1].endswith("x")
  ```

- [ ] **Step 2: Add a failing structural-fallback regression**

  Verify that a wrapped editor does not use an unsafe partial patch. At a narrow width, type enough plain text to change the editor's visual line count and assert the history is rendered again and the complete buffer remains present:

  ```python
  def test_tui_editor_input_fast_path_falls_back_when_editor_wraps() -> None:
      terminal = FakeTerminal(columns=12, rows=5)
      tui = TUI(terminal)
      history = [CountingText(f"history {index}") for index in range(8)]
      for item in history:
          tui.add(item)
      editor = Input(prompt="> ")
      tui.add(editor)
      tui.set_input_tail_components([editor])
      tui.set_focus(editor)
      tui.request_render(force=True)
      for item in history:
          item.render_calls = 0

      tui._handle_terminal_input("long-editor-text")

      assert sum(item.render_calls for item in history) > 0
      assert tui.previous_lines[:2] == ["history 0", "history 1"]
  ```

- [ ] **Step 3: Extend the existing complete-history/native-scrollback regression**

  Keep `test_tui_diff_render_keeps_complete_history_and_addresses_only_visible_tail` unchanged except for adding this assertion after the second render:

  ```python
  assert second.lines[:2] == ["history 0", "history 1"]
  ```

  This locks the non-negotiable behavior that the logical render buffer is complete even though terminal addressing is limited to the visible tail.

- [ ] **Step 4: Run the TUI tests and confirm the two new fast-path tests fail**

  Run:

  ```bash
  uv run pytest tests/test_tui_commands_and_extensions.py \
    -k 'input_fast_path or keeps_complete_history' -q
  ```

  Expected: the new API is absent or retained history is rendered during normal typing. The existing complete-history regression remains passing.

## Task 2: Implement the input-tail fast path without changing the full renderer

**Files:**
- Modify: `travis/tui/tui.py:116-435`
- Modify: `travis/tui/interactive_view.py:72-82`

**Interfaces:**
- Consumes: a suffix-ordered list of root-level `Component` instances registered by `InteractiveView`.
- Produces: `TUI.set_input_tail_components(components: list[Component]) -> None` and an internal `_try_render_input_tail() -> bool` used exclusively after focused keyboard input.

- [ ] **Step 1: Add the narrow registration API and snapshot state to `TUI`**

  Import `Component` alongside `Container`, then add these fields in `TUI.__init__`:

  ```python
  self._input_tail_components: tuple[Component, ...] = ()
  self._input_tail_start: int | None = None
  self._input_tail_line_count: int | None = None
  ```

  Add this public configuration method near `set_focus`:

  ```python
  def set_input_tail_components(self, components: list[Component]) -> None:
      self._input_tail_components = tuple(components)
      self._input_tail_start = None
      self._input_tail_line_count = None
  ```

  This API is intentionally TUI-only. It does not expose history/session state or introduce a new render scheduler.

- [ ] **Step 2: Capture a validated suffix snapshot after every existing full render**

  Add `_capture_input_tail_snapshot(width: int, lines: list[str]) -> None`. It must:

  1. Render only the registered tail components in order.
  2. Apply the same `truncate_to_width` operation used by `_do_render`.
  3. Confirm those lines exactly equal the suffix of the already-produced full `new_lines`.
  4. Store `start = len(lines) - len(tail_lines)` and `line_count = len(tail_lines)` only on that exact match.
  5. Clear both snapshot fields on empty tail, mismatch, overlays, or image lines.

  Call it after every successful complete-root render in `_do_render`, after `self.previous_lines` has been assigned. Do not replace `super().render(width)` in `_do_render`; that call remains the authoritative path for all non-editor changes.

- [ ] **Step 3: Add `_try_render_input_tail()` and use it only after focused input**

  In `_handle_terminal_input`, retain the existing input handling order. Replace the final unconditional render request with:

  ```python
  self._focused_component.handle_input(current)
  if not self._try_render_input_tail():
      self.request_render()
  ```

  Add a small private descendant check that follows `Container.children`, so `_try_render_input_tail()` can verify that `self._focused_component` belongs to the first registered tail component. `_try_render_input_tail()` must return `False` and leave render state untouched unless every condition below holds:

  - A validated snapshot exists and terminal width/height are unchanged.
  - No overlay is visible.
  - The focused component is contained by the first registered tail component.
  - Rendering the registered tail produces the same number of non-image visual lines as the snapshot.
  - The retained prefix of `previous_lines` remains available.

  On success, construct `new_lines` from the cached complete prefix plus the newly rendered tail, call the existing `_extract_cursor_position` and `_diff_render`, then update `previous_lines`, cursor state, and `last_render`. Do not call `super().render(width)`, `truncate_to_width` on history, `_full_render`, or `_collect_kitty_image_ids` in this fast path. Because only a stable tail is patched, native scrollback and absolute history indices remain unchanged.

- [ ] **Step 4: Register the existing live tail from `InteractiveView`**

  In `InteractiveView.init`, immediately after adding the direct TUI children in their current order, register exactly this suffix:

  ```python
  self.tui.set_input_tail_components(
      [
          self.editor_container,
          self.widget_container_below,
          self.status,
          self.footer_container,
      ]
  )
  ```

  Do not include `history`, `header_container`, or `widget_container_above`. Those are intentionally excluded from normal typing work. Dialogs, overlays, focus changes, widget reshaping, theme changes, session rebinds, and resize events naturally miss a fast-path precondition and use the unchanged full renderer.

- [ ] **Step 5: Run focused TUI tests**

  Run:

  ```bash
  uv run pytest tests/test_tui_commands_and_extensions.py \
    -k 'input_fast_path or keeps_complete_history' -q
  uv run pytest tests/test_tui_terminal_and_input.py -q
  ```

  Expected: PASS. In particular, the complete-history/native-scrollback test keeps all nine logical lines, and the input fast-path test proves zero retained-history renders for a stable one-line edit.

## Task 3: Lock down the on-demand tool and prompt contract

**Files:**
- Modify: `tests/test_coding_tools_and_subagents.py:1484-1520`

**Interfaces:**
- Consumes: `AgentSession.get_active_tool_names() -> list[str]`, `AgentSession.system_prompt -> str`, and `_SUBAGENT_TOOL_NAMES -> list[str]`.
- Produces: regression coverage establishing that subagent management tools remain absent by default, become model-visible for a parallel-delegation turn, and that the static prompt carries the delegation policy.

- [x] **Step 1: Replace the old opt-in regression with a failing on-demand availability regression**

  Replace `test_agent_session_keeps_subagent_tools_opt_in_by_default` with this test shape:

  ```python
    def test_agent_session_keeps_subagent_tools_on_demand_with_delegation_guidance(tmp_path: Path) -> None:
      session = AgentSession(cwd=str(tmp_path), model=faux_model())
      try:
          assert set(_SUBAGENT_TOOL_NAMES).isdisjoint(set(session.get_active_tool_names()))
          assert set(_SUBAGENT_TOOL_NAMES) <= {tool["name"] for tool in session.get_all_tools()}
          assert "Delegation is available for independent, bounded tasks." in session.system_prompt
      finally:
          session.shutdown()
  ```

- [x] **Step 2: Add a guard that this is temporary exposure, not auto-delegation**

  Add a second session-level test immediately after it. It must only construct a session and inspect state; it must not call a provider or invoke a tool:

  ```python
    def test_parallel_delegation_temporarily_exposes_subagent_tools_to_the_model(tmp_path: Path) -> None:
        # A faux provider records Context.tools for "multiple agents", then the
        # session restores its normal five-tool active set after the turn.
        ...
  ```

- [x] **Step 3: Run the focused tests and confirm they fail for the expected reason**

  Run:

  ```bash
    uv run python -m pytest tests/test_coding_tools_and_subagents.py \
    -k 'keeps_subagent_tools_on_demand or parallel_delegation_language' -q
  ```

  Expected: FAIL because an intermediate implementation made the tools permanent and the broader phrases were not recognized.

## Task 4: Preserve default tool restraint and broaden explicit intent detection

**Files:**
- Modify: `travis/coding_agent/session_types.py:91-154`

**Interfaces:**
- Consumes: `_SUBAGENT_TOOL_NAMES`, the canonical list of six existing tool names.
- Produces: the existing five-tool default set, with temporary subagent activation for clear parallel/delegation wording.

- [x] **Step 1: Keep the default active-tool declaration at the current five tools**

  Restore `_DEFAULT_ACTIVE_TOOL_NAMES` to `read`, `bash`, `tmux`, `edit`, and `write`. Do not alter `session_turns.py`; its existing activation and restoration block is the loading mechanism.

- [x] **Step 2: Broaden only `_SUBAGENT_OPT_IN_TERMS`**

  Run:

  ```bash
  uv run pytest tests/test_coding_tools_and_subagents.py \
    -k 'exposes_subagent_tools_and_parallel_guidance or visible_without_creating_a_subagent' -q
  ```

  Add conservative phrases: `multiple agents`, `multi-agent`, `multi agent`, `parallel agent(s)`, `parallel worker(s)`, `split the work`, and `independent review(s)`. Keep existing explicit opt-out terms authoritative.

## Task 5: Add concise, deterministic delegation guidance

**Files:**
- Modify: `travis/coding_agent/system_prompt.py`
- Modify: `travis/coding_agent/session_subagents.py:169-185`

**Interfaces:**
- Consumes: `ToolDefinition.prompt_guidelines: list[str]`, which is incorporated into the session system prompt only when the tool is active.
- Produces: a static model-facing discovery cue and detailed lifecycle guidance once `spawn_subagent` is temporarily active; no new tool calls, runtime methods, or schemas.

- [x] **Step 1: Add a static system-prompt discovery cue and retain detailed active-tool guidance**

  Add this static instruction to the OffSec preamble:

  ```python
  Delegation is available for independent, bounded tasks. When the operator asks for parallel or separate work, use `spawn_subagent` and the available subagent tools to investigate independently, retain shared edits in the parent, and synthesize the results.
  ```

  Do not change the existing `wait` default. The policy teaches the model when to choose `wait: false`; preserving the default prevents a compatibility regression for single-child calls and weaker providers.

- [x] **Step 2: Run the focused tests and confirm they pass**

  Run:

  ```bash
    uv run python -m pytest tests/test_coding_tools_and_subagents.py -q
  ```

  Expected: PASS.

- [x] **Step 3: Run adjacent subagent and prompt-generation regressions**

  Run:

  ```bash
    uv run python -m pytest tests/test_coding_policy_and_extensions.py tests/test_process_tools.py -q
  ```

  Expected: PASS.

## Task 6: Verify red-zone preservation and release surfaces

**Files:**
- Inspect only: `travis/coding_agent/agent_loop.py`, compaction modules, `travis/coding_agent/subagents.py`, package launchers, and container smoke scripts.

**Interfaces:**
- Consumes: the completed diff and existing verification commands.
- Produces: evidence that the implementation changed only tool exposure and system-prompt guidance.

- [ ] **Step 1: Inspect the diff for forbidden runtime changes**

  Run:

  ```bash
  git diff --check
  git diff --name-only
  git diff -- travis/coding_agent/agent_loop.py travis/coding_agent/subagents.py travis/coding_agent/session_turns.py
  ```

  Expected: whitespace check succeeds; the changed production paths are limited to `travis/tui/tui.py`, `travis/tui/interactive_view.py`, `travis/coding_agent/session_types.py`, and `travis/coding_agent/session_subagents.py`, plus their tests and this plan. The third command has no output. `session_turns.py` must remain untouched because existing temporary activation remains valid when the tools are already active.

- [ ] **Step 2: Run repository Python verification**

  Run:

  ```bash
  uv run pytest -q
  ```

  Expected: PASS.

- [ ] **Step 3: Verify packaging without publishing**

  Run:

  ```bash
  uv build
  npm --prefix packages/travis234-cli test
  ```

  Expected: wheel/sdist build succeeds and npm launcher tests pass. Do not publish artifacts.

- [ ] **Step 4: Run the relevant local container smoke check if Docker is available**

  Run:

  ```bash
  docker build -f Dockerfile.release -t travis234-offsec:subagent-availability-test .
  uv run python evals/container_smoke.py --image travis234-offsec:subagent-availability-test
  ```

  Expected: the image reports the OffSec CLI identity and accepts the unchanged entrypoint contract. Do not push the image.

- [ ] **Step 5: Hold for operator approval before any GitOps action**

  Do not run `git add`, `git commit`, `git push`, package publishing, or image publishing. Report the diff and verification results, then wait for explicit approval.

## Plan Self-Review

- **Coverage:** Task 1 establishes the native-scrollback-safe typing regression and Task 2 provides its bounded fast path; Task 3 establishes the subagent contract; Tasks 4 and 5 implement tool availability and prompt policy; Task 6 protects the red zones and release surfaces.
- **Scope:** No tail-only transcript renderer, wave tool, model-selection work, tool-schema changes, scheduler changes, or runtime-loop changes are included.
- **Compatibility:** Complete logical history and native terminal scrollback remain intact. Existing single-child behavior and bounded supervisor concurrency remain unchanged. Existing intent detection remains in place but is no longer required for tool visibility.
- **Ambiguity resolved:** Because this branch is already the OffSec product and `AgentSession` has no profile parameter, “OffSec only” means this branch only; no in-process profile switch will be introduced.
