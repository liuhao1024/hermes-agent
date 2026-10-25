"""Regression tests for targeted ``clear_provider_models_cache`` lookups (#144379).

``cached_fetch_api_models`` stores custom-endpoint rows keyed as
``custom:<base_url>#<fingerprint>``, so the clear side must match the row by
prefix: an exact lookup on the caller's spelling always missed, the function
returned silently, and a per-provider refresh could never drop the row.
"""

from unittest.mock import patch

from hermes_cli import models


ROW = {"models": ["m1"], "at": 1.0, "fp": "fp"}

URL = "http://127.0.0.1:8316/v1"
KEY = f"custom:{URL}#11dd19946973451a"


def _run_clear(cache: dict, provider):
    with patch.object(models, "_load_provider_models_cache", return_value=dict(cache)), \
         patch.object(models, "_save_provider_models_cache") as save:
        models.clear_provider_models_cache(provider)
    return save


class TestTargetedClearDropsFingerprintedCustomRows:

    def test_clear_custom_url_drops_fingerprinted_row(self):
        save = _run_clear({KEY: dict(ROW), "openrouter": dict(ROW)}, f"custom:{URL}")
        saved = save.call_args[0][0]
        assert KEY not in saved
        assert saved == {"openrouter": dict(ROW)}

    def test_clear_full_cache_key_spelling_drops_row(self):
        save = _run_clear({KEY: dict(ROW)}, KEY)
        assert KEY not in save.call_args[0][0]

    def test_clear_url_with_trailing_slash_and_case_drops_row(self):
        save = _run_clear({KEY: dict(ROW)}, f"custom:{URL}/".upper())
        assert save.called and KEY not in save.call_args[0][0]

    def test_clear_configured_slug_drops_custom_url_row(self):
        config = {"providers": {"yz-local": {"api": URL, "name": "Yz-local"}}}
        with patch("hermes_cli.config.load_config", return_value=config):
            save = _run_clear({KEY: dict(ROW)}, "yz-local")
        assert save.called and KEY not in save.call_args[0][0]

    def test_clear_custom_named_spelling_drops_custom_url_row(self):
        config = {"providers": {"yz-local": {"api": URL, "name": "Yz-local"}}}
        with patch("hermes_cli.config.load_config", return_value=config):
            save = _run_clear({KEY: dict(ROW)}, "custom:yz-local")
        assert save.called and KEY not in save.call_args[0][0]

    def test_clear_legacy_custom_providers_alias_drops_row(self):
        config = {"custom_providers": [{"name": "Yz Relay", "base_url": URL}]}
        with patch("hermes_cli.config.load_config", return_value=config):
            save = _run_clear({KEY: dict(ROW)}, "yz-relay")
        assert save.called and KEY not in save.call_args[0][0]

    def test_clear_unrelated_provider_keeps_custom_row(self):
        save = _run_clear({KEY: dict(ROW), "ollama": dict(ROW)}, "ollama")
        saved = save.call_args[0][0]
        assert saved[KEY] == dict(ROW)
        assert "ollama" not in saved

    def test_clear_unconfigured_slug_is_a_clean_noop(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            save = _run_clear({KEY: dict(ROW)}, "no-such-provider")
        assert not save.called
