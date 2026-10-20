"""Ollama ``/api/show`` payload interpretation — the shape gate and window extraction.

Sibling of ``model_metadata.py``: the facade keeps the probe transport, disk caches, and the
public query entry points; this module only interprets an already-fetched payload, so it
depends on nothing from the facade."""

from typing import Any, Optional


def _looks_like_ollama_show(data: Any) -> bool:
    """Positive shape check on an ``/api/show`` payload: real Ollama (local and cloud) nests GGUF
    metadata under dotted keys (``general.architecture``, ``llama.context_length``) and tags
    ``details.format``. Compatibility gateways answer 200 with flat look-alike keys instead — their
    windows must not become ``num_ctx`` and leak ``options`` into relayed non-Ollama models (#142239)."""
    if not isinstance(data, dict):
        return False
    model_info = data.get("model_info")
    if isinstance(model_info, dict) and any("." in str(key) for key in model_info):
        return True
    details = data.get("details")
    return isinstance(details, dict) and bool(str(details.get("format") or "").strip())


def _ollama_show_context(data: dict[str, Any], *, gguf_first: bool, minimum: Optional[int] = None) -> Optional[int]:
    """Context length from an Ollama ``/api/show`` payload. ``parameters.num_ctx`` is the RUNTIME
    window (Modelfile override), ``model_info.*.context_length`` the GGUF training max. Local users
    control num_ctx (prefer it); hosted operators may cap it arbitrarily (``gguf_first``)."""
    def _num_ctx():
        for line in data.get("parameters", "").split("\n"):
            parts = line.strip().split()
            if "num_ctx" in line and len(parts) >= 2:
                try:
                    yield int(parts[-1])
                except ValueError:
                    continue
    def _gguf():
        for key, value in data.get("model_info", {}).items():
            if "context_length" in key and isinstance(value, (int, float)):
                yield int(value)
    for reader in ((_gguf, _num_ctx) if gguf_first else (_num_ctx, _gguf)):
        for ctx in reader():
            if minimum is None or ctx >= minimum:
                return ctx
    return None
