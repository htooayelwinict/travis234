# Travis234 settings

Global settings live at `~/.travis234/agent/settings.json`. A trusted project may add `.travis234/settings.json`; project code and project settings remain trust-gated. Invalid hand-edited values are ignored at their own scope rather than hiding a valid value from the other scope.

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
