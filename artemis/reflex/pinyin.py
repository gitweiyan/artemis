"""Compatibility shim: pinyin preprocessing lives in artemis.text (Phase 9)."""

from __future__ import annotations

from artemis.text.pinyin import HAN_RUN, to_ascii_input  # noqa: F401

__all__ = ["HAN_RUN", "to_ascii_input"]
