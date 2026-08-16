# Combined parity and orchestration integration design

## Goal

Combine the approved Phase 1–5 contract-parity implementation and the approved
durable tmux orchestration implementation into one reviewable local branch,
without rewriting either implementation's history or changing release state.

## Inputs

- `main` supplies the lazy built-in `orchestration` skill, its tmux/worktree
  helper, documentation, and qualification tests.
- `codex/optional-ecosystem` supplies the linear Phase 1–5 stack: capability
  registry, model roles, durable artifacts, uniform tool policy, bounded LSP,
  typed coordination, observe-only operation journaling, explicit memory, MCP
  additions, and qualification gates.
- Both lines share commit `a922733`, the approved capability-registry plan.

Neither input is a disposable source tree. The integration must preserve both
commit histories and must not silently omit a phase or orchestration feature.

## Chosen integration strategy

Create `codex/combined-parity-orchestration` from the current orchestration
`main`, then merge `codex/optional-ecosystem` with a real merge commit.

This is preferred over rebasing 74 Phase commits across the orchestration line
because a merge keeps the already-qualified commit identities and makes the
integration boundary explicit. It is preferred over squash or bulk cherry-pick
because those approaches would hide phase ownership and make regression
bisection substantially harder.

The work happens in `.worktrees/combined-parity-orchestration`. The root
checkout and its unrelated uncommitted edits remain untouched.

## Conflict policy

Resolve conflicts additively under these rules:

1. Preserve the Phase 1–5 runtime implementations and their tests.
2. Preserve the orchestration skill, protocol, helper, packaging mirrors,
   documentation, and 21-scenario qualification.
3. Preserve generic MCP support; do not restore any retired Ghost component.
4. Preserve existing subagent result expansion and lifecycle behavior while
   adding typed-role, artifact, and supervision behavior.
5. Keep the generic agent-loop ordering, iteration budgets, cancellation,
   steering, follow-ups, and bounded parallel tool execution unchanged.
6. Do not introduce automatic merge, cherry-pick, push, branch deletion,
   worktree deletion, operation replay, memory retention, or MCP fan-out.
7. Do not change the package version or publish any artifact.

README and verification-document conflicts are resolved by retaining both
feature surfaces and removing only statements made obsolete by the combined
tree. Test-file conflicts retain assertions from both parents unless the
combined runtime proves that an assertion is mutually exclusive by design.

## Prompt alignment after integration

Prompt alignment is a separate commit after the combined runtime passes its
focused integration checks.

- Keep capability-registry, model-router, policy-engine, artifact-store, and
  operation-journal internals out of the global system prompt.
- Extend conditional subagent guidance only when subagent tools are exposed.
- Update both packaged copies of `subagent-delegation/SKILL.md` for typed role
  selection, effect ceilings, structured results, declared artifacts, and
  supervision.
- Make typed role names and descriptions discoverable through bounded,
  trust-gated model-facing guidance.
- Strengthen opt-in memory guidance: retain only on explicit user intent and
  always treat recalled content as untrusted data.
- Keep the orchestration skill independent from in-process subagents.

## Verification

Verification proceeds in increasing scope:

1. Prove the pre-merge orchestration baseline is clean.
2. Merge and run conflict-focused tests for resource packaging, subagents,
   artifacts, policy, LSP, memory, MCP, and orchestration.
3. Run both packaged-skill validators and mirror comparisons.
4. Add failing prompt-contract tests before prompt or skill edits, then make
   the smallest guidance changes that satisfy them.
5. Exercise TUI scenarios covering new Phase features and orchestration in the
   same installed wheel.
6. Run the complete Python suite, npm launcher suite, Python/npm package builds,
   adapter suite, and relevant unprivileged container smoke checks.

A failure introduced by the merge is fixed on the integration branch with a
focused regression test. A failure already present on an input branch is
reported distinctly rather than concealed as an integration success.

## Completion boundary

Completion means the combined local branch contains both histories, passes the
required verification, and documents any remaining limitation. It does not
mean pushing Git refs, opening a pull request, publishing PyPI/npm packages, or
promoting a GHCR image.
