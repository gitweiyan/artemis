def test_keyboard_shortcut_names_reach_the_canonical_tool():
    """The iOS rig is keyboard-first: these names must survive verbatim."""
    from artemis.mcp.action_names import to_canonical_call

    for keycode in ("KEYCODE_DPAD_DOWN", "KEYCODE_ESCAPE", "KEYCODE_ENTER"):
        name, args = to_canonical_call({"action": "press_key", "keycode": keycode})
        assert name == "press_key"
        assert args["key"] in {"down", "escape", "enter"}
