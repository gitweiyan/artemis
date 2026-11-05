"""Han characters are transliterated before they reach the ASCII-only keyboard."""

from artemis.reflex.pinyin import to_ascii_input


def test_a_han_run_becomes_toneless_space_free_pinyin():
    assert to_ascii_input("珍珠奶茶") == "zhenzhunaicha"


def test_mixed_text_keeps_ascii_and_spacing():
    assert to_ascii_input("搜索 珍珠奶茶!") == "sousuo zhenzhunaicha!"


def test_ascii_is_unchanged():
    assert to_ascii_input("taobao 42") == "taobao 42"


def test_empty_text_stays_empty():
    assert to_ascii_input("") == ""
