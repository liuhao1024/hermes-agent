"""Provider error payload readers shared by the chat-completions helpers.

Status-code extraction from a provider's structured error payload (top-level keys,
a nested ``error`` object, or a numeric ``code``), moved out of
``agent/chat_completion_helpers.py`` so the facade keeps its line budget.

Moved verbatim from ``agent/chat_completion_helpers.py``; the helpers facade
re-imports these under the same private names, so existing call sites are
unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Optional


def _status_code_from_value(value: Any) -> Optional[int]:
    if isinstance(value, int) and 100 <= value < 600:
        return value
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:HTTP_STATUS/)?\b([1-5]\d\d)\b", value, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _status_code_from_payload(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None

    candidates = [payload.get(k) for k in ("status_code", "status", "http_status")]
    error_obj = payload.get("error")
    if isinstance(error_obj, dict):
        candidates.extend(error_obj.get(k) for k in ("status_code", "status", "http_status", "code"))
    candidates.append(payload.get("code"))
    for candidate in candidates:
        status_code = _status_code_from_value(candidate)
        if status_code is not None:
            return status_code
    return None
