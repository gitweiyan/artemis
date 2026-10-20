"""Coordinate conversion between capture frame, normalized space, and screen."""

from __future__ import annotations


def _positive_pair(value: tuple[int, int], name: str) -> tuple[int, int]:
    width, height = (int(part) for part in value)
    if width <= 0 or height <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return width, height


class ScreenGeometry:
    """Owns the crop/letterbox and screen coordinate transforms.

    Agent-facing coordinates are normalized 0–1000. Frame coordinates are pixels
    in the captured image; screen coordinates are physical device pixels.
    """

    def __init__(
        self,
        frame_size: tuple[int, int],
        screen_size: tuple[int, int],
        content_rect: tuple[int, int, int, int] | None = None,
    ):
        self.frame_size = _positive_pair(frame_size, "frame_size")
        self.screen_size = _positive_pair(screen_size, "screen_size")
        if content_rect is None:
            content_rect = (0, 0, self.frame_size[0], self.frame_size[1])
        left, top, right, bottom = (int(value) for value in content_rect)
        if not (0 <= left < right <= self.frame_size[0]):
            raise ValueError(f"content_rect horizontal range invalid: {content_rect}")
        if not (0 <= top < bottom <= self.frame_size[1]):
            raise ValueError(f"content_rect vertical range invalid: {content_rect}")
        self.content_rect = (left, top, right, bottom)

    def _content_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.content_rect
        return right - left, bottom - top

    def frame_to_normalized(self, x: int, y: int) -> tuple[int, int]:
        left, top = self.content_rect[:2]
        width, height = self._content_size()
        nx = round((int(x) - left) * 1000 / width)
        ny = round((int(y) - top) * 1000 / height)
        return max(0, min(1000, nx)), max(0, min(1000, ny))

    def normalized_to_frame(self, nx: int, ny: int) -> tuple[int, int]:
        left, top = self.content_rect[:2]
        width, height = self._content_size()
        x = left + round(max(0, min(1000, int(nx))) * width / 1000)
        y = top + round(max(0, min(1000, int(ny))) * height / 1000)
        return x, y

    def normalized_to_screen(self, nx: int, ny: int) -> tuple[int, int]:
        width, height = self.screen_size
        x = round(max(0, min(1000, int(nx))) * width / 1000)
        y = round(max(0, min(1000, int(ny))) * height / 1000)
        return x, y

    def normalized_box_to_screen(
        self, box: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int]:
        width, height = self.screen_size
        left, top, right, bottom = box
        if not (0 <= left <= right <= 1 and 0 <= top <= bottom <= 1):
            raise ValueError(f"normalized box outside 0..1: {box}")
        return (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )

    def normalized_box_center(
        self, box: tuple[float, float, float, float]
    ) -> tuple[int, int]:
        left, top, right, bottom = self.normalized_box_to_screen(box)
        return (left + right) // 2, (top + bottom) // 2

    def screen_to_normalized(self, x: int, y: int) -> tuple[int, int]:
        """Convert device pixels back into the agent-facing 0–1000 space."""
        width, height = self.screen_size
        nx = round(int(x) * 1000 / width)
        ny = round(int(y) * 1000 / height)
        return max(0, min(1000, nx)), max(0, min(1000, ny))

    def screen_to_frame(self, x: int, y: int) -> tuple[int, int]:
        """Convert device pixels into captured-frame pixels.

        The pointer is detected in the frame, so a screen-space target (what the
        action layer speaks) must be re-expressed in the frame's pixel grid before
        the relative-pointer controller can compare the two.
        """
        nx, ny = self.screen_to_normalized(x, y)
        return self.normalized_to_frame(nx, ny)
