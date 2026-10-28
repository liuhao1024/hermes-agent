"""/bg and /btw on the shared gateway: the side task runs in the gateway (prompt.background /
prompt.btw) and its answer is routed off the event stream by task id, never built in the CLI."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from hermes_cli import gateway_side
from hermes_cli.gateway_client import GatewayRPCError
from hermes_cli.gateway_connection import GatewayConnection

SID = "local-side"


class _Client:
    def __init__(self, text):
        self.events = asyncio.Queue()
        self.calls = []
        self.text = text

    async def rpc(self, method, *, _timeout=None, **params):
        self.calls.append((method, params))
        kind = "background.complete" if method == "prompt.background" else "btw.complete"
        # The answer can land before the verb's reply reaches the caller.
        await self.events.put({"jsonrpc": "2.0", "method": "event", "params": {
            "type": kind, "session_id": SID, "payload": {"task_id": "bg_1", "text": self.text}}})
        return {"task_id": "bg_1"}


def _connection(client):
    @asynccontextmanager
    async def connect():
        yield client
    conn = GatewayConnection(connect=connect)
    conn.start(5)
    return conn


@pytest.mark.parametrize("verb", ["background", "btw"])
def test_side_task_runs_on_the_gateway_and_its_answer_is_routed_by_task_id(verb):
    client = _Client("DOG14 background answer")
    conn = _connection(client)
    try:
        task_id, wait = gateway_side.start(conn, SID, verb, "compute something")
        assert task_id == "bg_1"
        assert wait() == "DOG14 background answer"
        assert client.calls == [(f"prompt.{verb}", {"session_id": SID, "text": "compute something"})]
        assert conn.events.empty()  # never left for the turn pump / idle tick to drop
    finally:
        conn.close()


def test_a_failed_side_task_raises_the_gateways_error():
    conn = _connection(_Client("error: model unavailable"))
    try:
        _task_id, wait = gateway_side.start(conn, SID, "background", "x")
        with pytest.raises(RuntimeError, match="model unavailable"):
            wait()
    finally:
        conn.close()


class _RefusingClient:
    """``prompt.{verb}`` refuses a socket that holds no subscription for the session
    (``gateway/session_tui_side.py::_admit``) until a ``session.resume`` registers one."""

    def __init__(self, resolved=None, always_deny=False):
        self.events = asyncio.Queue()
        self.calls = []
        self.resolved = resolved or SID
        self.resumed = False
        self.always_deny = always_deny

    async def rpc(self, method, *, _timeout=None, **params):
        self.calls.append((method, params))
        if method == "session.resume":
            self.resumed = True
            return {"session_id": self.resolved, "subscription_id": "sub_1"}
        if self.always_deny or not self.resumed:
            raise GatewayRPCError("permission_denied")
        kind = "background.complete" if method == "prompt.background" else "btw.complete"
        await self.events.put({"jsonrpc": "2.0", "method": "event", "params": {
            "type": kind, "session_id": self.resolved, "payload": {"task_id": "bg_1", "text": "ok"}}})
        return {"task_id": "bg_1"}


@pytest.mark.parametrize("verb", ["background", "btw"])
def test_a_refused_side_task_reattaches_and_retries_once(verb):
    client = _RefusingClient(resolved="local-root")
    conn = _connection(client)
    try:
        task_id, wait = gateway_side.start(conn, SID, verb, "hello")
        assert task_id == "bg_1"
        assert wait() == "ok"
        assert client.resumed  # the retry rode one re-attach, not a blind resend
        assert [(m, p) for m, p in client.calls if m == f"prompt.{verb}"] == [
            (f"prompt.{verb}", {"session_id": SID, "text": "hello"}),
            (f"prompt.{verb}", {"session_id": "local-root", "text": "hello"}),
        ]
        assert conn.state("local-root").subscription_id == "sub_1"
        assert conn.events.empty()
    finally:
        conn.close()


@pytest.mark.parametrize("verb", ["background", "btw"])
def test_a_refusal_a_reattach_cannot_clear_is_raised(verb):
    client = _RefusingClient(always_deny=True)
    conn = _connection(client)
    try:
        with pytest.raises(GatewayRPCError, match="permission_denied"):
            gateway_side.start(conn, SID, verb, "hello")
        # One re-attach, one retry: the caller sees the owner's own verdict, not a retry storm.
        assert len([1 for m, _ in client.calls if m == f"prompt.{verb}"]) == 2
    finally:
        conn.close()


def test_refusal_message_is_one_line_per_refusal():
    assert "re-attaches" in gateway_side.refusal_message(GatewayRPCError("permission_denied"))
    assert "resume it" in gateway_side.refusal_message(GatewayRPCError("not_found"))
    other = gateway_side.refusal_message(GatewayRPCError("admission_closed"))
    assert "admission_closed" in other
    assert "\n" not in other
