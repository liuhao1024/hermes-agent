"""Version-stamp invalidation for the provider-models disk cache.

Regression tests for #141101: a cache written by a previous Hermes build kept
being served after an upgrade — for up to the 7-day stale-serve window, or
indefinitely when the live /v1/models probe kept failing — so models the new
build knows about never reached the picker. The cache now carries the running
build's identity (``derived_version``); a mismatched stamp reads as empty and
the next picker open refetches fresh rows.
"""

from __future__ import annotations

import json
import time

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import hermes_cli.models as mod
    return mod


class TestVersionStampRoundTrip:
    def test_save_stamps_the_running_build(self, isolated_home):
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["m1"]}})
        raw = json.loads((isolated_home._provider_models_cache_path()).read_text())
        assert raw["__hermes_version__"] == isolated_home._cache_version_stamp()
        assert raw["openrouter"]["models"] == ["m1"]

    def test_same_build_round_trips_rows(self, isolated_home):
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["m1"]}})
        cache = isolated_home._load_provider_models_cache()
        assert cache["openrouter"]["models"] == ["m1"]

    def test_save_does_not_mutate_the_caller_dict(self, isolated_home):
        data = {"openrouter": {"models": ["m1"]}}
        isolated_home._save_provider_models_cache(data)
        assert "__hermes_version__" not in data


class TestCrossBuildInvalidation:
    def test_cache_from_a_different_build_is_a_miss(self, isolated_home, monkeypatch):
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+1.gaaaaaaa")
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["old-build-model"]}})

        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+2.gbbbbbbb")
        assert isolated_home._load_provider_models_cache() == {}

    def test_unstamped_legacy_cache_is_invalidated_on_first_read(self, isolated_home):
        legacy = {"openrouter": {"fp": "fp", "at": time.time(), "models": ["legacy"]}}
        path = isolated_home._provider_models_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy))

        if isolated_home._cache_version_stamp() is not None:
            assert isolated_home._load_provider_models_cache() == {}

    def test_refetched_cache_carries_the_new_build_stamp(self, isolated_home, monkeypatch):
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+1.gaaaaaaa")
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["m1"]}})

        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+2.gbbbbbbb")
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["m2"]}})
        raw = json.loads((isolated_home._provider_models_cache_path()).read_text())
        assert raw["__hermes_version__"] == "0.22.2+2.gbbbbbbb"
        assert raw["openrouter"]["models"] == ["m2"]
        # The in-memory view stays provider-rows-only — callers iterate values() as entry dicts.
        assert set(isolated_home._load_provider_models_cache()) == {"openrouter"}

    def test_targeted_refresh_drops_a_stamp_missed_file(self, isolated_home, monkeypatch):
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+2.gbbbbbbb")
        stale = {"__hermes_version__": "0.22.2+1.gaaaaaaa", "zai": {"models": ["glm-5"]}}
        path = isolated_home._provider_models_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stale))

        isolated_home.clear_provider_models_cache("zai")
        assert not path.exists(), "an explicit refresh must not leave dead rows on disk"

    def test_targeted_refresh_keeps_a_healthy_cache_without_that_provider(self, isolated_home):
        isolated_home._save_provider_models_cache({"openrouter": {"models": ["m1"]}})
        isolated_home.clear_provider_models_cache("zai")
        cache = isolated_home._load_provider_models_cache()
        assert cache["openrouter"]["models"] == ["m1"]


class TestUnknownProvenanceDegradesToLegacy:
    def test_unstamped_read_and_write_when_version_is_unknowable(self, isolated_home, monkeypatch):
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: None)

        legacy = {"openrouter": {"fp": "fp", "at": time.time(), "models": ["legacy"]}}
        path = isolated_home._provider_models_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy))

        assert isolated_home._load_provider_models_cache()["openrouter"]["models"] == ["legacy"]

        isolated_home._save_provider_models_cache({"openai": {"models": ["gpt-x"]}})
        raw = json.loads(path.read_text())
        assert "__hermes_version__" not in raw
        assert raw["openai"]["models"] == ["gpt-x"]


class TestPickerPathRefetchesAfterUpgrade:
    def test_a_version_bump_busts_the_disk_hit_and_refetches_live(self, isolated_home, monkeypatch):
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+1.gaaaaaaa")
        monkeypatch.setattr(isolated_home, "fetch_api_models", lambda *a, **k: ["old-model"])
        assert isolated_home.cached_fetch_api_models("sk-key", "https://gw.example.com/v1") == ["old-model"]

        # The upgrade: same credentials, new build — the disk row must not serve the old catalog.
        monkeypatch.setattr(isolated_home, "_cache_version_stamp", lambda: "0.22.2+2.gbbbbbbb")
        monkeypatch.setattr(isolated_home, "fetch_api_models", lambda *a, **k: ["new-build-model"])
        assert isolated_home.cached_fetch_api_models("sk-key", "https://gw.example.com/v1") == ["new-build-model"]

        # And the row written back serves the new build without another live fetch.
        monkeypatch.setattr(isolated_home, "fetch_api_models", lambda *a, **k: pytest.fail("must be a disk hit"))
        assert isolated_home.cached_fetch_api_models("sk-key", "https://gw.example.com/v1") == ["new-build-model"]
