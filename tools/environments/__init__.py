"""Hermes execution environment backends.

Each backend provides the same interface (BaseEnvironment ABC) for running
shell commands in a specific execution context: local, Docker, SSH,
Singularity, Modal, or Daytona. (Modal additionally has direct and
Nous-managed modes, selected via terminal.modal_mode.)

The terminal_tool.py factory (_create_environment) selects the backend
based on the TERMINAL_ENV configuration.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from tools.environments.base import BaseEnvironment

__all__ = ["BaseEnvironment", "get_environment"]


def get_environment(config: Dict[str, Any]) -> BaseEnvironment:
    """Create an environment instance from a ``_get_env_config()`` dict.

    This is the convenience entry-point used by the backend probe in
    ``agent/prompt_builder.py``.  It mirrors the argument-assembly logic
    that ``terminal_tool._handle_terminal_command`` performs before
    calling ``_create_environment``, but without task-override resolution
    (the probe is a one-shot, non-interactive operation).
    """
    # Lazy-import to avoid pulling the heavy terminal_tool module at
    # package-init time (only needed when the probe actually fires).
    from tools.terminal_tool import _create_environment  # type: ignore[import-untyped]

    env_type: str = config.get("env_type", "local")
    cwd: str = config.get("cwd", ".")
    timeout: int = int(config.get("timeout", 180))
    host_cwd: Optional[str] = config.get("host_cwd")

    # --- Image selection (same mapping as _handle_terminal_command) ---
    _IMAGE_KEYS = {
        "docker": "docker_image",
        "singularity": "singularity_image",
        "modal": "modal_image",
        "daytona": "daytona_image",
    }
    image: str = config.get(_IMAGE_KEYS.get(env_type, ""), "") or ""

    # --- SSH config ---
    ssh_config: Optional[Dict[str, Any]] = None
    if env_type == "ssh":
        ssh_config = {
            "host": config.get("ssh_host", ""),
            "user": config.get("ssh_user", ""),
            "port": config.get("ssh_port", 22),
            "key": config.get("ssh_key", ""),
            "persistent": config.get("ssh_persistent", False),
        }

    # --- Container config ---
    container_config: Optional[Dict[str, Any]] = None
    if env_type in {"docker", "singularity", "modal", "daytona"}:
        container_config = {
            "container_cpu": config.get("container_cpu", 1),
            "container_memory": config.get("container_memory", 5120),
            "container_disk": config.get("container_disk", 51200),
            "container_persistent": config.get("container_persistent", True),
            "modal_mode": config.get("modal_mode", "auto"),
            "docker_volumes": config.get("docker_volumes", []),
            "docker_mount_cwd_to_workspace": config.get(
                "docker_mount_cwd_to_workspace", False
            ),
            "docker_forward_env": config.get("docker_forward_env", []),
            "docker_env": config.get("docker_env", {}),
            "docker_run_as_host_user": config.get(
                "docker_run_as_host_user", False
            ),
            "docker_extra_args": config.get("docker_extra_args", []),
            "docker_persist_across_processes": config.get(
                "docker_persist_across_processes", True
            ),
            "docker_orphan_reaper": config.get("docker_orphan_reaper", True),
        }

    # --- Local config ---
    local_config: Optional[Dict[str, Any]] = None
    if env_type == "local":
        local_config = {"persistent": config.get("local_persistent", False)}

    return _create_environment(
        env_type=env_type,
        image=image,
        cwd=cwd,
        timeout=timeout,
        ssh_config=ssh_config,
        container_config=container_config,
        local_config=local_config,
        host_cwd=host_cwd,
    )
