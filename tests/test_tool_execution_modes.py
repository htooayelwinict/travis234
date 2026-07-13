from __future__ import annotations


def test_mutating_coding_tools_keep_travis234_default_execution_mode(tmp_path) -> None:
    from travis.coding_agent.tools.edit import create_edit_tool_definition
    from travis.coding_agent.tools.write import create_write_tool_definition

    cwd = str(tmp_path)

    assert create_write_tool_definition(cwd).execution_mode is None
    assert create_edit_tool_definition(cwd).execution_mode is None
