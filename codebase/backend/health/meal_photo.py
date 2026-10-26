"""Reading an uploaded meal photo for recognition (Epic 1).

The photo is held in memory for the length of one request and then dropped:
nothing here writes it anywhere. Its type comes from the file's own magic
bytes, not the client's Content-Type, and only formats the vision model
accepts are let through.
"""

from __future__ import annotations

from fastapi import UploadFile

from codebase.backend.config import get_settings

_CHUNK_BYTES = 64 * 1024


class ImageTooLargeError(ValueError):
    pass


class UnsupportedImageError(ValueError):
    pass


def sniff_media_type(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


async def read_photo(upload: UploadFile) -> tuple[bytes, str]:
    """(bytes, media_type), reading at most settings.meal_photo_max_bytes + 1
    bytes. Raises ImageTooLargeError / UnsupportedImageError."""
    max_bytes = get_settings().meal_photo_max_bytes
    head = await upload.read(_CHUNK_BYTES)
    media_type = sniff_media_type(head)
    if media_type is None:
        raise UnsupportedImageError("only JPEG, PNG or WebP")
    chunks = [head]
    size = len(head)
    while size <= max_bytes and (chunk := await upload.read(_CHUNK_BYTES)):
        chunks.append(chunk)
        size += len(chunk)
    if size > max_bytes:
        raise ImageTooLargeError(f"larger than {max_bytes} bytes")
    return b"".join(chunks), media_type
