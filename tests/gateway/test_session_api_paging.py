"""GET /api/sessions has_more paging with pinned rows (#144349)."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    return adapter


def _paging_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get("/api/sessions", adapter._handle_list_sessions)
    return app


@pytest.mark.asyncio
async def test_has_more_counts_a_pinned_row_inside_the_window(adapter, session_db):
    """A pinned row inside the recency window fills the page like any other row (#144349).

    55 sessions, the newest one pinned: the window returns 50 rows, one of them
    pinned. Counting every pinned row out reported 49 < limit and has_more:
    false, so a client paging on has_more stopped after page one even though
    offset=50 still had five rows."""
    for i in range(55):
        session_db.create_session(f"s{i:02d}", "api_server")
    session_db.set_session_pinned("s54", True)

    app = _paging_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        first = await (await cli.get("/api/sessions?limit=50")).json()
        second = await (await cli.get("/api/sessions?limit=50&offset=50")).json()

    assert len(first["data"]) == 50
    assert first["data"][0]["id"] == "s54"
    assert first["has_more"] is True
    # The next page carries the five remaining rows; include_pinned also back-fills
    # s54 onto every page that missed it (existing back-fill contract).
    assert len(second["data"]) == 6
    assert second["data"][-1]["id"] == "s54"
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_has_more_still_true_when_a_backfilled_pin_extends_the_page(adapter, session_db):
    """A pin the window missed is back-filled PAST the limit; the page carries it
    and offset paging still reaches it, so has_more stays true."""
    for i in range(55):
        session_db.create_session(f"s{i:02d}", "api_server")
    session_db.set_session_pinned("s00", True)

    app = _paging_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        first = await (await cli.get("/api/sessions?limit=50")).json()
        second = await (await cli.get("/api/sessions?limit=50&offset=50")).json()

    assert len(first["data"]) == 51  # 50-window + the back-filled pin
    assert first["data"][-1]["id"] == "s00"
    assert first["has_more"] is True
    assert len(second["data"]) == 5
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_has_more_stays_false_for_a_short_page_with_pins(adapter, session_db):
    """A window smaller than the limit has no next page, pins or not: the
    back-filled pins must never inflate a short page into has_more: true."""
    for i in range(45):
        session_db.create_session(f"s{i:02d}", "api_server")
    for i in range(5):
        session_db.set_session_pinned(f"s{i:02d}", True)

    app = _paging_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        payload = await (await cli.get("/api/sessions?limit=50")).json()

    assert len(payload["data"]) == 45
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_has_more_false_for_a_short_unpinned_page(adapter, session_db):
    for i in range(45):
        session_db.create_session(f"s{i:02d}", "api_server")

    app = _paging_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        payload = await (await cli.get("/api/sessions?limit=50")).json()

    assert len(payload["data"]) == 45
    assert payload["has_more"] is False


def test_list_sessions_rich_reports_recency_count_before_the_backfill(session_db):
    """return_recency_count hands back the LIMIT/OFFSET window size BEFORE the
    pinned back-fill appends rows past it; the default shape is unchanged."""
    for i in range(55):
        session_db.create_session(f"s{i:02d}", "api_server")
    session_db.set_session_pinned("s00", True)

    rows, recency_count = session_db.list_sessions_rich(
        limit=50, order_by_last_active=True, include_pinned=True, return_recency_count=True)
    plain = session_db.list_sessions_rich(
        limit=50, order_by_last_active=True, include_pinned=True)

    assert recency_count == 50  # the window itself, not 51 with the back-filled pin
    assert len(rows) == 51
    assert rows == plain  # same rows, same order, just paired with the count
