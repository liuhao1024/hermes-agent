"""Regression test for MoA preset switch not loading preset config (#56078).

When a user switches to a MoA preset via ``/model <preset>`` (or the model
picker), the provider is set to ``"moa"`` and the model to the preset name.
Before this fix, ``moa_config`` was only populated by the one-shot ``/moa``
command — switching via ``/model`` left it ``None``, so the MoA aggregation
loop in ``conversation_loop.py`` was skipped entirely.  The reference models
and aggregator from the selected preset were never loaded.

This test verifies that ``resolve_moa_preset`` correctly resolves a named
preset from the MoA config, and that the config structure matches what the
conversation loop expects.
"""

from __future__ import annotations

from hermes_cli.moa_config import (
    normalize_moa_config,
    resolve_moa_preset,
)


def _sample_moa_config() -> dict:
    """Two presets with distinct reference models."""
    return {
        "default_preset": "coding",
        "presets": {
            "coding": {
                "reference_models": [
                    {"provider": "openai-codex", "model": "gpt-5.5"},
                ],
                "aggregator": {"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
                "reference_temperature": 0.6,
                "aggregator_temperature": 0.4,
                "max_tokens": 4096,
            },
            "review": {
                "reference_models": [
                    {"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"},
                    {"provider": "openai-codex", "model": "gpt-5.5"},
                ],
                "aggregator": {"provider": "openrouter", "model": "google/gemini-3.1-ultra"},
                "reference_temperature": 0.3,
                "aggregator_temperature": 0.2,
                "max_tokens": 8192,
            },
        },
    }


def test_resolve_moa_preset_returns_named_preset():
    """resolve_moa_preset must return the exact preset requested by name."""
    cfg = normalize_moa_config(_sample_moa_config())

    coding = resolve_moa_preset(cfg, "coding")
    assert coding["reference_models"] == [
        {"provider": "openai-codex", "model": "gpt-5.5"},
    ]
    assert coding["aggregator"]["model"] == "anthropic/claude-opus-4.8"

    review = resolve_moa_preset(cfg, "review")
    assert len(review["reference_models"]) == 2
    assert review["aggregator"]["model"] == "google/gemini-3.1-ultra"
    assert review["reference_temperature"] == 0.3


def test_resolve_moa_preset_different_presets_return_different_configs():
    """Switching between presets must return genuinely different configs.

    This is the core of #56078: the user created two presets with different
    models but switching didn't change anything because the config was never
    loaded.
    """
    cfg = normalize_moa_config(_sample_moa_config())

    coding = resolve_moa_preset(cfg, "coding")
    review = resolve_moa_preset(cfg, "review")

    # Different reference models
    assert coding["reference_models"] != review["reference_models"]
    # Different aggregators
    assert coding["aggregator"] != review["aggregator"]
    # Different temperatures
    assert coding["reference_temperature"] != review["reference_temperature"]


def test_resolve_moa_preset_raises_on_unknown_name():
    """Unknown preset names must raise KeyError, not silently default."""
    cfg = normalize_moa_config(_sample_moa_config())

    import pytest
    with pytest.raises(KeyError):
        resolve_moa_preset(cfg, "nonexistent")


def test_resolve_moa_preset_uses_default_when_name_is_none():
    """When no name is given, resolve_moa_preset falls back to default_preset."""
    cfg = normalize_moa_config(_sample_moa_config())

    result = resolve_moa_preset(cfg, None)
    # Should be the "coding" preset (the default)
    assert result["reference_models"] == [
        {"provider": "openai-codex", "model": "gpt-5.5"},
    ]


def test_moa_config_has_expected_keys_for_conversation_loop():
    """The resolved preset must contain the keys conversation_loop reads.

    conversation_loop.run_conversation reads: reference_models, aggregator,
    reference_temperature, aggregator_temperature.
    """
    cfg = normalize_moa_config(_sample_moa_config())
    preset = resolve_moa_preset(cfg, "review")

    assert "reference_models" in preset
    assert "aggregator" in preset
    assert "reference_temperature" in preset
    assert "aggregator_temperature" in preset

    # Verify types
    assert isinstance(preset["reference_models"], list)
    assert isinstance(preset["aggregator"], dict)
    assert isinstance(preset["reference_temperature"], float)
    assert isinstance(preset["aggregator_temperature"], float)
