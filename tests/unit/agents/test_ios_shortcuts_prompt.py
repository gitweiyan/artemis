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
    assert "pinyin" in prompt, "iOS app names are typed with an ASCII-only keyboard"
    assert "focused search/text field" in prompt
    # No Globe/Fn chord is sendable from the ESP32 HID keyboard, so the prompt must
    # not advertise the Full Keyboard Access command table as executable.
    assert "Globe" not in prompt


def test_android_prompt_is_unchanged():
    assert append_ios_shortcuts("BASE", _ctx("android")) == "BASE"


def test_the_spotlight_shortcut_is_annotated_as_in_app_search_only():
    """Cmd+Space sits in the capability list, so it reads like an approved launch route.

    2026-11-06 (milk-cha): the model used it to launch Taobao twice instead of
    manage_app, both times ending on the home screen.
    """
    prompt = append_ios_shortcuts("BASE", _ctx("ios"))
    line = next(line for line in prompt.splitlines() if "Cmd+Space" in line)

    assert "manage_app" in line, "the shortcut line must point at the tool that launches"
    assert "search" in line.lower()


def test_profile_guidance_is_used_instead_of_the_legacy_platform_branch():
    from artemis.core.capabilities import CapabilityManifest
    from artemis.core.platform_extension import (
        PlatformExtension,
        PromptGuidance,
        PromptSection,
    )

    extension = PlatformExtension(
        extension_id="test.keyboard",
        platform_family="test",
        capabilities=CapabilityManifest("test", "1"),
        prompt_guidance=PromptGuidance(
            guidance_id="test.keyboard",
            sections=(PromptSection("rule", "Profile rule", "Use the profile keyboard rule."),),
        ),
    )
    ctx = SimpleNamespace(
        device=SimpleNamespace(mobile_platform="ios"),
        device_session=SimpleNamespace(adapter_state={"platform_extension": extension}),
    )

    prompt = append_ios_shortcuts("BASE", ctx)

    assert "Profile rule" in prompt
    assert "Use the profile keyboard rule." in prompt
    assert "Cmd+Space" not in prompt
    assert append_ios_shortcuts(prompt, ctx) == prompt
