"""Regression for #143233: bare webhook routes under multiplex isolation.

A bare ``POST /webhooks/<route>`` is default-bound (``_route_allows_profile``), but
``WebhookAdapter._profile_scope(None)`` used to be a no-op. On a multiplexed gateway
``get_secret`` fails closed without a scope, so a route script's
``build_subprocess_env()`` raised ``UnscopedSecretError`` before the interpreter
spawned, and the route answered ``200 {"status": "ignored", "reason": "script"}``
while the script never ran. A single skill-registered env passthrough (a process-wide
allowlist) was enough to trigger it; prefixed ``/p/<profile>/`` routes were immune.

``_profile_scope`` now enters the launch home's runtime scope when multiplexing is
active and no profile prefix was requested — the same fix the api_server listener got
for #61276. Single-profile gateways keep the no-op. No live gateway or network.
"""

from __future__ import annotations

import pytest

# Collection-time load: gateway.run settles hermes_bootstrap's update-recovery probe when the
# per-test home-IO guard is not yet installed; at test time _profile_scope's lazy import then
# hits the module cache instead of re-running that probe against a guarded path.
import gateway.run
from agent import secret_scope as ss
from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter


@pytest.fixture(autouse=True)
def _reset_multiplex():
    ss.set_multiplex_active(False)
    yield
    ss.set_multiplex_active(False)


@pytest.fixture
def adapter() -> WebhookAdapter:
    return WebhookAdapter(
        PlatformConfig(
            enabled=True, extra={"host": "127.0.0.1", "port": 0, "routes": {}}
        )
    )


@pytest.fixture
def launch_home(tmp_path, monkeypatch):
    """An isolated launch home whose ``.env`` carries the passthrough credential."""
    (tmp_path / ".env").write_text("NOTION_TOKEN=from-launch-home\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("NOTION_TOKEN", "from-environ")
    return tmp_path


class TestProfileScopeBareRoute:
    def test_noop_when_multiplex_off(self, adapter, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "from-environ")
        with adapter._profile_scope(None):
            # Single-profile path: unscoped get_secret reads os.environ.
            assert ss.current_secret_scope() is None
            assert ss.get_secret("NOTION_TOKEN") == "from-environ"

    def test_bare_route_binds_launch_home_when_multiplex_active(
        self, adapter, launch_home
    ):
        token = ss.set_multiplex_context(True)
        try:
            with adapter._profile_scope(None):
                assert ss.current_secret_scope() is not None
                # An active multiplex scope is authoritative over the process env.
                assert ss.get_secret("NOTION_TOKEN") == "from-launch-home"
        finally:
            ss.reset_multiplex_context(token)
        assert ss.current_secret_scope() is None

    def test_named_profile_still_binds_its_own_home(
        self, adapter, tmp_path, monkeypatch
    ):
        profile_home = tmp_path / "profiles" / "worker"
        profile_home.mkdir(parents=True)
        (profile_home / ".env").write_text(
            "NOTION_TOKEN=from-worker\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "hermes_cli.profiles.get_profile_dir", lambda name: profile_home
        )
        token = ss.set_multiplex_context(True)
        try:
            with adapter._profile_scope("worker"):
                assert ss.get_secret("NOTION_TOKEN") == "from-worker"
        finally:
            ss.reset_multiplex_context(token)

    def test_route_script_env_resolves_passthrough_under_bare_route(
        self, adapter, launch_home
    ):
        """The issue's reproduction: with a passthrough registered, a bare route's
        ``build_subprocess_env()`` must not raise, and the credential resolves through
        the launch home's scope instead of the process env."""
        from tools import env_passthrough as ep
        from tools.environments.local import build_subprocess_env

        ep._get_allowed().add(
            "NOTION_TOKEN"
        )  # what skill_view → register_env_passthrough does
        token = ss.set_multiplex_context(True)
        try:
            with adapter._profile_scope(None):
                env = (
                    build_subprocess_env()
                )  # raised UnscopedSecretError before the fix
            assert env.get("NOTION_TOKEN") == "from-launch-home"
        finally:
            ss.reset_multiplex_context(token)
            ep._get_allowed().discard("NOTION_TOKEN")
