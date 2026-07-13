# appv23 Risk Hardening Design

## Goal

Fix the remaining post-seal appv23 QA risks without broad rewrites or git operations.

## Chosen approach

Use strict hardening. Subagent delegation and provider credential execution should fail closed when enforcement is uncertain, instead of relying on prompt text, extension trust, or shell behavior.

## Scope

- Extension subagent calls inherit the parent session workspace and read-only delegation policy.
- Codex subagent backend rejects custom allowed-tool sets because the backend cannot enforce them at process level.
- Provider command-backed config values execute through argv parsing with `shell=False`.
- Subagent observer failures remain non-fatal but become inspectable diagnostics.

## Non-goals

- No broad `AgentSession` refactor.
- No new plugin permission system.
- No push, commit, or tag after the already-created `v2.3` seal.
- No removal of command-backed provider secrets; only remove shell execution.

## Testing strategy

Use TDD for each behavior:

- Add failing extension safety override tests.
- Add failing Codex allowed-tools enforcement test.
- Add failing no-shell command config test and update env-command behavior to use an argv-safe command.
- Add failing observer diagnostics tests.

Then implement minimal production changes and run focused tests plus the full appv23 suite.
