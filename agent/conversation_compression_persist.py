"""Persist-row stamping for compaction commits (#144581).

What a compaction commit writes for the CURRENT turn's user row and the ``_db_persisted`` twins it
must keep consistent: the in-place archive and the rotation child handoff materialize compressor
copies as active rows, and both the transcript form (persist override) and the durability markers
have to land on those copies in the same commit — the turn finalizer cannot repair them afterwards.
"""

from typing import Any


def _messages_match_scoped_identity(left: Any, right: Any) -> bool:
    """Compare the live turn identity we care about for rotation stamping."""
    if (
        not isinstance(left, dict)
        or not isinstance(right, dict)
        or left.get("role") != right.get("role")
        or left.get("content") != right.get("content")
    ):
        return False
    left_timestamp = left.get("timestamp")
    right_timestamp = right.get("timestamp")
    if left_timestamp is not None and right_timestamp is not None:
        return left_timestamp == right_timestamp
    return True


def _stamp_scoped_twins(targets: list, source: dict, *, exact_counts_stamped: bool = False) -> None:
    """Stamp ``_db_persisted`` on every unstamped scoped twin of ``source`` in ``targets``.
    Exact-timestamp twins are preferred: when the source carries a timestamp and any exact twin was stamped
    (or, with ``exact_counts_stamped``, merely exists), the broad scoped pass is skipped so a content-equal
    old duplicate is left alone."""
    from agent.context_compressor import _DB_PERSISTED_MARKER
    source_timestamp = source.get("timestamp")
    exact_hit = False
    if source_timestamp is not None:
        for target in targets:
            if (
                not isinstance(target, dict)
                or target.get("timestamp") != source_timestamp
                or not _messages_match_scoped_identity(target, source)
            ):
                continue
            if target.get(_DB_PERSISTED_MARKER):
                exact_hit = exact_hit or exact_counts_stamped
                continue
            target[_DB_PERSISTED_MARKER] = True
            exact_hit = True
        if exact_hit:
            return
    for target in targets:
        if (
            isinstance(target, dict)
            and not target.get(_DB_PERSISTED_MARKER)
            and _messages_match_scoped_identity(target, source)
        ):
            target[_DB_PERSISTED_MARKER] = True


def _apply_persist_override_to_commit_rows(agent: Any, messages: list, rows: list) -> None:
    """Keep the current turn's user row in its clean transcript form across a commit (#144581).

    The live list holds the wire form (injected context blocks) until the turn finalizer runs, and
    the normal flush applies ``_persist_user_message_override`` to the DB row only. A commit that
    materializes compressor copies — the in-place archive and the rotation child handoff — would
    otherwise publish the wire form as the new active row, and the finalizer cannot repair it
    afterwards: ``_persist_user_message_idx`` no longer addresses the compacted list. compress()
    returns copies, so the row is matched by role/timestamp/content against
    ``messages[_persist_user_message_idx]``, not by identity; anything that does not match (a
    summary-merged turn, a drifted copy) is left untouched.
    """
    turn_idx = getattr(agent, "_persist_user_message_idx", None)
    if not (isinstance(turn_idx, int) and 0 <= turn_idx < len(messages)):
        return
    live = messages[turn_idx]
    if not (isinstance(live, dict) and live.get("role") == "user"):
        return
    live_content = live.get("content")
    live_timestamp = live.get("timestamp")
    if live_timestamp is None:
        return
    from agent.session_persistence import durable_user_row_content
    for row in rows:
        if not (isinstance(row, dict) and row.get("role") == "user"):
            continue
        if row.get("timestamp") != live_timestamp or row.get("content") != live_content:
            continue
        content, api_content = durable_user_row_content(
            agent, row, row.get("content"), row.get("api_content")
        )
        if api_content == content:
            api_content = None
        row["content"] = content
        if isinstance(api_content, str):
            row["api_content"] = api_content
        else:
            row.pop("api_content", None)
        # The flush writes the row's timestamp from the override anchor; keep the committed copy
        # consistent so display dedupe folds it with the archived twin.
        ov_timestamp = getattr(agent, "_persist_user_message_timestamp", None)
        if ov_timestamp is not None:
            row["timestamp"] = ov_timestamp
        break
