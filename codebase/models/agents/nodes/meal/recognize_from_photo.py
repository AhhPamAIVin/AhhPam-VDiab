"""Meal photo -> draft item list (Epic 1).

One vision-model call. The photo travels to the model provider inside this
call only: it is not written to disk or the database, not logged, and not
sent to tracing (get_llm(trace=False)). The caller must already hold the
patient's consent (User.meal_photo_ai_consent).

The model's output is validated item by item: an item that breaks the shape
(no name, a nonsense quantity) is dropped rather than repaired, and a reply
that isn't the requested JSON at all raises MealPhotoRecognitionError — there
is no draft to fall back to, so the patient types the meal instead.
"""

from __future__ import annotations

import base64
import logging
import re

from pydantic import ValidationError

from codebase.models.agents.json_output import parse_json_object
from codebase.models.agents.nodes.meal.schemas import MAX_MEAL_ITEMS, MealItemDraft, MealPhotoRecognition
from codebase.models.agents.prompts.meal_photo import (
    MEAL_PHOTO_HINT_TEMPLATE,
    MEAL_PHOTO_SYSTEM_PROMPT,
    MEAL_PHOTO_USER_TEXT,
)
from codebase.models.llm import get_llm

logger = logging.getLogger(__name__)

NODE_NAME = "recognize_meal_photo"
# "low" sends a 512px version: enough to tell dishes apart at a fraction of
# the image tokens, and bounds the latency of a request the patient waits on.
_IMAGE_DETAIL = "low"
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class MealPhotoRecognitionError(RuntimeError):
    """The model call failed or its reply was unusable."""


def _clean_text(value: object) -> str | None:
    """One line: control characters out, whitespace collapsed."""
    if not isinstance(value, str):
        return None
    return " ".join(_CONTROL_CHARS_RE.sub(" ", value).split()) or None


def _parse_items(raw_items: object) -> list[MealItemDraft]:
    items: list[MealItemDraft] = []
    if not isinstance(raw_items, list):
        return items
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        quantity = raw.get("quantity")
        try:
            item = MealItemDraft(
                name=_clean_text(raw.get("name")) or "",
                quantity=quantity if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) else None,
                unit=_clean_text(raw.get("unit")),
            )
        except ValidationError:
            continue
        items.append(item)
        if len(items) == MAX_MEAL_ITEMS:
            break
    return items


def parse_recognition(content: object, *, model: str) -> MealPhotoRecognition:
    parsed = parse_json_object(content)
    if parsed is None or not isinstance(parsed.get("is_food"), bool):
        raise MealPhotoRecognitionError("reply is not the requested JSON object")
    items = _parse_items(parsed.get("items")) if parsed["is_food"] else []
    # "Food" with nothing nameable is no draft at all.
    return MealPhotoRecognition(is_food=bool(items), items=items, model=model)


async def recognize_from_photo(image: bytes, media_type: str, *, hint: str | None = None) -> MealPhotoRecognition:
    llm = get_llm(NODE_NAME, trace=False)
    model = str(getattr(llm, "model_name", "") or "unknown")
    data_url = f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"
    user_content: list[dict] = [{"type": "text", "text": MEAL_PHOTO_USER_TEXT}]
    if hint and (clean_hint := _clean_text(hint)):
        user_content.append({"type": "text", "text": MEAL_PHOTO_HINT_TEMPLATE.format(hint=clean_hint)})
    user_content.append({"type": "image_url", "image_url": {"url": data_url, "detail": _IMAGE_DETAIL}})
    try:
        result = await llm.ainvoke(
            [
                {"role": "system", "content": MEAL_PHOTO_SYSTEM_PROMPT.format(max_items=MAX_MEAL_ITEMS)},
                {"role": "user", "content": user_content},
            ]
        )
    except Exception as exc:
        # Type only: the exception text can echo the request payload.
        logger.warning("meal photo recognition call failed: %s", type(exc).__name__)
        raise MealPhotoRecognitionError("model call failed") from exc
    recognition = parse_recognition(getattr(result, "content", None), model=model)
    logger.info("meal_photo_recognition is_food=%s items=%d model=%s", recognition.is_food, len(recognition.items), model)
    return recognition
