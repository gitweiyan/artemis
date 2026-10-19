import cv2
import numpy as np
import pytest

from artemis.reflex.vision import RingShapeDetector


def _ring(center=(100, 100), radius=10, size=(200, 200)):
    image = np.full((size[1], size[0], 3), 180, dtype=np.uint8)
    cv2.circle(image, center, radius, (240, 240, 240), 3)
    return cv2.imencode('.png', image)[1].tobytes()


def test_finds_a_ring_shape_without_color():
    found = RingShapeDetector().detect(_ring())
    assert found is not None
    assert found.x == pytest.approx(0.5, abs=0.03)
    assert found.y == pytest.approx(0.5, abs=0.03)
    assert found.method == 'ring-shape'


def test_near_prefers_the_closer_circle():
    image = np.full((200, 200, 3), 180, dtype=np.uint8)
    cv2.circle(image, (50, 100), 10, (240, 240, 240), 3)
    cv2.circle(image, (150, 100), 10, (240, 240, 240), 3)
    payload = cv2.imencode('.png', image)[1].tobytes()
    found = RingShapeDetector().detect(payload, near=(0.75, 0.5))
    assert found is not None
    assert found.x == pytest.approx(0.75, abs=0.03)
