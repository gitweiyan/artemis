"""Cross-layer result envelope for one agent tool execution.

This DTO used to live inside ``agents.validator.tool_declarations``; keeping it
there made the L3 action layer depend on L2. The old module re-exports this
object for compatibility.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

__all__ = ["ToolExecutionResult"]


class ToolExecutionResult(BaseModel):
    """Standardized result produced by executing a mobile automation or utility tool."""

    tool_call_id: str
    tool_name: str
    status: Literal["success", "error", "cancelled"]
    text_summary: str
    screenshot_bytes: bytes | None = None
    screenshot_path: str | None = None
    ui_elements_text: str | None = None
    raw_result: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_langchain_tool_message(self) -> ToolMessage:
        """Converts execution result into a standardized multimodal LangChain ToolMessage."""
        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": self.text_summary or f"Action '{self.tool_name}' dispatched."}
        ]
        if self.ui_elements_text:
            content_blocks.append(
                {
                    "type": "text",
                    "text": f"--- UI Element List ---\n{self.ui_elements_text}",
                }
            )
        if self.screenshot_bytes:
            b64_img = base64.b64encode(self.screenshot_bytes).decode("utf-8")
            content_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                }
            )
        return ToolMessage(
            tool_call_id=self.tool_call_id,
            name=self.tool_name,
            content=content_blocks,
            status=self.status,
        )
