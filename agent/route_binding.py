"""Bind a provider route onto a live agent in place, and put the primary route back.

``bind_route_entry`` is the fallback chain's swap (client, pool, compressor, reasoning,
extra_body) shared with per-turn routes such as the voice-chat model; ``try_activate_fallback``
drives that swap down the configured chain; ``reinstall_primary_runtime`` is the inverse that
``restore_primary_runtime`` runs once its gates pass.
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

from utils import base_url_host_matches

logger = logging.getLogger(__name__)


def bind_route_entry(agent: Any, entry: dict[str, Any], provider: str, model: str) -> Optional[tuple[str, str]]:
    """Swap ``agent`` onto ``entry`` (a fallback-chain-shaped dict). Returns ``(old_model,
    old_provider)``, or None when the provider has no usable client. Sets ``_fallback_activated``
    so the next turn's ``restore_primary_runtime`` reverts it; raises on a failed swap."""
    from agent.auxiliary_client import resolve_provider_client
    from agent.chat_completion_helpers import (
        _fallback_api_mode_hint, _fallback_api_mode_resolved, _rebind_fallback_credential_pool,
        _reresolve_fallback_reasoning_config, _rescope_fallback_extra_body, _update_fallback_context_compressor,
    )
    from hermes_cli.fallback_config import resolve_entry_api_key
    # Pass the entry's base_url/api_key so custom endpoints (Ollama Cloud) resolve instead
    # of falling through to OpenRouter defaults.
    base_url_hint = (entry.get("base_url") or "").strip() or None
    api_key_hint = resolve_entry_api_key(entry)
    api_mode_explicit, api_mode = _fallback_api_mode_hint(entry, provider, base_url_hint)
    # Ollama Cloud: OLLAMA_API_KEY from env when the entry has no key. Host match, not
    # substring — GHSA-76xc-57q6-vm5m.
    if base_url_hint and base_url_host_matches(base_url_hint, "ollama.com") and not api_key_hint:
        from agent.secret_scope import get_secret
        api_key_hint = get_secret("OLLAMA_API_KEY") or None
    # raw_codex=True: the main agent needs direct responses.stream() access for Codex providers.
    client, _resolved_model = resolve_provider_client(
        provider, model=model, raw_codex=True, explicit_base_url=base_url_hint, explicit_api_key=api_key_hint,
        api_mode=api_mode)
    if client is None:
        return None
    if provider == "moa":
        # A MoA entry means the preset itself, exactly like ``provider: moa`` in config. The client
        # is the preset's aggregator and only proves the preset resolves; bind the facade with the
        # same pins every other MoA build site uses (#112525, #112623).
        base_url, api_mode = "moa://local", "chat_completions"
    else:
        try:
            from hermes_cli.model_normalize import normalize_model_for_provider
            model = normalize_model_for_provider(model, provider)
        except Exception as norm_err:  # health: allow BLE001 -- moved verbatim from try_activate_fallback; a bad catalog entry keeps the raw id
            logger.warning("Could not normalize fallback model %r for provider %r: %s", model, provider, norm_err)
        base_url = str(client.base_url)
        from hermes_cli.providers import is_actual_route
        if is_actual_route(provider, base_url):
            api_mode = "chat_completions"
        elif not api_mode_explicit and api_mode == "chat_completions":
            api_mode = _fallback_api_mode_resolved(agent, provider, model, base_url)

    old_model, old_provider, old_base_url = agent.model, agent.provider, agent.base_url
    # Clear the per-config context_length override so the new model's own context window is
    # resolved instead of the previous model's stale value (#22387).
    agent._config_context_length = None
    agent.model, agent.provider, agent.requested_provider = model, provider, provider
    agent.base_url, agent.api_mode = base_url, api_mode
    # reasoning_content echo opt-in travels with the active provider; restore_primary_runtime reverts it.
    agent._reasoning_echo_flag = bool(entry.get("reasoning_echo", False))
    if hasattr(agent, "_transport_cache"):
        agent._transport_cache.clear()
    from agent.turn_recovery_codex import reset_codex_reasoning_replay
    reset_codex_reasoning_replay(agent)
    agent._fallback_activated = True

    _rebind_fallback_credential_pool(agent, provider, model)
    if provider == "moa":
        from agent.moa_loop import bind_moa_runtime
        bind_moa_runtime(agent, model)
    else:
        from agent.client_lifecycle import _swap_fallback_clients
        _swap_fallback_clients(agent, client, provider, model, base_url, api_mode)

    from agent.agent_runtime_helpers import sync_credential_pool_entry_id
    sync_credential_pool_entry_id(agent)

    agent._use_prompt_caching, agent._use_native_cache_layout = agent._anthropic_prompt_cache_policy(
        provider=provider, base_url=base_url, api_mode=api_mode, model=model)
    agent._ensure_lmstudio_runtime_loaded()  # LM Studio: preload before probing context length
    _update_fallback_context_compressor(agent)
    _reresolve_fallback_reasoning_config(agent)
    _rescope_fallback_extra_body(agent, old_model, old_provider, old_base_url)
    return old_model, old_provider


def _fallback_route_scope():
    """Scope guard for the fallback swap: the launch profile's runtime scope when the swap runs
    with no profile scope at all under multiplexing, else a no-op.

    A turn's swap normally inherits the turn's bound scope (the contextvars reach the executor
    thread via ``copy_context``); the unscoped cases are a swap escaping to a context that did not
    copy the turn's vars and one driven before any turn binds a profile. Unscoped, the swap's
    ``resolve_provider_client`` credential read raises ``UnscopedSecretError`` (fail closed) and the
    configured entry is skipped as if the provider were not configured (#142031). Binding the
    launch profile there keeps the swap resolvable; an already-bound scope (any profile's turn) is
    left untouched so a secondary's swap never borrows the launch profile's credentials.
    """
    from agent.secret_scope import current_secret_scope, is_multiplex_active
    if not is_multiplex_active() or current_secret_scope() is not None:
        return contextlib.nullcontext()
    from tui_gateway.launch_profile_policy import launch_profile_scope_if_multiplexed
    return launch_profile_scope_if_multiplexed()


def try_activate_fallback(agent, reason: Any = None, reset_at=None) -> bool:
    """Switch to the next fallback model/provider in the chain; False when exhausted. Swaps client,
    model slug and provider in place so the retry loop continues on the new backend; client
    construction goes through resolve_provider_client (no duplicated provider→key mappings).

    Moved here from ``chat_completion_helpers`` (FILE_LINES cap): this is the driver of
    ``bind_route_entry`` below; re-exported from its old home for existing importers."""
    from agent.chat_completion_helpers import (
        _buffer_fallback_notice, _fallback_chain_exhausted, _fallback_entry_key, _fallback_reason_text,
        _log_fallback_activated, _reset_stale_streak, _should_skip_fallback_candidate,
        rewrite_prompt_model_identity,
    )
    from agent.fallback_cooldown import _arm_rate_limit_cooldown, switch_deferred_by_reset
    if switch_deferred_by_reset(agent, reason, reset_at):
        return False
    cooldown_seconds = _arm_rate_limit_cooldown(agent, reason, reset_at=reset_at)
    while True:
        if agent._fallback_index >= len(agent._fallback_chain):
            return _fallback_chain_exhausted(agent, reason)
        fb = agent._fallback_chain[agent._fallback_index]
        agent._fallback_index += 1
        fb_key = _fallback_entry_key(fb)
        if getattr(agent, "_unavailable_fallback_keys", None) is None:
            agent._unavailable_fallback_keys = set()
        unavailable = agent._unavailable_fallback_keys
        fb_provider = (fb.get("provider") or "").strip().lower()
        fb_model = (fb.get("model") or "").strip()
        if _should_skip_fallback_candidate(agent, fb, fb_key, fb_provider, fb_model, unavailable):
            continue

        try:
            with _fallback_route_scope():
                bound = bind_route_entry(agent, fb, fb_provider, fb_model)
            if bound is None:
                logger.warning("Fallback to %s failed: provider not configured", fb_provider)
                unavailable.add(fb_key)
                continue
            old_model, old_provider = bound
            fb_model = agent.model  # normalized by the binder
            rewrite_prompt_model_identity(agent, fb_model, fb_provider)

            notice = (
                f"⚠️ Model fallback: {old_model} via {old_provider} unavailable "
                f"({_fallback_reason_text(reason)}); using {fb_model} via {fb_provider}.")
            if cooldown_seconds is not None:
                remaining = max(0, math.ceil(agent._rate_limited_until - time.monotonic()))
                notice += f" Primary retry eligible in ~{remaining} s; recovery is not guaranteed."
            _buffer_fallback_notice(agent, notice)
            # ``_fallback_activated`` is also reused by `/model --once` restoration; separate
            # provenance so the restore path only emits a recovery notice after a real fallback.
            agent._provider_fallback_active = True
            agent._provider_fallback_route = (str(fb_model), str(fb_provider))
            _log_fallback_activated(agent, reason, old_model, old_provider, fb_model, fb_provider)
            from hermes_cli.observability.shared_metrics_events import record_fallback
            record_fallback(from_provider=old_provider, to_provider=fb_provider, reason=reason)
            # The stale-call streak measured the OLD provider; carrying it over would
            # short-circuit the fresh fallback before its first stream attempt.
            _reset_stale_streak(agent)
            from agent.native_compaction import resolve_native_compaction_capabilities
            agent.runtime_capabilities = resolve_native_compaction_capabilities(
                model=agent.model, base_url=agent.base_url, provider=fb_provider, is_codex_backend=fb_provider == "openai-codex")
            return True
        except Exception as e:
            if fb_provider == "nous":
                unavailable.add(fb_key)
            # An UnscopedSecretError is an internal profile-scoping bug, not an operator error;
            # its traceback names the spawn site that ran the swap scopeless (#142031).
            from agent.secret_scope import UnscopedSecretError
            logger.error("Failed to activate fallback %s: %s", fb_model, e,
                         exc_info=isinstance(e, UnscopedSecretError))
            continue  # try next in chain


def reinstall_runtime_snapshot(agent: Any, rt: dict[str, Any]) -> None:
    """Put ``agent`` on the runtime recorded in ``rt`` (a ``_primary_runtime``-shaped snapshot):
    identity, client, caching flags, compressor, reasoning and prompt identity. Credential pool
    and fallback bookkeeping are the caller's. Raises on failure."""
    from agent.agent_runtime_helpers import (
        _apply_primary_runtime_fields, _rebuild_primary_client, _restore_runtime_capabilities,
    )
    _apply_primary_runtime_fields(agent, rt)
    from agent.turn_recovery_codex import reset_codex_reasoning_replay
    reset_codex_reasoning_replay(agent)
    _restore_runtime_capabilities(agent, rt)
    agent._use_prompt_caching = rt["use_prompt_caching"]
    # Default to native layout for snapshots predating the native-vs-proxy split.
    agent._use_native_cache_layout = rt.get(
        "use_native_cache_layout",
        agent.api_mode == "anthropic_messages" and agent.provider == "anthropic",
    )
    # An operator cache disable (_cache_disabled) must survive snapshot restoration.
    if getattr(agent, "_cache_disabled", False):
        agent._use_prompt_caching = False
        agent._use_native_cache_layout = False
    _rebuild_primary_client(agent, rt, reason="restore_primary")
    agent.context_compressor.update_model(
        model=rt["compressor_model"], context_length=rt["compressor_context_length"],
        base_url=rt["compressor_base_url"], api_key=rt["compressor_api_key"],
        provider=rt["compressor_provider"], api_mode=rt.get("compressor_api_mode", ""),
    )
    # Same rule as fallback activation: refresh an existing verdict only; never-probed sessions stay lazy.
    if getattr(agent, "_compression_feasibility_checked", False) is True:
        from agent.conversation_compression import revalidate_compression_feasibility
        revalidate_compression_feasibility(agent)
    # Older snapshots have no reasoning_config; keep the current value.
    saved_reasoning = rt.get("reasoning_config")
    if saved_reasoning is not None:
        agent.reasoning_config = dict(saved_reasoning)
    # Reset the stale-call circuit breaker: its streak measured the route being left.
    from agent.chat_completion_helpers import _reset_stale_streak, rewrite_prompt_model_identity
    _reset_stale_streak(agent)
    # Undo the identity rewrite so the prompt is byte-identical to the stored copy again
    # (prefix cache match).
    rewrite_prompt_model_identity(agent, rt["model"], rt["provider"])


def reinstall_primary_runtime(
    agent: Any, rt: dict[str, Any], primary_provider: str, primary_model: str, matches_primary,
    load_primary_pool, prefetched_pool=None, prefetched: bool = False,
) -> None:
    """Put ``agent`` back on its ``_primary_runtime`` snapshot ``rt`` and clear the fallback state."""
    from agent.agent_runtime_helpers import _rebind_primary_credential_pool
    reinstall_runtime_snapshot(agent, rt)
    _rebind_primary_credential_pool(
        agent, primary_provider, primary_model, matches_primary, load_primary_pool, prefetched_pool, prefetched
    )
    agent._fallback_activated = False
    agent._fallback_index = 0
    agent._rate_limit_backoff_count = 0
