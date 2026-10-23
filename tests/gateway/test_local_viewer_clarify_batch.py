"""A classic-CLI viewer's clarify batch goes out as ONE ``clarify`` request (main's batch panel shows
every question at once); the messaging path still asks one card per question."""
import threading
import time
from types import SimpleNamespace

from gateway.config import Platform


class _Peer:
    def __init__(self):
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)
        return True

    def close(self):
        pass


def _turn():
    from gateway.session_events import SessionEvents
    events, peer = SessionEvents(), _Peer()
    events.fanout.attach(peer)
    authority = SimpleNamespace(sessions={'s1': SimpleNamespace(event_stream=events)})
    turn = SimpleNamespace(_approval_owner=(authority, 's1', 1), _ctx=SimpleNamespace(
        source=SimpleNamespace(platform=Platform.LOCAL), agent_holder=[SimpleNamespace(_interrupt_requested=False)]))
    return turn, peer


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


QUESTIONS = [{'qid': 'q0', 'question': 'colour?', 'choices': ['red', 'blue'], 'multi_select': False},
             {'qid': 'q1', 'question': 'size?', 'choices': None, 'multi_select': False}]


def test_batch_is_one_request_and_the_panels_answers_return(monkeypatch):
    from gateway.session_clarify_batch import ask_clarify_batch
    from gateway.session_server_requests import resolve
    turn, peer = _turn()
    out = {}
    worker = threading.Thread(target=lambda: out.update(ask_clarify_batch(turn, QUESTIONS)))
    worker.start()
    _wait_for(lambda: any(f.get('method') == 'clarify' for f in peer.frames))
    requests = [f for f in peer.frames if f.get('method') == 'clarify']
    assert len(requests) == 1
    params = requests[0]['params']
    assert params == {'session_id': 's1', 'questions': [
        {'qid': 'q0', 'question': 'colour?', 'choices': ['red', 'blue'], 'multi_select': False},
        {'qid': 'q1', 'question': 'size?', 'multi_select': False}]}
    from tui_gateway.contracts.registry import SERVER_REQUESTS, validate_params
    assert validate_params(SERVER_REQUESTS['clarify'], params)[1] is None
    resolve({'jsonrpc': '2.0', 'id': requests[0]['id'],
             'result': {'answers': {'q0': 'red', 'q1': None, 'zz': 'x'}, 'outcome': 'submitted'}})
    worker.join(5)
    assert out == {'answers': {'q0': 'red', 'q1': None}, 'outcome': 'submitted'}


def test_runner_routes_a_classic_cli_turn_to_the_batch(monkeypatch):
    from gateway import run_turn_progress, run_turn_runner, session_cli_turn
    import gateway.session_clarify_batch as batch
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch', lambda turn, qs: {'answers': {'q0': 'blue'}, 'outcome': 'submitted'})
    runner = run_turn_runner.TurnRunner.__new__(run_turn_runner.TurnRunner)
    runner._ctx = SimpleNamespace(inbound_message_id='adm-1', source=SimpleNamespace(platform=Platform.LOCAL))
    runner._ask_clarify_question = lambda *a, **k: (_ for _ in ()).throw(AssertionError('per-card path used'))
    with session_cli_turn.cli_turn_scope('adm-1', {'clarify_batch': True}):
        assert runner._clarify_callback_sync(QUESTIONS) == {'answers': {'q0': 'blue'}, 'outcome': 'submitted'}


def test_a_cli_source_turn_the_classic_cli_did_not_submit_keeps_one_card_per_question(monkeypatch):
    """A ``cli``-source session is also driven by editors (ACP) and raw WebSocket viewers that answer
    ``clarify.request`` cards, never the batch ``clarify`` server request: only a turn the classic
    CLI submitted with ``clarify_batch`` goes to main's batch panel."""
    from gateway import run_turn_progress, run_turn_runner
    from gateway import session_cli_turn
    import gateway.session_clarify_batch as batch
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch', lambda *a: (_ for _ in ()).throw(AssertionError('batch path used')))
    runner = run_turn_runner.TurnRunner.__new__(run_turn_runner.TurnRunner)
    runner._ctx = SimpleNamespace(inbound_message_id='adm-1', source=SimpleNamespace(platform=Platform.LOCAL))
    asked = []
    runner._ask_clarify_question = lambda question, *a, **k: asked.append(question) or ('blue', True)
    with session_cli_turn.cli_turn_scope('adm-1', {'api_text': 'x'}):
        reply = runner._clarify_callback_sync(QUESTIONS)
    assert asked == ['colour?', 'size?']
    assert reply == {'answers': {'q0': 'blue', 'q1': 'blue'}, 'outcome': 'submitted'}


def test_a_tui_turn_that_asked_for_the_batch_gets_the_batch_panel(monkeypatch):
    """TUI-18: main's Ink TUI (like its classic CLI and Desktop) showed the clarify tool's whole
    question set in one batch panel. A turn whose submitting viewer declares ``clarify_batch`` gets
    the one ``clarify`` request whatever its surface, not one card per question."""
    from gateway import run_turn_progress, run_turn_runner, session_cli_turn
    import gateway.session_clarify_batch as batch
    from gateway.session_policy import SURFACES
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr('gateway.session_policy.policy_for_source',
                        lambda runner, source: SimpleNamespace(platform=SURFACES['tui']))
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch', lambda turn, qs: {'answers': {'q0': 'red'}, 'outcome': 'submitted'})
    runner = run_turn_runner.TurnRunner.__new__(run_turn_runner.TurnRunner)
    runner._runner = SimpleNamespace()
    runner._ctx = SimpleNamespace(inbound_message_id='adm-1', source=SimpleNamespace(platform=Platform.LOCAL))
    runner._ask_clarify_question = lambda *a, **k: (_ for _ in ()).throw(AssertionError('per-card path used'))
    with session_cli_turn.cli_turn_scope('adm-1', {'clarify_batch': True}):
        assert runner._clarify_callback_sync(QUESTIONS) == {'answers': {'q0': 'red'}, 'outcome': 'submitted'}


def _batch_runner(live, admission_id='adm-notice'):
    from gateway import run_turn_runner
    runner = run_turn_runner.TurnRunner.__new__(run_turn_runner.TurnRunner)
    runner._ctx = SimpleNamespace(inbound_message_id=admission_id,
                                  source=SimpleNamespace(platform=Platform.LOCAL))
    runner._approval_owner = (SimpleNamespace(sessions={'s1': live}), 's1', 1)
    return runner


def test_a_notice_woken_turn_batches_while_a_batch_viewer_stays_attached(monkeypatch):
    """#143477: a turn the gateway admits itself (an ``async_delegation_complete`` / ``process_complete``
    notice) has no viewer submit to carry ``clarify_batch``; the live viewer that declared it on an
    earlier submit still answers the one batch ``clarify`` request, so the turn batches."""
    from gateway import run_turn_progress, run_turn_runner, session_cli_turn
    import gateway.session_clarify_batch as batch
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch',
                        lambda turn, qs: {'answers': {'q0': 'red'}, 'outcome': 'submitted'})
    live = SimpleNamespace(clarify_batch={'sub-1'}, subscribers={'sub-1': SimpleNamespace(transport_id='t1')})
    runner = _batch_runner(live)
    runner._ask_clarify_question = lambda *a, **k: (_ for _ in ()).throw(AssertionError('per-card path used'))
    # A notice admission commits no ``cli_turn_v1`` and ``viewer_turn`` stays unset (no viewer input).
    with session_cli_turn.cli_turn_scope('adm-notice', None):
        assert runner._clarify_callback_sync(QUESTIONS) == {'answers': {'q0': 'red'}, 'outcome': 'submitted'}


def test_a_notice_woken_turn_after_the_batch_viewer_detached_keeps_one_card_per_question(monkeypatch):
    """The capability is the viewer's, not the session's forever: once the batch-capable viewer
    detached (its subscription retired), a notice-woken turn asks the per-question cards again —
    no attached viewer can answer the batch request."""
    from gateway import run_turn_progress, run_turn_runner, session_cli_turn
    import gateway.session_clarify_batch as batch
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch', lambda *a: (_ for _ in ()).throw(AssertionError('batch path used')))
    live = SimpleNamespace(clarify_batch=set(), subscribers={})  # detach discarded 'sub-1'
    runner = _batch_runner(live)
    asked = []
    runner._ask_clarify_question = lambda question, *a, **k: asked.append(question) or ('blue', True)
    with session_cli_turn.cli_turn_scope('adm-notice', None):
        reply = runner._clarify_callback_sync(QUESTIONS)
    assert asked == ['colour?', 'size?']
    assert reply == {'answers': {'q0': 'blue', 'q1': 'blue'}, 'outcome': 'submitted'}


def test_a_viewer_submit_without_the_flag_keeps_cards_even_with_a_batch_viewer_attached(monkeypatch):
    """The session-level capability never overrides a submitter's own declaration: a viewer that
    submits without ``clarify_batch`` (an ACP editor, a raw WebSocket client) keeps per-question
    cards on ITS turn even while a batch-capable viewer is attached to the same session."""
    from gateway import run_turn_progress, run_turn_runner, session_cli_turn
    from gateway.session_ingress import viewer_turn
    import gateway.session_clarify_batch as batch
    monkeypatch.setattr(run_turn_progress, 'local_viewer_turn', lambda turn: True)
    monkeypatch.setattr(run_turn_runner, '_finite_viewer_turn', lambda: False)
    monkeypatch.setattr(batch, 'ask_clarify_batch', lambda *a: (_ for _ in ()).throw(AssertionError('batch path used')))
    live = SimpleNamespace(clarify_batch={'sub-1'}, subscribers={'sub-1': SimpleNamespace(transport_id='t1')})
    runner = _batch_runner(live, admission_id='adm-2')
    asked = []
    runner._ask_clarify_question = lambda question, *a, **k: asked.append(question) or ('blue', True)
    # A viewer input that committed no CLI fields: empty ``cli_turn``, but ``viewer_turn`` is bound.
    with session_cli_turn.cli_turn_scope('adm-2', None):
        token = viewer_turn.set(live)
        try:
            reply = runner._clarify_callback_sync(QUESTIONS)
        finally:
            viewer_turn.reset(token)
    assert asked == ['colour?', 'size?']
    assert reply == {'answers': {'q0': 'blue', 'q1': 'blue'}, 'outcome': 'submitted'}

