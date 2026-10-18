"""``session.list`` rows must carry a profile-scoped ``live`` stamp. Regression for #140959.

Canonical status surfaces count live sessions off the listing (the sidecar's
``session.active_list`` never holds the owner's sessions, #9ff778d341's sweep), so the listing
length read as "N sessions" on stores larger than the limit. The stamp is the cross of the
stored rows against this process's live table; stored ids repeat across profiles' stores
(#100029), so a same-id runtime under another profile must not mark the row.
"""

import pytest

import tui_gateway.server as srv
from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = SessionDB(tmp_path / "state.db")
    monkeypatch.setattr(srv, "_get_db", lambda: database)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def live_table(monkeypatch):
    table: dict[str, dict] = {}
    monkeypatch.setattr(srv, "_sessions", table)
    return table


def _call(params: dict | None = None) -> list[dict]:
    return srv._methods["session.list"](1, params or {})["result"]["sessions"]


def _seed_two_sessions(db) -> None:
    db.create_session("live-one", "cli")
    db.create_session("idle-two", "cli")


def test_rows_with_a_live_runtime_are_stamped(db, live_table):
    _seed_two_sessions(db)
    live_table["s1"] = {"session_key": "live-one"}
    by_id = {row["id"]: row for row in _call()}
    assert by_id["live-one"]["live"] is True
    assert not by_id["idle-two"].get("live")


def test_finalized_runtime_does_not_stamp(db, live_table):
    _seed_two_sessions(db)
    live_table["s1"] = {"session_key": "live-one", "_finalized": True}
    by_id = {row["id"]: row for row in _call()}
    assert not by_id["live-one"].get("live")


def test_foreign_profile_runtime_does_not_stamp(live_table, monkeypatch):
    """Stored ids repeat across profiles' stores; only this request's profile marks its rows."""
    live_table["s1"] = {"session_key": "live-one", "profile_home": "/srv/other-home"}
    monkeypatch.setattr(
        srv, "_profile_home", lambda profile: "/srv/this-home" if profile else None
    )
    rows = [{"id": "live-one"}]
    srv._live_stamped(rows, {"profile": "p"})
    assert not rows[0].get("live")
    # Same profile -> stamped.
    live_table["s1"] = {"session_key": "live-one", "profile_home": "/srv/this-home"}
    srv._live_stamped(rows, {"profile": "p"})
    assert rows[0]["live"] is True


def test_runtime_without_a_stored_row_stamps_nothing(db, live_table):
    _seed_two_sessions(db)
    live_table["s1"] = {"session_key": "not-in-this-store"}
    assert all(not row.get("live") for row in _call())
