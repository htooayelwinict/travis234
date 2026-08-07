from __future__ import annotations

import pytest

from travis234_mcp_adapter.extension import ExtensionState
from travis234_mcp_adapter.proxy_tool import dispatch_proxy


@pytest.mark.anyio
async def test_non_status_operation_is_explicitly_unimplemented() -> None:
    result = await dispatch_proxy(ExtensionState(), {"server": "fixture"}, None)

    assert result.content[0].text == "MCP operation is not implemented yet."
    assert result.details == {
        "travis234Mcp": {
            "operation": "not_implemented",
            "isError": True,
        }
    }
