"""/new on a route paused behind a lost (``unknown``) turn.

The pause notice promised the queued followers stay saved for the operator's resume, and /stop
honors that; a messaging chat has no resume control, so /new is its only way out (#145177). The
reset cancels the saved human inputs and says so in its reply, instead of stranding them silently
on a session the route no longer points at.
"""
import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import SessionAuthority, initialize_session_authority
from hermes_state_runtime import list_session_admissions

CHAT = '15551234567@s.whatsapp.net'


async def _setup(tmp_path, monkeypatch, *, lose_head):
    """Real authority over SQLite, no drain. One head turn, then two follower messages; with
    ``lose_head`` the head is a lost (``unknown``) turn, so the FIFO is paused."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    monkeypatch.setenv('WHATSAPP_ALLOWED_USERS', CHAT)
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    adapter = WhatsAppAdapter(PlatformConfig(enabled=True, extra={'session_path': str(tmp_path / 'wa')}))
    runner.adapters = {Platform.WHATSAPP: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='reset-paused')
    source = adapter.build_source(chat_id=CHAT, user_id=CHAT, chat_type='dm')
    await authority.admit_native(MessageEvent(text='lost turn', source=source, message_id='lost-turn'))
    if lose_head:
        authority.db._execute_write(lambda conn: conn.execute(
            "UPDATE session_admissions SET status='unknown' WHERE request_id='lost-turn'"))
    await authority.admit_native(MessageEvent(text='how much is left?', source=source, message_id='follow-1'))
    await authority.admit_native(MessageEvent(text='so where is the result?', source=source, message_id='follow-2'))

    def rows():
        return list_session_admissions(authority.db, session_id=next(iter(authority.sessions)),
                                       pending_only=False)
    return runner, authority, source, rows


@pytest.mark.asyncio
@pytest.mark.parametrize('lose_head', [True, False], ids=['paused', 'not_paused'])
async def test_cancel_paused_route_queue(tmp_path, monkeypatch, lose_head):
    """The helper cancels exactly the queued human inputs of a paused route and reports the count;
    on a live route it is a no-op (``_hm_stop_owner_turn`` already handled those rows)."""
    runner, authority, source, rows = await _setup(tmp_path, monkeypatch, lose_head=lose_head)
    try:
        dropped = await runner._cancel_paused_route_queue(runner._session_key_for_source(source))
        after = rows()
        if lose_head:
            assert dropped == 2
            assert all(row['status'] == 'terminal' and row['outcome'] == 'cancelled'
                       for row in after if row['request_id'] in ('follow-1', 'follow-2'))
        else:
            assert dropped == 0
            assert all(row['status'] == 'queued'
                       for row in after if row['request_id'] in ('follow-1', 'follow-2'))
    finally:
        authority.db.close()


@pytest.mark.asyncio
async def test_reset_reply_reports_the_cancelled_inputs(tmp_path, monkeypatch):
    """/new on a paused route: the followers are cancelled and the reset reply says so."""
    runner, authority, source, rows = await _setup(tmp_path, monkeypatch, lose_head=True)
    try:
        reply = await runner._handle_reset_command(MessageEvent(text='/new', source=source,
                                                                  message_id='cmd-1'))
        text = reply.text if hasattr(reply, 'text') else str(reply)
        assert '2 message(s)' in text and 'paused' in text, text
        after = rows()
        assert all(row['status'] == 'terminal'
                   for row in after if row['request_id'] in ('follow-1', 'follow-2'))
    finally:
        authority.db.close()


@pytest.mark.asyncio
async def test_stop_keeps_the_paused_queue(tmp_path, monkeypatch):
    """/stop honors the pause notice: the followers stay queued (the operator's to resume)."""
    runner, authority, source, rows = await _setup(tmp_path, monkeypatch, lose_head=True)
    try:
        await runner._hm_stop_owner_turn(runner._session_key_for_source(source))
        after = rows()
        assert [row['request_id'] for row in after if row['status'] == 'queued'] == ['follow-1', 'follow-2']
    finally:
        authority.db.close()
