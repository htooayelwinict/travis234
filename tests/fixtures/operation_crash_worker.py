from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import time

import psutil

from travis.coding_agent.operations import OperationStore


def _write_line(path: Path, value: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-dir", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    agent_dir = Path(args.agent_dir)
    agent_dir.mkdir(parents=True, exist_ok=True)
    journal = agent_dir / "operations.sqlite3"
    session = agent_dir / f"{args.session_id}.jsonl"
    effects = agent_dir / f"{args.session_id}.effects"
    ready = agent_dir / f"{args.session_id}.ready"

    if args.checkpoint == "before_intent":
        os._exit(70)

    store = OperationStore(journal)
    runtime_id = hashlib.sha256(args.session_id.encode("utf-8")).hexdigest()[:32]
    store.open_runtime(
        runtime_id,
        os.getpid(),
        psutil.Process().create_time(),
        int(time.time() * 1000),
    )
    operation = store.create_operation(
        runtime_id,
        hashlib.sha256(args.session_id.encode("utf-8")).hexdigest(),
        "turn",
        int(time.time() * 1000),
    )
    effect = store.begin_effect(
        operation.operation_id,
        "tool",
        "fixture.effect",
        hashlib.sha256(b"bounded-fixture").hexdigest(),
        int(time.time() * 1000),
    )
    ready.write_text(operation.operation_id, encoding="utf-8")
    if args.hold:
        while True:
            time.sleep(0.05)
    if args.checkpoint == "after_intent":
        os._exit(71)

    _write_line(effects, "effect-started")
    if args.checkpoint == "during_effect":
        os._exit(72)
    _write_line(effects, "effect-complete")
    if args.checkpoint == "after_effect_before_settlement":
        os._exit(73)

    now_ms = int(time.time() * 1000)
    store.settle_effect(effect.effect_id, "settled", "ok", now_ms)
    store.settle_operation(operation.operation_id, "settled", "ok", now_ms + 1)
    if args.checkpoint == "after_settlement":
        os._exit(74)

    _write_line(session, '{"type":"message","role":"assistant","content":"persisted"}')
    if args.checkpoint == "after_jsonl_persistence":
        os._exit(75)
    raise ValueError("unknown checkpoint")


if __name__ == "__main__":
    main()
