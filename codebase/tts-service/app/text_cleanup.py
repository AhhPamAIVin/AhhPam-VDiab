"""Turn an assistant reply's markdown into plain speakable text.

The AI chat renders replies via ChatMarkdown (bold/italic, headings, bullets,
links, code spans, tables) — reading that markup aloud verbatim sounds bad
("sao sao Xin chào sao sao"). Citations are never embedded as markdown links
in the reply text itself (they're a separate structured `sources` field on
AgentChatResponse — see codebase/backend/agent/schemas.py), so this only
needs to handle the model's own prose formatting, not URL stripping.

Regex-based on purpose — a full markdown parser is overkill for stripping
formatting before a TTS call.
"""

from __future__ import annotations

import re

_FENCED_CODE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_ITALIC_UNDERSCORE_RE = re.compile(r"_(.+?)_")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_NUMBERED_LIST_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s*", re.MULTILINE)
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def strip_markdown_for_speech(text: str) -> str:
    text = _FENCED_CODE_RE.sub(lambda m: m.group(1), text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_UNDERSCORE_RE.sub(lambda m: m.group(1), text)
    text = _ITALIC_RE.sub(lambda m: m.group(1), text)
    text = _ITALIC_UNDERSCORE_RE.sub(lambda m: m.group(1), text)
    text = _HR_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _NUMBERED_LIST_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = text.replace("|", ", ")
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n", text)
    return text.strip()
