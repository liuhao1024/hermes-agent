"""#145445: ``chat -c <title> --create-if-missing -m/--provider ...`` must create the new titled
session with the requested launch policy. The create dropped every typed flag (``_creation_policy({})``),
so the session froze the profile default and the same command's resume check then refused the flags
against the route it had just silently created with — the command exited 2 before any model call."""
import types
from contextlib import asynccontextmanager

import pytest

from hermes_cli.gateway_client import GatewayClientError
from hermes_cli.gateway_connection import GatewayConnection
from hermes_cli.gateway_session_store import GatewaySessionStore, create_titled_session

_CREATE_ONLY = {"cwd", "row_cwd", "request_id", "source", "title", "client_env"}


def snapshot(sid):
    return {"session_id": sid, "stored_session_id": sid, "messages": [], "revision": 3,
            "execution_generation": 0, "subscription_id": "sub-" + sid,
            "info": {"model": "fake-model", "cwd": "/w"}}


class RecordingClient:
    """Fake gateway: ``session.create`` freezes the policy it was sent (the owner resolves the
    ``-m`` alias and stamps ``model_requested``), ``session.resume`` answers from that freeze."""

    def __init__(self):
        self.calls = []
        self.frozen = {}

    async def rpc(self, method, *, _timeout=None, **params):
        self.calls.append((method, params))
        if method == "session.create":
            sid = "local-new"
            self.frozen[sid] = dict(params)
            return snapshot(sid)
        if method == "session.resume":
            if params["session_id"] not in self.frozen:
                raise GatewayClientError("not_found")
            snap = snapshot(params["session_id"])
            snap["info"]["launch_request"] = {key: value for key, value in self.frozen[params["session_id"]].items()
                                              if key not in _CREATE_ONLY}
            return snap
        return {"status": "ok"}


@pytest.fixture
def gateway(monkeypatch):
    client = RecordingClient()

    @asynccontextmanager
    async def connect():
        yield client

    conn = GatewayConnection(connect=connect)
    conn.start(5)
    store = GatewaySessionStore(conn)
    monkeypatch.setattr("hermes_cli.gateway_session_store.session_store", lambda: store)
    yield client, store
    conn.close()


def _create_call(client):
    return next(call for call in client.calls if call[0] == "session.create")


def test_create_titled_session_sends_the_typed_launch_policy(gateway):
    client, _ = gateway
    sid = create_titled_session("probe", launch={"model": "gpt-6.1-sol", "provider": "openai-codex",
                                                 "max_turns": 7})
    assert sid == "local-new"
    _, params = _create_call(client)
    assert (params["model"], params["provider"], params["max_turns"]) == ("gpt-6.1-sol", "openai-codex", 7)
    assert params["title"] == "probe" and params["cwd"] and params["source"] == "cli"


def test_create_titled_session_without_launch_stays_on_the_profile_default(gateway):
    client, _ = gateway
    create_titled_session("probe")
    _, params = _create_call(client)
    assert "model" not in params and "provider" not in params


def test_repeating_the_same_command_resumes_the_session_it_created(gateway):
    """The frozen route equals the flags the create carried, so the identical re-run passes the resume
    check; a different ``-m`` on the now-existing title is still refused (it would change the route)."""
    _, store = gateway
    sid = create_titled_session("probe", launch={"model": "gpt-6.1-sol", "provider": "openai-codex"})
    assert store.open_session(types.SimpleNamespace(session_id=sid, _resumed=True),
                              {"model": "gpt-6.1-sol", "provider": "openai-codex"}) == sid
    with pytest.raises(GatewayClientError, match="Resume retains gateway session policy"):
        store.open_session(types.SimpleNamespace(session_id=sid, _resumed=True), {"model": "other-model"})


def test_resolve_continue_arg_hands_the_typed_flags_to_the_create(gateway, monkeypatch):
    from hermes_cli import main as main_module
    seen = {}
    monkeypatch.setattr("hermes_cli.gateway_session_store.create_titled_session",
                        lambda title, launch=None: seen.update(title=title, launch=launch) or "local-new")
    monkeypatch.setattr(main_module, "_resolve_session_by_name_or_id", lambda name: None)
    args = types.SimpleNamespace(continue_last="fresh-title", create_if_missing=True, resume=None,
                                 model="gpt-6.1-sol", provider="openai-codex", max_turns=5, reasoning=None,
                                 api_key=None, base_url=None, toolsets=None, ignore_rules=False,
                                 checkpoints=False, pass_session_id=False, skills=None, run_budget=None,
                                 verbose=False)
    main_module._resolve_continue_arg(args, use_tui=False)
    assert seen["title"] == "fresh-title"
    assert seen["launch"]["model"] == "gpt-6.1-sol" and seen["launch"]["provider"] == "openai-codex"
    assert seen["launch"]["max_turns"] == 5
    assert seen["launch"]["run_budget"] is None and seen["launch"]["verbose"] is None


def test_resolve_continue_arg_tui_create_carries_the_flags_too(gateway, monkeypatch):
    from hermes_cli import main as main_module
    seen = {}
    monkeypatch.setattr("hermes_cli.gateway_session_store.create_titled_session",
                        lambda title, launch=None: seen.update(title=title, launch=launch) or "local-new")
    monkeypatch.setattr(main_module, "_resolve_session_by_name_or_id", lambda name: None)
    args = types.SimpleNamespace(continue_last="fresh-tui", create_if_missing=True, resume=None,
                                 model="m1", provider=None, max_turns=None, reasoning=None, api_key=None,
                                 base_url=None, toolsets=None, ignore_rules=False, checkpoints=False,
                                 pass_session_id=False, skills=None, run_budget=None, verbose=False)
    main_module._resolve_continue_arg(args, use_tui=True)
    assert seen["launch"]["model"] == "m1" and seen["launch"]["provider"] is None
