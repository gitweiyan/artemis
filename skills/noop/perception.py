"""Frame validity only: the noop skill measures the path, it does not read a screen.

A skill's ``parse`` returns something truthy for a frame it can act on and falsy
for one it cannot — the runtime counts the falsy ones as *blind* frames and stops
the session once they run away. This one accepts any decodable image, which is
exactly what "the capture path works" means.
"""

from __future__ import annotations

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def parse(frame):
    image = bytes(getattr(frame, "image", b"") or b"")
    if not image:
        return None
    if image.startswith(JPEG_MAGIC):
        kind = "jpeg"
    elif image.startswith(PNG_MAGIC):
        kind = "png"
    else:
        return None
    return {
        "kind": kind,
        "bytes": len(image),
        "capture_ms": float(getattr(frame, "capture_ms", 0.0)),
    }
