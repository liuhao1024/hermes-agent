"""Test title generation strips leading workspace_context tags."""

import pytest

from agent.title_generator import _strip_leading_workspace_context


def test_title_generator_strip_leading_workspace_context_full():
    raw = '<workspace_context active="true" name="Home" path="/home/sean/hermes-work" />\nNew chats should show useful titles'
    expected = 'New chats should show useful titles'
    assert _strip_leading_workspace_context(raw) == expected


def test_title_generator_strip_leading_workspace_context_self_closing():
    raw = '<workspace_context active="true"/>User: help me set up cron'
    expected = 'User: help me set up cron'
    assert _strip_leading_workspace_context(raw) == expected


def test_title_generator_strip_leading_workspace_context_no_tag():
    raw = "Just a regular first message"
    assert _strip_leading_workspace_context(raw) == raw


def test_title_generator_strip_leading_workspace_context_only_tag():
    raw = '<workspace_context active="true"/>'
    assert _strip_leading_workspace_context(raw) == ''