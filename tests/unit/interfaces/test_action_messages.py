"""L1 LangChain message adapter keeps L3 result DTOs framework-neutral."""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from artemis.actions.results import ToolExecutionResult
from artemis.interfaces.action_messages import to_langchain_tool_message


def test_tool_message_keeps_text_ui_list_and_image_blocks():
    result = ToolExecutionResult(
        tool_call_id="tc-1",
        tool_name="click",
        status="success",
        text_summary="clicked",
        ui_elements_text="[1] Text: 'OK'",
        screenshot_bytes=b"fake-image",
    )

    message = to_langchain_tool_message(result)

    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "tc-1"
    assert message.content[0]["text"] == "clicked"
    assert message.content[1]["text"].startswith("--- UI Element List ---")
    assert any(block.get("type") == "image_url" for block in message.content)
