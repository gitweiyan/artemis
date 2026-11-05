"""Chinese-to-pinyin preprocessing for ``input_text`` on the HID keyboard.

The ESP32 keyboard types ASCII only, but mainstream Chinese apps accept toneless
pinyin in their search boxes (珍珠奶茶 -> zhenzhunaicha). Han runs are converted
before the ASCII guard so an agent's Chinese target text is usable instead of
failing the action; other non-ASCII (emoji, kana, ...) is still refused.
"""

from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin

__all__ = ["HAN_RUN", "to_ascii_input"]

#: CJK Unified Ideographs (+ Extension A and the compatibility block).
HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def to_ascii_input(text: str) -> str:
    """Replace each Han run with toneless, space-free pinyin; leave the rest as-is."""
    if not text:
        return ""
    return HAN_RUN.sub(
        lambda match: "".join(lazy_pinyin(match.group(0), style=Style.NORMAL)),
        str(text),
    )
