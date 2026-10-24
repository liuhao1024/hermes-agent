"""A timed-out ``kill -0`` liveness probe says nothing about liveness (#144117).

The env poller used to finish a background job as ``exited`` (exit code -1)
from the output of a probe that never answered: under load, ``docker exec``
returns ``[Command timed out after 5s]`` with returncode 124, which the poller
read as proof the PID was gone. A probe that did not run must be skipped, like
an unparseable log header, and the job kept running until a probe that did
answer reports the PID gone.
"""

import time
from unittest.mock import patch

from tools.process_registry import ProcessRegistry, ProcessSession


def _make_session(sid="proc_probe_timeout", output=""):
    return ProcessSession(
        id=sid,
        command="sleep 300",
        task_id="t1",
        started_at=time.time(),
        exited=False,
        exit_code=None,
        output_buffer=output,
    )


def _run_poller(registry, session, responses):
    """Drive the env poller over a canned response sequence, one per execute call."""

    class FakeEnv:
        def __init__(self):
            self.commands = []
            self._responses = iter(responses)

        def execute(self, command, **kwargs):
            self.commands.append(command)
            return next(self._responses)

    env = FakeEnv()
    with patch("tools.process_registry.time.sleep", return_value=None), \
        patch.object(registry, "_move_to_finished"):
        registry._env_poller_loop(
            session, env, "/tmp/bg.log", "/tmp/bg.pid", "/tmp/bg.exit"
        )
    return env.commands


# What tools/environments/base.py returns when a command outlives its timeout.
TIMEOUT_PROBE = {
    "output": "[Command timed out after 5s]",
    "returncode": 124,
    "hermes_timed_out": True,
}


def test_timed_out_probe_keeps_the_job_running():
    registry = ProcessRegistry()
    session = _make_session()
    commands = _run_poller(
        registry,
        session,
        [
            {"output": "0 0\n"},   # log delta: nothing new
            dict(TIMEOUT_PROBE),   # kill -0 probe times out (docker exec under load)
            {"output": "0 0\n"},
            {"output": "0\n"},     # probe answered: PID still alive
            {"output": "0 0\n"},
            {"output": "1\n"},     # probe answered: PID gone
            {"output": "7\n"},     # the wrapper's captured exit code
        ],
    )
    probes = [c for c in commands if "kill -0" in c]
    assert len(probes) == 3, "the timed-out probe must be followed by two more probes"
    assert session.exited is True
    assert session.exit_code == 7
    assert session.completion_reason == "exited"


def test_probe_hard_failure_also_skips_the_poll():
    """A probe whose exec failed outright (no exit status in sight) must not
    finish the session either."""
    registry = ProcessRegistry()
    session = _make_session()
    commands = _run_poller(
        registry,
        session,
        [
            {"output": "0 0\n"},
            {"output": "docker exec: container paused\n", "returncode": 126},
            {"output": "0 0\n"},
            {"output": "1\n"},
            {"output": "3\n"},
        ],
    )
    probes = [c for c in commands if "kill -0" in c]
    assert len(probes) == 2
    assert session.exited is True
    assert session.exit_code == 3
