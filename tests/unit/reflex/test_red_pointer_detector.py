"""Detect the current red AssistiveTouch pointer in one on-demand UVC frame.

The pointer is a saturated red ring (roughly 38x38 px in the 449x974 crop). Red
badges are smaller, so the detector filters by blob size/area, prefers a blob near
the last known position, and returns normalized coordinates for the servo loop.
"""

from __future__ import annotations

import pytest

from artemis.reflex.vision import RedPointerDetector


def _ring(center=(100, 100), radius=20, thickness=4, size=(200, 200)) -> bytes:
    import cv2
    import numpy as np

    image = np.full((size[1], size[0], 3), 240, dtype=np.uint8)
    cv2.circle(image, center, radius, (0, 0, 255), thickness)
    return cv2.imencode(".png", image)[1].tobytes()


def test_finds_the_red_ring_center():
    detector = RedPointerDetector(size_range=(30, 50), area_range=(300, 1200))

    found = detector.detect(_ring())

    assert found is not None
    assert found.x == pytest.approx(0.5, abs=0.02)
    assert found.y == pytest.approx(0.5, abs=0.02)


def test_ignores_a_small_red_dot():
    import cv2
    import numpy as np

    image = np.full((200, 200, 3), 240, dtype=np.uint8)
    cv2.circle(image, (50, 50), 3, (0, 0, 255), -1)
    payload = cv2.imencode(".png", image)[1].tobytes()
    detector = RedPointerDetector(size_range=(30, 50), area_range=(300, 1200))

    assert detector.detect(payload) is None


def test_near_prefers_the_closer_ring():
    import cv2
    import numpy as np

    image = np.full((200, 200, 3), 240, dtype=np.uint8)
    cv2.circle(image, (50, 100), 20, (0, 0, 255), 4)
    cv2.circle(image, (150, 100), 20, (0, 0, 255), 4)
    payload = cv2.imencode(".png", image)[1].tobytes()
    detector = RedPointerDetector(size_range=(30, 50), area_range=(300, 1200))

    found = detector.detect(payload, near=(0.75, 0.5))

    assert found is not None
    assert found.x == pytest.approx(0.75, abs=0.02)


def test_non_image_is_rejected():
    detector = RedPointerDetector()
    assert detector.detect(b"not an image") is None


def test_rejects_a_solid_red_ad_block():
    import cv2
    import numpy as np

    image = np.full((200, 200, 3), 240, dtype=np.uint8)
    cv2.rectangle(image, (80, 80), (120, 120), (0, 0, 255), -1)
    payload = cv2.imencode(".png", image)[1].tobytes()
    detector = RedPointerDetector(size_range=(30, 50), area_range=(300, 1200))

    assert detector.detect(payload) is None


def _ring_image(center, radius=19, thickness=7, size=(449, 200)):
    """A red AssistiveTouch ring on a light background (BGR via cv2)."""
    import cv2
    import numpy as np

    image = np.full((size[1], size[0], 3), 235, dtype=np.uint8)
    cv2.circle(image, center, radius, (0, 0, 220), thickness)
    return cv2.imencode(".png", image)[1].tobytes()


def test_a_clipped_ring_at_the_frame_edge_is_still_the_pointer():
    """The capture is a crop of the phone: a pointer at the edge is cut in half.

    Live (2026-10-21): the pointer parked at the right edge was rejected by the
    size/area filters, so the loop believed it had vanished mid-tap.
    """
    from artemis.reflex.vision import RedPointerDetector

    image = _ring_image((443, 100))  # the ring is cut by the right border

    pointer = RedPointerDetector().detect(image)

    assert pointer is not None
    assert pointer.x > 0.9 and 0.4 < pointer.y < 0.6


def test_a_solid_red_block_at_the_edge_is_not_a_pointer():
    import cv2
    import numpy as np

    from artemis.reflex.vision import RedPointerDetector

    image = np.full((200, 449, 3), 235, dtype=np.uint8)
    cv2.rectangle(image, (420, 60), (449, 140), (0, 0, 220), -1)
    png = cv2.imencode(".png", image)[1].tobytes()

    assert RedPointerDetector().detect(png) is None


def test_a_full_ring_is_still_found():
    from artemis.reflex.vision import RedPointerDetector

    pointer = RedPointerDetector().detect(_ring_image((225, 100), radius=19, thickness=7))

    assert pointer is not None
    assert 0.45 < pointer.x < 0.55
