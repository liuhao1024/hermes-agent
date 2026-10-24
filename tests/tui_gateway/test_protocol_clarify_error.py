"""Regression coverage for a client error reply to a batch clarify (#144158).

``resolve_response`` used to settle an error frame to ``result=None, answered=False``
without going through :func:`tools.clarify_batch.settle_batch`, so a batch clarify came
back to the agent as ``{"answers": {}, "outcome": "undelivered"}`` — the question looked
never-delivered (agents re-present it, the user sees it twice) and answers already locked
through ``clarify.lock`` were dropped. ``settle_batch``'s contract, the session owner's
registry (``gateway/session_server_requests.py::resolve``) and ``cancel()`` all treat an
error frame as a cancel-all: locked answers stand, outcome ``cancelled``.
"""

from __future__ import annotations

from tui_gateway import server_requests
from tests.tui_gateway.test_protocol import _start_batch_clarify, capture, server


def test_clarify_batch_error_reply_settles_cancelled_and_keeps_locks(capture):  # health: allow F811 -- pytest injects the imported capture fixture by parameter name
    """A client error reply to a batch clarify is a cancel-all per settle_batch's contract —
    outcome "cancelled" with the locked answers kept, not "undelivered" with empty answers."""
    srv, buf = capture
    thread, box, req = _start_batch_clarify(srv, buf, ["q0", "q1"])
    locked = srv.handle_request({"id": "e1", "method": "clarify.lock",
                                 "params": {"request_id": req.id, "question_id": "q0", "answer": "kept"}})
    assert locked["result"]["status"] == "ok"
    assert srv.dispatch({"jsonrpc": "2.0", "id": req.id, "error": {"code": -32601, "message": "no handler"}}) is None
    thread.join(timeout=5)
    assert box["answer"] == {"answers": {"q0": "kept"}, "outcome": "cancelled"}
    assert server_requests.open_requests("s1") == []
