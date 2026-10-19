"""``delegation.compression_threshold_ratio`` is an OPTIONAL base-ratio override on a subagent's
compaction trigger, off by default, and its value is validated rather than coerced.

Default off: children compact at the global ``compression.threshold`` (0.50 x window). The knob
exists because ``compression_threshold_tokens`` can only clamp a child's trigger DOWN: a long-lived
parent that compacts early (cheap, cache-priced context) has no way to let children use more of the
window first. The override replaces the config-loaded base, so model switches and window corrections
re-derive from it, per-model ``model_thresholds`` still win, and the raise-only small-context floor
and the token cap still apply on top. The validation matters because YAML ``true`` coerces to 1.0
(a trigger the window can never reach) and a percentage written as ``45`` instead of the fraction
``0.45`` is out of range.
"""
from types import SimpleNamespace

from agent.context_compressor import ContextCompressor
from tools.delegate_tool import (
    _apply_child_compression_cap,
    _apply_child_compression_ratio,
    _child_compression_ratio,
)


def _child(window=1_000_000, threshold=0.50, model_thresholds=None):
    cc = ContextCompressor(model="anthropic/claude-fable-5.1", threshold_percent=threshold,
                           config_context_length=window, model_thresholds=model_thresholds)
    return SimpleNamespace(context_compressor=cc)


def test_default_is_no_override_child_keeps_the_global_ratio():
    child = _child()
    _apply_child_compression_ratio(child, {})
    assert child.context_compressor.threshold_tokens == 500_000
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0})
    assert child.context_compressor.threshold_tokens == 500_000


def test_explicit_ratio_raises_the_childs_trigger():
    child = _child()
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    assert child.context_compressor.threshold_percent == 0.70
    assert child.context_compressor.threshold_tokens == 700_000
    # Lowering works too: the override replaces the base in either direction.
    child = _child(threshold=0.45)
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.30})
    assert child.context_compressor.threshold_tokens == 300_000


def test_override_survives_a_model_switch():
    child = _child()
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    child.context_compressor.update_model("openai/gpt-5.2", 1_000_000)
    assert child.context_compressor.threshold_tokens == 700_000  # re-derived from the override


def test_override_survives_a_window_correction():
    child = _child()
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    child.context_compressor.context_length = 2_000_000
    assert child.context_compressor.threshold_tokens == 1_400_000  # 0.70 x the corrected window


def test_model_thresholds_still_win_over_the_delegation_ratio():
    child = _child(model_thresholds={"claude-fable-5.1": 0.30})
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    assert child.context_compressor.threshold_tokens == 300_000


def test_small_context_floor_still_applies():
    child = _child(window=128_000)
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.10})
    # Raise-only floor: models under 512K trigger at >= 75% regardless of the override.
    assert child.context_compressor.threshold_tokens == 96_000


def test_token_cap_still_lower_of_on_top_of_the_ratio():
    child = _child()
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    _apply_child_compression_cap(child, {"compression_threshold_tokens": 300_000})
    assert child.context_compressor.threshold_tokens == 300_000


def test_override_reapplies_on_an_already_resolved_compressor():
    child = _child()
    assert child.context_compressor.threshold_tokens == 500_000  # force lazy resolution
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})
    assert child.context_compressor.threshold_tokens == 700_000


def test_config_values_are_validated_not_coerced():
    """Independent-review witnesses: YAML ``true`` -> 1.0 (a never-fires trigger) and ``45`` (a
    percentage, not a fraction) both land out of contract. Both are ignored with a warning; the
    child keeps the global ratio trigger."""
    for bad in (True, "0.7", 45, -0.1, 1.5):
        assert _child_compression_ratio(bad) is None, bad
    for off in (None, 0, 0.0, False):
        assert _child_compression_ratio(off) is None
    assert _child_compression_ratio(0.45) == 0.45
    assert _child_compression_ratio(0.70) == 0.70
    assert _child_compression_ratio(1) == 1.0
    child = _child()
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": True})
    assert child.context_compressor.threshold_tokens == 500_000


def test_child_without_a_context_compressor_is_left_alone():
    child = SimpleNamespace(context_compressor=None)
    _apply_child_compression_ratio(child, {"compression_threshold_ratio": 0.70})  # must not raise
