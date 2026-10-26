"""skills.auto_load repeat-view dedup: a pinned skill the session later views returns the
repeat-view stub instead of the full content — the auto-load block already carries it
verbatim (#145280).

Before the fix the dedup registry only knew views served by skill_view itself, so viewing
a pinned skill returned the full content a second time (~28k wasted tokens for two pinned
40k-character skills). A changed skill file, another session's task_id, or a view after
context compression (cache reset) must still return the full content.
"""

import json
from unittest.mock import patch

import pytest

from tools.skills_tool_dedup import reset_skill_view_dedup


@pytest.fixture(autouse=True)
def _fresh_dedup_registry():
    reset_skill_view_dedup()
    yield
    reset_skill_view_dedup()


def _write_skill(root, name, body):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test.\n---\n\n{body}\n", encoding="utf-8"
    )
    return skill_dir / "SKILL.md"


_AUTO_LOAD_CFG = {"skills": {"auto_load": ["pinned-skill"]}}


def _view(name, task_id):
    from tools.skills_tool import _skill_view_with_bump

    return _skill_view_with_bump({"name": name}, task_id=task_id)


def test_pinned_skill_view_in_its_session_returns_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    from agent.skill_commands import build_auto_load_prompt

    _write_skill(tmp_path, "pinned-skill", "PINNED CONTENT")
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
        prompt, loaded, missing = build_auto_load_prompt(
            task_id="s1", user_config=_AUTO_LOAD_CFG
        )
        assert loaded == ["pinned-skill"] and missing == []
        assert "PINNED CONTENT" in prompt

        result = _view("pinned-skill", "s1")
    parsed = json.loads(result)
    assert parsed.get("dedup") is True and parsed.get("status") == "unchanged", result
    assert "auto_load" in parsed.get("message", ""), result
    assert "PINNED CONTENT" not in result, (
        "the session had the full skill twice: once in the prompt, once in the view"
    )


def test_other_session_changed_file_and_compression_return_full_content(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    from agent.skill_commands import build_auto_load_prompt

    skill_md = _write_skill(tmp_path, "pinned-skill", "PINNED CONTENT")
    with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
        build_auto_load_prompt(task_id="s1", user_config=_AUTO_LOAD_CFG)

        # Another session never saw the auto-load block: full content.
        other = _view("pinned-skill", "s2")
        assert "PINNED CONTENT" in other

        # The file changed under the same session: the digest no longer matches.
        skill_md.write_text(
            "---\nname: pinned-skill\ndescription: Test.\n---\n\nMUTATED CONTENT\n",
            encoding="utf-8",
        )
        changed = _view("pinned-skill", "s1")
        assert "MUTATED CONTENT" in changed

        # Context compression resets the cache: the full content comes back.
        reset_skill_view_dedup("s1")
        after = _view("pinned-skill", "s1")
        assert "MUTATED CONTENT" in after
