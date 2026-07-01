"""Regression tests for #56465: vendor prefix mapping must not pin a direct
provider before checking credentials.

When a user types ``/model mimo-v2.5`` (bare name), the static catalog
(``_PROVIDER_MODELS["xiaomi"]``) matches and ``detect_provider_for_model``
returns ``("xiaomi", "mimo-v2.5")``.  If the user has no ``XIAOMI_API_KEY``
but has OAuth credentials for an aggregator (e.g. ``nous``), the model should
route through the aggregator as ``xiaomi/mimo-v2.5`` — not pin to the direct
``xiaomi`` provider and fail at ``init_agent()`` with a generic RuntimeError.

Hermetic: all resolution/metadata/credential lookups are mocked.
"""

from unittest.mock import patch, MagicMock

from hermes_cli.model_switch import switch_model

_ACCEPTED = {"accepted": True, "persist": True, "recognized": True, "message": None}


def _run_switch(
    *,
    raw_input,
    current_provider="openrouter",
    current_model="anthropic/claude-sonnet-4.6",
    detected_provider=None,
    detected_model=None,
    authed_slugs=None,
    current_base_url="",
):
    """Drive ``switch_model`` with the resolution chain partially mocked.

    Unlike the configured-provider routing tests, we let
    ``detect_provider_for_model`` return a real value (simulating a static
    catalog match) so the new credential-gate code path is exercised.
    """
    _detected = (detected_provider, detected_model) if detected_provider else None
    _authed = authed_slugs or []

    with patch("hermes_cli.model_switch.resolve_alias", return_value=None), \
         patch("hermes_cli.model_switch.list_provider_models", return_value=[]), \
         patch("hermes_cli.model_switch.normalize_model_for_provider",
               side_effect=lambda model, provider: model), \
         patch("hermes_cli.models.validate_requested_model", return_value=_ACCEPTED), \
         patch("hermes_cli.models.detect_provider_for_model",
               return_value=_detected), \
         patch("hermes_cli.model_switch.get_model_info", return_value=None), \
         patch("hermes_cli.model_switch.get_model_capabilities", return_value=None), \
         patch("hermes_cli.model_switch.get_authenticated_provider_slugs",
               return_value=_authed), \
         patch(
             "hermes_cli.runtime_provider.resolve_runtime_provider",
             return_value={
                 "api_key": "***",
                 "base_url": current_base_url or "http://resolved/v1",
                 "api_mode": "",
             },
         ):
        return switch_model(
            raw_input=raw_input,
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
        )


def test_unauthed_direct_provider_routes_to_authenticated_aggregator():
    """Core #56465 repro: bare ``mimo-v2.5`` pins to ``xiaomi`` via static
    catalog, but user only has ``nous`` credentials.  Should route to
    ``nous`` with ``xiaomi/mimo-v2.5``."""
    result = _run_switch(
        raw_input="mimo-v2.5",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider="xiaomi",
        detected_model="mimo-v2.5",
        authed_slugs=["nous", "openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "nous"
    assert result.new_model == "xiaomi/mimo-v2.5"


def test_unauthed_direct_provider_routes_to_openrouter_if_nous_not_authed():
    """When only ``openrouter`` is authenticated (not ``nous``), route there."""
    result = _run_switch(
        raw_input="mimo-v2.5",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider="xiaomi",
        detected_model="mimo-v2.5",
        authed_slugs=["openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "openrouter"
    assert result.new_model == "xiaomi/mimo-v2.5"


def test_authed_direct_provider_keeps_direct_route():
    """When the user HAS credentials for the detected direct provider, keep
    the direct route (don't hop to an aggregator)."""
    result = _run_switch(
        raw_input="mimo-v2.5",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider="xiaomi",
        detected_model="mimo-v2.5",
        authed_slugs=["xiaomi", "nous", "openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "xiaomi"
    assert result.new_model == "mimo-v2.5"


def test_aggregator_detection_not_gated():
    """When ``detect_provider_for_model`` returns an aggregator (e.g.
    ``openrouter``), the credential gate is skipped — aggregators are always
    accepted."""
    result = _run_switch(
        raw_input="anthropic/claude-sonnet-4.6",
        current_provider="nous",
        current_model="mimo-v2.5",
        detected_provider="openrouter",
        detected_model="anthropic/claude-sonnet-4.6",
        authed_slugs=["nous", "openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "openrouter"
    assert result.new_model == "anthropic/claude-sonnet-4.6"


def test_no_authed_aggregator_keeps_original_detection():
    """When no aggregator is authenticated either, fall through to the
    original detection (credential resolution will fail with a clear error
    later)."""
    result = _run_switch(
        raw_input="mimo-v2.5",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider="xiaomi",
        detected_model="mimo-v2.5",
        authed_slugs=[],  # nothing authenticated
    )
    assert result.success is True, result.error_message
    # Should still pin to xiaomi — the credential resolution block will
    # handle the error.
    assert result.target_provider == "xiaomi"
    assert result.new_model == "mimo-v2.5"


def test_no_detection_is_noop():
    """When ``detect_provider_for_model`` returns None, nothing changes."""
    result = _run_switch(
        raw_input="unknown-model-xyz",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider=None,
        detected_model=None,
        authed_slugs=["nous", "openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "openrouter"


def test_glm_zai_routing_through_aggregator():
    """Another vendor prefix case from the issue: ``glm`` → ``z-ai``.
    Should route through authenticated aggregator when z-ai has no creds."""
    result = _run_switch(
        raw_input="glm-5",
        current_provider="openrouter",
        current_model="anthropic/claude-sonnet-4.6",
        detected_provider="z-ai",
        detected_model="glm-5",
        authed_slugs=["nous", "openrouter"],
    )
    assert result.success is True, result.error_message
    assert result.target_provider == "nous"
    assert result.new_model == "z-ai/glm-5"
