# AppV2.1 Context Governance Design

Date: 2026-06-15
Status: Implementation-ready design
Scope: `appV2.1/appv21/context/`, `appV2.1/appv21/runtime/`, `appV2.1/appv21/extensions/`, `appV2.1/appv21/tools/`, `tests/appv21/`, and live matrix probes.

## Goal

AppV2.1 needs a context management system that stays small as tools, skills, world refs, receipts, and model turns grow.

The target is not generic summarization. The target is a runtime-owned context governor:

- Pi-style agent loop freedom: observe, plan, act, verify, revise from current evidence.
- Hermes-style context discipline: preflight selection, post-response compaction, overflow recovery, lineage, and tool-result cleanup.
- AppV2.1 safety: raw evidence stays outside prompt, model summaries never replace trusted runtime evidence, mutation and verification receipts are never compacted away.

## Current Reality

AppV2.1 already has the right skeleton:

- `AppV21AgentRuntime` is the runtime facade.
- `DualContextManager` builds prompt-facing world context.
- `RuntimeContextCompactor` can emit compaction events.
- `PromptBuilder` assembles state/system/agent/skills/world/tools/output contract.
- `ToolBroker` now stores raw successful tool payloads behind `payload_ref`.
- `EvidenceStore` preserves raw tool payloads out of prompt/event context.
- `SkillRouter` can activate `workspace_cleanup`.

But context is still primitive:

- Each turn rebuilds a larger prompt payload from accumulated state.
- Skills are prompt patches, not budgeted skill cards.
- Tool specs are always present as a full list, not mode-filtered by decision need.
- Compaction is threshold-based and late.
- There is no section budget, relevance selector, or overflow recovery path.
- Completed job context is not compacted into durable task memory.
- Planner gets hydrated repo snapshot for planning, but the prompt path does not have a general retrieval policy.

The probe showed the symptom:

```text
prompt tokens: 1036 -> 1166 -> 3407 -> 3450 -> 3516
```

This growth is expected with the current implementation. The runtime is safe enough to finish, but not disciplined enough to scale with more tools and skills.

## Design Decision

Use a three-layer context model:

```text
Durable Layer
  Event log, session JSONL, evidence store, artifacts, leases, receipts, run memories.

Working Layer
  Small prompt-visible state selected per turn and per mode.

Retrieval Layer
  On-demand hydration of raw evidence by ref for planner/verifier/tool execution, never by default prompt stuffing.
```

The runtime must own selection. The model may request more observation, but it must not decide what trusted evidence is copied into prompt context.

## Adopted Pi Patterns

Adopt:

- Let the agent loop decide the next action from current evidence.
- Keep tool results as first-class evidence for later turns.
- Allow revision after failed verification or rejected mutation.
- Keep provider/model behavior behind adapters.

Do not adopt:

- Unbounded conversation/tool history in every call.
- Model-authored memory as trusted state.
- Direct mutating tools exposed as ordinary model tools.

## Adopted Hermes Patterns

Adopt:

- Preflight context selection before every model call.
- Post-response compaction after meaningful state changes.
- Overflow recovery if provider context fails.
- Tool result cleanup before prompt assembly.
- Skill cards and tool filters rather than global prompt dumps.
- Session/run lineage for compacted summaries.

Do not adopt:

- Large gateway/coordinator as the owner of runtime truth.
- Hidden background learning that changes safety policy.
- Caches without invalidation events.

## Context Planes

### Conversation Plane

Contains:

- user request
- current turn summary
- unresolved user constraints
- latest rejection/failure summaries
- optional conversation digest

Rules:

- Can be summarized.
- Must preserve current request and explicit constraints exactly.
- Must not include raw tool payloads.

### World Plane

Contains:

- repo snapshots
- file reads
- search/tool outputs
- mutation receipts
- verification receipts
- evidence refs

Rules:

- Raw payloads live in `EvidenceStore`.
- Prompt sees compact summaries and refs.
- Receipts and pause state are immutable for a run.
- Raw evidence can be hydrated only by runtime services that need it.

### Skill Plane

Contains:

- active skill cards
- optional full skill body when mode/task requires it
- skill constraints
- skill validators and verification hints

Rules:

- Prompt gets skill cards by default.
- Full skill body is loaded only when relevant and budget allows.
- Skill output cannot bypass `ToolBroker`, `DecisionValidator`, mutation leases, or artifact validators.

### Tool Plane

Contains:

- mode-filtered callable tool specs
- tool policy summary
- denied/unavailable tool summaries when relevant

Rules:

- Tool specs are shown only when callable and useful for current mode.
- Registry metadata is not enough; broker handler availability is required.
- Mutating execution remains runtime-internal behind mutation leases.

### Artifact/Memory Plane

Contains:

- final summaries
- run memory artifacts
- accepted plans
- manifests
- verification reports

Rules:

- Completed jobs compact into a durable `run_memory` artifact.
- Future turns receive run memories, not full event histories.
- Model-generated memories are untrusted until validated.

## Required Components

### ContextBudgetManager

Defines per-section budgets and estimates prompt size before model call.

Initial deterministic budgets:

```python
DEFAULT_SECTION_BUDGETS = {
    "system": 1200,
    "agent": 1200,
    "skills": 1200,
    "tools": 1800,
    "world": 3500,
    "state": 1800,
    "output_contract": 1200,
}
```

Budgets are character/token estimates, not provider billing truth. They are a preflight governor.

### ContextSelector

Selects prompt-visible context by mode:

```text
START/THINK: request, active constraints, skill cards, observe tools
OBSERVE: repo refs, inspect tools, recent denials
PLAN: compact repo map ref, accepted constraints, active skill body if needed
ACT: accepted plan slice, mutation intent requirements, no raw read payloads
VERIFY: latest mutation receipt, verification hints, inspect tools
FINALIZE: verification receipts, final artifact refs, run memory summary
```

### SkillRegistry

Replaces hardcoded `SkillRouter`.

Each skill has:

```python
SkillCard(
    skill_id="workspace_cleanup",
    triggers=["cleanup", "organize", "move", "workspace"],
    modes=["THINK", "PLAN", "VERIFY"],
    summary="Organize workspace files using observed repo evidence.",
    tool_preferences=["repo_snapshot", "read_file"],
    preservation_rules=["tests/**", "src/**", "assets/**", "secrets/**", "README.md", "docs/**"],
    budget_priority=80,
)
```

Full skill bodies are optional and mode-gated.

### ToolContextPolicy

Filters broker tool specs by mode and skill.

Default:

```text
OBSERVE: repo_snapshot, read_file
PLAN: no extra tools unless skill asks for plan-helper tools
ACT: no model-callable mutation tools
VERIFY: repo_snapshot, read_file, future verify tools
FINALIZE: no tools by default
```

### RunCompactor

On finalize, produces a compact durable artifact:

```json
{
  "artifact_id": "run_memory",
  "kind": "context_summary",
  "content": {
    "goal": "...",
    "outcome": "completed",
    "decision_counts": {},
    "tools_used": [],
    "files_changed": [],
    "receipts": [],
    "verification": [],
    "open_risks": [],
    "lessons": []
  },
  "trust": "runtime_verified",
  "lifecycle": "runtime_verified",
  "evidence_refs": []
}
```

Future prompts receive this artifact summary instead of replaying the completed loop.

### ContextOverflowRecovery

If provider fails with context limit:

1. Emit `ContextOverflowDetected`.
2. Force compaction using strict budget.
3. Retry once.
4. If retry fails, fail with `context_overflow_unrecoverable`.

This mirrors Hermes overflow recovery without hiding failure.

## Correctness Rules

- Never compact away user request, constraints, pause state, active leases, mutation receipts, or verification receipts.
- Never put raw file content or raw repo snapshot arrays into prompt unless a runtime service explicitly hydrates for a non-model operation.
- Never expose a tool spec unless `ToolBroker` has a registered callable handler.
- Never load full skill bodies by default.
- Never treat model summary as a replacement for runtime evidence.
- Always emit events for selection, compaction, overflow, and run-memory creation.

## Tradeoffs

### Cost

Token use should drop in longer runs because prompt-visible world/tool/skill sections are bounded.

There is some extra local CPU work for selection and estimation. That cost is negligible compared with model calls.

### Behavior

The runtime becomes more conservative. The model may need to ask for observation instead of seeing everything.

This is acceptable. Missing context should produce an observation/tool decision, not hallucinated scope.

### Complexity

The system adds new services, but each has a narrow owner:

- budget estimates
- context selection
- skill activation/body loading
- tool-spec filtering
- run-memory compaction
- overflow recovery

This is cleaner than putting all context policy into `PromptBuilder` or `AppV21AgentRuntime`.

## Acceptance Criteria

- Prompt context has explicit section budgets.
- `PromptContextPrepared` records section sizes, selected refs, selected tools, selected skills, and compacted status.
- Tool specs are mode-filtered.
- Skill cards replace raw prompt patches as the default.
- Workspace cleanup preservation rules are available as structured skill constraints.
- Completed runs emit a `run_memory` artifact.
- Context compaction preserves receipts and pause state.
- The staged file-management probe shows bounded context growth and records the preservation failure as policy-relevant context.
- Existing Gate A/B tests still pass.

