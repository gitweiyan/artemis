"""The playfield as a *proportion* of the screen, cropped to one fixed geometry.

The board does not move, but the screen it sits on varies: a different phone, a
different density, a screenshot with a status bar. An absolute pixel box measured on
one device is wrong on the next; a ratio survives, and the crop it produces is the
single geometry every later stage reads — so cell pitch, grid counting and piece
separation all have the same numbers to work with.

420x855 px -> 400x800 puts the cell at 40 x 40 px in the crop, which is what makes a
cell's pixels a well-defined region (see ``cells.py``).

Downscaling uses a bilinear filter on purpose: blocks have anti-aliased, glowing
edges, and point sampling along such an edge would flip a cell's reading frame by
frame. Averaging is what a 5x5 centre median then reads stably.
"""

from __future__ import annotations

from typing import Any, Sequence

#: Calibrated ratios are relative to the *image*, in ``(x0, y0, x1, y1)`` order.
RATIO_KEY = "board_roi_ratios"
#: ``[width, height]`` of the normalised crop.
SIZE_KEY = "normalized_size"


def _ratios(calibration: dict[str, Any]) -> Sequence[float]:
    ratios = (calibration or {}).get(RATIO_KEY)
    if not isinstance(ratios, (list, tuple)) or len(ratios) != 4:
        raise ValueError(
            f"calibration is missing {RATIO_KEY}: [x0, y0, x1, y1] as 0-1 fractions of the screen"
        )
    return ratios


def _normalised_size(calibration: dict[str, Any]) -> tuple[int, int]:
    size = (calibration or {}).get(SIZE_KEY)
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise ValueError(f"calibration is missing {SIZE_KEY}: [width, height] of the crop")
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:  # pragma: no cover - defensive
        raise ValueError(f"{SIZE_KEY} must be positive, got {size!r}")
    return width, height


def roi_pixels(shape: Sequence[int], calibration: dict[str, Any]) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` in pixels of an image of ``shape`` (``(height, width, ...)``).

    Clamped to the image: a ratio outside 0-1 is a calibration mistake, and
    wrapping around would crop a strip of the *opposite* side of the screen — a
    silent, plausible-looking wrong crop.
    """
    height, width = int(shape[0]), int(shape[1])
    x0, y0, x1, y1 = (float(value) for value in _ratios(calibration))
    box = (
        min(max(round(x0 * width), 0), width),
        min(max(round(y0 * height), 0), height),
        min(max(round(x1 * width), 0), width),
        min(max(round(y1 * height), 0), height),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(
            f"calibrated {RATIO_KEY} gives an empty region on a {width}x{height} screen"
        )
    return box


def board_crop(image, calibration: dict[str, Any]):
    """The playfield as ``(normalised_height, normalised_width, 3)`` float32 RGB.

    Accepts whatever a capture path hands over (uint8 or float): the crop is
    converted, never re-encoded, and the input array is left alone.
    """
    import numpy as np
    from PIL import Image

    array = np.asarray(image, dtype="float32")
    if array.ndim != 3 or array.shape[2] < 3:  # pragma: no cover - defensive
        raise ValueError(f"expected an (H, W, 3) image, got {array.shape}")
    x0, y0, x1, y1 = roi_pixels(array.shape, calibration)
    width, height = _normalised_size(calibration)
    region = array[y0:y1, x0:x1, :3]
    if region.shape[0] == height and region.shape[1] == width:
        return np.ascontiguousarray(region, dtype="float32")
    channels = [
        Image.fromarray(region[:, :, index], mode="F").resize((width, height), Image.BILINEAR)
        for index in range(3)
    ]
    return np.stack([np.asarray(channel, dtype="float32") for channel in channels], axis=2)


__all__ = ["RATIO_KEY", "SIZE_KEY", "board_crop", "roi_pixels"]
