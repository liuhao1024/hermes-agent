"""A following session's model pick keeps non-followed private values at their own path.

The follows-profile branch names the model keys it drops from the frozen route; the secrets
re-keying below it maps an unrelated legacy entry's old index to its new one. The dropped-keys
tuple once shared the re-key map's name, so a pick on a session whose frozen config also held a
private value outside the followed sections (a platform token) crashed on ``tuple.get`` and the
WS dispatcher answered ``-32603`` instead of switching the model. Real SessionStore/SessionAuthority,
a real config.yaml and a loopback catalog endpoint; only models.dev metadata is held offline.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from tests.gateway.test_session_follow_profile_config import _create, _owner, _route

MODELS = ('original', 'picked')


class _Catalog(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        raw = json.dumps({'data': [{'id': name} for name in MODELS]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def endpoints():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Catalog)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, f'http://127.0.0.1:{server.server_port}/v1'
    server.shutdown()
    server.server_close()


@pytest.fixture
def home(monkeypatch):
    from agent import models_dev
    from gateway import run
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    # gateway.run binds its home at first import; the loader itself is real.
    monkeypatch.setattr(run, '_hermes_home', home)
    catalog = {'custom': {'id': 'custom', 'models': {
        name: {'id': name, 'name': name, 'tool_call': True, 'cost': {'input': 0, 'output': 0},
               'limit': {'context': 100000, 'output': 4000}} for name in MODELS}}}
    monkeypatch.setattr(models_dev, 'fetch_models_dev', lambda *args, **kwargs: catalog)
    return home


async def _pick(owner, authority, sid, payload, request_id='model-pick'):
    row = authority.db.get_session(sid)
    return await owner.dispatch({'id': 'pick', 'method': 'session.mutate', 'params': {
        'session_id': sid, 'request_id': request_id, 'expected_revision': row['runtime_revision'],
        'expected_generation': row['runtime_generation'], 'operation': 'model', 'payload': payload}})


def _bind_resolver(runner):
    from gateway.run_turn_prepare import GatewayTurnPrepareMixin
    runner._peek_session_state = lambda key: None
    runner._resolve_session_agent_runtime = GatewayTurnPrepareMixin._resolve_session_agent_runtime.__get__(runner)


@pytest.mark.asyncio
async def test_following_pick_switches_the_model_with_unfollowed_section_secrets(home, endpoints):
    """A Bot Chat whose profile also declares a platform token: the pick switches the model (instead
    of crashing on the dropped-keys tuple) and the token stays committed at its own config path."""
    _, url = endpoints
    token = 'tg-' + 'follow-review-token'
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'original', 'base_url': url, 'api_key': 'key-a'},
        'platforms': {'telegram': {'token': token}}}), encoding='utf-8')
    store, runner, authority, owner = _owner(home)
    _bind_resolver(runner)
    try:
        live = await _create(owner, authority, home, 'follow')
        sid = next(sid for sid, value in authority.sessions.items() if value is live)

        response = await _pick(owner, authority, sid, {'model': 'picked', 'provider': 'custom'})

        assert 'result' in response, response
        assert _route(runner, live) == ('picked', url, 'key-a')
        from gateway.session_policy import policy_for_source
        committed = policy_for_source(runner, live.source).config(authority)
        assert committed['platforms']['telegram']['token'] == token
    finally:
        await owner.close()
        store.close_all_db_handles()
