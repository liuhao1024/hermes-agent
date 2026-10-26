"""Skills hub: pinned raw fetches resolve a commit SHA (never a tree SHA), and a quota-exhausted
unpinned read degrades to raw HEAD instead of failing the install (#144851).

raw.githubusercontent.com only resolves commit-ish refs — a tree SHA 404s there, which turned
every pinned raw fetch into a wasted round trip followed by the Contents API call it was meant
to avoid — and the API can be exhausted before the tree call even lands, the path where the
caller has no revision to pin anyway."""

import httpx
import pytest
from unittest.mock import MagicMock, patch

from tools.skills_hub_github import GitHubAuth, GitHubSource

COMMIT_SHA = "ab27f9caa20d1234567890abcdef1234567890a"
TREE_SHA = "9a9747ce3395fedcba0987654321fedcba0987654"


def _source():
    return GitHubSource(auth=MagicMock(spec=GitHubAuth))


def _branch_payload():
    return {"commit": {"sha": COMMIT_SHA, "commit": {"tree": {"sha": TREE_SHA}}}}


def _rate_limited():
    return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"},
                          request=httpx.Request("GET", "https://api.github.com/repos/o/r"))


# --- the pinned revision is a commit SHA the raw endpoint accepts -----------

def test_tree_revision_records_the_commit_sha_of_the_probed_branch():
    src = _source()
    with patch.object(src, "_github_json", side_effect=[
        {"default_branch": "main"}, _branch_payload(), {"sha": TREE_SHA, "tree": [], "truncated": False},
    ]):
        assert src._get_repo_tree("o/r") == ("main", [])
    assert src._tree_revisions["o/r"] == COMMIT_SHA


def test_pinned_raw_fetch_uses_the_commit_sha_and_skips_the_api():
    src = _source()
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get") as api_get:
        raw_get.return_value = httpx.Response(200, content=b"pinned bytes",
                                              request=httpx.Request("GET", "https://raw.githubusercontent.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md", ref=COMMIT_SHA) == b"pinned bytes"
    assert raw_get.call_args.args[0] == f"https://raw.githubusercontent.com/o/r/{COMMIT_SHA}/skills/s/SKILL.md"
    api_get.assert_not_called()


def test_pinned_raw_miss_still_falls_back_to_the_api_at_that_revision():
    src = _source()
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get") as api_get:
        raw_get.return_value = httpx.Response(404, request=httpx.Request("GET", "https://raw.githubusercontent.com/x"))
        api_get.return_value = httpx.Response(200, content=b"api bytes",
                                              request=httpx.Request("GET", "https://api.github.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md", ref=COMMIT_SHA) == b"api bytes"
    assert api_get.call_args.kwargs.get("params") == {"ref": COMMIT_SHA}


# --- quota exhaustion on the unpinned path degrades to raw HEAD -------------

def test_unpinned_rate_limited_read_falls_back_to_raw_head():
    src = _source()
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get", return_value=_rate_limited()):
        raw_get.return_value = httpx.Response(200, content=b"head bytes",
                                              request=httpx.Request("GET", "https://raw.githubusercontent.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md") == b"head bytes"
    assert raw_get.call_args.args[0] == "https://raw.githubusercontent.com/o/r/HEAD/skills/s/SKILL.md"


def test_unpinned_api_miss_is_not_rescued_by_raw():
    """A plain 404 is a missing file, not a quota problem — raw must stay out of it."""
    src = _source()
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get") as api_get:
        api_get.return_value = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md") is None
    raw_get.assert_not_called()


def test_pinned_rate_limit_never_unpins_to_head():
    """The pinned raw try already 404'd that revision; HEAD bytes would silently break the pin,
    so a quota refusal on the pinned path must keep returning None."""
    src = _source()
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get", return_value=_rate_limited()):
        raw_get.return_value = httpx.Response(404, request=httpx.Request("GET", "https://raw.githubusercontent.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md", ref=COMMIT_SHA) is None
    assert [c.args[0] for c in raw_get.call_args_list] == [
        f"https://raw.githubusercontent.com/o/r/{COMMIT_SHA}/skills/s/SKILL.md"]


def test_unusable_raw_goes_straight_to_the_api():
    src = _source()
    src._raw_unusable = True
    with patch("tools.skills_hub._skills_hub_http_get") as raw_get, \
         patch.object(src, "_github_get") as api_get:
        api_get.return_value = httpx.Response(200, content=b"api bytes",
                                              request=httpx.Request("GET", "https://api.github.com/x"))
        assert src._fetch_file_bytes("o/r", "skills/s/SKILL.md", ref=COMMIT_SHA) == b"api bytes"
    raw_get.assert_not_called()


# --- moved from tests/tools/test_skills_hub.py (cap offset): the same fetch path -------------

class TestGitHubSourceFileFetch:
    def test_quotes_decoded_support_path_before_contents_api_fetch(self):
        src = GitHubSource(auth=MagicMock(spec=GitHubAuth))
        src._github_get = MagicMock(return_value=None)

        src._fetch_file_bytes("owner/repo", "skill/references/foo#bar.md")

        assert src._github_get.call_args.args[0] == (
            "https://api.github.com/repos/owner/repo/contents/"
            "skill/references/foo%23bar.md"
        )

    @pytest.mark.parametrize("gh_token", ["gh-live", "gh-dead"])
    def test_rejected_token_falls_through_to_next_credential(self, monkeypatch, gh_token):
        """A 401 on the .env token retries with `gh auth token`, then anonymously (#98725)."""
        import tools.skills_hub as hub
        auth = GitHubAuth()
        monkeypatch.setattr(auth, "_try_pat", lambda: "pat-dead")
        monkeypatch.setattr(auth, "_try_gh_cli", lambda: gh_token)
        monkeypatch.setattr(auth, "_try_github_app", lambda: None)
        sent = []

        def fake_get(url, *, headers, **_kw):
            sent.append(headers)
            dead = headers.get("Authorization", "").endswith("-dead")
            return httpx.Response(401 if dead else 200, content=b"body")

        monkeypatch.setattr(hub, "_skills_hub_http_get", fake_get)
        assert GitHubSource(auth)._fetch_file_bytes("o/r", "s/SKILL.md") == b"body"
        expected = ["token pat-dead", "token gh-live"] if gh_token == "gh-live" else [
            "token pat-dead", "token gh-dead", None]
        assert [h.get("Authorization") for h in sent] == expected
        assert sent[-1]["Accept"] == "application/vnd.github.v3.raw"
        assert auth.rejected[0] == "GITHUB_TOKEN/GH_TOKEN"
