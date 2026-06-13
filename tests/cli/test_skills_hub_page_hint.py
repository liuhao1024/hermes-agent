"""Tests for skills_hub browse page clamp hint."""
import pytest
from unittest.mock import patch, MagicMock
from rich.console import Console


def test_browse_page_hint_when_requested_exceeds_total():
    """When --page exceeds total_pages, a dim hint should be printed."""
    from hermes_cli.skills_hub import do_browse

    # Mock the source router + parallel search to return a small catalog
    mock_meta = MagicMock()
    mock_meta.identifier = "test-skill"
    mock_meta.name = "Test Skill"
    mock_meta.source = "official"
    mock_meta.trust_level = "builtin"
    mock_meta.description = "desc"
    mock_meta.tags = []

    fake_console = Console(record=True, width=120)

    with patch("tools.skills_hub.create_source_router") as mock_router, \
         patch("tools.skills_hub.parallel_search_sources", return_value=([mock_meta], {}, [])), \
         patch("tools.skills_hub.GitHubAuth"):
        do_browse(page=5, page_size=20, source="all", console=fake_console)

    output = fake_console.export_text()
    assert "page 5 requested" in output
    assert "Showing page 1" in output


def test_browse_no_hint_when_page_within_range():
    """When --page is within range, no hint should be printed."""
    from hermes_cli.skills_hub import do_browse

    # Create 25 mock skills to span 2 pages at size=20
    mock_metas = []
    for i in range(25):
        m = MagicMock()
        m.identifier = f"skill-{i}"
        m.name = f"Skill {i}"
        m.source = "official"
        m.trust_level = "builtin"
        m.description = "desc"
        m.tags = []
        mock_metas.append(m)

    fake_console = Console(record=True, width=120)

    with patch("tools.skills_hub.create_source_router"), \
         patch("tools.skills_hub.parallel_search_sources", return_value=(mock_metas, {}, [])), \
         patch("tools.skills_hub.GitHubAuth"):
        do_browse(page=1, page_size=20, source="all", console=fake_console)

    output = fake_console.export_text()
    assert "page 1 requested" not in output
