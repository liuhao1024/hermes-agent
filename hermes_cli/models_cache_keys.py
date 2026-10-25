"""Cache-key spellings for ``provider_models_cache.json`` rows.

Slug-keyed rows and custom-endpoint rows (``custom:<base_url>#<fingerprint>``) share one
cache file, so the normalization behind a provider id and the resolution of every key
spelling that provider owns live together here (#144379).
"""

from typing import Optional

_CONFIG_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError)


def _normalized_cache_slug(provider: Optional[str]) -> str:
    """``ollama`` stays a raw slug (its alias would canonicalize to ``custom``); everything else normalizes."""
    requested = str(provider or "").strip().lower()
    if requested == "ollama":
        return requested
    from hermes_cli.models import normalize_provider

    return normalize_provider(provider) or (provider or "")


def _targeted_clear_keys(provider: str) -> set[str]:
    """Every cache-key spelling a targeted clear must drop.

    ``cached_fetch_api_models`` stores custom-endpoint rows as ``custom:<base_url>#<fingerprint>``
    (#144379), so an exact lookup on the caller's ``custom:<base_url>`` (or a configured slug)
    always misses. Cover the full row key (a caller may hold the complete ``...#<fp>`` spelling),
    the writer-normalized URL spelling, and — for a configured custom provider — the
    ``custom:<base_url>`` key its rows are written under.
    """
    from hermes_cli.models import _get_provider_config_dict

    keys: set[str] = set()
    for raw in (provider, str(provider).split("#", 1)[0]):
        normalized = _normalized_cache_slug(raw)
        if normalized:
            keys.add(normalized)
    for key in list(keys):
        stem = key.split("#", 1)[0]
        name = stem.removeprefix("custom:")
        if not name:
            continue
        if "://" in name:
            # A URL spelling: also match its writer-normalized form, so a trailing slash or
            # uppercase the caller kept still finds the row.
            keys.add(f"custom:{name.strip().rstrip('/').lower()}")
            continue
        entry = _get_provider_config_dict(name)
        url = str(entry.get("api", "") or entry.get("url", "") or entry.get("base_url", "") or "").strip()
        if not url:
            try:
                from hermes_cli.config import load_config
                from hermes_cli.providers import resolve_custom_provider

                legacy = load_config().get("custom_providers", [])
                pdef = resolve_custom_provider(name, legacy if isinstance(legacy, list) else None)
                url = (pdef.base_url if pdef else "") or ""
            except _CONFIG_ERRORS:
                url = ""
        if url:
            keys.add(f"custom:{url.strip().rstrip('/').lower()}")
    return keys
