"""PID-1 orphan warning, moved verbatim from ``hermes_cli/main.py``.

A Compose service that overrides ``entrypoint:`` to invoke hermes directly
makes hermes itself PID 1; see the function docstring for the full rationale.
"""

import os
import sys


def _warn_if_unsupervised_pid1(pid: int | None = None) -> None:
    """Warn when this process is PID 1 with nothing above it to reap orphans.

    The official image's ENTRYPOINT (``docker/entrypoint-dispatch.sh`` -> s6-overlay's
    ``/init``) is the reaper for orphaned grandchildren (browser tooling, MCP servers, shell
    children). A Compose service that overrides ``entrypoint:`` to invoke hermes directly makes
    hermes itself PID 1 — nothing then ``wait()``s on those orphans and they accumulate as
    zombies without bound (#111577). Outside a container a user process is never PID 1, so
    this is quiet everywhere else; it mirrors the dispatcher's own non-PID-1 warning.
    """
    if (pid if pid is not None else os.getpid()) != 1:
        return
    print(
        "[hermes] WARNING: this process is PID 1 with no init above it "
        "(entrypoint override?). Orphaned child processes will not be "
        "reaped and will accumulate as zombies. Use the image's default "
        "ENTRYPOINT (docker/entrypoint-dispatch.sh) instead of overriding "
        "it, or run with `docker run --init` / `init: true` in Compose.",
        file=sys.stderr,
    )
