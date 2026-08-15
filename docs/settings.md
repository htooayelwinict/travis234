# Travis234 settings

Global settings live at `~/.travis234/agent/settings.json`. A trusted project may add `.travis234/settings.json`; project code and project settings remain trust-gated. Invalid hand-edited values are ignored at their own scope rather than hiding a valid value from the other scope.

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
