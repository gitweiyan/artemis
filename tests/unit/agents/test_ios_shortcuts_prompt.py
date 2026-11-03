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
    assert "Globe" in prompt
    assert "App Switcher" in prompt
    assert "Activate" in prompt
    assert "focused search/text field" in prompt


def test_android_prompt_is_unchanged():
    assert append_ios_shortcuts("BASE", _ctx("android")) == "BASE"
