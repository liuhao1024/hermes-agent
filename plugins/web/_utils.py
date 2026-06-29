"""Shared utilities for bundled web search provider plugins."""

from __future__ import annotations

import json
from typing import Any

import httpx

# 10 MiB — generous for search-result JSON, tight enough to block
# runaway proxy / misconfigured self-hosted endpoints.
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def read_json_capped(
    response: httpx.Response,
    *,
    max_bytes: int = _MAX_RESPONSE_BYTES,
) -> Any:
    """Parse an httpx response as JSON, enforcing a body-size cap.

    Raises ``ValueError`` when the response body exceeds *max_bytes*
    before any JSON parsing occurs.  Callers should catch ``ValueError``
    alongside ``httpx.HTTPStatusError`` and return a typed error dict.
    """
    # Fast-path: honour Content-Length when the server sends it.
    cl = response.headers.get("content-length")
    if cl is not None:
        try:
            cl_bytes = int(cl)
        except (ValueError, TypeError, OverflowError):
            pass  # malformed Content-Length — fall through to body check
        else:
            if cl_bytes > max_bytes:
                raise ValueError(
                    f"Response body too large ({cl} bytes, cap {max_bytes})"
                )

    # Definitive check on the already-buffered body.
    body = response.content
    if len(body) > max_bytes:
        raise ValueError(
            f"Response body too large ({len(body)} bytes, cap {max_bytes})"
        )
    return json.loads(body)
