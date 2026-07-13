# Travis234 repository guidance

This repository contains the Travis234 application and its release tooling.

- The product and CLI are `Travis234` and `travis234`; the Python import package is `travis`.
- Treat the repository root as the only active application tree.
- Preserve user data under `~/.travis234`; do not introduce alternate state paths or migration aliases.
- Keep credentials out of tracked files and command output.
- Preserve the core agent-loop ordering, iteration budgeting, and bounded parallel execution unless a focused regression test requires a change.
- Add a failing regression test before each bug fix, then run focused and repository-level verification.
- Use the bundled skills only when the task calls for them. Subagents must be explicitly requested by the user.
- Before reporting completion, verify Python tests, npm launcher tests, package builds, and relevant container smoke checks.
