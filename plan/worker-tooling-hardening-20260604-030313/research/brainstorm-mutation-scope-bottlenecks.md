# Mutation Scope Bottlenecks Brainstorm

## Problem

The worker runtime currently treats malformed mutation scope artifacts as a hard
block before mutation. That protects the repo, but live probes show the gate is
too brittle: model outputs such as `operations[].path` or root hidden files like
`.dockerignore` can stop the whole workflow even when user intent, plan, and
target files are otherwise clear.

## Constraints

- The kernel must remain the write-safety control plane.
- Workers should not receive raw shell or broad filesystem access.
- Replan should be reserved for planner-level mismatch, not worker formatting or
tool-contract issues.
- Mutation must not proceed with unbounded or ambiguous write access.

## Evaluated Options

1. Keep strict `mutation_scope` exactly as-is.
   - Safest but too brittle for live LLM output.
   - Causes unnecessary blocks on recoverable formatting issues.

2. Make planner/worker prompts stricter.
   - Useful but insufficient.
   - LLMs will still emit near-valid shapes under pressure.

3. Add a kernel-side scope resolver.
   - Worker emits a flexible `write_intent` or `file_operations` proposal.
   - Kernel resolves it into deterministic `ResolvedWriteScope`.
   - Invalid or ambiguous paths become local worker/kernel repair, not planner
     replan.

4. Allow broad directory scopes for greenfield work.
   - Smooth but risky if used too often.
   - Good only when repo is confirmed empty and the plan explicitly declares
     greenfield scaffolding.

## Recommendation

Use a two-layer contract:

- `MutationProposal`: flexible worker-facing artifact with `target_paths`,
  `operations`, `files_to_create`, `files_to_modify`, `files_to_delete`,
  `directories`, and rationale.
- `ResolvedWriteScope`: kernel-owned strict contract with normalized paths,
  max file count, forbidden paths/globs, and source artifact references.

The worker may produce imperfect-but-understandable proposals. The kernel owns
normalization, validation, repair hints, and the final task permissions. A block
should happen only after the resolver cannot safely convert the proposal.

## Next Steps

- Rename the internal concept from "mutation_scope must be perfect" to
  "worker proposes, kernel resolves."
- Keep existing `mutation_scope` name for planner compatibility, but accept it
  as proposal input.
- Add resolver diagnostics: accepted paths, rejected paths, reason, and repair
  hint.
- For confirmed empty repos, allow a bounded greenfield policy that accepts
  project-root files and new top-level directories from the proposal.
