"""pm applies the user's network.force_ipv4 before its downloader dials.

pm boots on a bare bootstrap Python with no YAML library, so the flag is
read with a narrow stdlib reader instead of hermes_cli's loader (#144208).
"""

import socket

import pytest

from pm import network


@pytest.fixture(autouse=True)
def _unpatch_getaddrinfo(monkeypatch):
    """apply_configured_ipv4_preference patches the module-global socket; undo it."""
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)


@pytest.mark.parametrize("text,expected", [
    pytest.param("network:\n  force_ipv4: true\n", True, id="true"),
    pytest.param("network:\n  force_ipv4: True\n", True, id="capitalized"),
    pytest.param("network:\n  force_ipv4: 'true'\n", True, id="quoted"),
    pytest.param("network:\n  force_ipv4: true # dead ipv6 route\n", True, id="comment"),
    pytest.param("network:\n  force_ipv4: 1\n", True, id="numeric-one"),
    pytest.param("network:\n  force_ipv4: false\n", False, id="explicit-false"),
    pytest.param("network:\n  force_ipv4: no\n", False, id="explicit-no"),
    pytest.param("", False, id="empty"),
    pytest.param("network:\n", False, id="section-without-key"),
    pytest.param("gateway:\n  force_ipv4: true\n", False, id="other-section"),
    pytest.param("network:\n  monitoring:\n    force_ipv4: true\n", False, id="nested-not-direct"),
    pytest.param("network: {}\n", False, id="flow-mapping"),
])
def test_config_flag_reads_only_the_flat_network_scalar(text, expected):
    assert network._config_flag(text, "network", "force_ipv4") is expected


def test_realistic_config_only_network_section_decides():
    config = (
        "# Hermes configuration\n"
        "security:\n"
        "  redact_secrets: true\n"
        "\n"
        "model:\n"
        "  default: openai/gpt-5\n"
        "\n"
        "network:\n"
        "  force_ipv4: true\n"
        "\n"
        "gateway:\n"
        "  force_ipv4: false\n"
    )
    assert network._config_flag(config, "network", "force_ipv4") is True


def test_flag_applied_before_pm_dials(tmp_path):
    # tests/pm/conftest.py points HERMES_HOME at tmp_path / ".hermes".
    (tmp_path / ".hermes" / "config.yaml").write_text("network:\n  force_ipv4: true\n")
    network.apply_configured_ipv4_preference()
    assert getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False)


def test_flag_off_leaves_resolution_alone(tmp_path):
    (tmp_path / ".hermes" / "config.yaml").write_text("network:\n  force_ipv4: false\n")
    network.apply_configured_ipv4_preference()
    assert not getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False)


def test_missing_config_leaves_resolution_alone():
    network.apply_configured_ipv4_preference()
    assert not getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False)


def test_unparseable_config_leaves_resolution_alone(tmp_path):
    (tmp_path / ".hermes" / "config.yaml").write_text("network: [unclosed\n")
    network.apply_configured_ipv4_preference()
    assert not getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False)
