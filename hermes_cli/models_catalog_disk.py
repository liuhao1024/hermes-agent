"""Disk-cache storage primitives for the model catalogs.

Split out of ``hermes_cli/models.py``: the generic JSON cache read/write helpers and the
OpenRouter curated-catalog disk layer (path, TTL, read, write, row filters) live here so the
facade module stops growing. ``models.py`` re-imports every name, so existing callers and
monkeypatch points are unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _read_json_cache(path: Path, *, errors=Exception) -> Optional[dict]:
    """Load a JSON-object cache file; None when missing, unreadable, or not a dict."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except errors:
        return None
    return data if isinstance(data, dict) else None


def _write_json_cache(path: Path, data: Any, **dump_kwargs: Any) -> None:
    """Atomically persist a cache file (creating parents). Raises on failure — callers decide
    whether a failed cache write is worth logging."""
    from utils import atomic_json_write
    from hermes_constants import mkdir_under_hermes_home

    mkdir_under_hermes_home(path.parent)
    atomic_json_write(path, data, **dump_kwargs)


def _openrouter_catalog_disk_ttl() -> float:
    """Same TTL as the catalog manifest this list is filtered from (honours ``model_catalog.ttl_minutes``)."""
    from hermes_cli.model_catalog import refresh_interval_seconds

    return refresh_interval_seconds()


def _openrouter_catalog_disk_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "cache" / "openrouter_curated_catalog.json"


def _read_openrouter_catalog_disk(*, allow_stale: bool = False) -> list[tuple[str, str]] | None:
    """Fresh curated catalog from disk, or None (missing, corrupt, expired, or empty).

    ``allow_stale`` ignores the TTL — the cache-only read path prefers a stale copy over a live GET."""
    obj = _read_json_cache(_openrouter_catalog_disk_path())
    if obj is None:
        return None
    try:
        if not allow_stale and time.time() - float(obj.get("fetched_at", 0)) > _openrouter_catalog_disk_ttl():
            return None
    except (TypeError, ValueError):
        return None
    items = obj.get("curated")
    if not isinstance(items, list):
        return None
    out = [(str(it[0]), str(it[1])) for it in items if isinstance(it, (list, tuple)) and len(it) == 2]
    return out or None


def _write_openrouter_catalog_disk(curated: list[tuple[str, str]]) -> None:
    try:
        _write_json_cache(
            _openrouter_catalog_disk_path(),
            {"fetched_at": time.time(), "curated": [list(c) for c in curated]})
    except Exception as exc:
        logger.debug("openrouter curated catalog disk write failed: %s", exc)


def _zero_priced(pricing: Any, keys: tuple[str, str], default: str) -> bool:
    """True when both pricing fields parse to 0 (missing fields read as ``default``)."""
    if not isinstance(pricing, dict):
        return False
    try:
        return all(float(pricing.get(k, default)) == 0 for k in keys)
    except (TypeError, ValueError):
        return False


def _openrouter_model_is_free(pricing: Any) -> bool:
    return _zero_priced(pricing, ("prompt", "completion"), "0")


def _openrouter_model_supports_tools(item: Any) -> bool:
    """True when ``supported_parameters`` advertises ``tools`` (hermes-agent is tool-calling-first).
    Permissive when the field is absent/malformed: some OpenRouter-compatible gateways (Nous Portal,
    private mirrors) don't populate it, and the picker must not silently empty for them.

    Ported from Kilo-Org/kilocode#9068.
    """
    params = item.get("supported_parameters") if isinstance(item, dict) else None
    return "tools" in params if isinstance(params, list) else True
