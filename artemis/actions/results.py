"""Cross-layer result envelope for one agent tool execution.

This DTO used to live inside ``agents.validator.tool_declarations``; keeping it
there made the L3 action layer depend on L2. The old module re-exports this
object for compatibility.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

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
