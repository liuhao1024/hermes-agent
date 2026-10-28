"""E2E tests for the unified provider-credential lifecycle (#51071 #59761 #62269).

A provider API key can live in .env, auth.json's credential_pool, and
config.yaml mirrors at once. These tests drive the REAL dashboard endpoint
handlers (PUT/DELETE /api/env) against real on-disk fixtures in a temp
HERMES_HOME (tests/conftest.py isolation) and assert every store agrees
afterwards.

All fake secrets are constructed at runtime so no key-shaped literal ever
lands in the repo.
"""

import json

import pytest
from fastapi.testclient import TestClient

from hermes_cli.web_server import _SESSION_TOKEN, app

client = TestClient(app)
HEADERS = {"X-Hermes-Session-Token": _SESSION_TOKEN}

# Runtime-constructed fake credentials (never literal key-shaped strings).
FAKE_ZAI_KEY = "zk-" + "a" * 24
FAKE_OAUTH_TOKEN = "oa-" + "b" * 24
NEW_KEY = "zk-" + "c" * 24


@pytest.fixture
def hermes_home(monkeypatch, tmp_path):
    """Fresh HERMES_HOME with .env + auth.json + config.yaml fixtures."""
    home = tmp_path / "cred_home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    from hermes_cli.config import invalidate_env_cache

    invalidate_env_cache()
    return home


def _write_env(home, **pairs):
    home.joinpath(".env").write_text(
        "".join(f"{k}={v}\n" for k, v in pairs.items()), encoding="utf-8"
    )
    from hermes_cli.config import invalidate_env_cache

    invalidate_env_cache()


def _write_auth(home, pool):
    home.joinpath("auth.json").write_text(
        json.dumps({"credential_pool": pool}), encoding="utf-8"
    )


def _read_auth(home):
    return json.loads(home.joinpath("auth.json").read_text(encoding="utf-8"))


def _zai_pool_fixture():
    """One env-seeded API-key entry plus one OAuth entry for the same provider."""
    return {
        "zai": [
            {
                "id": "e1",
                "label": "env",
                "auth_type": "api_key",
                "priority": 0,
                "source": "env:ZAI_API_KEY",
                "access_token": FAKE_ZAI_KEY,
            },
            {
                "id": "o1",
                "label": "oauth",
                "auth_type": "oauth",
                "priority": 0,
                "source": "device_code",
                "access_token": FAKE_OAUTH_TOKEN,
                "refresh_token": "rt-" + "d" * 16,
            },
        ]
    }


# ---------------------------------------------------------------------------
# DELETE — #51071 / #59761: stale credential_pool entries must be pruned
# ---------------------------------------------------------------------------




def test_delete_clears_provider_models_cache(hermes_home):
    _write_env(hermes_home, ZAI_API_KEY=FAKE_ZAI_KEY)
    _write_auth(hermes_home, {"zai": [_zai_pool_fixture()["zai"][0]]})
    cache_path = hermes_home / "provider_models_cache.json"
    cache_path.write_text(
        json.dumps({"zai": {"models": ["glm-5"], "ts": 0}}), encoding="utf-8"
    )

    resp = client.request(
        "DELETE", "/api/env", json={"key": "ZAI_API_KEY"}, headers=HEADERS
    )
    assert resp.status_code == 200
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert "zai" not in cache


# ---------------------------------------------------------------------------
# UPDATE — #62269: config.yaml mirrors of the old key must rotate with .env
# ---------------------------------------------------------------------------


def _write_config(home, text):
    home.joinpath("config.yaml").write_text(text, encoding="utf-8")


def test_update_rotates_config_yaml_model_mirror(hermes_home):
    old = "sk-oe-" + "f" * 24
    new = "sk-oe-" + "g" * 24
    _write_env(hermes_home, OPENAI_API_KEY=old)
    _write_config(
        hermes_home,
        "model:\n"
        "  provider: custom\n"
        "  default: my-model\n"
        "  base_url: https://llm.example.test/v1\n"
        f"  api_key: {old}\n",
    )

    resp = client.put(
        "/api/env", json={"key": "OPENAI_API_KEY", "value": new}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert "model.api_key" in resp.json().get("config_updates", [])

    cfg_text = hermes_home.joinpath("config.yaml").read_text(encoding="utf-8")
    assert old not in cfg_text, "stale old key left in config.yaml (#62269)"
    assert new in cfg_text, "config.yaml mirror not rotated to the new key"

    from hermes_cli.config import load_env

    assert load_env()["OPENAI_API_KEY"] == new




# ---------------------------------------------------------------------------
# Desktop PUT /api/env — #96058: credential_pool must be materialized so the
# live runtime picks up the new key without waiting for its next background
# load_pool() or a separate `hermes auth add`.
# ---------------------------------------------------------------------------


# OpenCode Go is a registered api_key provider with api_key_env_vars containing
# the single env var OPENCODE_GO_API_KEY — exercising the exact reproducer
# from issue #96058 (Ubuntu 24.04, openai_sdk 2.24.0, provider=opencode-go).
OPENCODE_KEY_NEW = "ocg-" + "e" * 28


def test_put_api_env_materializes_credential_pool_entry(hermes_home):
    """Desktop Providers → API keys → Save must write a credential_pool entry.

    Pre-fix: save_provider_env_credential only mutated .env. The live pool
    kept authenticating with a stale higher-precedence config.yaml mirror or
    the old cached credential until a separate ``hermes auth add opencode-go``
    ran. auth.json mtime was unchanged before/after Save (#96058).

    Post-fix: the same PUT /api/env call must also materialize an entry under
    ``credential_pool.<provider>`` in auth.json so the next request
    authenticates immediately, matching ``hermes auth add <provider> --type
    api-key`` behavior.
    """
    # Start clean: empty auth.json so the only way a pool entry shows up is
    # via the PUT /api/env handler we're testing.
    _write_auth(hermes_home, {})

    resp = client.put(
        "/api/env",
        json={"key": "OPENCODE_GO_API_KEY", "value": OPENCODE_KEY_NEW},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("key") == "OPENCODE_GO_API_KEY"

    # auth.json must now have a credential_pool entry for opencode-go. The
    # exact source string lives in source="env:OPENCODE_GO_API_KEY" — env
    # sources are sanitized on disk (the raw token is replaced with a
    # fingerprint; the canonical secret lives in .env and gets re-hydrated by
    # load_pool() on each read). The critical observable is: load_pool() on
    # the next call returns an in-memory entry carrying the just-saved token.
    auth = _read_auth(hermes_home)
    pool = auth.get("credential_pool", {})
    assert "opencode-go" in pool, (
        "PUT /api/env did not materialize a credential_pool entry for "
        "opencode-go (#96058)"
    )
    entries = pool["opencode-go"]
    assert isinstance(entries, list) and entries, pool
    matched_disk = [
        e for e in entries
        if isinstance(e.get("source"), str)
        and e["source"] == "env:OPENCODE_GO_API_KEY"
    ]
    assert matched_disk, (
        f"credential_pool.opencode-go env-seeded reference missing on disk; "
        f"got {entries!r}"
    )
    # And: a fresh load_pool() must surface the just-saved token to the
    # runtime. This is the actual end-to-end contract — anything weaker
    # means the OpenAI client will 401 because it never receives the new key.
    from agent.credential_pool import load_pool
    pool_obj = load_pool("opencode-go")
    runtime_entries = pool_obj.entries()
    matched_runtime = [
        e for e in runtime_entries
        if e.access_token == OPENCODE_KEY_NEW
        and isinstance(e.source, str)
        and e.source == "env:OPENCODE_GO_API_KEY"
    ]
    assert matched_runtime, (
        "load_pool('opencode-go') did not surface the just-saved token; "
        "the live runtime will keep 401'ing (#96058). "
        f"Got sources: {[e.source for e in runtime_entries]!r}"
    )
    assert matched_runtime[0].auth_type == "api_key"




# ---------------------------------------------------------------------------
# Keyed `providers` schema (v12+) — where the dashboard writes custom
# endpoints. Its inline api_key is a real credential and higher-precedence
# than the env var, so a stale copy left here shadows a rotation (#62269) and
# survives a "remove from EVERY store" delete.
# ---------------------------------------------------------------------------


def test_update_rotates_keyed_providers_mirror(hermes_home):
    old = "sk-kp-" + "n" * 24
    new = "sk-kp-" + "o" * 24
    _write_env(hermes_home, OPENAI_API_KEY=old)
    _write_config(
        hermes_home,
        "providers:\n"
        "  myendpoint:\n"
        "    base_url: https://llm.example.test/v1\n"
        f"    api_key: {old}\n",
    )

    resp = client.put(
        "/api/env", json={"key": "OPENAI_API_KEY", "value": new}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert "providers.myendpoint.api_key" in resp.json().get("config_updates", [])

    cfg_text = hermes_home.joinpath("config.yaml").read_text(encoding="utf-8")
    assert old not in cfg_text, "stale key in providers.<id> shadows the rotation (#62269)"
    assert new in cfg_text, "keyed providers mirror not rotated to the new key"


def test_delete_scrubs_keyed_providers_mirror(hermes_home):
    old = "sk-kp-" + "p" * 24
    _write_env(hermes_home, OPENAI_API_KEY=old)
    _write_config(
        hermes_home,
        "providers:\n"
        "  myendpoint:\n"
        "    base_url: https://llm.example.test/v1\n"
        f"    api_key: {old}\n",
    )

    resp = client.request(
        "DELETE", "/api/env", json={"key": "OPENAI_API_KEY"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert "providers.myendpoint.api_key" in resp.json()["config_scrubbed"]
    cfg_text = hermes_home.joinpath("config.yaml").read_text(encoding="utf-8")
    assert old not in cfg_text, "delete must clear the credential from EVERY store"


def test_scrub_never_touches_providers_base_url_alias(hermes_home):
    """In the keyed ``providers`` schema ``api`` is the base_url alias, NOT a
    credential. Even if the .env value coincided with a base_url, the scrub
    must not rewrite a provider's endpoint URL."""
    old = "https://llm.example.test/v1"  # a URL that also happens to be the key value
    _write_env(hermes_home, OPENAI_API_KEY=old)
    _write_config(
        hermes_home,
        "providers:\n"
        "  myendpoint:\n"
        f"    api: {old}\n"          # base_url alias — must be preserved
        "    model: my-model\n",
    )

    resp = client.request(
        "DELETE", "/api/env", json={"key": "OPENAI_API_KEY"}, headers=HEADERS
    )
    assert resp.status_code == 200
    cfg_text = hermes_home.joinpath("config.yaml").read_text(encoding="utf-8")
    assert old in cfg_text, "providers.<id>.api is a base_url and must survive"
    assert "providers.myendpoint.api" not in resp.json().get("config_scrubbed", [])


# ---------------------------------------------------------------------------
# Suppression round-trip: delete sticks, re-add lifts it
# Numbered rotation siblings (<VAR>_2) ride the same round-trip (#146059):
# they seed a pool row via _env_key_var_candidates, but they matched NO
# provider in _providers_for_env_var — so the re-add after a delete never
# lifted the suppression and the key was silently ignored while the save
# reported ok.
# ---------------------------------------------------------------------------

FAKE_DEEPSEEK_KEY = "sk-dsk-" + "h" * 24
FAKE_DEEPSEEK_SIBLING = "sk-dsk-" + "i" * 24


def test_put_api_env_seeds_numbered_sibling_immediately(hermes_home):
    """First-time PUT of <VAR>_2 must materialize the pool row right away.

    Pre-fix: _providers_for_env_var("DEEPSEEK_API_KEY_2") matched nothing
    (the registry declares only DEEPSEEK_API_KEY), so the save only touched
    .env and the sibling surfaced in the pool only after the NEXT background
    load_pool() — unlike the base var, which materializes immediately
    (#96058).
    """
    _write_auth(hermes_home, {})
    _write_env(hermes_home, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY)

    resp = client.put(
        "/api/env",
        json={"key": "DEEPSEEK_API_KEY_2", "value": FAKE_DEEPSEEK_SIBLING},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text

    # auth.json must hold the seeded row NOW: the save itself runs
    # load_pool() for the owning provider (the #96058 contract). Pre-fix the
    # sibling matched no provider, so the row only appeared after the NEXT
    # load_pool() — this assertion pins the immediacy on disk, before any
    # further load_pool() call could paper over the gap.
    pool = _read_auth(hermes_home).get("credential_pool", {}).get("deepseek", [])
    assert any(
        isinstance(e, dict) and e.get("source") == "env:DEEPSEEK_API_KEY_2"
        for e in pool
    ), (
        "first-time PUT of a numbered sibling did not materialize the pool "
        f"row on disk; got {pool!r} (#146059)"
    )

    # And the live pool must surface the just-saved token (re-hydrated from
    # .env — the sanitized disk row never carries the raw secret).
    from agent.credential_pool import load_pool

    matched = [
        e
        for e in load_pool("deepseek").entries()
        if e.source == "env:DEEPSEEK_API_KEY_2"
    ]
    assert matched and matched[0].access_token == FAKE_DEEPSEEK_SIBLING, (
        "load_pool('deepseek') did not surface the just-saved sibling token "
        f"(#146059); got {[e.source for e in load_pool('deepseek').entries()]!r}"
    )


def test_readd_numbered_sibling_lifts_suppression(hermes_home):
    """save → remove → save on <VAR>_2 must end with the key usable again.

    Pre-fix: the delete suppressed env:DEEPSEEK_API_KEY_2 (the prune side
    walks the pools it actually touched), but the re-add matched no provider,
    so the source stayed in suppressed_sources and load_pool() never ran —
    failover to the second key silently stopped working (#146059).
    """
    _write_auth(hermes_home, {})
    _write_env(hermes_home, DEEPSEEK_API_KEY=FAKE_DEEPSEEK_KEY)

    from agent.credential_pool import load_pool
    from hermes_cli.auth import is_source_suppressed

    # First save. On pre-fix code the pool row lands only via the live
    # runtime's periodic load_pool(); drive it once explicitly, exactly as
    # the issue's reproducer does, so the remove below has something to
    # prune — this test targets the re-add, not the materialization gap.
    resp = client.put(
        "/api/env",
        json={"key": "DEEPSEEK_API_KEY_2", "value": FAKE_DEEPSEEK_SIBLING},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    load_pool("deepseek")

    resp = client.request(
        "DELETE", "/api/env", json={"key": "DEEPSEEK_API_KEY_2"}, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert is_source_suppressed("deepseek", "env:DEEPSEEK_API_KEY_2")

    # The re-add is an explicit re-add: suppression must lift and the pool
    # row must come back carrying the saved token.
    resp = client.put(
        "/api/env",
        json={"key": "DEEPSEEK_API_KEY_2", "value": FAKE_DEEPSEEK_SIBLING},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True

    assert not is_source_suppressed("deepseek", "env:DEEPSEEK_API_KEY_2"), (
        "re-adding a removed numbered sibling left env:DEEPSEEK_API_KEY_2 "
        "suppressed; the new credential is silently never used (#146059)"
    )
    entries = [
        e
        for e in load_pool("deepseek").entries()
        if e.source == "env:DEEPSEEK_API_KEY_2"
    ]
    assert entries and entries[0].access_token == FAKE_DEEPSEEK_SIBLING, (
        "the re-added numbered sibling never surfaced in the pool; failover "
        "to the second key silently stopped working (#146059)"
    )


# ---------------------------------------------------------------------------
# GET /api/env — provider_primary pass-through for the Desktop Keys tab
# ---------------------------------------------------------------------------
# The Desktop groups a provider card's rows by provider_label and picks the
# card's main "Paste key" field from `provider_primary` first. That flag is
# computed per catalog entry in _catalog_provider_env_metadata (index == 0 of
# the provider's own api_key_env_vars) but _row used to drop it, so a card's
# own first credential arrived with provider_primary=None and the grouping
# fell back to the first non-advanced key var — which, for a profile-shared
# credential contributed by peer providers (DASHSCOPE_API_KEY is index >= 1
# of alibaba-coding-plan-cn), could be a FOREIGN tier's key.


def test_get_api_env_passes_provider_primary_through(hermes_home):
    """Every provider card's own index-0 credential must stay its main field."""
    resp = client.get("/api/env", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    env = resp.json()

    # The CN Coding Plan card: its own key is its primary; the shared
    # DASHSCOPE_API_KEY alias joins the card marked primary=False.
    assert env["ALIBABA_CODING_PLAN_CN_API_KEY"]["provider_primary"] is True
    dashscope = env["DASHSCOPE_API_KEY"]["provider_profiles"]
    cn_profile = next(
        p for p in dashscope if p["provider"] == "alibaba-coding-plan-cn"
    )
    assert cn_profile["primary"] is False, (
        "DASHSCOPE_API_KEY is a fallback alias for alibaba-coding-plan-cn; "
        "marking it primary would re-point the card's main field away from "
        "ALIBABA_CODING_PLAN_CN_API_KEY"
    )
