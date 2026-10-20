import pytest

from artemis.devices.geometry import ScreenGeometry


def test_full_frame_maps_center_to_screen_center():
    geometry = ScreenGeometry((449, 974), (1080, 2400))

    assert geometry.frame_to_normalized(224, 487) == (499, 500)
    assert geometry.normalized_to_screen(500, 500) == (540, 1200)


def test_letterboxed_content_rect_is_honored():
    geometry = ScreenGeometry(
        (1920, 1080),
        (1080, 2400),
        content_rect=(736, 53, 1185, 1027),
    )

    assert geometry.normalized_to_frame(0, 0) == (736, 53)
    assert geometry.normalized_to_screen(1000, 1000) == (1080, 2400)
    assert geometry.frame_to_normalized(736, 53) == (0, 0)


def test_invalid_geometry_is_rejected():
    with pytest.raises(ValueError):
        ScreenGeometry((0, 100), (100, 100))

    with pytest.raises(ValueError):
        ScreenGeometry((100, 100), (100, 100), content_rect=(0, 0, 200, 100))


def test_normalized_box_maps_to_screen_center():
    geometry = ScreenGeometry((449, 974), (1080, 2400))
    box = (0.296, 0.0719, 0.394, 0.0955)

    px = geometry.normalized_box_to_screen(box)
    center = geometry.normalized_box_center(box)

    assert px == (320, 173, 426, 229)
    assert center == (373, 201)


def test_screen_pixels_round_trip_through_the_capture_frame():
    from artemis.devices.geometry import ScreenGeometry

    geometry = ScreenGeometry((449, 974), (1080, 2400))

    assert geometry.screen_to_frame(540, 1200) == (224, 487)
    assert geometry.screen_to_normalized(540, 1200) == (500, 500)
    assert geometry.screen_to_frame(0, 0) == (0, 0)
    assert geometry.screen_to_frame(1080, 2400) == (449, 974)


def test_screen_conversion_honours_the_content_rect():
    from artemis.devices.geometry import ScreenGeometry

    geometry = ScreenGeometry((600, 1000), (1080, 2400), (100, 10, 500, 990))

    assert geometry.screen_to_normalized(540, 1200) == (500, 500)
    assert geometry.screen_to_frame(540, 1200) == (300, 500)
