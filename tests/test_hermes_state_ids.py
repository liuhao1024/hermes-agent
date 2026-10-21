"""Recognizer coverage for derived session-id shapes (``SESSION_ID_RECOGNIZERS``).

Salvage (``hermes_cli/session_lost_and_found.py``) classifies a schema-less row cell as a
session id only when ``is_known_session_id`` matches it, so every id a surface derives
instead of minting via ``new_session_id`` needs a pinned test here — otherwise those rows
drop out of salvage and the table layout gets mis-inferred.
"""

from __future__ import annotations

import pytest

from hermes_state_ids import is_known_session_id, new_session_id

# The exact example from the review-caller contract: owner-repo folded to [A-Za-z0-9_-]
# lower-case, one session per pull request.
HERMES_REVIEW_IDS = [
    "hermes-review-s3ntin3l8-runway-ai-usage-tracker-752",
    "hermes-review-nousresearch-hermes-agent-142647",
    "hermes-review-owner_name-repo_name-1",
    "hermes-review-a1-b2-9999999",
]

NOT_SESSION_IDS = [
    "hermes-review-owner-repo",  # shape without the trailing PR number is not minted
    "review-owner-repo-752",  # missing the hermes-review- prefix
    "hermes-review-",  # empty owner/repo/pr
    "hermes-review-owner-repo.name-1",  # '.' is folded out before minting, never on disk
    "not a session id",
    "",
]


class TestHermesReviewSessionId:
    def test_recognized_by_is_known_session_id(self):
        for session_id in HERMES_REVIEW_IDS:
            assert is_known_session_id(session_id), session_id

    def test_recognized_by_salvage_cell_predicate(self):
        from hermes_cli.session_lost_and_found import _is_session_id

        for session_id in HERMES_REVIEW_IDS:
            assert _is_session_id(session_id), session_id
        assert not _is_session_id(1234)

    @pytest.mark.parametrize("session_id", NOT_SESSION_IDS)
    def test_near_misses_stay_unknown(self, session_id):
        assert not is_known_session_id(session_id)


class TestExistingShapesStayRecognized:
    """The recognizer tuple must keep classifying the shapes minted before this file."""

    @pytest.mark.parametrize(
        "session_id",
        [
            new_session_id(),
            "20260101_000000_abcdef",
            "cron_job-1_20260101_000000",
            "bg_123456_abcdef",
            "room_" + "0a1b2c3d" * 4,
            "api_1700000000_deadbeef",
            "api-" + "0a1b2c3d" * 2,
            "run_" + "0a1b2c3d" * 4,
            "0a1b2c3d-1234-4abc-9abc-def012345678",
        ],
    )
    def test_known_shape(self, session_id):
        assert is_known_session_id(session_id)
