# Travis234 Orchestration Protocol

- Schema version: 1
- Protocol version: 1

This reference will define the version-matched run, task, worker, dispatch,
message, handoff, recovery, and exact-cleanup contracts implemented by
`scripts/orchestrate.py`.

The private relay is helper-owned. Do not invoke it directly or use terminal
screen scraping as a message protocol.
