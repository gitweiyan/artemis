from types import SimpleNamespace

import pytest

from artemis.perception.paddle_ocr import PaddleOcrEngine


class FakePaddle:
    def __init__(self):
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        return [
            SimpleNamespace(
                json={
                    "res": {
                        "rec_texts": ["关注", "闪购", "外卖"],
                        "rec_scores": [0.99, 0.98, 0.97],
                        "rec_polys": [
                            [[10, 20], [60, 20], [60, 40], [10, 40]],
                            [[70, 20], [120, 20], [120, 45], [70, 45]],
                            [[130, 20], [180, 20], [180, 40], [130, 40]],
                        ],
                    }
                }
            )
        ]


def test_lazy_factory_and_box_conversion():
    fake = FakePaddle()
    calls = []
    engine = PaddleOcrEngine(factory=lambda: (calls.append(1), fake)[1])

    boxes = engine.recognize(b"png", width=200, height=100)

    assert calls == [1]
    assert [box.text for box in boxes] == ["关注", "闪购", "外卖"]
    assert boxes[1].box == pytest.approx((0.35, 0.2, 0.6, 0.45))
    assert boxes[1].confidence == pytest.approx(0.98)
    assert engine.recognize(b"png", width=200, height=100)
    assert calls == [1], "engine is initialized once"


def test_empty_result_returns_empty_list():
    engine = PaddleOcrEngine(factory=lambda: SimpleNamespace(predict=lambda image: []))
    assert engine.recognize(b"png", width=10, height=10) == []
