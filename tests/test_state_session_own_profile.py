"""Symlinked profile stores keep their owner (#143713).

A Docker deployment keeps each profile's ``state.db`` on a native volume and symlinks
it back under ``<HERMES_HOME>/profiles/<name>/`` (SQLite on a Windows bind mount is
unreliable). ``SessionDB._own_profile_name`` derived the owner from the *resolved*
database path, so a symlinked store read as outside the profile tree: the owner came
back ``None``, callers fell back to ``'default'``, and every legacy row stamped with
the real profile name vanished from the session picker. The owner must come from the
path the store was OPENED from; the resolved path stays a fallback, and the root is
matched as-configured and as-resolved the same way.
"""

import pytest


@pytest.fixture
def custom_root(tmp_path, monkeypatch):
    """A Docker-style root: HERMES_HOME on the bind mount, databases on a native volume."""
    root = tmp_path / "home"
    (root / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))
    return root


def _symlinked_store(root, tmp_path, name="foo"):
    """``<root>/profiles/<name>/state.db`` -> native volume outside the Hermes root."""
    native = tmp_path / "native-volume" / "profiles" / name
    native.mkdir(parents=True)
    link = root / "profiles" / name / "state.db"
    link.parent.mkdir(parents=True)
    link.symlink_to(native / "state.db")
    return link


def test_symlinked_state_db_keeps_its_profile_owner(custom_root, tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(db_path=_symlinked_store(custom_root, tmp_path))
    try:
        assert db._own_profile_name() == "foo"
    finally:
        db.close()


def test_new_rows_stamp_the_profile_through_the_symlink(custom_root, tmp_path):
    """Rows minted on a symlinked store must carry the store's real profile, not NULL —
    NULL is what made post-upgrade sessions read as unowned on the next resume."""
    from hermes_state import SessionDB

    db = SessionDB(db_path=_symlinked_store(custom_root, tmp_path))
    try:
        db.create_session("symlinked-1", source="cli")
        row = db._conn.execute(
            "SELECT profile_name FROM sessions WHERE id = ?", ("symlinked-1",)
        ).fetchone()
        assert row[0] == "foo"
    finally:
        db.close()


def test_default_and_outside_tree_stores_are_unchanged(custom_root, tmp_path):
    from hermes_state import SessionDB

    default_db = SessionDB(db_path=custom_root / "state.db")
    try:
        assert default_db._own_profile_name() == "default"
    finally:
        default_db.close()

    outside = tmp_path / "elsewhere" / "state.db"
    outside.parent.mkdir()
    outside_db = SessionDB(db_path=outside)
    try:
        assert outside_db._own_profile_name() is None
    finally:
        outside_db.close()
