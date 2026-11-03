from types import SimpleNamespace

from artemis.agents.ios_shortcuts import append_ios_shortcuts, is_ios_device


def _ctx(platform):
    return SimpleNamespace(device=SimpleNamespace(mobile_platform=platform))


def test_ios_device_is_detected():
    assert is_ios_device(_ctx("ios")) is True
    assert is_ios_device(_ctx("android")) is False
    assert is_ios_device(SimpleNamespace(device=None)) is False


def test_ios_prompt_contains_keyboard_first_shortcuts():
    prompt = append_ios_shortcuts("BASE", _ctx("ios"))
    assert prompt.startswith("BASE")
    assert "keyboard before pointer" in prompt
    assert "Cmd+Space" in prompt
    assert "Cmd+H" in prompt
    assert "Activate" in prompt
    assert "Shift+Tab" in prompt
    assert "focused search/text field" in prompt
    # No Globe/Fn chord is sendable from the ESP32 HID keyboard, so the prompt must
    # not advertise the Full Keyboard Access command table as executable.
    assert "Globe" not in prompt


def test_android_prompt_is_unchanged():
    assert append_ios_shortcuts("BASE", _ctx("android")) == "BASE"
