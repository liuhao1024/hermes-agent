"""Telegram ``text_link`` expansion, kept out of the adapter facade.

A rich-text hyperlink arrives as visible anchor text plus a ``text_link`` entity carrying the
URL; the model only ever sees the anchor unless the URL is inlined (#31071).
"""

from __future__ import annotations

from typing import Any


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _code_point_index(text: str, utf16_offset: int) -> int | None:
    """Python index for a UTF-16 code-unit offset; None when it splits a surrogate pair."""
    try:
        return len(text.encode("utf-16-le")[: utf16_offset * 2].decode("utf-16-le"))
    except UnicodeDecodeError:
        return None


def rich_message_text(message: Any) -> str:
    """Flatten an ``api_kwargs`` ``rich_message`` into plain text, or "" when there is none.

    Long / multi-paragraph messages can arrive with an empty ``text`` and the content in
    ``rich_message.blocks`` — a Bot API field python-telegram-bot does not model, so it survives
    only inside ``Message.api_kwargs``. ``paragraph`` blocks render as lines, ``list_item`` blocks
    get a ``- `` bullet, and container blocks recurse into their ``blocks`` / ``items``. Without
    this fallback such a message matches no text handler and is dropped silently (#146067).
    """
    api_kwargs = getattr(message, "api_kwargs", None)
    rich = api_kwargs.get("rich_message") if isinstance(api_kwargs, dict) else None
    blocks = rich.get("blocks") if isinstance(rich, dict) else None
    if not isinstance(blocks, list):
        return ""
    lines: list[str] = []

    def _render(block: Any) -> None:
        if isinstance(block, str):
            lines.append(block)
            return
        if not isinstance(block, dict):
            return
        nested = block.get("blocks") or block.get("items")
        if isinstance(nested, list):
            for child in nested:
                _render(child)
            return
        text = block.get("text")
        if isinstance(text, str):
            lines.append(f"- {text}" if block.get("type") == "list_item" else text)

    for block in blocks:
        _render(block)
    return "\n".join(lines)


def expand_link_entities(message: Any) -> str:
    """Message text (or caption) with every hidden ``text_link`` URL inlined after its anchor.

    Entity offsets are UTF-16 code units (emoji before the anchor count twice), so they are mapped
    to code-point indices before slicing. Malformed entities are skipped; an anchor that already
    reads as its own URL, or text already carrying the inline form, is left untouched.
    """
    text = getattr(message, "text", None)
    if text:
        entities = getattr(message, "entities", None) or []
    else:
        text = getattr(message, "caption", None) or ""
        entities = getattr(message, "caption_entities", None) or []
    if not text:
        rich = rich_message_text(message)
        if rich:
            return rich
    if not text or not entities:
        return text

    utf16_length = _utf16_length(text)
    links: list[tuple[int, int, str]] = []
    for entity in entities:
        entity_type = str(getattr(entity, "type", "")).split(".")[-1].lower()
        raw_url = getattr(entity, "url", None)
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        if entity_type != "text_link" or not url:
            continue
        try:
            offset = int(getattr(entity, "offset", -1))
            length = int(getattr(entity, "length", 0))
        except (TypeError, ValueError):
            continue
        if offset < 0 or length <= 0 or offset + length > utf16_length:
            continue
        start, end = _code_point_index(text, offset), _code_point_index(text, offset + length)
        if start is None or end is None or end <= start:
            continue
        if text[start:end].strip() == url:
            continue
        links.append((start, end, url))

    expanded = text
    for _start, end, url in sorted(links, reverse=True):
        inline = f" ({url})"
        if expanded[end:].startswith(inline):
            continue
        expanded = f"{expanded[:end]}{inline}{expanded[end:]}"
    return expanded
