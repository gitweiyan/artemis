"""Coordinate spaces and the single transform graph (ADR-002)."""

from __future__ import annotations

import pytest

from artemis.core.coordinates import (
    TRANSFORM_SCHEMA,
    BoxRef,
    CoordinateSpace,
    CoordinateTransform,
    PointRef,
    TransformParams,
    TransformRecord,
    transform_id,
)
from artemis.devices.geometry import ScreenGeometry

FRAME = (449, 974)
SCREEN = (1080, 2400)


def _frame_transform(**kwargs):
    return CoordinateTransform.frame_to_device(frame_size=FRAME, screen_size=SCREEN, **kwargs)


def test_frame_to_device_maps_corners_and_roundtrips():
    transform = _frame_transform()
    origin = transform.apply(PointRef(CoordinateSpace.FRAME_PIXEL, 0, 0))
    corner = transform.apply(PointRef(CoordinateSpace.FRAME_PIXEL, 448, 973))

    assert origin.space is CoordinateSpace.DEVICE_PIXEL
    assert (round(origin.x), round(origin.y)) == (0, 0)
    assert abs(corner.x - SCREEN[0]) <= 3
    assert abs(corner.y - SCREEN[1]) <= 3

    back = transform.invert(corner)
    assert abs(back.x - 448) <= 1.5
    assert abs(back.y - 973) <= 1.5


def test_crop_and_letterbox_are_part_of_the_transform():
    transform = _frame_transform(content_rect=(100, 50, 300, 250))
    point = transform.apply(PointRef(CoordinateSpace.FRAME_PIXEL, 100, 50))
    assert (round(point.x), round(point.y)) == (0, 0)

    center = PointRef(CoordinateSpace.FRAME_PIXEL, 200, 150)
    device = transform.apply(center)
    assert abs(device.x - SCREEN[0] / 2) < 1
    assert abs(device.y - SCREEN[1] / 2) < 1


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotation_roundtrips(rotation):
    transform = _frame_transform(rotation_degrees=rotation)
    point = PointRef(CoordinateSpace.FRAME_PIXEL, 123, 456)

    device = transform.apply(point)
    back = transform.invert(device)

    assert abs(back.x - point.x) <= 1.5
    assert abs(back.y - point.y) <= 1.5


def test_rotation_90_swaps_the_axes():
    transform = _frame_transform(rotation_degrees=90)
    top_left = transform.apply(PointRef(CoordinateSpace.FRAME_PIXEL, 0, 0))

    assert abs(top_left.x - SCREEN[0]) <= 2
    assert abs(top_left.y) <= 2


def test_device_to_pointer_frame_uses_the_full_crop():
    transform = CoordinateTransform.device_to_frame(
        frame_size=FRAME,
        screen_size=SCREEN,
        content_rect=(0, 0, *FRAME),
        target=CoordinateSpace.POINTER_FRAME_PIXEL,
    )
    point = transform.apply(PointRef(CoordinateSpace.DEVICE_PIXEL, SCREEN[0] / 2, SCREEN[1] / 2))

    assert point.space is CoordinateSpace.POINTER_FRAME_PIXEL
    assert abs(point.x - FRAME[0] / 2) <= 1.5
    assert abs(point.y - FRAME[1] / 2) <= 1.5


def test_agent_point_and_target_box_convert_through_the_screen():
    params = TransformParams(
        source=CoordinateSpace.AGENT_POINT,
        target=CoordinateSpace.DEVICE_PIXEL,
        frame_size=FRAME,
        screen_size=SCREEN,
    )
    transform = CoordinateTransform(TransformRecord(transform_id(params), params))
    center = transform.apply(PointRef(CoordinateSpace.AGENT_POINT, 500, 500))

    assert (center.x, center.y) == (SCREEN[0] / 2, SCREEN[1] / 2)

    box = BoxRef(CoordinateSpace.TARGET_BOX, 0.25, 0.25, 0.75, 0.75)
    # A target-box transform is only meaningful against the same screen size.
    box_params = TransformParams(
        source=CoordinateSpace.TARGET_BOX,
        target=CoordinateSpace.DEVICE_PIXEL,
        frame_size=FRAME,
        screen_size=SCREEN,
    )
    box_transform = CoordinateTransform(TransformRecord(transform_id(box_params), box_params))
    device_box = box_transform.apply_box(box)
    assert device_box.left == SCREEN[0] * 0.25
    assert device_box.bottom == SCREEN[1] * 0.75


def test_a_point_from_the_wrong_space_is_rejected():
    transform = _frame_transform()
    with pytest.raises(ValueError, match="does not match transform source"):
        transform.apply(PointRef(CoordinateSpace.DEVICE_PIXEL, 1, 1))


def test_transform_id_is_a_content_hash_not_a_revision():
    first = _frame_transform(frame_id="frame-a")
    same_math = _frame_transform(frame_id="frame-b")

    assert first.transform_id == same_math.transform_id
    assert first.transform_id.startswith("xform-")

    record = TransformRecord(
        first.transform_id,
        first.params,
        revision="ios-rig/2026-11-10",
    )
    assert record.transform_id == first.transform_id
    assert record.verify() is True


def test_parameter_or_schema_changes_change_the_id():
    base = _frame_transform()
    cropped = _frame_transform(content_rect=(10, 10, 400, 900))
    rotated = _frame_transform(rotation_degrees=90)

    assert base.transform_id != cropped.transform_id
    assert base.transform_id != rotated.transform_id
    assert transform_id(base.params, schema="coordinate-transform/v2") != base.transform_id


def test_a_tampered_transform_record_is_rejected():
    transform = _frame_transform()
    with pytest.raises(ValueError, match="transform_id mismatch"):
        TransformRecord("xform-deadbeef", transform.params)


def test_screen_geometry_exposes_the_same_transform_numbers():
    geometry = ScreenGeometry(FRAME, SCREEN)
    transform = geometry.transform()

    for x, y in ((0, 0), (100, 200), (448, 973)):
        device = transform.apply(PointRef(CoordinateSpace.FRAME_PIXEL, x, y))
        expected = geometry.screen_to_normalized(round(device.x), round(device.y))
        actual = geometry.frame_to_normalized(x, y)
        assert abs(expected[0] - actual[0]) <= 1
        assert abs(expected[1] - actual[1]) <= 1

    pointer = geometry.pointer_transform()
    for x, y in ((0, 0), (540, 1200), (1079, 2399)):
        device = PointRef(CoordinateSpace.DEVICE_PIXEL, x, y)
        point = pointer.apply(device)
        expected = geometry.screen_to_frame(x, y)
        assert abs(round(point.x) - expected[0]) <= 1
        assert abs(round(point.y) - expected[1]) <= 1
