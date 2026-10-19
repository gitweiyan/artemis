"""I2: the pointer helper shared by the UVC and AirPlay capture paths.

The 2026-10-19 run could see the AssistiveTouch pointer in UVC, but artemis had no
``vision.pointer`` to turn that into a screen position. These tests pin the
two-frame contract: the pointer moved right by a known fraction, and the helper
returns where it was in the *before* frame.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from artemis.reflex.vision import pointer


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _frame(center: tuple[int, int]) -> bytes:
    image = Image.new("RGB", (400, 800), (210, 210, 210))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        [center[0] - 6, center[1] - 6, center[0] + 6, center[1] + 6],
        fill=(40, 40, 40),
    )
    return _png(image)


def test_pointer_returns_the_before_position_from_a_known_rightward_move():
    before = _frame((120, 400))
    after = _frame((140, 400))  # moved right by 0.05 of the frame width

    found = pointer(before, after, near=(0.30, 0.50), shift=0.05, reach=(0.20, 0.20))

    assert found is not None
    assert abs(found[0] - 0.30) < 0.02
    assert abs(found[1] - 0.50) < 0.02


def test_pointer_returns_none_when_nothing_moved():
    frame = _frame((120, 400))

    assert pointer(frame, frame, near=(0.30, 0.50), shift=0.05) is None


def test_pointer_returns_none_when_the_change_is_too_large_to_be_one_pointer():
    before = Image.new("RGB", (400, 800), (210, 210, 210))
    after = Image.new("RGB", (400, 800), (10, 10, 10))

    result = pointer(_png(before), _png(after), near=(0.30, 0.50), shift=0.05)

    assert result is None


def test_pointer_returns_none_when_the_change_is_outside_reach():
    before = _frame((40, 100))
    after = _frame((60, 100))

    result = pointer(before, after, near=(0.80, 0.80), shift=0.05, reach=(0.05, 0.05))

    assert result is None


def test_pointer_returns_the_before_position_for_a_large_nudge():
    before = _frame((120, 400))
    after = _frame((200, 400))  # moved right by 0.20 of the frame width

    found = pointer(before, after, near=(0.30, 0.50), shift=0.20, reach=(0.30, 0.30))

    assert found is not None
    assert abs(found[0] - 0.30) < 0.03
    assert abs(found[1] - 0.50) < 0.03
