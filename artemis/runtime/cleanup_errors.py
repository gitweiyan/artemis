"""Exception-safe cleanup capture without broad ``except Exception`` handlers."""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["CleanupErrorCapture"]


class CleanupErrorCapture:
    """Capture one ordinary cleanup exception; let cancellation/exit propagate."""

    def __init__(self) -> None:
        self.error: BaseException | None = None

    def __enter__(self) -> CleanupErrorCapture:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if exc_type is None:
            return False
        if issubclass(
            exc_type,
            (KeyboardInterrupt, SystemExit, GeneratorExit, asyncio.CancelledError),
        ):
            return False
        self.error = exc
        return True
