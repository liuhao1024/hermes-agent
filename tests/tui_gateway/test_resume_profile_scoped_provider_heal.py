"""LIVE E2E: multiplexed-gateway ``session.resume`` must evaluate a stored
session's provider overrides against the SESSION's profile config, never the
launch profile's (#115607).

The report shape: one gateway launched under ``default`` also serves a
secondary profile whose config registers the SAME endpoint under a DIFFERENT
custom-provider name (``local-vllm`` vs the launch profile's ``local-code``).
The heal path inside ``_stored_session_runtime_overrides``
(``is_routable_provider`` / ``canonical_custom_identity``) reads the ACTIVE
home's config — on pre-fix main the deferred and cold resume paths evaluated
it unscoped, so the secondary session's ``custom:local-vllm`` was deemed
unroutable and "healed" to the LAUNCH profile's ``custom:local-code``; the
deferred build inside the secondary profile then failed with
``AuthError: Unknown provider 'custom:local-code'``.

Post-fix both paths evaluate inside ``_profile_build_scope`` (the eager path
already did), so the stored provider survives verbatim.

Run:  python -m pytest tests/tui_gateway/test_resume_profile_scoped_provider_heal.py -o addopts= -v -s
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest
import yaml

SHARED_URL = "https://litellm-shared.invalid/v1"

# Dummy fixture credentials — placeholders for the fake litellm endpoint so
# each config's custom_providers entry is a fully-formed routable provider.
# Never real secrets.
_DUMMY_LAUNCH_KEY = "dummy-launch-local-code-key"
_DUMMY_PROFILE_KEY = "dummy-profile-local-vllm-key"


@pytest.fixture()
def multiplex_homes(monkeypatch):
    """A launch home (default) + a secondary profile home sharing one endpoint
    under different custom-provider names, both REAL on-disk HERMES_HOMEs."""
    tmp = Path(tempfile.mkdtemp(prefix="hermes-multiplex-heal-"))
    launch_home = tmp / "launch" / ".hermes"
    launch_home.mkdir(parents=True)
    profile_home = tmp / "profiles" / "homelab"
    profile_home.mkdir(parents=True)

    (launch_home / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {"default": "test-model-live", "provider": "custom:local-code"},
            "custom_providers": [
                {
                    "name": "local-code",
                    "base_url": SHARED_URL,
                    "api_key": _DUMMY_LAUNCH_KEY,
                    "api_mode": "chat_completions",
                }
            ],
        })
    )
    # Secondary profile: the SAME endpoint, registered under a different name.
    (profile_home / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {"default": "test-model-live", "provider": "custom:local-vllm"},
            "custom_providers": [
                {
                    "name": "local-vllm",
                    "base_url": SHARED_URL,
                    "api_key": _DUMMY_PROFILE_KEY,
                    "api_mode": "chat_completions",
                }
            ],
        })
    )

    monkeypatch.setenv("HERMES_HOME", str(launch_home))
    # hermes_constants caches the resolved home at first read — the env var
    # alone doesn't repoint an already-imported process. Use the override API
    # (the same mechanism profile-scoped resumes use).
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_token = set_hermes_home_override(str(launch_home))
    # Neutralize ambient provider creds so resolution uses ONLY the configs
    # above — this must behave the same on a dev box and a bare CI runner.
    for var in list(os.environ):
        if var.endswith("_API_KEY") or var in ("OPENROUTER_KEY", "NOUS_KEY"):
            monkeypatch.delenv(var, raising=False)

    import hermes_cli.config as hconfig
    import hermes_cli.profiles as profiles_mod
    import hermes_cli.runtime_provider as rp

    for mod in (hconfig, rp):
        for attr in ("_config_cache", "_cache", "_CONFIG_CACHE"):
            if hasattr(mod, attr):
                try:
                    setattr(mod, attr, None)
                except Exception:
                    pass
    # get_profile_dir("homelab") must resolve to the tmp profiles root so the
    # gateway can serve the secondary profile from this test's tmpdir.
    monkeypatch.setattr(profiles_mod, "_get_profiles_root", lambda: tmp / "profiles")

    import hermes_state
    import tui_gateway.server as server

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", launch_home / "state.db")
    monkeypatch.setattr(server, "_db", None, raising=False)
    monkeypatch.setattr(server, "_db_error", None, raising=False)
    monkeypatch.setattr(server, "_hermes_home", str(launch_home), raising=False)
    yield launch_home, profile_home, server
    try:
        if server._db is not None:
            server._db.close()
    except Exception:
        pass
    server._db = None
    try:
        reset_hermes_home_override(home_token)
    except Exception:
        pass


def _seed_profile_session_row(profile_home: Path) -> str:
    """Write a REAL session row pinned to the secondary profile's provider."""
    from hermes_state import SessionDB

    db = SessionDB(db_path=profile_home / "state.db")
    sid = uuid.uuid4().hex[:12]
    db.create_session(
        sid,
        source="desktop",
        model="test-model-live",
        model_config={
            "model": "test-model-live",
            "provider": "custom:local-vllm",
            "base_url": SHARED_URL,
            "api_mode": "chat_completions",
        },
        session_key=f"live-test:{sid}",
    )
    db.set_session_title(sid, "Homelab local chat")
    db.close()
    return sid


def _resume(server, sid: str, extra_params: dict) -> dict:
    """Drive the REAL dispatch entry (the funnel the Desktop WS transport uses)."""
    return server.handle_request({
        "id": f"rid-{sid}",
        "method": "session.resume",
        "params": {"session_id": sid, "profile": "homelab", **extra_params},
    })


def _teardown(server, resp) -> None:
    live_sid = (resp.get("result") or {}).get("session_id")
    if live_sid:
        server.handle_request({
            "id": "close",
            "method": "session.close",
            "params": {"session_id": live_sid},
        })


class TestMultiplexedResumeProfileScopedHeal:
    def test_deferred_resume_keeps_session_profile_provider(self, multiplex_homes):
        """defer_history resume (the Desktop reopen path): the stored
        ``custom:local-vllm`` must NOT be healed to the launch profile's
        ``custom:local-code`` entry for the same endpoint."""
        launch_home, profile_home, server = multiplex_homes
        sid = _seed_profile_session_row(profile_home)
        resp = _resume(server, sid, {"defer_history": True, "omit_messages": True})
        try:
            assert "error" not in resp, (
                f"deferred resume failed live: {resp.get('error')}"
            )
            info = (resp.get("result") or {}).get("info") or {}
            assert info.get("provider") == "custom:local-vllm", (
                f"provider healed against the launch profile: {info.get('provider')!r}"
            )
            # The live record's deferred build input must carry the same identity.
            live_sid = resp["result"]["session_id"]
            record = server._sessions.get(live_sid) or {}
            overrides = record.get("resume_runtime_overrides") or {}
            assert overrides.get("provider_override") == "custom:local-vllm", (
                f"resume_runtime_overrides healed against the launch profile: {overrides}"
            )
        finally:
            _teardown(server, resp)

    def test_cold_resume_keeps_session_profile_provider(self, multiplex_homes):
        """Default cold resume (agent pre-warmed off the response path): same
        contract — the stored provider is evaluated against the session's own
        profile config, not the launch profile's."""
        launch_home, profile_home, server = multiplex_homes
        sid = _seed_profile_session_row(profile_home)
        resp = _resume(server, sid, {"omit_messages": True})
        try:
            assert "error" not in resp, f"cold resume failed live: {resp.get('error')}"
            info = (resp.get("result") or {}).get("info") or {}
            assert info.get("provider") == "custom:local-vllm", (
                f"provider healed against the launch profile: {info.get('provider')!r}"
            )
        finally:
            _teardown(server, resp)
