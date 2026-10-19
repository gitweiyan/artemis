from artemis.devices.capabilities import CapabilitySet


def test_capabilities_default_to_none():
    caps = CapabilitySet()
    assert caps.supports_ui_tree is False
    assert caps.supports_absolute_tap is False
    assert caps.supports_relative_pointer is False
    assert caps.requires_pointer_precheck is False
    assert caps.supports_ocr is False
    assert caps.supports_text_input is False
    assert caps.supports_shell is False


def test_capabilities_are_frozen():
    caps = CapabilitySet(supports_ocr=True)
    assert caps.supports_ocr is True
