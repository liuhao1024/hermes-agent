"""Regression tests for xAI curated model list (OAuth picker)."""

from hermes_cli.models import _PROVIDER_MODELS, provider_model_ids


def test_xai_oauth_includes_grok_composer_2_5_fast():
    models = provider_model_ids("xai-oauth")
    assert "grok-composer-2.5-fast" in models


def test_grok_composer_slots_after_grok_build():
    models = _PROVIDER_MODELS["xai-oauth"]
    assert models[0] == "grok-build-0.1"
    assert models[1] == "grok-composer-2.5-fast"


def test_xai_curated_excludes_imagine_models(monkeypatch):
    """grok-imagine-* media-generation models must not appear in the picker.

    They have tiny context windows (1K–8K) that fail Hermes' 64K minimum
    and do not support tool calling.  (#52595)
    """
    fake_cache = {
        "xai": {
            "models": {
                "grok-4.3": {"tool_call": True},
                "grok-imagine-image": {"tool_call": False},
                "grok-imagine-image-quality": {"tool_call": False},
                "grok-imagine-video": {"tool_call": False},
            }
        }
    }
    monkeypatch.setattr(
        "agent.models_dev._load_disk_cache", lambda: fake_cache
    )
    # Re-import to pick up the patched cache
    from hermes_cli.models import _xai_curated_models

    models = _xai_curated_models()
    assert "grok-4.3" in models
    for m in models:
        assert "imagine" not in m, f"media-gen model {m!r} leaked into picker"
