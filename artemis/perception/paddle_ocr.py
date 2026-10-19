"""PaddleOCR output adapter: text, confidence, normalized boxes."""

from __future__ import annotations

from io import BytesIO

from artemis.perception.base import TextBox


class _PaddleAdapter:
    """PaddleOCR accepts numpy images, not raw screenshot bytes."""

    def __init__(self, engine):
        self.engine = engine

    def predict(self, image: bytes):
        import numpy as np
        from PIL import Image

        array = np.asarray(Image.open(BytesIO(image)).convert("RGB"))
        return self.engine.predict(array)


class PaddleOcrEngine:
    """Lazily own a PaddleOCR engine; parsing stays independent of the runtime."""

    def __init__(self, factory=None, *, lang: str = "ch", version: str = "PP-OCRv6"):
        self.factory = factory
        self.lang = lang
        self.version = version
        self._engine = None

    def _ensure(self):
        if self._engine is None:
            if self.factory is not None:
                self._engine = self.factory()
            else:
                from paddleocr import PaddleOCR  # lazy: tests need no Paddle

                self._engine = _PaddleAdapter(
                    PaddleOCR(
                        lang=self.lang,
                        ocr_version=self.version,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
                )
        return self._engine

    @staticmethod
    def _normalized_box(poly, width: int, height: int):
        xs = [point[0] for point in poly]
        ys = [point[1] for point in poly]
        return (
            min(xs) / width,
            min(ys) / height,
            max(xs) / width,
            max(ys) / height,
        )

    def recognize(self, image: bytes, *, width: int, height: int) -> list[TextBox]:
        engine = self._ensure()
        results = engine.predict(image)
        boxes: list[TextBox] = []
        for result in results or []:
            payload = getattr(result, "json", {}) or {}
            data = payload.get("res", payload)
            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []
            polys = data.get("rec_polys") or []
            for text, score, poly in zip(texts, scores, polys):
                if not text or not poly:
                    continue
                boxes.append(
                    TextBox(
                        text=str(text),
                        box=self._normalized_box(poly, width, height),
                        confidence=float(score),
                    )
                )
        return boxes
