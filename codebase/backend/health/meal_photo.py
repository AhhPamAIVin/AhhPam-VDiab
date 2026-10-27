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


async def recognize_upload(upload: UploadFile, hint: str | None, patient):
    """The whole photo -> recognition step behind both the meal form
    (POST /health/meals/photo-analysis) and the chat (POST /agent/meal-photo):
    feature switch, the patient's own consent, file checks, one vision call.
    Raises the HTTP errors both endpoints return."""
    from fastapi import HTTPException, status

    from codebase.models.agents.nodes.meal.recognize_from_photo import (
        MealPhotoRecognitionError,
        recognize_from_photo,
    )

    if not get_settings().meal_photo_analysis_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Tính năng phân tích ảnh đang tắt.")
    if not patient.meal_photo_ai_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bạn cần đồng ý gửi ảnh cho AI phân tích trước khi dùng tính năng này.",
        )
    try:
        image, media_type = await read_photo(upload)
    except UnsupportedImageError:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Chỉ nhận ảnh JPG, PNG hoặc WebP."
        ) from None
    except ImageTooLargeError:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Ảnh không được vượt quá 10MB."
        ) from None
    try:
        return await recognize_from_photo(image, media_type, hint=hint)
    except MealPhotoRecognitionError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Chưa phân tích được ảnh lúc này. Bạn có thể nhập món ăn bằng tay.",
        ) from None
