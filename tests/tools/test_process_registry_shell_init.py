"""Background-spawn parity for ``terminal.shell_init_files`` (#142264).

The foreground env snapshot sources every configured init file
(``LocalEnvironment._run_bash(login=True)`` → ``_prepend_shell_init``), but the
background spawn argv (``ProcessRegistry._scope_argv`` → ``$SHELL -lic``) used to
read only what a login+interactive shell sources on its own, so a PATH (or any
export) living in a custom init file applied to foreground calls only.
"""

import time

import pytest

import tools.process_registry as pr
from tools.process_registry import ProcessRegistry


@pytest.fixture()
def registry():
    return ProcessRegistry()


@pytest.fixture(autouse=True)
def _reset_systemd_scope_cache():
    """Keep ``_scope_argv`` off the systemd-scope path regardless of host."""
    original = pr._SYSTEMD_SCOPE_AVAILABLE
    pr._SYSTEMD_SCOPE_AVAILABLE = False
    yield
    pr._SYSTEMD_SCOPE_AVAILABLE = original


def _make_session(sid="shell_init_test"):
    return pr.ProcessSession(
        id=sid,
        command="printf parity",
        task_id="t1",
        started_at=time.time(),
        output_buffer="",
    )


def test_scope_argv_prepends_configured_shell_init_files(registry, monkeypatch):
    monkeypatch.setattr(pr, "_resolve_shell_init_files", lambda: ["/x/env-142264.sh"])
    argv = registry._scope_argv(_make_session(), "printf parity", "u1", "Local")
    assert argv[1] == "-lic"
    assert argv[2].startswith("set +m; set +e\n")
    assert "[ -r '/x/env-142264.sh' ] && . '/x/env-142264.sh' 2>/dev/null || true" in argv[2]
    assert argv[2].endswith("\nprintf parity")


def test_scope_argv_unchanged_without_init_files(registry, monkeypatch):
    monkeypatch.setattr(pr, "_resolve_shell_init_files", list)
    argv = registry._scope_argv(_make_session(), "printf parity", "u1", "Local")
    assert argv[2] == "set +m; printf parity"


@pytest.mark.platforms("posix")
def test_background_spawn_exports_from_shell_init_file(registry, monkeypatch, tmp_path):
    init_file = tmp_path / "env-142264.sh"
    init_file.write_text(
        'export HERMES_TEST_SHELL_INIT_PARITY_142264="sourced-by-background"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pr, "_resolve_shell_init_files", lambda: [str(init_file)])

    session = registry.spawn_local(
        'printf "%s" "$HERMES_TEST_SHELL_INIT_PARITY_142264"'
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        sess = registry._finished.get(session.id) or registry._running.get(session.id)
        if sess is not None and sess.exited:
            break
        time.sleep(0.2)
    else:
        pytest.fail("background session did not exit within 30s")

    assert sess.exit_code == 0
    assert "sourced-by-background" in sess.output_buffer
