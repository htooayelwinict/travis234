from __future__ import annotations

from travis.agent import AgentContext, AgentLoopConfig, run_agent_loop

from tests._support_coding_agent import *  # noqa: F403


def test_default_tool_loop_guardrail_recovers_third_identical_failure_without_halting() -> None:
    from travis.coding_agent.policies.tool_guardrails import (
        REPEATED_EXACT_FAILURE_RECOVERY_BLOCK_CODE,
        ToolCallGuardrailController,
    )

    controller = ToolCallGuardrailController()
    args = {"command": "node --test index.test.js"}
    result = "FAIL index.test.js\nCommand exited with code 1"

    assert controller.before_call("bash", args).action == "allow"
    controller.after_call("bash", args, result, failed=True)
    assert controller.before_call("bash", args).action == "allow"
    controller.after_call("bash", args, result, failed=True)

    third = controller.before_call("bash", args)

    assert third.action == "block"
    assert third.code == REPEATED_EXACT_FAILURE_RECOVERY_BLOCK_CODE
    assert third.count == 3
    assert third.should_halt is False
    assert "earlier failure" in third.message


def test_default_tool_loop_guardrail_recovers_third_identical_bash_success() -> None:
    from travis.coding_agent.policies.tool_guardrails import (
        REPEATED_EXACT_SUCCESS_RECOVERY_BLOCK_CODE,
        ToolCallGuardrailController,
    )

    controller = ToolCallGuardrailController()
    args = {"command": "node --test index.test.js"}
    result = "tests 1\npass 1\nfail 0"

    assert controller.before_call("bash", args).action == "allow"
    controller.after_call("bash", args, result, failed=False)
    assert controller.before_call("bash", args).action == "allow"
    second = controller.after_call("bash", args, result, failed=False)
    third = controller.before_call("bash", args)

    assert second.action == "warn"
    assert second.code == "repeated_exact_success_warning"
    assert third.action == "block"
    assert third.code == REPEATED_EXACT_SUCCESS_RECOVERY_BLOCK_CODE
    assert third.count == 3
    assert third.should_halt is False

    controller.after_call(
        "write",
        {"path": "index.js", "content": "fixed"},
        "Successfully wrote index.js",
        failed=False,
    )
    assert controller.before_call("bash", args).action == "allow"


@pytest.mark.parametrize(
    ("bash_result", "recovery_code"),
    [
        (
            "FAIL index.test.js\nCommand exited with code 1",
            "repeated_exact_failure_recovery_block",
        ),
        (
            "tests 1\npass 1\nfail 0",
            "repeated_exact_success_recovery_block",
        ),
    ],
)
def test_agent_session_recovers_exact_bash_loop_in_same_turn(
    tmp_path: Path,
    bash_result: str,
    recovery_code: str,
) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    bash_executions: list[dict] = []
    write_executions: list[dict] = []
    repeated_args = {"command": "node --test index.test.js"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text=bash_result)], details={})

    def execute_write(tool_call_id, args, signal=None, on_update=None, ctx=None):
        write_executions.append(dict(args))
        return AgentToolResult(content=[TextContent(text="wrote recovery.txt")], details={})

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute_bash,
    )
    write_definition = ToolDefinition(
        name="write",
        label="write",
        description="Write a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        execute=execute_write,
    )

    def script(m, c):
        provider_calls["n"] += 1
        internal_recovery = any(
            getattr(message, "role", None) == "user"
            and recovery_code in _content_text(message.content)
            for message in c.messages
        )
        wrote = any(
            getattr(message, "role", None) == "toolResult"
            and getattr(message, "tool_name", None) == "write"
            for message in c.messages
        )
        if wrote:
            return text_response_events(m, "Recovered and completed without user follow-up.")
        if internal_recovery:
            return tool_call_response_events(
                m,
                "write",
                {"path": "recovery.txt", "content": "changed strategy\n"},
                call_id="write_recovery",
            )
        return tool_call_response_events(
            m,
            "bash",
            repeated_args,
            call_id=f"bash_{provider_calls['n']}",
        )

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tool_definitions=[bash_definition, write_definition],
        max_iterations=10,
    )

    session.prompt("finish without asking me to resume")

    tool_results = [message for message in session.messages if getattr(message, "role", None) == "toolResult"]
    assert bash_executions == [repeated_args, repeated_args]
    assert write_executions == [{"path": "recovery.txt", "content": "changed strategy\n"}]
    assert any(recovery_code in _content_text(result.content) for result in tool_results)
    assert session.messages[-1].content[0].text == "Recovered and completed without user follow-up."
    assert provider_calls["n"] == 5


def test_iteration_limit_replaces_literal_tool_markup_with_honest_runtime_message() -> None:
    model = faux_model()
    provider_calls = {"n": 0}

    def convert(messages):
        return [message for message in messages if getattr(message, "role", None) in {"user", "assistant", "toolResult"}]

    def script(m, c):
        provider_calls["n"] += 1
        if c.tools:
            return tool_call_response_events(
                m,
                "echo",
                {"text": "again"},
                call_id=f"call_{provider_calls['n']}",
            )
        return text_response_events(
            m,
            "<tool_call><function=echo><parameter=text>again</parameter></function></tool_call>",
        )

    register_api_provider(create_faux_provider(script))
    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        label="Echo",
        execute=lambda *_args, **_kwargs: AgentToolResult(content=[TextContent(text="ok")]),
    )
    config = AgentLoopConfig(model=model, convert_to_llm=convert)
    config.max_iterations = 1
    config.on_iteration_limit = lambda context: UserMessage(
        content="provide a plain-prose final response without tools",
        timestamp=now_ms(),
    )

    messages = run_agent_loop(
        [UserMessage(content="loop", timestamp=now_ms())],
        AgentContext(system_prompt="sys", messages=[], tools=[echo]),
        config,
        lambda _event: None,
    )

    final_text = messages[-1].content[0].text
    assert provider_calls["n"] == 2
    assert "<tool_call>" not in final_text
    assert "iteration limit (1/1)" in final_text
    assert "could not produce a reliable final response" in final_text
