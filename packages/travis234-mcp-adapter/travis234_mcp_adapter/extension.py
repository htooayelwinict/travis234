from __future__ import annotations


def extension(travis) -> None:
    travis.register_command(
        "mcp-package-probe",
        {
            "description": "Confirm that the optional MCP adapter package loaded.",
            "handler": lambda _args, _ctx: [],
        },
    )
