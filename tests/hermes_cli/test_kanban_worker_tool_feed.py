"""The owner-run kanban worker's log carries a live tool feed again.

Since claims moved to owner admission, the dispatcher subprocess only submits and polls
receipts, and the #140597 drain threw every frame away: the Worker log the dashboard
drawer and ``hermes kanban log`` show stayed empty for the whole run, and the stderr exit
trailer landed in the log before the block-buffered stdout reply. The feed now renders
each ``tool.start`` frame as it happens while still taking every frame off the queue —
the bounded queue must never park the socket reader with the next receipt behind it.
"""
import asyncio
import json

import pytest

from hermes_cli import kanban_worker_client
from hermes_cli.gateway_client import GatewayClient


def _tool_start(name, context):
    return {'jsonrpc': '2.0', 'method': 'event', 'params': {
        'type': 'tool.start', 'session_id': 's1', 'seq': 1,
        'payload': {'tool_id': 'c1', 'name': name, 'context': context}}}


def test_feed_line_renders_a_tool_start():
    line = kanban_worker_client._feed_line(_tool_start('terminal', 'ls -la /tmp'))
    stamp, rest = line.split(' ┊ ', 1)
    assert stamp.count(':') == 2  # HH:MM:SS
    assert rest == 'terminal ls -la /tmp'


def test_feed_line_renders_only_tool_starts():
    assert kanban_worker_client._feed_line(
        {'params': {'type': 'message.delta', 'payload': {'text': 'prose'}}}) == ''
    assert kanban_worker_client._feed_line({'params': {'type': 'tool.start'}}) == ''
    assert kanban_worker_client._feed_line({'jsonrpc': '2.0'}) == ''
    assert kanban_worker_client._feed_line('not a frame') == ''


def test_feed_line_redacts_the_context(monkeypatch):
    from agent import redact
    seen = {}

    def fake_redact(text, *, force=False, **_):
        seen['text'], seen['force'] = text, force
        return 'safe-preview'

    monkeypatch.setattr(redact, 'redact_sensitive_text', fake_redact)
    line = kanban_worker_client._feed_line(_tool_start('terminal', 'raw context'))
    assert seen == {'text': 'raw context', 'force': True}
    assert 'safe-preview' in line and 'raw context' not in line


async def _run_feed(events):
    feed = asyncio.create_task(kanban_worker_client._feed(events))
    while not events.empty():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    feed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await feed


@pytest.mark.asyncio
async def test_feed_drains_every_frame_and_prints_tool_starts(capsys):
    events = asyncio.Queue()
    frames = [
        {'params': {'type': 'message.delta', 'payload': {'text': 'interim prose'}}},
        _tool_start('terminal', 'ls'),
        'not-a-frame',
        {'params': {'type': 'tool.start'}},
        _tool_start('file_write', 'OK.txt'),
    ]
    for frame in frames:
        events.put_nowait(frame)
    await _run_feed(events)
    assert events.empty()  # every frame taken: the reader never parks behind a full queue
    out = capsys.readouterr().out
    assert ' ┊ terminal ls' in out and ' ┊ file_write OK.txt' in out
    assert 'interim prose' not in out


@pytest.mark.asyncio
async def test_feed_survives_a_render_failure(monkeypatch, capsys):
    from agent import redact
    monkeypatch.setattr(redact, 'redact_sensitive_text',
                        lambda text, *, force=False, **_: (_ for _ in ()).throw(RuntimeError('render failure')))
    events = asyncio.Queue()
    events.put_nowait(_tool_start('terminal', 'x'))
    events.put_nowait(_tool_start('file_write', 'y'))
    await _run_feed(events)
    assert events.empty()  # the drain lives on; a frame that cannot render is skipped, not fatal
    assert capsys.readouterr().out == ''


class _Owner:
    """Answers ``kanban.run`` with a terminal receipt, same shape as the admission-wait tests."""

    def __init__(self):
        self.inbox = asyncio.Queue()

    async def send(self, raw):
        req = json.loads(raw)
        if req['method'] == 'kanban.run':
            await self.inbox.put(json.dumps({'id': req['id'], 'result': {
                'session_id': 's1', 'receipt': {'status': 'terminal', 'admission_id': 'a1'}}}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.inbox.get()


@pytest.mark.asyncio
async def test_the_reply_prints_flushed(monkeypatch, tmp_path):
    prints = []
    real_print = print

    def spy(*args, **kwargs):
        prints.append((args, kwargs))
        return real_print(*args, **kwargs)

    monkeypatch.setattr('builtins.print', spy)
    from gateway import session_kanban
    monkeypatch.setattr(session_kanban, 'worker_result',
                        lambda db, params: {'exit_code': 0, 'last_output': 'the final reply'})
    async with GatewayClient(_Owner()) as client:
        code = await kanban_worker_client._submit_claim(
            client, {'board': 'b', 'task_id': 't1', 'run_id': 1, 'claim_lock': 'lock'},
            str(tmp_path / 'kanban.db'))
    assert code == 0
    reply = [kwargs for args, kwargs in prints if args and args[0] == 'the final reply']
    assert reply and reply[0].get('flush') is True
