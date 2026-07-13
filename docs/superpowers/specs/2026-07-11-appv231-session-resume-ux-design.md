# appv231 Persistent Session UX Design

Date: 2026-07-11
Status: Approved for inline implementation

## Goal

Expose appv231's existing append-only JSONL persistence through a Pi-compatible
CLI and TUI session workflow. A user must be able to continue the latest
workspace session, select an older session, open an exact session, start a new
session, inspect the active session, or run ephemerally.

## Constraints

- Keep `appv231.agent` domain-neutral.
- Do not modify `appV2.3.1/appv231/compaction/`.
- Preserve existing JSONL files and default new-session startup behavior.
- Keep session discovery under the coding-agent profile.
- Serialize live TUI session replacement through `SessionCommandExecutor`.
- Keep npm/container sessions persistent through the existing `/agent-home`
  host mount.
- Do not perform mutating git or release operations.

## User Contract

### Startup

Session mode options are mutually exclusive:

| Invocation | Behavior |
| --- | --- |
| `appv231` | Create a new persistent session. |
| `appv231 -c`, `appv231 --continue` | Open the most recently active valid session for the resolved `--cwd`. |
| `appv231 -r`, `appv231 --resume` | Start ephemerally, select a previous session, then enter the normal TUI. |
| `appv231 --session <path-or-id>` | Open one exact session path or unique session ID. |
| `appv231 --no-session` | Run without a JSONL session file. |

`--continue` never falls back to a new session. `--resume` cancellation exits
without creating a session. Relative `--session` paths resolve from the launch
directory using the same npm `INIT_CWD` rule as other host-facing paths. A
resumed session restores its header cwd unless the user explicitly supplied
`--cwd`; `--continue` already targets the resolved current cwd.

### Interactive Commands

- `/resume`: select and switch to another session.
- `/new`: replace the active session with a new persistent session.
- `/session`: show the active file or `ephemeral`, session ID, message count,
  token usage, current model, and thinking level.

Commands are accepted only through the normal interactive command dispatcher.
`/resume` and `/new` wait for the active turn to settle, execute as serialized
session commands, rebind runtime/TUI state, and render a terminal success or
error status.

## Architecture

Add `coding_agent/session_catalog.py` as the persistence discovery boundary.
It owns no model or UI state.

```text
CLI or TUI
    -> SessionCatalog(agent_dir)
         -> list_for_cwd(cwd)
         -> continue_recent(cwd)
         -> resolve(path_or_id, launch_dir)
    -> existing AgentSessionRuntime / CodingApp(session_path=...)
         -> existing SessionStore.build_context()
```

`SessionCatalog` returns immutable `SessionInfo` records containing path,
session ID, header cwd, creation time, last activity time, display name when
present, and a short last-user-message preview. Discovery reads only `.jsonl`
files below the app-owned session root, ignores lock/partial files, and sorts by
last activity descending with path as a deterministic tie-breaker.

The generic agent loop does not import the catalog, store, CLI, or TUI.

Add one shared session-directory resolver used by new-session creation, catalog
discovery, and `AgentSessionRuntime`. Resolution honors, in order, an explicit
runtime directory, `APPV231_CODING_AGENT_SESSION_DIR`, the trusted
`SettingsManager.getSessionDir()` value, and the current app-owned
`<agent_dir>/sessions/--<encoded-cwd>--` default.

`CodingApp` owns the existing `AgentSessionRuntime` as the session-replacement
authority. Its rebind callback updates `CodingApp.session`, cwd, compaction
integration, renderer subscriptions, tool definitions, and TUI footer state.
The runtime factory reuses the same provider control plane, settings manager,
generation options, and profile configuration. No replacement logic enters the
generic agent core.

## Resolution Rules

1. An existing path wins over ID lookup.
2. A bare ID is matched against the session header ID, then an exact filename
   suffix `_<id>.jsonl` for recoverable legacy files.
3. ID lookup searches the current workspace first, then the app-owned session
   root.
4. Multiple matches are an error and list candidate paths.
5. The selected file must have a valid session header and be readable by
   `SessionStore`; corruption is reported instead of skipped for explicit
   selection.
6. Catalog listing skips invalid candidates and exposes diagnostics to the
   picker/status line rather than failing the entire list.

`--continue` remains scoped to the resolved CLI cwd. `--resume`, `--session`,
and interactive `/resume` restore the selected header cwd after verifying that
it exists. An explicitly supplied `--cwd` is an override. A missing stored cwd
is an error that names the missing directory and explains the override option;
the runtime never silently substitutes another tool workspace.

## Picker

Use the existing interactive selector surface for startup `--resume` and
interactive `/resume`.
Rows show session name or preview, relative age, workspace, model when known,
and shortened ID. Current-workspace sessions appear first; other sessions remain
selectable. The selector performs no provider or network calls.

Startup `--resume` constructs `CodingApp` in explicit ephemeral boot mode and
opens the picker before the startup transcript/editor. Selection uses the same
runtime switch as `/resume`; cancellation exits. This reuses one tested TUI
component without creating an orphan JSONL file. Interactive selection calls
the existing runtime session-switch API through `SessionCommandExecutor`; UI
mutation and rendering remain on the UI dispatcher owner.

## Ephemeral Mode

`--no-session` passes an explicit `session_persistence=False` option through
the coding profile and prevents `SessionStore` creation. This is distinct from
an omitted session path, which keeps the current auto-create behavior.
Compaction may still operate in memory. `/new` leaves ephemeral mode by creating
a persistent session through the shared directory resolver; `/resume` switches
to the selected persistent session.

## Failure Contract

- No recent session: `No previous session for this workspace.`
- Missing explicit target: `Session not found: <value>`.
- Ambiguous ID: error plus matching paths.
- Corrupt target: include the target path and safe corruption detail.
- Missing stored cwd: fail unless an explicit cwd override was supplied.
- Cancelled picker: no app/session creation at startup; no state change in TUI.
- Failed interactive switch: retain the original session and conversation.

Errors go to stderr before startup or to a TUI error status after startup.
Credentials and message bodies beyond the bounded preview are never included in
diagnostics.

## Verification

Implementation follows red-green-refactor and proves:

1. Catalog ordering, workspace filtering, path/ID resolution, ambiguity, and
   corrupt-file behavior.
2. CLI mutual exclusion and all five startup modes.
3. Existing model, thinking level, messages, compaction context, and session ID
   restore from selected JSONL.
4. `/resume`, `/new`, and `/session` execute through serialized session commands
   without concurrent UI mutation.
5. npm launcher state survives two separate `--rm` containers.
6. An actual TUI first run writes a unique marker; a second `--continue` run in
   the same workspace can answer from that persisted marker.
7. Full Python and npm suites pass and the compaction redzone diff is empty.

## Non-Goals

- No change to compaction algorithms or JSONL schema.
- No automatic resume on a default launch.
- No session deletion or retention policy.
- No branch/tree/clone redesign in this slice.
- No remote session synchronization.
