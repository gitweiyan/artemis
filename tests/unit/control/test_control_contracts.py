from artemis.control.base import ActionResult, ControlSink
from artemis.devices.capabilities import CapabilitySet


class FakeSink:
    capabilities = CapabilitySet(
        supports_relative_pointer=True,
        requires_pointer_precheck=True,
    )

    async def tap_abs(self, x, y):
        return ActionResult(True, "tap_abs")

    async def tap_rel(self, dx, dy):
        return ActionResult(True, "tap_rel")

    async def swipe(self, *args, **kwargs):
        return ActionResult(True, "swipe")

    async def input_text(self, text):
        return ActionResult(True, "input_text")

    async def press_key(self, key):
        return ActionResult(True, "press_key")


def test_fake_sink_satisfies_protocol():
    assert isinstance(FakeSink(), ControlSink)


def test_action_result_defaults():
    result = ActionResult(True, "tap")
    assert result.incident is None
    assert result.detail == ""
