"""Regression tests for #101113 — a credentialless satellite profile under
``gateway.profile_routes`` delivers cron output through the PRIMARY adapter
for exactly the targets the primary routes to it, and fails closed otherwise.

The multiplex ticker hands such a profile a ``SharedRouteAdapters`` view over
the primary adapter map; ``_deliver_result`` resolves a transport from it per
target using the same ``ProfileRoute.matches`` predicate as inbound routing.
"""
import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import hermes_yaml as yaml

from cron.scheduler import _deliver_result
from cron.scheduler_preflight import SharedRouteAdapters, _primary_profile_routes_for_current_home
from gateway.config import Platform, PlatformConfig
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

PRIMARY_YAML = {
    "gateway": {
        "multiplex_profiles": True,
        "profile_routes": [
            {"name": "fit", "platform": "discord", "chat_id": "1543065293755256852", "profile": "fitness"},
            {"name": "off", "platform": "discord", "chat_id": "999", "profile": "fitness", "enabled": False},
            {"name": "other", "platform": "discord", "chat_id": "777", "profile": "other"},
        ],
    }
}


def _job(chat_id: str) -> dict:
    return {"id": "a7ae1520356c", "name": "brief", "deliver": f"discord:{chat_id}"}


def _run(job, adapters):
    """Drive ``_deliver_result`` with a live loop and a real DeliveryRouter."""
    loop = MagicMock()
    loop.is_running.return_value = True

    def fake_run_coro(coro, _loop):
        future = Future()
        future.set_result(asyncio.run(coro))
        return future

    standalone = []

    async def _fake_send_to_platform(platform, pconfig, chat_id, text, **kwargs):
        standalone.append(chat_id)
        return {"success": False, "error": "DISCORD_BOT_TOKEN is not set"}

    config = MagicMock()
    config.platforms = {Platform.DISCORD: PlatformConfig(enabled=True)}
    config.get_home_channel = lambda p: None
    with patch("gateway.config.load_gateway_config", return_value=config), \
         patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}), \
         patch("tools.send_message_tool._send_to_platform", _fake_send_to_platform), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro):
        error = _deliver_result(job, "hello", adapters=adapters, loop=loop)
    return error, standalone


def _primary_adapter():
    adapter = MagicMock()
    adapter.sent = []

    async def send(chat_id, content, metadata=None):
        adapter.sent.append(chat_id)
        return {"success": True, "message_id": "m1"}

    adapter.send = send
    return adapter


def test_satellite_routes_exact_target_through_primary_adapter(tmp_path, monkeypatch):
    root = tmp_path / "root"
    fitness_home = root / "profiles" / "fitness"
    fitness_home.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump(PRIMARY_YAML), encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    primary = _primary_adapter()

    token = set_hermes_home_override(str(fitness_home))
    try:
        shared = SharedRouteAdapters(
            {Platform.DISCORD: primary}, _primary_profile_routes_for_current_home()
        )
        # exact enabled route → primary adapter sends, no standalone attempt
        error, standalone = _run(_job("1543065293755256852"), shared)
        assert error is None, error
        assert primary.sent == ["1543065293755256852"]
        assert standalone == []

        # unmatched chat, disabled route, route for another profile → the
        # primary bot is NEVER used; delivery stays on the satellite's own
        # (credentialless) standalone path and reports its failure.
        for chat in ("424242", "999", "777"):
            primary.sent.clear()
            error, standalone = _run(_job(chat), shared)
            assert error is not None and "DISCORD_BOT_TOKEN" in error
            assert primary.sent == []
            assert standalone == [chat]
    finally:
        reset_hermes_home_override(token)


def test_shared_view_is_falsy_without_routes_or_primary_adapters():
    assert not SharedRouteAdapters({}, [])
    assert SharedRouteAdapters({Platform.DISCORD: object()}, []).get(Platform.DISCORD) is None


def test_guild_scoped_route_authorizes_cron_target_even_when_satellite_has_no_platform_block(
    tmp_path, monkeypatch,
):
    """The documented Discord route shape is ``guild_id + chat_id``. A cron target carries no guild
    anchor, so the route must be matched on its target-exact discriminators; and the satellite's
    missing/disabled ``platforms.discord`` block must not veto the PRIMARY's authorized transport
    (#89302 sibling) — before, both fell to standalone "DISCORD_BOT_TOKEN is not set"."""
    root = tmp_path / "root"
    sat_home = root / "profiles" / "fitness"
    sat_home.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({
        "gateway": {"multiplex_profiles": True, "profile_routes": [
            {"platform": "discord", "guild_id": "G1", "chat_id": "C1", "profile": "fitness"}]},
    }), encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    primary = _primary_adapter()

    token = set_hermes_home_override(str(sat_home))
    try:
        shared = SharedRouteAdapters({Platform.DISCORD: primary}, _primary_profile_routes_for_current_home())
        for satellite_platforms in ({}, {Platform.DISCORD: PlatformConfig(enabled=False)}):
            primary.sent.clear()
            config = MagicMock()
            config.platforms = satellite_platforms
            config.get_home_channel = lambda p: None
            with patch("gateway.config.load_gateway_config", return_value=config):
                error, standalone = _run(_job("C1"), shared)
            assert error is None, error
            assert primary.sent == ["C1"] and standalone == []
    finally:
        reset_hermes_home_override(token)


def test_live_native_adapter_without_platform_block_is_not_treated_as_disabled():
    """#89302: a live native adapter handed in by the gateway is the authorization; an absent
    ``platforms.<p>`` block in the firing profile means "no config", not "disabled"."""
    from cron.scheduler_delivery import _resolve_target_transport

    config = MagicMock()
    config.platforms = {}
    adapter = object()
    resolved, err = _resolve_target_transport(
        {"id": "j"}, Platform.DISCORD, "discord", {"platform": "discord", "chat_id": "C1"},
        {Platform.DISCORD: adapter}, config)
    assert err is None and resolved[2] is adapter and resolved[1].enabled
    # an explicitly disabled block still vetoes
    config.platforms = {Platform.DISCORD: PlatformConfig(enabled=False)}
    resolved, err = _resolve_target_transport(
        {"id": "j"}, Platform.DISCORD, "discord", {"platform": "discord", "chat_id": "C1"},
        {Platform.DISCORD: adapter}, config)
    assert resolved is None and "not configured/enabled" in err


def test_multiplex_tick_routes_desktop_served_satellite_with_only_local_adapter(
    tmp_path, monkeypatch,
):
    """#139825: a satellite profile also chatted with from Hermes Desktop holds
    ``{LOCAL: adapter}`` in ``_profile_adapters``. LOCAL carries no platform credential, so its
    cron tick must still hand it the routed primary view — before, the non-empty map skipped
    ``SharedRouteAdapters`` and delivery fell through to the profile's own credentialless config
    (``platform 'discord' not configured/enabled``)."""
    import threading

    from cron.scheduler_provider import InProcessCronScheduler
    from cron.scheduler_preflight import SharedRouteAdapters

    root = tmp_path / "root"
    sat_home = root / "profiles" / "sat"
    (sat_home / "cron").mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({
        "gateway": {"multiplex_profiles": True, "profile_routes": [
            {"platform": "discord", "guild_id": "G1", "chat_id": "C1", "profile": "sat"}]},
    }), encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)

    primary = _primary_adapter()
    captured = {}
    stop = threading.Event()

    def _tick_and_stop(*_args, **kwargs):
        captured["adapters"] = kwargs["adapters"]
        stop.set()

    provider = InProcessCronScheduler()
    with patch("cron.scheduler.tick", side_effect=_tick_and_stop):
        thread = threading.Thread(
            target=provider.start,
            args=(stop,),
            kwargs={
                "interval": 0,
                "profile_homes": [("sat", sat_home)],
                "adapters": {Platform.DISCORD: primary},
                "profile_adapters": {"sat": {Platform.LOCAL: object()}},
                "default_profile": "default",
            },
            daemon=True,
        )
        thread.start()
        thread.join(timeout=10)
        stop.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    shared = captured["adapters"]
    assert isinstance(shared, SharedRouteAdapters)
    # The routed view releases the primary adapter for exactly the route-mapped target.
    assert shared.get(Platform.DISCORD, {"chat_id": "C1"}) is primary
    assert shared.get(Platform.DISCORD, {"chat_id": "unmapped"}) is None


BOT_PROFILE_YAML = {
    "gateway": {
        "multiplex_profiles": True,
        "profile_routes": [
            {"name": "client-forum", "platform": "discord", "chat_id": "100",
             "profile": "client", "bot_profile": "bot-b"},
        ],
    }
}


def test_bot_profile_route_resolves_the_named_bots_adapter():
    """#145971: a route with ``bot_profile`` names the bot serving the conversation. Cron
    delivery must match the route against its own bot profile and hand out that profile's
    adapter — before, ``matches`` ran without ``adapter_profile``, so a ``bot_profile`` route
    never matched at all (``platform 'discord' not configured/enabled``), and even a match
    would have handed out the default profile's bot."""
    from gateway.profile_routing import parse_profile_routes

    primary, bot_b = object(), object()
    routes = parse_profile_routes([
        {"platform": "discord", "chat_id": "100", "profile": "client", "bot_profile": "bot-b"},
    ])
    shared = SharedRouteAdapters(
        {Platform.DISCORD: primary}, routes, profile_adapters={"bot-b": {Platform.DISCORD: bot_b}})
    assert shared.get(Platform.DISCORD, {"chat_id": "100"}) is bot_b

    # Default-profile routes keep resolving the PRIMARY adapter.
    default_routes = parse_profile_routes(
        [{"platform": "discord", "chat_id": "100", "profile": "client"}])
    assert SharedRouteAdapters(
        {Platform.DISCORD: primary}, default_routes,
        profile_adapters={"bot-b": {Platform.DISCORD: bot_b}},
    ).get(Platform.DISCORD, {"chat_id": "100"}) is primary

    # Fail closed when the named profile's adapters are absent, don't cover the platform,
    # or the caller never supplied per-profile maps: never the default profile's bot.
    no_map = SharedRouteAdapters({Platform.DISCORD: primary}, routes)
    assert no_map.get(Platform.DISCORD, {"chat_id": "100"}) is None
    other_platform = SharedRouteAdapters(
        {Platform.DISCORD: primary}, routes,
        profile_adapters={"bot-b": {Platform.WHATSAPP: object()}})
    assert other_platform.get(Platform.DISCORD, {"chat_id": "100"}) is None


def test_bot_profile_route_delivery_sends_through_named_profile_adapter(tmp_path, monkeypatch):
    """End to end: a credentialless ``client`` profile's cron job delivers into the channel its
    route serves through ``bot_profile``'s bot — before, delivery fell back to the satellite's
    own credentialless config and reported ``platform 'discord' not configured/enabled``."""
    root = tmp_path / "root"
    client_home = root / "profiles" / "client"
    client_home.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump(BOT_PROFILE_YAML), encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    primary = _primary_adapter()
    bot_b = _primary_adapter()

    token = set_hermes_home_override(str(client_home))
    try:
        shared = SharedRouteAdapters(
            {Platform.DISCORD: primary}, _primary_profile_routes_for_current_home(),
            profile_adapters={"bot-b": {Platform.DISCORD: bot_b}})
        error, standalone = _run(_job("100"), shared)
        assert error is None, error
        assert bot_b.sent == ["100"]
        assert primary.sent == [] and standalone == []
    finally:
        reset_hermes_home_override(token)


def test_multiplex_tick_hands_bot_profile_satellite_the_named_bots_adapter(
    tmp_path, monkeypatch,
):
    """The multiplex ticker must pass the live per-profile adapter maps into the shared view so
    a ``bot_profile`` route can resolve the bot it names (#145971)."""
    import threading

    from cron.scheduler_provider import InProcessCronScheduler
    from cron.scheduler_preflight import SharedRouteAdapters

    root = tmp_path / "root"
    sat_home = root / "profiles" / "client"
    (sat_home / "cron").mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump(BOT_PROFILE_YAML), encoding="utf-8")
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)

    primary, bot_b = _primary_adapter(), _primary_adapter()
    captured = {}
    stop = threading.Event()

    def _tick_and_stop(*_args, **kwargs):
        captured["adapters"] = kwargs["adapters"]
        stop.set()

    provider = InProcessCronScheduler()
    with patch("cron.scheduler.tick", side_effect=_tick_and_stop):
        thread = threading.Thread(
            target=provider.start,
            args=(stop,),
            kwargs={
                "interval": 0,
                "profile_homes": [("client", sat_home)],
                "adapters": {Platform.DISCORD: primary},
                "profile_adapters": {
                    "client": {Platform.LOCAL: object()},
                    "bot-b": {Platform.DISCORD: bot_b},
                },
                "default_profile": "default",
            },
            daemon=True,
        )
        thread.start()
        thread.join(timeout=10)
        stop.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    shared = captured["adapters"]
    assert isinstance(shared, SharedRouteAdapters)
    assert shared.get(Platform.DISCORD, {"chat_id": "100"}) is bot_b
    assert shared.get(Platform.DISCORD, {"chat_id": "unmapped"}) is None
