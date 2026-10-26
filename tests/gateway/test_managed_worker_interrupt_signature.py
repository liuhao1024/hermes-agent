"""The busy-path interrupt callers pass the would-be follow-up text (``run_busy`` /
``run_inbound``); a managed worker must accept it and still latch the stop. No real child is
needed: ``interrupt`` only records the stop and queues the control frame."""
import io

from gateway.session_managed_worker import ManagedWorker


class _FakeProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = None
        self.pid = 0

    def poll(self):
        return 0  # already exited: close() skips signaling

    def wait(self, timeout=None):
        return 0


def test_interrupt_accepts_text_and_stops():
    worker = ManagedWorker(_FakeProcess())
    worker.interrupt('new instructions')  # must not raise TypeError
    assert worker.stopped_at is not None
    assert worker.commands.get_nowait() == {'type': 'stop'}
    worker.close()


def test_interrupt_without_text_stops():
    worker = ManagedWorker(_FakeProcess())
    worker.interrupt()
    assert worker.stopped_at is not None
    assert worker.commands.get_nowait() == {'type': 'stop'}
    worker.close()
