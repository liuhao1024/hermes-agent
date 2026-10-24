"""A satellite profile under ``gateway.multiplex_profiles`` has no webhook platform of its
own: the host gateway runs the one listener and the profile-bound routes live in the HOST's
``webhook_subscriptions.json`` with a ``profile`` field (#143901).

These pin the Webhooks view contract for a satellite request: enablement and base URL come
from the host, listed routes are the host's bound to that profile with their ``/p/<profile>``
URLs, writes land in the HOST's store bound to the satellite, and the host-only routes are
not the satellite's to act on. Without a live multiplexer every endpoint keeps reading the
profile's own scope (the pre-multiplex behaviour).
"""
import json

import pytest
import hermes_yaml as yaml


@pytest.fixture(autouse=True)
def _multiplex_state_is_per_test():
    """Entering a satellite home flips the process to fail-closed multi-profile hosting (a
    one-way flip); contain it to one test like test_web_routers_destructive_profile_scope."""
    from agent import secret_scope
    from tui_gateway import launch_profile_policy

    was_active = secret_scope.is_multiplex_active()
    snapshot = launch_profile_policy._snapshot
    secret_scope.set_multiplex_active(False)
    launch_profile_policy._snapshot = None
    try:
        yield
    finally:
        secret_scope.set_multiplex_active(was_active)
        launch_profile_policy._snapshot = snapshot


@pytest.fixture
def homes(tmp_path, monkeypatch, _isolate_hermes_home):
    """Launch (host) home with the webhook platform on 8647 + two bound routes; satellite
    ``mara`` with no webhook config and no subscriptions of its own."""
    from hermes_constants import get_hermes_home
    from hermes_cli import profiles

    launch_home = get_hermes_home()
    profiles_root = launch_home / "profiles"
    mara = profiles_root / "mara"
    mara.mkdir(parents=True, exist_ok=True)
    for home in (launch_home, mara):
        (home / "config.yaml").write_text(yaml.safe_dump({
            "proxy": {"label": home.name},
        }), encoding="utf-8")
        (home / ".env").write_text("PROFILE_MARKER=1\n", encoding="utf-8")
    (launch_home / "config.yaml").write_text(yaml.safe_dump({
        "proxy": {"label": "default"},
        "platforms": {"webhook": {"enabled": True, "extra": {"host": "0.0.0.0", "port": 8647}}},
    }), encoding="utf-8")
    (launch_home / "webhook_subscriptions.json").write_text(json.dumps({
        "festival-status-refresh": {
            "secret": "s-festival", "events": [], "profile": "mara",
        },
        "alerts": {"secret": "s-alerts", "events": []},
    }), encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: launch_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"launch": launch_home, "mara": mara}


@pytest.fixture
def client(homes, monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    from hermes_cli.web_routers import ops

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", homes["launch"] / "state.db")
    # The live multiplexer probe is the host record's job; here it answers "default serves mara".
    monkeypatch.setattr(
        ops, "_multiplex_webhook_host",
        lambda profile: "default" if (profile or "").strip().lower() == "mara" else None,
    )
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def test_satellite_list_reads_the_host_platform_and_bound_routes(client):
    body = client.get("/api/webhooks", params={"profile": "mara"}).json()

    assert body["enabled"] is True
    assert body["base_url"] == "http://localhost:8647"
    assert body["multiplex_host"] == "default"
    assert [row["name"] for row in body["subscriptions"]] == ["festival-status-refresh"]
    route = body["subscriptions"][0]
    assert route["url"] == "http://localhost:8647/p/mara/webhooks/festival-status-refresh"
    assert route["secret_set"] is True
    assert "secret" not in route  # redacted on read like every other surface


def test_satellite_create_binds_the_route_in_the_host_store(client, homes):
    r = client.post(
        "/api/webhooks", params={"profile": "mara"},
        json={"name": "sync-trigger", "deliver": "log"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "http://localhost:8647/p/mara/webhooks/sync-trigger"
    assert body["secret"]

    # The write landed in the HOST's store, bound to the satellite; the satellite got none.
    host_file = json.loads((homes["launch"] / "webhook_subscriptions.json").read_text())
    assert host_file["sync-trigger"]["profile"] == "mara"
    assert not (homes["mara"] / "webhook_subscriptions.json").exists()


def test_satellite_enable_is_refused_instead_of_writing_a_second_listener(client, homes):
    r = client.post("/api/webhooks/enable", params={"profile": "mara"})

    assert r.status_code == 409
    assert "multiplexer" in r.json()["detail"]
    # The satellite's config was not written: a second listener on the default port is the
    # collision the refusal prevents.
    assert "webhook" not in (homes["mara"] / "config.yaml").read_text()


def test_satellite_delete_and_toggle_only_touch_routes_bound_to_it(client, homes):
    r = client.put(
        "/api/webhooks/festival-status-refresh/enabled",
        params={"profile": "mara"}, json={"enabled": False},
    )
    assert r.status_code == 200
    host_file = json.loads((homes["launch"] / "webhook_subscriptions.json").read_text())
    assert host_file["festival-status-refresh"]["enabled"] is False

    # A host-bound route is not the satellite's to act on: 404, like a missing name.
    assert client.delete("/api/webhooks/alerts", params={"profile": "mara"}).status_code == 404
    assert client.put(
        "/api/webhooks/alerts/enabled", params={"profile": "mara"}, json={"enabled": False},
    ).status_code == 404

    assert client.delete(
        "/api/webhooks/festival-status-refresh", params={"profile": "mara"},
    ).status_code == 200
    host_file = json.loads((homes["launch"] / "webhook_subscriptions.json").read_text())
    assert "festival-status-refresh" not in host_file
    assert "alerts" in host_file  # untouched


def test_without_a_live_multiplexer_the_profile_keeps_its_own_scope(homes, monkeypatch):
    """No patched probe here: the real one finds no host record in the isolated home, so the
    satellite reads its own (absent) webhook config — the pre-multiplex behaviour stands."""
    from starlette.testclient import TestClient

    import hermes_state
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", homes["launch"] / "state.db")
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    body = c.get("/api/webhooks", params={"profile": "mara"}).json()
    assert body["enabled"] is False
    assert body["base_url"] == "http://localhost:8644"
    assert body["subscriptions"] == []
    assert "multiplex_host" not in body
