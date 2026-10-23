"""``${VAR}`` refs in a profile's ``terminal:`` config must expand in the rebuilt scope (#143505).

``build_profile_terminal_scope`` re-reads ``config.yaml`` directly instead of through
``load_config``, which is where every other surface expands ``${VAR}`` refs. Gateway and
routed sessions therefore froze literal values into the docker backend (``-v
${MOUNT_DIR}workspace:/workspace`` -> exit 125, nothing logged) while the CLI on the same
config expanded correctly.
"""

import json

import pytest

from agent.secret_scope import (
    reset_multiplex_context,
    reset_secret_scope,
    set_multiplex_context,
    set_secret_scope,
)
from tools.terminal_scope import build_profile_terminal_scope

_CFG = """
terminal:
  backend: docker
  docker_image: ${ISOLATION_IMAGE}
  docker_volumes:
    - "${MOUNT_DIR}workspace:/workspace"
"""


def _profile(tmp_path, config_yaml=_CFG):
    home = tmp_path / "profiles" / "p"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(config_yaml, encoding="utf-8")
    return home


def test_config_env_refs_expand_into_scope(tmp_path, monkeypatch):
    home = _profile(tmp_path)
    monkeypatch.setenv("ISOLATION_IMAGE", "alpine:3.20")
    monkeypatch.setenv("MOUNT_DIR", "/srv/x/")
    scope = build_profile_terminal_scope(home)
    assert scope["TERMINAL_DOCKER_IMAGE"] == "alpine:3.20"
    assert json.loads(scope["TERMINAL_DOCKER_VOLUMES"]) == ["/srv/x/workspace:/workspace"]
    assert scope["TERMINAL_DOCKER_IMAGE_PINNED"] == "1"


def test_unresolved_ref_stays_literal(tmp_path, monkeypatch):
    home = _profile(tmp_path)
    monkeypatch.delenv("ISOLATION_IMAGE", raising=False)
    monkeypatch.delenv("MOUNT_DIR", raising=False)
    scope = build_profile_terminal_scope(home)
    assert scope["TERMINAL_DOCKER_IMAGE"] == "${ISOLATION_IMAGE}"
    assert json.loads(scope["TERMINAL_DOCKER_VOLUMES"]) == ["${MOUNT_DIR}workspace:/workspace"]


def test_ref_resolves_from_bound_secret_scope_not_process_env(tmp_path, monkeypatch):
    """Multiplexed turn: the routed profile's own ``.env`` (secret scope mapping) is the source,
    never another profile's value left in the process env."""
    home = _profile(tmp_path)
    monkeypatch.setenv("MOUNT_DIR", "/launch-profile-mount/")
    monkeypatch.delenv("ISOLATION_IMAGE", raising=False)
    multiplex = set_multiplex_context(True)
    scope_token = set_secret_scope(
        {"ISOLATION_IMAGE": "alpine:3.20", "MOUNT_DIR": "/srv/p/"}, profile_home=str(home))
    try:
        scope = build_profile_terminal_scope(home)
    finally:
        reset_secret_scope(scope_token)
        reset_multiplex_context(multiplex)
    assert scope["TERMINAL_DOCKER_IMAGE"] == "alpine:3.20"
    assert json.loads(scope["TERMINAL_DOCKER_VOLUMES"]) == ["/srv/p/workspace:/workspace"]


@pytest.mark.parametrize("placeholder", ["${MOUNT_DIR}", "${env:MOUNT_DIR}"])
def test_env_colon_ref_form_also_expands(tmp_path, monkeypatch, placeholder):
    home = _profile(tmp_path, f"terminal:\n  backend: local\n  cwd: {placeholder}ws\n")
    monkeypatch.setenv("MOUNT_DIR", "/srv/x/")
    scope = build_profile_terminal_scope(home)
    assert scope["TERMINAL_CWD"] == "/srv/x/ws"
