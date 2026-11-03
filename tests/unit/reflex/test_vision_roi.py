"""Corridor-scoped pointer observation: search where the pointer should be, then widen."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from artemis.reflex.vision import CompositePointerDetector, corridor_roi, detect_in_roi

FRAME = (449, 974)


def _pointer_image(center: tuple[int, int], size: tuple[int, int] = FRAME) -> bytes:
    image = Image.new("RGB", size, (200, 200, 200))
    x, y = center
    ImageDraw.Draw(image).ellipse((x - 12, y - 12, x + 12, y + 12), outline=(0, 0, 255), width=4)
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_corridor_roi_pads_the_segment_and_clips_to_the_frame():
    assert corridor_roi((100, 900), (400, 100), radius=50, frame_size=FRAME) == (50, 50, 449, 950)
    assert corridor_roi((10, 10), (40, 40), radius=100, frame_size=FRAME) == (0, 0, 140, 140)


def test_a_pointer_inside_the_corridor_is_found_and_mapped_back():
    image = _pointer_image((300, 700))
    roi = corridor_roi((200, 600), (320, 760), radius=80, frame_size=FRAME)

    found = detect_in_roi(CompositePointerDetector(), image, roi)

    assert found is not None
    assert abs(found.x * FRAME[0] - 300) < 6, f"mapped x {found.x * FRAME[0]:.0f}"
    assert abs(found.y * FRAME[1] - 700) < 6, f"mapped y {found.y * FRAME[1]:.0f}"


def test_a_pointer_outside_the_corridor_is_missed_locally_and_found_globally():
    image = _pointer_image((60, 120))
    roi = corridor_roi((300, 600), (380, 700), radius=40, frame_size=FRAME)

    assert detect_in_roi(CompositePointerDetector(), image, roi) is None
    assert CompositePointerDetector().detect(image) is not None
