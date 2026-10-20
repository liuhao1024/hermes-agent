"""Nested bind-mount points inside the persistent sandbox binds must exist before ``docker run``.

dockerd auto-creates a missing mount point as root inside the user-owned ``<sandbox>/home``
bind, and the ownership handback walk prunes every nested mount point, so those root-owned
directories made a profile that ever ran a Docker sandbox impossible to delete without
out-of-band ``sudo chown`` (#141911)."""

import subprocess

from tools.environments import docker as docker_env
from tools.environments.docker import _precreate_nested_mount_points


def test_readonly_mount_points_are_precreated_in_the_sandbox_home(
    monkeypatch, tmp_path
):
    """The ``/root/.hermes/...`` read-only mounts must land on user-owned directories the
    container start creates inside ``<sandbox>/home``, not on dockerd's root-owned ones."""
    monkeypatch.setenv("TERMINAL_SANDBOX_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_get_active_profile_name", lambda: "default")
    _mock_subprocess_run(monkeypatch)
    (tmp_path / "hermes-home" / "skills").mkdir(parents=True)

    _make_dummy_env(task_id="profile_demo1-abc", persistent_filesystem=True)

    home = tmp_path / "docker" / "profile_demo1-abc" / "home"
    assert (home / ".hermes" / "skills").is_dir(), (
        "skills mount point missing from the sandbox home — dockerd would create it root-owned"
    )
    assert (home / ".hermes" / "cache" / "documents").is_dir()


def test_precreate_covers_workspace_binds_and_ignores_other_targets(tmp_path):
    """Targets nested under a known bind are created; the bind roots themselves, paths outside
    every bind and non-volume args are left alone."""
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "sandbox" / "workspace"

    _precreate_nested_mount_points(
        [
            "-v",
            "/etc/ca.pem:/etc/ssl/certs/ca.pem:ro",
            "-v",
            "/data:/root",
            "-v",
            "/proj:/workspace/project",
            "-e",
            "TOKEN",
        ],
        [("/root", str(home)), ("/workspace", str(workspace))],
    )

    assert (workspace / "project").is_dir()
    assert list(home.iterdir()) == [], (
        "nothing may be created at or beside the /root bind itself"
    )


def _mock_subprocess_run(monkeypatch):
    docker_env._cgroup_limits_ok = True
    calls = []

    def _run(cmd, **kwargs):
        calls.append((list(cmd) if isinstance(cmd, list) else cmd, kwargs))
        if isinstance(cmd, list) and len(cmd) >= 2:
            if cmd[1] == "version":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="Docker version", stderr=""
                )
            if cmd[1] == "run":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="fake-container-id\n", stderr=""
                )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_env.subprocess, "run", _run)
    return calls


def _make_dummy_env(**kwargs):
    return docker_env.DockerEnvironment(
        image=kwargs.get("image", "python:3.11"),
        cwd=kwargs.get("cwd", "/root"),
        timeout=kwargs.get("timeout", 60),
        cpu=kwargs.get("cpu", 0),
        memory=kwargs.get("memory", 0),
        disk=kwargs.get("disk", 0),
        persistent_filesystem=kwargs.get("persistent_filesystem", False),
        task_id=kwargs.get("task_id", "test-task"),
        volumes=kwargs.get("volumes", []),
        forward_env=kwargs.get("forward_env"),
        network=kwargs.get("network", True),
        host_cwd=kwargs.get("host_cwd"),
        auto_mount_cwd=kwargs.get("auto_mount_cwd", False),
        env=kwargs.get("env"),
        run_as_host_user=kwargs.get("run_as_host_user", False),
        extra_args=kwargs.get("extra_args", []),
        persist_across_processes=kwargs.get("persist_across_processes", True),
        shared_container_key=kwargs.get("shared_container_key", ""),
        shm_size=kwargs.get("shm_size", docker_env._DEFAULT_SHM_SIZE),
        snap_compat=kwargs.get("snap_compat", False),
    )
