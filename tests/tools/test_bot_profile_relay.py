"""Relay discovery is profile scoped; the mailbox is install-wide (#146035)."""

import json
from types import SimpleNamespace

from tools import bot_mode_dm, bot_mode_probe, bot_relay


def test_named_sender_queues_the_install_mailbox(tmp_path, monkeypatch):
    """A named profile resolves the relay against the install-wide roster and queues the envelope
    into the install-wide outbox the Desktop drains; a profile-scoped mailbox is never read
    (#146035). The per-profile discovery paragraph is a display contract and keeps showing the
    profile's own view."""
    root = tmp_path / "state"
    sender = root / "profiles" / "sender"
    sender.mkdir(parents=True)
    (sender / "profile.yaml").write_text("ui_meta:\n  hermes-bots:\n    shape: cloud\n")
    own = dict(
        profile="remote", handle="remote", connection_id="own", title="Owned remote"
    )
    other = dict(
        profile="remote",
        handle="remote",
        connection_id="foreign",
        title="Foreign remote",
    )
    bot_relay.write_remote_roster(sender, [own])
    bot_relay.write_remote_roster(root, [other])
    agent = SimpleNamespace(
        _session_db=SimpleNamespace(db_path=str(sender / "state.db")),
        _session_title_hint="Bot Chat",
    )
    commands = []
    monkeypatch.setattr(
        bot_mode_dm,
        "_spawn_delivery",
        lambda command, *a, **kw: (
            commands.append(command) or json.dumps({"status": "sent"})
        ),
    )
    result = json.loads(bot_mode_dm.message_agent_tool("remote", "hello", agent=agent))
    assert result["status"] == "sent"
    assert bot_relay.claim_pending_envelopes(sender) == []
    envelopes = bot_relay.claim_pending_envelopes(root)
    assert len(envelopes) == 1 and envelopes[0]["target_connection"] == "foreign"
    assert envelopes[0]["message"] == "Message from 🤖 sender (@sender): hello"
    section = bot_mode_probe.get_bot_mode_protocol_section(sender, force_refresh=True)
    assert "Owned remote" in section and "Foreign remote" not in section
