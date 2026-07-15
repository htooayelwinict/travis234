# Package Command Prompt Tokenization Guard Design

**Status:** Implemented and verified

**Date:** 2026-07-15

**Scope:** Interactive TUI package-command recognition only
**Git operations:** not authorized

**Verification:** The red regression failed with the observed `True is False` result before the fix and passed afterward. Four package tests, 69 command/extension tests, 335 TUI tests, 1,614 full Python tests, and 20 npm launcher tests passed. Python/npm packages built successfully, the release-container smoke check passed, and a freshly installed wheel delivered the apostrophe prompt to MiMo Pro with exact `APOSTROPHE-OK` output and clean shutdown.

## Problem

`InteractiveMode._run_package_command()` currently applies `shlex.split()` to every submitted prompt before it determines whether the prompt is a package command. Ordinary prose containing an unmatched shell-style quote, such as `README.md's`, raises `ValueError("No closing quotation")`. The dispatcher consumes the prompt as an invalid package command, so it never reaches the provider.

## Required behavior

1. Only prompts whose first whitespace-delimited token is exactly `/install`, `/remove`, `/update`, or `/packages` may enter package-command tokenization.
2. Every other prompt must return `False` from `_run_package_command()` without calling `shlex.split()`, adding history output, mutating state, or consuming the prompt.
3. A malformed real package command must retain the existing `Invalid package command: ...` error and return `True`.
4. Existing quoted package sources and `--local` parsing must remain unchanged.
5. Agent, provider, context, compaction, session, tool, extension, and package-manager behavior outside this recognition boundary must remain unchanged.

## Design

Add a constant set for the four package command names and perform a cheap prefix-token check at the start of `_run_package_command()`:

- Strip only leading whitespace for recognition.
- Extract the first token using whitespace partitioning, not shell parsing.
- Return `False` immediately when that token is not one of the four exact package commands.
- Run the existing `shlex.split()` and command implementation unchanged only after recognition succeeds.

Exact-token matching prevents `/packages-extra` and ordinary slash-like prose from being consumed. Deferring `shlex.split()` preserves shell-style quoting for actual package commands without imposing shell grammar on normal conversation.

## Error handling

For recognized package commands, `ValueError` from `shlex.split()` continues to render `Invalid package command: <error>` and consumes the command. Ordinary prompts cannot produce that package-specific error because they bypass tokenization.

## Regression tests

Add focused tests using the real `_run_package_command()` path:

1. An ordinary prompt containing `README.md's` returns `False` and adds no package-command error.
2. A malformed recognized package command such as `/install 'unterminated` returns `True` and retains the current error.
3. Existing quoted package-path behavior remains covered by the package command integration test.
4. Run the focused command/extension suite, the broader TUI suite, and the complete Python repository suite.

## Non-goals

- Refactoring dispatcher ownership
- Changing package command syntax
- Changing prompt templates or slash-command fallback behavior
- Changing any Agent or context-envelope behavior
- Git operations
