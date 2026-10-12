"""The calibration corpus: real device frames, loaded as data.

Algorithm tests use synthetic images (they prove logic); calibration asserts on
these (they prove the numbers fit the actual game). They come from the live
observation run on PEMM00, 2026-10-12 — a 720x1600 screenshot of
cn.flytalk.tetris.nearme.gamecenter with the playfield, HUD and the ad banner.
"""

from __future__ import annotations

import io
from pathlib import Path

SAMPLES_DIR = Path(__file__).with_name("samples")
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def load_reference_frames(
    directory: str | Path | None = None,
) -> list[tuple[str, bytes, tuple[int, int]]]:
    """``[(name, raw_bytes, (width, height)), ...]``, sorted by name, bad files skipped."""
    root = Path(directory) if directory is not None else SAMPLES_DIR
    frames: list[tuple[str, bytes, tuple[int, int]]] = []
    if not root.is_dir():
        return frames
    for path in sorted(root.glob("reference-*")):
        data = path.read_bytes()
        if not (data.startswith(JPEG_MAGIC) or data.startswith(PNG_MAGIC)):
            continue
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            frames.append((path.name, data, image.size))
    return frames


__all__ = ["SAMPLES_DIR", "load_reference_frames"]
