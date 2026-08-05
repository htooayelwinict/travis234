# Travis234 OffSec seven-scenario TUI qualification

This protocol qualifies the installed `travis234` entrypoint as one Travis234
OffSec agent. Run all scenarios in order in one persistent session. Record
timestamps, tool calls, exit states, resolved paths/session names, and redacted
evidence. Never place provider credentials in the transcript.

Prepare and start once:

```bash
test_root=$(mktemp -d)
mkdir -p "$test_root/workspace"
TRAVIS234_CODING_AGENT_DIR="$test_root/agent" \
travis234 --cwd "$test_root/workspace" --target local-ctf-fixture
```

Model mistakes, weak tactical choices, or incomplete prose are agent-quality
defects. Missing tools, broken ordering, incorrect process state, lost writes,
unbounded children, failed compaction, or failed resume are runtime defects.

## Scenario 1: Role and target context

### Setup

Use the fresh persistent session. Do not create files or run shell commands.

### Exact prompt

```text
State your operating role and operator-authorized target context. Do not run commands.
```

### Expected tools/events

The response identifies Travis234 OffSec and `local-ctf-fixture`. No tool event
is emitted.

### Observed evidence

Record the response and whether any tool call appeared.

### Pass criteria

The role and optional operator context are accurate; no command ran and no
profile or manifest was requested.

### Cleanup

None.

## Scenario 2: Finite bash execution

### Setup

Remain in the same session and workspace.

### Exact prompt

```text
Use bash to run printf 'FINITE-RECON-OK\n'. Report the exact command, output, and exit status.
```

### Expected tools/events

One finite `bash` call returns `FINITE-RECON-OK` and exit status zero without a
managed process handle.

### Observed evidence

Record the rendered call, output, and exit metadata.

### Pass criteria

The exact marker and successful exit status are reported from tool evidence.

### Cleanup

None.

## Scenario 3: Interactive managed PTY follow-up

### Setup

Confirm `python3` is available. Remain in the same session.

### Exact prompt

```text
Use a managed PTY to run python3 -u -c 'value=input("token: "); print("PTY-OK:" + value)'. Send INTERACTIVE-OK as follow-up input, wait for exit, and report the evidence.
```

### Expected tools/events

`bash` launches an interactive PTY and returns a `proc_...` handle. A `process`
write sends `INTERACTIVE-OK`, then `process.wait` observes natural exit.

### Observed evidence

Record the handle, acknowledged input, `token:` prompt, `PTY-OK:INTERACTIVE-OK`,
and final status.

### Pass criteria

Follow-up input reaches the same spawned shell, the marker appears, and the
process exits successfully without being killed.

### Cleanup

Use `process.list` and terminate only the scenario process if it unexpectedly
remains active.

## Scenario 4: One writable child

### Setup

Create no `evidence/child.txt` manually; the child owns it.

### Exact prompt

```text
Delegate one child to create evidence/child.txt containing draft, edit draft to CHILD-EDIT-OK, verify it with bash, and return changed-file evidence. The parent must inspect the child result pack.
```

### Expected tools/events

The parent uses `spawn_subagent`. The child uses `write`, `edit`, and `bash`, then
returns a bounded result pack whose changed files include `evidence/child.txt`.
The parent inspects and reconciles that pack.

### Observed evidence

Record the child status, result summary/expansion, changed-file metadata, and the
parent's final verification.

### Pass criteria

The file contains exactly `CHILD-EDIT-OK`, the child—not the parent—performed the
mutation, and the parent reports tool-derived evidence.

### Cleanup

Keep the file for scenario 7; stop any child-owned managed process or tmux session.

## Scenario 5: Three bounded parallel children

### Setup

Ensure `evidence/a.txt`, `evidence/b.txt`, and `evidence/c.txt` do not exist.

### Exact prompt

```text
Spawn exactly three parallel children with disjoint ownership: evidence/a.txt, evidence/b.txt, and evidence/c.txt. Each child writes its uppercase letter plus -OK, verifies its own file, and returns evidence. Reconcile all three results.
```

### Expected tools/events

One parallel `spawn_subagent` batch starts exactly three children. Their ownership
is disjoint; each uses `write` and `bash`. No child receives `spawn_subagent`.

### Observed evidence

Record all three task identifiers, completion states, changed-file packs, file
contents, and the parent reconciliation.

### Pass criteria

The files contain exactly `A-OK`, `B-OK`, and `C-OK`; exactly three children ran,
all finished, and no child modified another child's file.

### Cleanup

Keep the files for scenario 7; confirm no child process remains active.

## Scenario 6: Detached tmux lifecycle

### Setup

Confirm the `tmux` tool is available and no logical `callback-check` session is
already present.

### Exact prompt

```text
Use tmux to start a named session callback-check running sh -lc 'sleep 1; printf "TMUX-CALLBACK-OK\n"; sleep 5'. List it, capture TMUX-CALLBACK-OK after the wait, report the resolved session name, then stop it and prove it is absent.
```

### Expected tools/events

The `tmux` tool performs start, list, capture, stop, and final list. The resolved
name follows `travis234-{12-character digest}-callback-check`; follow-up tool
calls may use that returned value without adding another prefix. The pane retains
its final output if the short command exits before capture.

### Observed evidence

Record the resolved session name, list result, captured marker, stop result, and
final absence evidence.

### Pass criteria

`TMUX-CALLBACK-OK` is captured, the exact resolved session is reported, and the
final list proves it is absent.

### Cleanup

If the normal stop failed, run `tmux kill-session -t <exact-resolved-name>` using
the name returned by the tool, then prove absence.

## Scenario 7: Compact, exit, resume, and reconcile

### Setup

All scenario markers and evidence files remain available. Note the active session
path with `/session`.

### Exact prompt

First enter:

```text
/compact
```

Then enter `/exit`. Restart against the same state and workspace:

```bash
TRAVIS234_CODING_AGENT_DIR="$test_root/agent" \
travis234 --cwd "$test_root/workspace" --continue
```

Enter this exact prompt:

```text
Using the compacted and resumed session, report the target, confirmed markers from scenarios 2 through 6, changed files, failed attempts, and current tmux sessions.
```

### Expected tools/events

`/compact` creates a durable compaction checkpoint. `/exit` closes the first
process. `--continue` resumes its session, and the final response may use `read`,
`bash`, and `tmux` list to verify durable facts.

### Observed evidence

Record compaction status, original/resumed session identity, recovered target and
markers, verified file contents, failed-attempt ledger, and tmux list.

### Pass criteria

The same session resumes; `local-ctf-fixture`, scenarios 2 through 6, all changed
files, and failed attempts survive compaction; current tmux sessions are reported
from fresh evidence and `callback-check` remains absent.

### Cleanup

Enter `/exit`, stop any remaining namespaced tmux sessions, redact credentials
from the qualification record, then remove the temporary root:

```bash
rm -rf "$test_root"
```
