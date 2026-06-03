# Worker Tooling Hardening

Goal: make greenfield and file-heavy worker runs smoother without loosening the
kernel safety model.

Current blocker from live QA:

- `repo_snapshot` now observes empty repos correctly.
- The next failure is `infra_worker` calling compound shell commands through
  `run_readonly_command`.
- Greenfield scaffolding also needs fewer model/tool turns than repeated
  primitive writes.

Implementation slices:

1. Add structured runtime capability probing and batch filesystem tools.
2. Add planner-visible `filesystem_worker`.
3. Update planner prompts/contracts/validator so file scaffolding can choose it.
4. Verify with unit/regression tests and rerun a no-mock greenfield calculator
   API probe.

