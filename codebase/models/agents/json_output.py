"""Parsing helpers for LLM steps that are asked to answer in JSON.

Models often wrap the JSON in a ```json fenced block even when told not to;
every caller strips that the same way here. Parsers never raise — a caller
gets None and applies its own fallback (fail open/closed is its decision).
"""

from __future__ import annotations

import json
from typing import Any


def strip_code_fence(text: str) -> str:
    """Remove a surrounding ``` / ```json fence, multi-line or on one line."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`")
    if "\n" in text:
        first_line, rest = text.split("\n", 1)
        if first_line.strip().lower() in {"json", ""}:
            text = rest
    elif text.lower().startswith("json"):
        text = text[4:]
    return text.strip()


def parse_json(content: Any) -> Any | None:
    """Decoded JSON value, or None when the content isn't valid JSON."""
    if not isinstance(content, str):
        return None
    try:
        return json.loads(strip_code_fence(content))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_json_object(content: Any) -> dict | None:
    """Decoded JSON object, or None for invalid JSON or any other top-level type."""
    parsed = parse_json(content)
    return parsed if isinstance(parsed, dict) else None
