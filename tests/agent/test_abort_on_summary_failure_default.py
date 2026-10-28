"""``compression.abort_on_summary_failure`` defaults to True (fail-closed) — #146088.

A failed summary call (aux route down / timeout / non-JSON) used to silently
commit a "summary unavailable" placeholder and drop the middle of the
conversation; the default now aborts and keeps the transcript, with the
gateway surfacing the failure on-channel. Three layers must agree:
DEFAULT_CONFIG (the ``load_config()`` deep-merge truth), the
``_parse_compression_config`` fallback (its docstring: "Defaults here MUST
match DEFAULT_CONFIG"), and the explicit ``false`` opt-out must still restore
the silent deterministic-fallback degrade.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from agent.agent_init import _parse_compression_config
from hermes_cli.config import DEFAULT_CONFIG, cfg_get


def _agent_stub() -> SimpleNamespace:
    return SimpleNamespace(model="test-model", provider="openai")


def test_default_config_pins_abort_on_summary_failure_true():
    assert cfg_get(DEFAULT_CONFIG, "compression", "abort_on_summary_failure") is True


def test_parse_compression_config_defaults_to_abort():
    cs = _parse_compression_config(_agent_stub(), {})
    assert cs.abort_on_summary_failure is True


def test_explicit_false_still_opts_out():
    cs = _parse_compression_config(
        _agent_stub(), {"compression": {"abort_on_summary_failure": False}}
    )
    assert cs.abort_on_summary_failure is False


def test_deep_merged_user_config_missing_key_inherits_abort_default():
    from hermes_cli.config import _deep_merge

    merged = _deep_merge(
        copy.deepcopy(DEFAULT_CONFIG), {"compression": {"threshold": 0.6}}
    )
    assert merged["compression"]["abort_on_summary_failure"] is True
