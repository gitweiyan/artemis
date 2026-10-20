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


def test_an_exact_match_at_moderate_confidence_is_accepted():
    """Deliberate contract change (2026-10-21, live rig).

    An OCR box whose whole text equals the requested label is the strongest signal the
    text locator can get; engines disagree about what a given certainty *means*
    (macOS Vision returns correct Chinese labels at 0.30, Paddle at 0.95). Only text
    that is barely legible is rejected, and a *partial* match still needs 0.5.
    """
    assert locate_text([box("闪购", 0.49)], "闪购") is not None
    assert locate_text([box("闪购", 0.30)], "闪购") is not None
    assert locate_text([box("闪购", 0.10)], "闪购") is None


def test_no_match_returns_none():
    assert locate_text([box("推荐"), box("外卖")], "闪购") is None


def test_exact_beats_higher_confidence_contains():
    target = locate_text([box("闪购频道", 0.99), box("闪购", 0.8)], "闪购")
    assert target is not None
    assert target.label == "闪购"


def test_an_exact_label_match_survives_a_low_ocr_confidence():
    """macOS Vision reports correct text at 0.30; an exact whole-label match is proof.

    Observed live (2026-10-21): 设置 came back at confidence 0.30 and the 0.5 gate
    discarded it, so the rig reported "text not found" on a screen that showed it.
    """
    from artemis.perception.base import TextBox
    from artemis.perception.text_locator import locate_text

    boxes = [
        TextBox("设置", (0.8, 0.29, 0.88, 0.32), 0.30),
        TextBox("备忘录", (0.1, 0.29, 0.21, 0.32), 0.30),
    ]

    target = locate_text(boxes, "设置")

    assert target is not None
    assert target.label == "设置"
    assert target.confidence == 0.30, "the evidence keeps the real confidence"


def test_a_substring_match_still_needs_a_confident_box():
    from artemis.perception.base import TextBox
    from artemis.perception.text_locator import locate_text

    weak = [TextBox("限时闪购中", (0.3, 0.1, 0.5, 0.2), 0.30)]
    strong = [TextBox("限时闪购中", (0.3, 0.1, 0.5, 0.2), 0.80)]

    assert locate_text(weak, "闪购") is None
    assert locate_text(strong, "闪购") is not None


def test_a_barely_legible_exact_match_is_still_rejected():
    from artemis.perception.base import TextBox
    from artemis.perception.text_locator import locate_text

    assert locate_text([TextBox("设置", (0.8, 0.29, 0.88, 0.32), 0.05)], "设置") is None
