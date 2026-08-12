from __future__ import annotations

from weakref import WeakKeyDictionary

from travis234_mcp_adapter import PackagedServer, register_packaged_server

from travis234_ghost_mcp.commands import doctor_handler, setup_handler
from travis234_ghost_mcp.host import ghost_binary, package_root

_INSTALLED: WeakKeyDictionary[object, bool] = WeakKeyDictionary()


def ghost_server_descriptor() -> PackagedServer:
    root = package_root()
    return PackagedServer(
        name="ghost-os",
        package_root=root,
        command=ghost_binary(),
        args=("mcp",),
        request_timeout_ms=1_800_000,
    )


def extension(travis) -> None:
    runner = getattr(travis, "_runner", travis)
    if runner in _INSTALLED:
        return
    register_packaged_server(ghost_server_descriptor())
    travis.register_command(
        "ghost-setup",
        {
            "description": "Set up bundled Ghost permissions and recipes",
            "handler": setup_handler,
        },
    )
    travis.register_command(
        "ghost-doctor",
        {
            "description": "Diagnose the bundled Ghost computer-use MCP",
            "handler": doctor_handler,
        },
    )
    _INSTALLED[runner] = True


__all__ = ["extension", "ghost_server_descriptor"]
