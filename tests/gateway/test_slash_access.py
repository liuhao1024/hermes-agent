"""Unit tests for gateway.slash_access — per-platform slash command access control.

Tests the pure policy resolver (no gateway plumbing). Integration tests that
exercise the dispatch site live in test_slash_access_dispatch.py.
"""
from __future__ import annotations

import json

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionSource
from gateway.slash_access import (
    policy_for_source,
    policy_from_extra,
)
from hermes_constants import get_hermes_home


# ---------------------------------------------------------------------------
# policy_from_extra — input normalization + scope resolution
# ---------------------------------------------------------------------------


class TestPolicyFromExtra:

    def test_disabled_policy_treats_anyone_as_admin(self):
        # When gating is off, downstream code uses is_admin/can_run uniformly.
        # Both must short-circuit to True so existing behavior is preserved.
        p = policy_from_extra({}, "dm")
        assert p.is_admin("anyone") is True
        assert p.can_run("anyone", "stop") is True


    def test_id_coercion_ints_become_strings(self):
        # YAML often loads numeric IDs as ints; we stringify on ingest.
        p = policy_from_extra({"allow_admin_from": [12345, 67890]}, "dm")
        assert p.admin_user_ids == frozenset({"12345", "67890"})
        assert p.is_admin("12345") is True
        assert p.is_admin(12345) is True  # is_admin also stringifies


    def test_command_coercion_strips_leading_slash_and_lowercases(self):
        p = policy_from_extra(
            {
                "allow_admin_from": ["111"],
                "user_allowed_commands": ["/Status", "MODEL", "/help"],
            },
            "dm",
        )
        assert p.user_allowed_commands == frozenset({"status", "model", "help"})


    def test_dm_admin_does_not_imply_group_admin(self):
        # Admin lists are scope-specific. DM admin must not auto-promote in groups.
        extra = {"allow_admin_from": ["111"]}
        dm = policy_from_extra(extra, "dm")
        assert dm.is_admin("111") is True
        # Group has no admin list set → gating disabled in groups → "111"
        # gets unrestricted access, but that's the backward-compat fallback,
        # not implicit admin promotion. The distinction matters when the
        # group DOES have an admin list set:
        extra2 = {
            "allow_admin_from": ["111"],
            "group_allow_admin_from": ["222"],
        }
        gp2 = policy_from_extra(extra2, "group")
        assert gp2.is_admin("111") is False
        assert gp2.is_admin("222") is True


# ---------------------------------------------------------------------------
# policy_for_source — wires GatewayConfig + SessionSource together
# ---------------------------------------------------------------------------


class TestPolicyForSource:


    def test_dm_chat_type_resolves_to_dm_scope(self):
        cfg = GatewayConfig(
            platforms={
                Platform.DISCORD: PlatformConfig(
                    enabled=True,
                    extra={
                        "allow_admin_from": ["111"],
                        "user_allowed_commands": ["status"],
                        "group_allow_admin_from": ["222"],
                        "group_user_allowed_commands": ["help"],
                    },
                )
            }
        )
        dm_src = SessionSource(
            platform=Platform.DISCORD, chat_id="A", chat_type="dm", user_id="111"
        )
        p = policy_for_source(cfg, dm_src)
        assert p.is_admin("111") is True
        assert p.can_run("999", "status") is True
        assert p.can_run("999", "help") is True  # always-allowed floor
        assert p.can_run("999", "kanban") is False


    def test_no_admin_list_for_dm_means_unrestricted_in_dm(self):
        # Group has admin list, DM does not → DM gating disabled, group active.
        cfg = GatewayConfig(
            platforms={
                Platform.DISCORD: PlatformConfig(
                    enabled=True,
                    extra={"group_allow_admin_from": ["222"]},
                )
            }
        )
        dm_src = SessionSource(
            platform=Platform.DISCORD, chat_id="A", chat_type="dm", user_id="999"
        )
        grp_src = SessionSource(
            platform=Platform.DISCORD, chat_id="G", chat_type="group", user_id="999"
        )
        dm_p = policy_for_source(cfg, dm_src)
        grp_p = policy_for_source(cfg, grp_src)
        assert dm_p.enabled is False
        assert dm_p.can_run("999", "stop") is True  # backward compat
        assert grp_p.enabled is True
        assert grp_p.can_run("999", "stop") is False  # gated


@pytest.mark.parametrize(
    ("extra", "chat_type", "admin", "gated"),
    [
        # DM-only gated: blank used to fall into the ungated group scope (#116282).
        ({"allow_admin_from": ["111"]}, "", "111", True),
        ({"allow_admin_from": ["111"]}, None, "111", True),
        # Mirror image and tie keep the historical group scope; ungated stays ungated.
        ({"group_allow_admin_from": ["222"]}, "", "222", True),
        ({"allow_admin_from": ["111"], "group_allow_admin_from": ["222"]}, None, "222", True),
        ({}, "", None, False),
    ],
)
def test_blank_chat_type_resolves_to_the_gated_scope(extra, chat_type, admin, gated):
    """A blank/None chat_type (relay frames may carry ""/null, restored rows keep a
    stored empty value) must resolve to whichever scope is gated, never to an
    ungated allow-everything scope (#116282)."""
    cfg = GatewayConfig(platforms={Platform.DISCORD: PlatformConfig(enabled=True, extra=extra)})
    src = SessionSource(platform=Platform.DISCORD, chat_id="A", chat_type=chat_type, user_id="999")
    p = policy_for_source(cfg, src)
    assert p.enabled is gated
    assert p.can_run("999", "stop") is (not gated)
    if admin is not None:
        assert p.is_admin(admin) is True


# ---------------------------------------------------------------------------
# Aliased identities — a WhatsApp admin listed in one form, messages in another
# ---------------------------------------------------------------------------

PHONE = "15551234567"
LID = "77214955630717"


def _write_lid_mapping(phone: str = PHONE, lid: str = LID) -> None:
    """Mirror what the JS bridge writes: phone→lid and lid→phone (reverse)."""
    session_dir = get_hermes_home() / "whatsapp" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / f"lid-mapping-{phone}.json").write_text(json.dumps(lid), encoding="utf-8")
    (session_dir / f"lid-mapping-{lid}_reverse.json").write_text(json.dumps(phone), encoding="utf-8")


def _whatsapp_policy(admin_entries: list[str], chat_type: str = "dm"):
    cfg = GatewayConfig(
        platforms={
            Platform.WHATSAPP: PlatformConfig(
                enabled=True,
                extra={"allow_admin_from": admin_entries, "group_allow_admin_from": admin_entries},
            )
        }
    )
    src = SessionSource(
        platform=Platform.WHATSAPP, chat_id="G" if chat_type == "group" else "A", chat_type=chat_type, user_id=PHONE
    )
    return policy_for_source(cfg, src)


class TestWhatsAppAliasedAdmin:

    def test_admin_listed_as_bare_number_matches_phone_jid_sender(self):
        # The documented form: a bare phone in allow_admin_from; WhatsApp delivers
        # the JID spelling of the same number. No mapping files needed.
        p = _whatsapp_policy([PHONE])

        assert p.is_admin(f"{PHONE}@s.whatsapp.net") is True
        assert p.is_admin(f"+{PHONE}") is True

    def test_admin_listed_as_bare_number_matches_lid_sender(self):
        # Current WhatsApp delivers most DMs as @lid; only the bridge's lid maps
        # tie that form back to the listed number.
        _write_lid_mapping()
        p = _whatsapp_policy([PHONE])

        assert p.is_admin(f"{LID}@lid") is True

    def test_admin_listed_as_lid_matches_phone_jid_sender(self):
        # The reverse listing: the operator pasted the LID, the message arrives
        # as the phone JID.
        _write_lid_mapping()
        p = _whatsapp_policy([f"{LID}@lid"])

        assert p.is_admin(f"{PHONE}@s.whatsapp.net") is True

    def test_group_scope_resolves_aliases_too(self):
        _write_lid_mapping()
        p = _whatsapp_policy([PHONE], chat_type="group")

        assert p.is_admin(f"{LID}@lid") is True
        assert p.is_admin(f"{PHONE}@s.whatsapp.net") is True

    def test_unlisted_identities_stay_non_admin(self):
        _write_lid_mapping()
        p = _whatsapp_policy([PHONE])

        assert p.is_admin("7999@s.whatsapp.net") is False
        assert p.is_admin("7999@lid") is False

    def test_exact_platforms_keep_exact_matching(self):
        # A platform without aliased identities must not grow alias matching.
        cfg = GatewayConfig(
            platforms={Platform.DISCORD: PlatformConfig(enabled=True, extra={"allow_admin_from": ["111"]})}
        )
        src = SessionSource(platform=Platform.DISCORD, chat_id="A", chat_type="dm", user_id="111")
        p = policy_for_source(cfg, src)

        assert p.alias_expander is None
        assert p.is_admin("111@s.whatsapp.net") is False
