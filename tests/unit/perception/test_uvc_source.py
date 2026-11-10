import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from artemis.perception.uvc import UvcSource


def _png(color, size=(10, 10)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeCapture:
    def __init__(self, image):
        self.image = image

    def grab(self):
        return SimpleNamespace(image=self.image, width=10, height=10, captured_at=1.0)


def test_snapshot_returns_an_ok_frame():
    source = UvcSource(FakeCapture(_png((200, 200, 200))))

    frame = asyncio.run(source.snapshot())

    assert frame.health == "ok"
    assert frame.source == "uvc"
    assert frame.width == 10


def test_black_frame_is_classified_not_ok():
    source = UvcSource(FakeCapture(_png((0, 0, 0))))

    assert asyncio.run(source.snapshot()).health == "black"


def test_undecodable_frame_is_classified():
    source = UvcSource(FakeCapture(b"not an image"))

    assert asyncio.run(source.snapshot()).health == "decode_error"


def test_unavailable_methods_return_none():
    source = UvcSource(FakeCapture(_png((10, 10, 10))))

    assert asyncio.run(source.ui_tree()) is None
    assert asyncio.run(source.ocr(None)) is None
    assert asyncio.run(source.locate(None, None)) is None


def test_capture_exception_propagates():
    class Broken:
        def grab(self):
            raise RuntimeError("no signal")

    with pytest.raises(RuntimeError):
        asyncio.run(UvcSource(Broken()).snapshot())


class FakeOcr:
    def __init__(self):
        self.calls = []

    def recognize(self, image, *, width, height):
        from artemis.perception.base import TextBox

        self.calls.append((image, width, height))
        return [TextBox("闪购", (0.2, 0.1, 0.3, 0.2), 0.99)]


def test_uvc_source_delegates_ocr_when_injected():
    engine = FakeOcr()
    source = UvcSource(FakeCapture(_png((200, 200, 200))), ocr_engine=engine)
    frame = asyncio.run(source.snapshot())

    boxes = asyncio.run(source.ocr(frame))

    assert boxes[0].text == "闪购"
    assert engine.calls == [(frame.image, 10, 10)]
    assert source.capabilities.supports_ocr is True


def test_uvc_source_without_engine_returns_none():
    source = UvcSource(FakeCapture(_png((200, 200, 200))))
    assert asyncio.run(source.ocr(None)) is None
    assert source.capabilities.supports_ocr is False


def test_uvc_source_locates_text_target():
    from artemis.perception.base import Target

    source = UvcSource(FakeCapture(_png((200, 200, 200))), ocr_engine=FakeOcr())
    frame = asyncio.run(source.snapshot())

    target = asyncio.run(source.locate(frame, Target("闪购", (0, 0, 0, 0), 0, "ocr")))

    assert target is not None
    assert target.box == (0.2, 0.1, 0.3, 0.2)


def test_locate_lifts_a_label_onto_the_icon_above_it():
    """The springboard's label is not tappable; the target must be the icon."""
    from artemis.perception.base import Target, TextBox

    class Ocr:
        def recognize(self, image, *, width, height):
            return [TextBox("设置", (0.30, 0.40, 0.36, 0.42), 0.9)]

    def adjuster(image, box):
        return Target("", (0.28, 0.30, 0.38, 0.38), 1.0, "icon-above-label")

    source = UvcSource(
        FakeCapture(_png((200, 200, 200))), ocr_engine=Ocr(), target_adjuster=adjuster
    )
    frame = asyncio.run(source.snapshot())

    located = asyncio.run(source.locate(frame, Target("设置", (0, 0, 0, 0), 0, "ocr")))

    assert located is not None
    assert located.box == (0.28, 0.30, 0.38, 0.38)
    assert located.label == "设置", "the matched text stays as evidence"
    assert located.confidence == 0.9
    assert located.source == "icon-above-label"


def test_locate_keeps_the_text_box_when_nothing_is_above_it():
    from artemis.perception.base import Target, TextBox

    class Ocr:
        def recognize(self, image, *, width, height):
            return [TextBox("登录", (0.30, 0.40, 0.36, 0.42), 0.9)]

    source = UvcSource(
        FakeCapture(_png((200, 200, 200))),
        ocr_engine=Ocr(),
        target_adjuster=lambda image, box: None,
    )
    frame = asyncio.run(source.snapshot())

    located = asyncio.run(source.locate(frame, Target("登录", (0, 0, 0, 0), 0, "ocr")))

    assert located is not None
    assert located.box == (0.30, 0.40, 0.36, 0.42)


def test_a_profile_locator_is_used_before_ocr_and_declares_grounding_locate():
    import asyncio
    from types import SimpleNamespace

    from artemis.core.capabilities import CapabilityId
    from artemis.perception.base import Target
    from artemis.perception.uvc import UvcSource

    class Locator:
        def __init__(self):
            self.calls = []

        def locate(self, image, query):
            self.calls.append((image, query))
            return (0.5, 0.5)

    locator = Locator()
    source = UvcSource(FakeCapture(_png((200, 200, 200))), locator=locator)
    frame = SimpleNamespace(image=b"png", width=200, height=200, frame_id="frame-1", health="ok")

    target = asyncio.run(source.locate(frame, Target("Settings", (0.1, 0.1, 0.2, 0.2), 0.9, "ocr")))

    assert locator.calls == [(b"png", "Settings")]
    assert target is not None
    assert target.source == "locate-anything"
    assert target.frame_id == "frame-1"
    assert CapabilityId.GROUNDING_LOCATE in source.capability_manifest.declared_ids
