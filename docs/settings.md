# Travis234 settings

Global settings live at `~/.travis234/agent/settings.json`. A trusted project may add `.travis234/settings.json`; project code and project settings remain trust-gated. Invalid hand-edited values are ignored at their own scope rather than hiding a valid value from the other scope.

## Explicit memory

Persistent fact memory is disabled by default. It uses the existing Travis234
state root at `~/.travis234/agent/memory.sqlite3` only after the global user
setting enables it and the `memory` tool remains active:

```json
{
  "memory": {
    "enabled": true,
    "allowedScopes": ["project"],
    "maxFactBytes": 65536,
    "maxFactsPerScope": 5000,
    "maxTotalBytes": 1073741824,
    "recallLimit": 20,
    "recallBytes": 32768
  }
}
```

The global user setting is the authority for `enabled` and `allowedScopes`.
A trusted project may lower numeric limits but cannot enable memory, disable it,
or add the `global` scope. Untrusted project memory settings are ignored.
`--no-tools`, an explicit tool list, and `--exclude-tools memory` prevent the
tool and store connection from being created.

There is exactly one model-facing `memory` tool with exact `status`, `recall`,
`retain`, and `delete` actions. Retention always requires an explicit tool call
and provenance label; recall is never injected into the system prompt or
conversation automatically. Recalled facts are repeated inside `[Untrusted
memory data]` envelopes so saved text cannot acquire instruction authority.
Credential-shaped content is rejected without echoing it. Delete requires an
exact opaque memory ID, expiry makes facts invisible, and oversized complete
recall output becomes a session artifact.

The tool declares both `read` and `write` effects because policy metadata is
tool-level rather than action-level. This is deliberately conservative: in
enforced policy, even status or recall may need approval unless both effects
are allowed. Internal subagents do not receive `memory` through their coding
tool allowlist.

Use `/memory status` for the read-only native-TUI view. It reports enablement,
store availability, allowed scopes, effective limits, and current project and
global counts. It never displays fact content, tags, query history, project
paths or hashes, session identity, or credentials, and it never opens storage
when memory is disabled. An unreadable or incompatible store is reported as
unavailable without failing the coding turn.

## Observe-only operations

The operation journal is enabled by default and stored only at
`~/.travis234/agent/operations.sqlite3`:

```json
{
  "operations": {
    "mode": "observe",
    "maxBytes": 1073741824
  }
}
```

Global `mode` is `observe` or `disabled`. A trusted project's `maxBytes` may
only lower the global cap; project settings cannot enable, disable, or widen
the global journal. Disabled mode performs no journal database writes.

The SQLite journal records intent before an external provider/tool effect and
settlement afterward. It contains bounded metadata only and is not conversation
history: JSONL remains the source for resume, fork, clone, steering, and
subagent results. Prompts, completions, tool arguments/results, environment
values, file contents, credentials, steering text, and subagent goals are never
stored in the journal.

If Travis234 can prove that an intent's runtime died, the intent and its running
operation become `uncertain`. Replay policy is always `never`; restart never
calls the provider or tool on the intent's behalf. A settled effect remains
settled even if a crash occurred before its conversation output reached JSONL.
This means the journal reports the real uncertainty window and does not promise
exactly-once effects.

`/operations [operation-id]` is a read-only native-TUI view scoped to the active
session's hashed identity. It displays identifiers, kinds, states, counters,
effect names, replay policy, and timestamps only. Retention remains the explicit
programmatic `prune_settled_before` action and is never coupled to inspection.
An unreadable, full, or incompatible journal fails open for the coding turn and
does not alter readable JSONL history.

## Typed agent roles

Agent roles are optional JSON resources that narrow a delegated child for a
specific job. Put global definitions in
`~/.travis234/agent/roles/<name>.json`. A trusted project may add
`.travis234/roles/<name>.json`; the project definition wins a same-name
collision while trust remains active. Installed resource packages may also
declare role JSON files. The optional `roles` settings list adds explicit role
file paths at that settings scope.

```json
{
  "name": "reviewer",
  "description": "Review a change and return structured findings.",
  "modelRole": "reviewer",
  "allowedTools": ["read", "grep", "find", "ls"],
  "allowedEffects": ["read"],
  "canSpawn": false,
  "maxDepth": 1,
  "skills": ["review-guidance.md"],
  "context": ["architecture.md"],
  "resultSchema": {
    "type": "object",
    "required": ["findings"],
    "properties": {
      "findings": {"type": "array", "items": {"type": "string"}}
    },
    "additionalProperties": false
  },
  "defaultTimeoutSeconds": 900,
  "artifactPolicy": "declared"
}
```

`name` must be a lowercase identifier. `modelRole` is `worker` or `reviewer`.
`allowedTools` and `allowedEffects` are ceilings: missing fields inherit the
parent's current capability ceiling, while an explicit empty list grants none.
Every typed tool must declare effects and fit completely inside both ceilings.
Role context and skill paths are relative to the role file, cannot escape its
directory, and are read into a bounded context pack when the child is spawned.
A reload changes future children only.

`defaultTimeoutSeconds` is between 1 and 3600; a caller may lower it but cannot
raise it. The scheduler remains authoritative at three concurrent children and
one child level. Therefore `canSpawn` and `maxDepth` cannot enable recursive
delegation in the current runtime.

When `resultSchema` is present, the child must return one JSON object with
`summary`, `output`, and optional `artifacts`. The complete envelope is capped
at 256 KiB and `output` is validated with JSON Schema Draft 2020-12. Invalid
JSON or schema mismatch becomes a bounded failed child result; it does not
throw from the parent wait operation. `artifactPolicy` is `none`, `declared`,
or `declared_and_trace`. Declared artifacts must be real UTF-8 regular files
inside the workspace; public results expose durable artifact IDs, never host
paths. Without a matching role definition, existing untyped subagent behavior
is unchanged.

Use `/agents status`, `/agents inspect <id>`, `/agents steer <id> <message>`,
and `/agents cancel <id>` in the native TUI. `/subagents` keeps its existing
meaning: select the delegation skill for the next prompt.

## Tool effect policy

Every coding tool declares one or more effects: `read` observes state, `write`
changes durable state, `execute` starts or controls computation, and `network`
can communicate outside the local process. A tool may declare several effects;
all of them must be auto-allowed or granted for the tool to run without a prompt.

The default is behavior-neutral audit mode with read-only tools auto-allowed:

```json
{
  "toolPolicy": {
    "mode": "audit",
    "autoAllowEffects": ["read"]
  }
}
```

`mode` accepts these exact values:

- `disabled` allows every tool and emits a disabled decision.
- `audit` allows every tool while reporting the decision that enforcement would
  make. Legacy extension tools with no effect metadata are reported as
  undeclared but continue to run.
- `enforce` auto-allows tools whose complete effect set is included in
  `autoAllowEffects`. Other declared tools require native-TUI approval, while
  undeclared tools are denied.

The native TUI offers `allow once`, `allow for session`, and `deny`. A session
grant matches the tool name and its exact declared effect set; it does not
become a permanent setting. Grants are held only by the current in-memory
session and never survive resume, fork, clone, session replacement, or process
restart. Internal child sessions use the same UI broker but own independent
grant sets.

Print, JSON, RPC, SDK, and other non-interactive sessions have no approval
broker. In enforce mode, a tool that would require approval is denied with
`approval_unavailable` rather than reading stdin or hanging. Cancellation,
Escape, approval UI failure, and shutdown also fail closed.

A trusted project may define the same object in `.travis234/settings.json`, but
it can only tighten the global policy: the effective mode is the stricter mode
and the effective auto-allow list is the intersection. An untrusted project's
policy is ignored. Project trust decides whether project settings and code are
loaded; it never counts as a tool-effect grant.

## Language servers

Language-server support is optional. Travis234 never downloads a server: the
user owns the executable and configures its command and argument vector in
`languageServers`. A trusted project may replace the global list; an untrusted
project list is ignored. Invalid entries are skipped independently, so one bad
project entry does not hide a valid global configuration.

```json
{
  "languageServers": [
    {
      "name": "python",
      "command": "pyright-langserver",
      "args": ["--stdio"],
      "languages": ["python"],
      "extensions": {".py": "python", ".pyi": "python"},
      "rootMarkers": ["pyproject.toml", ".git"],
      "initializationOptions": {}
    }
  ]
}
```

`command` must be one bare executable name or an absolute path. Shell command
strings are rejected; arguments belong in `args`. Extensions are normalized to
lowercase suffixes and must map to a declared language. Root markers must be
relative and stay inside the workspace. Initialization options must be JSON and
keys resembling credentials, tokens, passwords, cookies, or authentication are
rejected. Put server credentials in the server's own protected environment,
not in tracked settings.

The single optional `lsp` tool exposes status, diagnostics, symbols, hover,
definition, references, code actions, reviewed rename/code-action previews,
and apply. Tool coordinates are zero-based lines and UTF-16 columns. The tool
conservatively declares all four effects—read, write, execute, and network—so
enforced policy approves or denies the whole boundary before a server is
started or a preview token is consumed.

At most three servers are active. Startup is bounded to 10 seconds, requests to
20 seconds, raw protocol frames to 2 MiB, and restarts to two in 60 seconds.
Normalized inline output is capped at 256 KiB and larger completed output is
promoted to an artifact. Action and preview tokens last ten minutes, with at
most 32 of each per session. One reviewed apply may stage at most 64 MiB of
original bytes.

Preview never changes files. Apply rechecks workspace containment, regular-file
status, permissions, and exact content hashes under the same mutation queues as
other coding tools. If a later write fails, Travis234 restores earlier writes
in reverse order and reports `changed`, `restored`, and `unresolved` paths; an
external process can still interfere, so unresolved paths require manual
inspection. Run `/lsp status` to inspect configured, active, and
restart-exhausted state without starting a server or displaying its command.

## Durable artifact limits

The optional `artifacts` object accepts positive integers only:

```json
{
  "artifacts": {
    "maxObjectBytes": 67108864,
    "maxSessionLogicalBytes": 536870912,
    "maxSessionObjects": 10000,
    "maxPhysicalBytes": 2147483648,
    "maxPhysicalObjects": 100000,
    "minFreeBytes": 134217728
  }
}
```

These values are the defaults: 64 MiB per object, 512 MiB and 10,000 references per session, 2 GiB and 100,000 physical objects per installation, and a 128 MiB free-space reserve. Global user settings may raise or lower a default. Trusted project settings may only lower the resulting numeric value; they cannot raise the global allowance. Untrusted project artifact settings are ignored.

Only completed truncated output, explicitly retained output, and declared subagent artifacts are promoted. Identical content is stored once. Durable reads use opaque artifact IDs and byte pagination capped at 50 KiB per call. Resume reloads the session manifest; fork and clone retain reachable references and retained entries. Historical sessions without a manifest continue to load normally.

Artifact storage failures preserve the original bounded successful tool result and add a stable `artifactUnavailable` diagnostic. Garbage collection is an explicit internal maintenance operation, never an automatic shutdown action. It deletes only objects proven unreferenced after every manifest under the session catalog has been read successfully.
