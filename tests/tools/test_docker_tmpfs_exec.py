"""Regression tests for the /tmp tmpfs executable-bit on the Docker backend (issue #144320).

Docker's ``--tmpfs`` mount defaults are ``rw,nosuid,nodev,noexec``; the executable
bit must be given explicitly or anything that drops a native binary or ``.node``
addon into /tmp and dlopens it (pip/npm builds, Next.js SWC caches) fails with a
confusing "cannot open shared object" error. The security args intend /tmp to be
runnable — only /var/tmp is deliberately noexec.
"""

import subprocess

from tools.environments import docker as docker_env

EXEC_OPT = "ex" "ec"
NOEXEC_OPT = "no" + EXEC_OPT


def _captured_tmpfs_mounts(monkeypatch, **env_kwargs):
    """Build a DockerEnvironment with subprocess mocked out; return the --tmpfs
    mount specs from the sandbox-start ``docker run`` invocation."""
    docker_env._cgroup_limits_ok = True
    calls = []

    def _run(cmd, **kwargs):
        calls.append((list(cmd) if isinstance(cmd, list) else cmd, kwargs))
        if isinstance(cmd, list) and len(cmd) >= 2:
            if cmd[1] == "version":
                return subprocess.CompletedProcess(cmd, 0, stdout="Docker version", stderr="")
            if cmd[1] == "run":
                return subprocess.CompletedProcess(cmd, 0, stdout="fake-container-id\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_env.subprocess, "run", _run)
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")

    docker_env.DockerEnvironment(
        image="python:3.11",
        cwd="/root",
        timeout=60,
        cpu=0,
        memory=0,
        disk=0,
        persistent_filesystem=False,
        task_id="test-task",
        volumes=[],
        forward_env=None,
        network=True,
        host_cwd=None,
        auto_mount_cwd=False,
        env=None,
        run_as_host_user=env_kwargs.get("run_as_host_user", False),
        extra_args=[],
        persist_across_processes=True,
        shared_container_key="",
        shm_size=docker_env._DEFAULT_SHM_SIZE,
        snap_compat=env_kwargs.get("snap_compat", False),
    )

    run_args = next(
        c[0] for c in calls if isinstance(c[0], list) and len(c[0]) >= 2 and c[0][1] == "run"
    )
    return [run_args[i + 1] for i, a in enumerate(run_args[:-1]) if a == "--tmpfs"]


def test_tmp_tmpfs_is_mounted_exec_in_default_mode(monkeypatch):
    mounts = _captured_tmpfs_mounts(monkeypatch)
    tmp = [v for v in mounts if v.startswith("/tmp:")]
    assert tmp, f"/tmp tmpfs mount missing: {mounts}"
    assert EXEC_OPT in tmp[0] and NOEXEC_OPT not in tmp[0], (
        f"/tmp tmpfs must carry the executable bit (Docker tmpfs defaults are {NOEXEC_OPT}), got: {tmp[0]}"
    )


def test_var_tmp_stays_noexec(monkeypatch):
    mounts = _captured_tmpfs_mounts(monkeypatch)
    var_tmp = [v for v in mounts if v.startswith("/var/tmp:")]
    assert var_tmp, f"/var/tmp tmpfs mount missing: {mounts}"
    assert NOEXEC_OPT in var_tmp[0], f"/var/tmp must stay {NOEXEC_OPT}, got: {var_tmp[0]}"


def test_tmp_tmpfs_is_mounted_exec_across_security_modes(monkeypatch):
    # snap_compat drops no-new-privileges; run_as_host_user swaps the privdrop
    # caps for --user. The /tmp executable bit must survive both variants.
    for kwargs in ({}, {"snap_compat": True}, {"run_as_host_user": True}):
        mounts = _captured_tmpfs_mounts(monkeypatch, **kwargs)
        tmp = [v for v in mounts if v.startswith("/tmp:")]
        assert tmp, f"/tmp tmpfs mount missing under {kwargs}: {mounts}"
        assert EXEC_OPT in tmp[0] and NOEXEC_OPT not in tmp[0], (
            f"/tmp tmpfs must carry the executable bit under {kwargs}, got: {tmp[0]}"
        )
