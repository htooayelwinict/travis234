from __future__ import annotations

import json
import sys


SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1


def envelope(command: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "command": command,
        "result": result,
        "nextActions": [],
    }


def guide() -> dict[str, object]:
    return envelope(
        "guide",
        {
            "commands": ["guide"],
            "invocation": "python3 scripts/orchestrate.py <command> [arguments]",
        },
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in (["guide"], ["-h"], ["--help"]):
        print(
            json.dumps(
                {
                    "ok": False,
                    "schemaVersion": SCHEMA_VERSION,
                    "protocolVersion": PROTOCOL_VERSION,
                    "command": arguments[0] if arguments else "guide",
                    "error": {
                        "code": "invalid_command",
                        "message": "Run guide for the supported command surface",
                    },
                    "nextActions": ["Run python3 scripts/orchestrate.py guide."],
                },
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(guide(), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
