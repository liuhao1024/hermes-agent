"""Test workspace_context stripping for titles/previews."""

import pytest

from hermes_state import _strip_leading_workspace_context


def test_strip_leading_workspace_context_full_tag():
    raw = '<workspace_context active="true" name="Home" path="/home/sean/hermes-work" />\nNew chats should show useful titles'
    expected = 'New chats should show useful titles'
    assert _strip_leading_workspace_context(raw) == expected


def test_strip_leading_workspace_context_self_closing():
    raw = '<workspace_context active="true"/>User: help me set up cron'
    expected = 'User: help me set up cron'
    assert _strip_leading_workspace_context(raw) == expected


def test_strip_leading_workspace_context_no_tag():
    raw = "Just a regular first message"
    assert _strip_leading_workspace_context(raw) == raw


def test_strip_leading_workspace_context_only_prefix():
    raw = '<workspace_context active="true"/>'
    assert _strip_leading_workspace_context(raw) == ''


def test_strip_leading_workspace_context_later_mentions_preserved():
    raw = "First user message <workspace_context active=\"true\"> still here later"
    # Only leading tags are stripped; later mentions stay
    assert _strip_leading_workspace_context(raw).startswith("First user message")