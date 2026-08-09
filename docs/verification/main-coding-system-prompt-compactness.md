# Coding system-prompt compactness verification

Date: 2026-08-10
Baseline commit: `8093ee7b06fccc798cd69286299839039df34d7c`

This record measures the combined process-tool quality and default coding-prompt cleanup in the existing main worktree. Token counts use `travis.ai.context_estimate.estimate_text_tokens`. Tool contracts serialize only `name`, `description`, and `parameters` as compact, sorted JSON.

## Recorded pre-change baseline

| Surface | Characters | Estimated tokens |
|---|---:|---:|
| Default coding prompt with managed process tools | 6,974 | 1,744 |
| Root-repository prompt with root `AGENTS.md` and two packaged lazy skills | 9,155 | 2,289 |
| Subagent policy contribution | 1,606 | 402 |
| Installed Travis234 documentation section | 1,001 | 251 |
| Process schema | 4,570 | 1,143 |

## Final deterministic measurements

| Variant | Characters | Estimated tokens | Lines |
|---|---:|---:|---:|
| Core default: read, Bash, process, tmux, edit, write, spawn, wait | 6,657 | 1,665 | 68 |
| Same prompt without process | 5,639 | 1,410 | 60 |
| Same prompt without subagent tools | 5,556 | 1,389 | 64 |
| Root repository with root `AGENTS.md` and two packaged lazy skills | 7,739 | 1,935 | 91 |

The final process prompt delta is 1,018 characters / 255 estimated tokens. The final subagent prompt delta is 1,101 characters / 276 estimated tokens.

| Tool surface | Characters | Estimated tokens | Count |
|---|---:|---:|---:|
| Flat process schema | 1,359 | 340 | 1 |
| Complete default tool contracts | 8,146 | 2,037 | 8 |

## Semantic review

- Senior-engineering ownership, evidence verification, exact test-count/file verification, and non-fabrication remain present.
- Subagent policy has one high-level authority; tool metadata retains only operation-specific spawn guidance.
- Active-tool routing distinguishes finite Bash, managed running processes, PTY-only terminal interaction, and durable tmux work.
- The permanent system prompt contains no placeholder process session ID or cursor JSON.
- Project instructions remain after generic policy and can restrict delegation.
- The documentation index lists only installed paths, retains complete-read/cross-reference guidance, and never assumes an unlisted file.
- Lazy skill name, description, and location metadata remain after project context and before date/cwd; skill bodies remain unloaded.

This implementation did not modify the generic loop, AgentHarness, session/compaction/accounting, persistence, provider, process lifecycle, or managed-process ownership code. Those modules may have unrelated pre-existing dirty changes in this handed-off worktree and are verified separately rather than attributed to this prompt/process repair.
