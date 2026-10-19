from artemis.devices.capabilities import CapabilitySet
from artemis.perception.base import Frame, PerceptionSource, Target


class FakeSource:
    capabilities = CapabilitySet(supports_ocr=True)

    async def snapshot(self):
        return Frame(b"x", 1.0, 10, 20, "fake")

    async def ui_tree(self):
        return None

    async def ocr(self, frame):
        return []

    async def locate(self, frame, target):
        return target


def test_fake_source_satisfies_protocol():
    assert isinstance(FakeSource(), PerceptionSource)


def test_frame_and_target_keep_contract_fields():
    frame = Frame(b"x", 1.0, 10, 20, "fake")
    target = Target("button", (0.1, 0.2, 0.3, 0.4), 0.9, "ocr")
    assert frame.health == "ok"
    assert target.box == (0.1, 0.2, 0.3, 0.4)
