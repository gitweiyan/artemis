import cv2
import numpy as np
import pytest

from artemis.reflex.vision import BluePointerDetector, CompositePointerDetector, RedPointerDetector


def _ring(center=(100, 100), radius=20, color=(255, 0, 0), size=(200, 200)):
    image = np.full((size[1], size[0], 3), 180, dtype=np.uint8)
    cv2.circle(image, center, radius, color, 4)
    return cv2.imencode('.png', image)[1].tobytes()


def test_blue_ring_is_found_by_hollow_ring():
    found = BluePointerDetector().detect(_ring())
    assert found is not None
    assert found.method == 'blue-ring'
    assert found.x == pytest.approx(0.5, abs=0.03)
    assert found.y == pytest.approx(0.5, abs=0.03)


def test_blue_disc_is_rejected():
    image = np.full((200, 200, 3), 180, dtype=np.uint8)
    cv2.circle(image, (100, 100), 20, (255, 0, 0), -1)
    payload = cv2.imencode('.png', image)[1].tobytes()
    assert BluePointerDetector().detect(payload) is None


def test_roi_rejects_far_blue_ring():
    image = np.full((200, 200, 3), 180, dtype=np.uint8)
    cv2.circle(image, (50, 100), 20, (255, 0, 0), 4)
    cv2.circle(image, (150, 100), 20, (255, 0, 0), 4)
    payload = cv2.imencode('.png', image)[1].tobytes()
    found = BluePointerDetector().detect(payload, near=(0.75, 0.5), roi_px=50)
    assert found is not None
    assert found.x == pytest.approx(0.75, abs=0.03)


def test_composite_falls_back_to_red_when_blue_missing():
    red = _ring(color=(0, 0, 255))
    detector = CompositePointerDetector([BluePointerDetector(), RedPointerDetector()])
    found = detector.detect(red)
    assert found is not None
    assert found.method == 'red-ring'
