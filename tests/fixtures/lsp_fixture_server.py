from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def _frame(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _write(payload: dict[str, object], *, split: bool = False) -> None:
    framed = _frame(payload)
    if split:
        for byte in framed:
            sys.stdout.buffer.write(bytes((byte,)))
            sys.stdout.buffer.flush()
        return
    sys.stdout.buffer.write(framed)
    sys.stdout.buffer.flush()


def _read() -> dict[str, object] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, value = line.decode("ascii").split(":", 1)
        headers[name.lower()] = value.strip()
    size = int(headers["content-length"])
    body = sys.stdin.buffer.read(size)
    return json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="echo")
    parser.add_argument("--record")
    args = parser.parse_args()
    write_lock = threading.Lock()
    record_lock = threading.Lock()

    def record(payload: dict[str, object]) -> None:
        if not args.record:
            return
        with record_lock:
            with Path(args.record).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, separators=(",", ":")) + "\n")

    def respond(payload: dict[str, object], *, split: bool = False) -> None:
        with write_lock:
            _write(payload, split=split)

    while request := _read():
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params")
        record({"method": method, "id": request_id, "params": params})
        if method == "$/cancelRequest":
            continue
        if method == "exit":
            return 0
        if request_id is None:
            continue
        if method == "fixture/env":
            respond({"jsonrpc": "2.0", "id": request_id, "result": sorted(os.environ)})
            continue
        if args.mode == "exit":
            return 7
        if args.mode == "malformed":
            sys.stdout.buffer.write(b"Content-Length nope\r\n\r\n")
            sys.stdout.buffer.flush()
            continue
        if args.mode == "oversized":
            sys.stdout.buffer.write(b"Content-Length: 2097153\r\n\r\n")
            sys.stdout.buffer.flush()
            continue
        if args.mode == "error":
            respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "fixture failure"}})
            continue
        if args.mode == "stderr":
            sys.stderr.write("OPENROUTER_API_KEY=fixture-super-secret\n")
            sys.stderr.flush()
            return 9
        if args.mode == "delay":
            def delayed(current_id: object, current_params: object) -> None:
                time.sleep(2)
                respond({"jsonrpc": "2.0", "id": current_id, "result": current_params})

            threading.Thread(target=delayed, args=(request_id, params), daemon=True).start()
            continue
        response = {"jsonrpc": "2.0", "id": request_id, "result": params}
        if args.mode == "lowercase-header":
            body = json.dumps(response, separators=(",", ":")).encode("utf-8")
            with write_lock:
                sys.stdout.buffer.write(f"content-length: {len(body)}\r\n\r\n".encode("ascii") + body)
                sys.stdout.buffer.flush()
            continue
        if args.mode in {"combined", "notify"}:
            notification = _frame({"jsonrpc": "2.0", "method": "fixture/note", "params": {"ok": True}})
            with write_lock:
                sys.stdout.buffer.write(notification + _frame(response))
                sys.stdout.buffer.flush()
            continue
        respond(response, split=args.mode == "fragment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
