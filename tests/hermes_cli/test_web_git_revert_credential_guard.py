"""``review_revert`` must refuse to delete credential-looking untracked files (#146032).

The review pane's discard is a no-stash, no-confirmation ``git clean -fd``, and Hermes
itself maintains a git repo in the user's home directory — a stray discard there can
permanently remove untracked SSH private keys, which no stash or recovery path holds.
The guard fails closed on credential-shaped names and mirrors the clean's exact reach
(non-ignored untracked files under the same pathspec).
"""

import subprocess
from pathlib import Path

import pytest

from hermes_cli import web_git


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit_all(cwd: str, message: str) -> None:
    _git(cwd, "add", "-A")
    _git(
        cwd,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-m",
        message,
    )


@pytest.fixture
def repo(tmp_path):
    cwd = str(tmp_path)
    _git(cwd, "init", "-q")
    _git(
        cwd,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "init",
    )
    return cwd


def _write(cwd: str, name: str, content: str = "x\n") -> None:
    path = Path(cwd) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_revert_all_refuses_ssh_private_key_and_abandons_the_whole_revert(repo):
    _write(repo, "id_ed25519", "PRIVATE KEY MATERIAL\n")
    _write(repo, "notes.txt")
    with pytest.raises(RuntimeError, match="id_ed25519"):
        web_git.review_revert(repo, None)
    assert (Path(repo) / "id_ed25519").read_text() == "PRIVATE KEY MATERIAL\n"
    assert (Path(repo) / "notes.txt").exists()


@pytest.mark.parametrize(
    "name",
    [
        "id_rsa",
        "id_ed25519_github",
        "authorized_keys",
        "authorized_keys2",
        "server.pem",
        "server.key",
        ".ssh/id_ed25519",
        "keys/tank.pem",
    ],
)
def test_revert_refuses_credential_shaped_names(repo, name):
    _write(repo, name)
    with pytest.raises(RuntimeError, match="credential-looking"):
        web_git.review_revert(repo, None)
    assert (Path(repo) / name).exists()


@pytest.mark.parametrize(
    "name", ["notes.txt", "identity", "id.txt", "readme.md", "data.keys"]
)
def test_revert_still_discards_non_credentials(repo, name):
    _write(repo, name)
    assert web_git.review_revert(repo, None) == {"ok": True}
    assert not (Path(repo) / name).exists()


def test_revert_single_file_refuses_credential(repo):
    _write(repo, "id_ed25519")
    with pytest.raises(RuntimeError, match="id_ed25519"):
        web_git.review_revert(repo, "id_ed25519")
    assert (Path(repo) / "id_ed25519").exists()


def test_revert_ignored_credential_is_outside_the_guard_and_the_clean(repo):
    _write(repo, ".gitignore", "id_ed25519\n")
    _commit_all(repo, "ignore keys")
    _write(repo, "id_ed25519")
    _write(repo, "notes.txt")
    assert web_git.review_revert(repo, None) == {"ok": True}
    assert (Path(repo) / "id_ed25519").exists()
    assert not (Path(repo) / "notes.txt").exists()


def test_revert_discards_untracked_and_restores_tracked(repo):
    _write(repo, "tracked.txt", "committed\n")
    _commit_all(repo, "track")
    _write(repo, "tracked.txt", "dirty\n")
    _write(repo, "untracked.txt")
    assert web_git.review_revert(repo, None) == {"ok": True}
    assert (Path(repo) / "tracked.txt").read_text() == "committed\n"
    assert not (Path(repo) / "untracked.txt").exists()
