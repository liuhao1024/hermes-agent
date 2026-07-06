"""Tests for _is_fork() credential stripping (#59584)."""

import pytest

from hermes_cli.main import _is_fork, _strip_url_credentials


class TestStripUrlCredentials:
    """Test credential stripping from git remote URLs."""

    def test_strips_https_token(self):
        """Strip token from HTTPS URLs."""
        url = "https://ghp_xxxx@github.com/NousResearch/hermes-agent.git"
        result = _strip_url_credentials(url)
        assert result == "https://github.com/NousResearch/hermes-agent.git"

    def test_strips_https_user_token(self):
        """Strip user:token from HTTPS URLs."""
        url = "https://user:ghp_xxxx@github.com/NousResearch/hermes-agent.git"
        result = _strip_url_credentials(url)
        assert result == "https://github.com/NousResearch/hermes-agent.git"

    def test_leaves_ssh_unchanged(self):
        """SSH URLs don't have @ in the protocol portion."""
        url = "git@github.com:NousResearch/hermes-agent.git"
        result = _strip_url_credentials(url)
        assert result == "git@github.com:NousResearch/hermes-agent.git"

    def test_leaves_plain_https_unchanged(self):
        """HTTPS URLs without credentials are unchanged."""
        url = "https://github.com/NousResearch/hermes-agent.git"
        result = _strip_url_credentials(url)
        assert result == "https://github.com/NousResearch/hermes-agent.git"

    def test_handles_url_without_trailing_slash(self):
        """Handle URLs without trailing slashes."""
        url = "https://token@github.com/NousResearch/hermes-agent"
        result = _strip_url_credentials(url)
        assert result == "https://github.com/NousResearch/hermes-agent"


class TestIsForkWithCredentials:
    """Test _is_fork() with credential-bearing URLs."""

    def test_official_repo_with_token_not_fork(self):
        """Official repo URL with embedded credentials is not a fork."""
        url = "https://ghp_xxxx@github.com/NousResearch/hermes-agent.git"
        assert not _is_fork(url)

    def test_official_repo_with_user_token_not_fork(self):
        """Official repo URL with user:token is not a fork."""
        url = "https://user:ghp_xxxx@github.com/NousResearch/hermes-agent.git"
        assert not _is_fork(url)

    def test_fork_with_token_is_fork(self):
        """Fork URL with embedded credentials is still a fork."""
        url = "https://ghp_xxxx@github.com/liuhao/hermes-agent.git"
        assert _is_fork(url)

    def test_official_repo_without_token_not_fork(self):
        """Official repo URL without credentials is not a fork."""
        url = "https://github.com/NousResearch/hermes-agent.git"
        assert not _is_fork(url)

    def test_none_url_not_fork(self):
        """None URL is not a fork."""
        assert not _is_fork(None)

    def test_empty_url_not_fork(self):
        """Empty URL is not a fork."""
        assert not _is_fork("")

    def test_official_without_git_suffix_not_fork(self):
        """Official repo URL without .git suffix is not a fork."""
        url = "https://ghp_xxxx@github.com/NousResearch/hermes-agent"
        assert not _is_fork(url)

    def test_fork_without_git_suffix_is_fork(self):
        """Fork URL without .git suffix is still a fork."""
        url = "https://ghp_xxxx@github.com/liuhao/hermes-agent"
        assert _is_fork(url)

    def test_official_with_trailing_slash_not_fork(self):
        """Official repo URL with trailing slash is not a fork."""
        url = "https://ghp_xxxx@github.com/NousResearch/hermes-agent/"
        assert not _is_fork(url)

    def test_official_ssh_not_fork(self):
        """Official SSH URL is not a fork."""
        url = "git@github.com:NousResearch/hermes-agent.git"
        assert not _is_fork(url)