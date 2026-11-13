"""L1 adapters from framework-neutral action results to LangChain messages."""

from __future__ import annotations

import base64
from typing import Any

from langchain_core.messages import ToolMessage

from artemis.actions.results import ToolExecutionResult

__all__ = ["to_langchain_tool_message"]


def to_langchain_tool_message(result: ToolExecutionResult) -> ToolMessage:
    """Convert one action result into the multimodal LangChain ToolMessage."""

    content_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": result.text_summary or f"Action '{result.tool_name}' dispatched."}
    ]
    if result.ui_elements_text:
        content_blocks.append(
            {"type": "text", "text": f"--- UI Element List ---\n{result.ui_elements_text}"}
        )
    if result.screenshot_bytes:
        b64_img = base64.b64encode(result.screenshot_bytes).decode("utf-8")
        content_blocks.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
        )
    return ToolMessage(
        tool_call_id=result.tool_call_id,
        name=result.tool_name,
        content=content_blocks,
        status=result.status,
    )
