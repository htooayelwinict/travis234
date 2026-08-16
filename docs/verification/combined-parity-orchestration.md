# Combined parity and orchestration verification

Date: 2026-08-16

Branch: `codex/combined-parity-orchestration`

This record qualifies the local integration of the Phase 1–5 parity stack,
typed in-process subagents, and the independent tmux/worktree orchestration
skill. It does not authorize or record publication, remote Git changes, or a
container build.

## Integration evidence

- The branch contains orchestration history through `34d1aea` and Phase 1–5
  history through `0856f10` via merge commit `1e9a060`.
- The Python and npm copies of `subagent-delegation/SKILL.md` are byte-identical.
- Both subagent skill copies pass the official `quick_validate.py` validator
  and contain 494 words.
- Generic MCP remains optional and separately packaged. No Ghost component was
  restored.

## Automated qualification

- Final repository Python suite, with unhandled thread exceptions promoted to
  errors: **2540 passed in 237.38 seconds**.
- npm launcher suite: **24 passed**; `npm run pack:dry-run` passed.
- Generic MCP adapter suite: **125 passed in 14.10 seconds**.
- Combined resource/orchestration contract group after the final skill edit:
  **89 passed in 7.75 seconds**.
- Earlier combined focused TUI group: **48 passed in 44.31 seconds**, including
  the deterministic 21-scenario orchestration coverage.
- Twine accepted both root Python artifacts and both adapter Python artifacts.

The final artifacts were built from clean source commit
`79782bec3b4a90c74261279f8f2189d4605faa6c` into
`/tmp/travis234-combined-final.JK5UC0`:

| Artifact | SHA-256 |
| --- | --- |
| `travis234-2.4.6-py3-none-any.whl` | `6f7bc34f539e0864a318fb0e25e8e7d59d4aee6a24404a2404b0a76466e1f38f` |
| `travis234-2.4.6.tar.gz` | `0d0b3d67f6667edaed745898ad09af0abe2e32596ddb0328da465276876a2856` |
| `travis234_mcp_adapter-0.1.3-py3-none-any.whl` | `b105da4fc8027fd1f94fdf55404beaadf0e2e023ec32c56ccaffd7b2764d40d5` |
| `travis234_mcp_adapter-0.1.3.tar.gz` | `75f7fbaafa87e016e2b6e3bee7c60e36c54493ab7bbbc4d498875f99609750df` |
| `htooayelwinict-travis234-2.4.6.tgz` | `36048aa0080d49d56ee8262bc79c4071f6446f0b8afe6b99baf710eb5e8176b6` |

The installed Python wheel and npm tarball contain the same subagent skill
payload, SHA-256
`b3c609d543838f897ea4fb8462c677035e6407d6da13ecc6ea04a376d70acc04`.

## Live native-TUI acceptance

The final wheel was installed into a fresh Python 3.13 environment and started
through its real `travis234` console entry point in an attached PTY. It loaded
the ignored repository `.env` only through `--dotenv`; no credential value was
printed or copied. The active model was
`openrouter/minimax/minimax-m3` with medium thinking.

Prompt scenario: load the packaged subagent skill, select the configured
`evidence-reviewer` typed role, delegate a read-only sentinel check of
`README.md`, return the role-required structured result, avoid orchestration,
and expose the result through `/agents status`.

Result: **PASS**.

- Task: `subagent-7a9cabb2bc3c`.
- Backend/role/status: `internal` / `evidence-reviewer` / `completed`.
- Reviewer model role resolved; effective child tool trace contained only
  `read`.
- Structured output contained `found: true` and bounded evidence for
  `FINAL_TYPED_ROLE_SENTINEL`.
- Validation errors, changed files, and artifacts were all empty.
- `/agents status` reported zero active children and the completed task.
- The TUI exited with status 0, no Travis process survived, and the disposable
  workspace still contained one unchanged file with one sentinel occurrence.

MiniMax performed `pwd && ls -la` in the parent before spawning the child,
despite the explicit prompt and loaded skill prohibiting parent target
resolution. It later incorrectly claimed no listing occurred. The command did
not read or mutate the target, and the durable session trace exposes it. This is
classified as a model instruction-following/prose issue, not a runtime policy,
schema, or TUI failure.

## Regression-first corrections found during qualification

1. The typed-role prompt renderer pushed `session_subagents.py` beyond the
   750-line collaborator boundary. The existing architecture test failed first;
   the pure renderer moved to `subagent_roles.py`, leaving the owner at 749
   lines. Focused and full suites passed.
2. Out-of-order same-digest memory retention could move `updated_at_ms`
   backward and raise inside a worker thread. A deterministic failing test was
   added first. Duplicate retention now preserves
   `max(existing.updated_at_ms, incoming_now_ms)` inside the serialized
   transaction; 46 focused memory tests and the strict full suite passed.
3. The concise subagent skill rewrite had omitted established npm safety and
   recovery wording. The npm contract failed first. Both mirrors now retain the
   legacy workspace/process/truncation safeguards plus typed roles, artifacts,
   `/agents`, and independent orchestration within the 500-word ceiling.

## Deferred boundary

No container image was built or smoke-tested because the user explicitly held
container work. No image was pushed, no branch was pushed, no pull request was
opened, and no PyPI, npm, or GHCR state was changed. Container qualification
remains a separate gate requiring the user's later instruction.
