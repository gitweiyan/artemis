from artemis.perception.base import TextBox
from artemis.perception.text_locator import locate_text


def box(text, confidence=0.9, box=(0.1, 0.1, 0.2, 0.2)):
    return TextBox(text=text, box=box, confidence=confidence)


def test_exact_match_returns_target():
    target = locate_text([box("闪购", 0.99, (0.3, 0.1, 0.4, 0.2))], "闪购")
    assert target is not None
    assert target.label == "闪购"
    assert target.box == (0.3, 0.1, 0.4, 0.2)
    assert target.source == "ocr"


def test_contains_matches_when_no_exact():
    target = locate_text([box("淘宝闪购外卖", 0.9)], "闪购")
    assert target is not None
    assert target.label == "淘宝闪购外卖"


def test_low_confidence_is_rejected():
    assert locate_text([box("闪购", 0.49)], "闪购") is None


def test_no_match_returns_none():
    assert locate_text([box("推荐"), box("外卖")], "闪购") is None


def test_exact_beats_higher_confidence_contains():
    target = locate_text([box("闪购频道", 0.99), box("闪购", 0.8)], "闪购")
    assert target is not None
    assert target.label == "闪购"
