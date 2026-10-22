"""Reasoning-field projection for auxiliary Codex Responses calls.

Extracted from ``_CodexCompletionsAdapter._build_responses_kwargs`` (agent/auxiliary_client.py)
as the code-health remedy for its complexity cap; the projection itself mirrors what the main
transport emits via ``agent/transports/codex.py::build_kwargs``.
"""

from typing import Any


def _codex_aux_reasoning_fields(
    reasoning_cfg: Any, model: str, host: str, is_xai: bool, route: Any,
) -> dict[str, Any]:
    """``reasoning`` / ``include`` Responses fields for one auxiliary Codex call.

    An absent or non-dict ``reasoning`` config defaults to effort ``medium`` with
    ``summary: "auto"``, mirroring ``build_kwargs()``: without a summary request a
    reasoning model emits zero substantive events while it thinks, so the aux
    no-progress watchdog aborts the call at 60s (#143284). The route vocabulary stays
    the gate — a model that takes no ``reasoning`` field at all still sends none
    (#76255) — and ``enabled: False`` still goes on the wire as ``effort: "none"``
    where the vocabulary has it, since an omitted field leaves the model's default
    effort on (#75227).
    """
    if not isinstance(reasoning_cfg, dict):
        reasoning_cfg = {}
    # Shared per-model vocabulary with the main transport ("max" only where the model publishes it;
    # "minimal"/"ultra" clamp to a listed level).
    from agent.reasoning_effort import clamp_effort
    from agent.transports.codex import _codex_efforts_for_route

    supported = _codex_efforts_for_route(model, host, is_codex_backend=route.is_codex_backend)
    if supported and reasoning_cfg.get("enabled") is not False:
        # Truthy-only: Codex 400s on e.g. {"effort": null}, so falsy → default.
        effort = clamp_effort(reasoning_cfg.get("effort") or "medium", supported)
        return {
            "reasoning": {"effort": effort, "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
        }
    if "none" in supported and not is_xai:
        return {"reasoning": {"effort": "none"}}
    return {}
