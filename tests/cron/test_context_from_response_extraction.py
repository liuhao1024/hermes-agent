"""context_from must inject the previous run's answer, not its prompt (#117290).

Stored agent-run archives are ``# Cron Job`` / ``## Prompt`` / ``## Response``
documents; skill-bearing prompts routinely exceed the 8000-char injection
budget, so head-truncation amputated the ``## Response`` section and
self-continuity silently became a no-op.
"""

import sys
from pathlib import Path

import pytest
import cron.scheduler
import run_agent

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def cron_env(tmp_path, monkeypatch):
    """Isolated cron environment with temp HERMES_HOME."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "cron").mkdir()
    (hermes_home / "cron" / "output").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import cron.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "HERMES_DIR", hermes_home)
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    return hermes_home


def _write_archive(cron_env, job_id: str, filename: str, body: str) -> None:
    from cron.jobs import OUTPUT_DIR

    out_dir = OUTPUT_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / filename).write_text(body, encoding="utf-8")


def _framed_archive(prompt: str, answer: str, *, prompt_delta: int = 0,
                    response_delta: int = 0) -> str:
    """Build a writer-shaped archive, optionally simulating stale length stamps."""
    return (
        "# Cron Job: probe\n\n"
        f"**Prompt Characters:** {len(prompt) + prompt_delta}\n## Prompt\n\n"
        f"{prompt}\n\n"
        f"**Response Characters:** {len(answer) + response_delta}\n## Response\n\n"
        f"{answer}\n"
    )


def _run_stub_job(monkeypatch, job, answer):
    """Run ``job`` through the real writer with a stub agent returning ``answer``."""
    class Agent:
        def __init__(self, *args, **kwargs):
            pass

        def run_conversation(self, *args, **kwargs):
            return {"final_response": answer, "completed": True, "failed": False}

    monkeypatch.setattr(run_agent, "AIAgent", Agent)
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider",
                        lambda **kwargs: {"provider": "openai", "api_key": "fixture"})
    return cron.scheduler.run_job(job)


class TestResponseSurvivesLongPrompt:
    """The answer, not the prompt, is the part continuity needs."""

    def test_long_prompt_archive_keeps_response(self, cron_env):
        from cron.jobs import create_job
        from cron.scheduler import _build_job_prompt

        job = create_job(prompt="Run the daily check", schedule="0 8 * * *", context_from="self")
        _write_archive(
            cron_env, job["id"], "2026-09-19_08-00-00.md",
            "# Cron Job: probe\n\n## Prompt\n\n" + "SKILL LINE\n" * 1800 +
            "\n\n## Response\n\nCONCLUSION-MARKER-42\n",
        )

        prompt = _build_job_prompt(job)

        assert "CONCLUSION-MARKER-42" in prompt
        assert "SKILL LINE" not in prompt  # the prompt half is dropped, not the answer


class TestUnusableAnswersFallThrough:
    """A [SILENT] or blank response is not usable continuity."""

    def test_silent_response_falls_through_to_older_archive(self, cron_env):
        from cron.jobs import create_job
        from cron.scheduler_prompt import _inject_context_from

        job = create_job(prompt="Report", schedule="0 8 * * *", context_from="self")
        _write_archive(
            cron_env, job["id"], "2026-09-18_08-00-00.md",
            "# Cron Job: probe\n\n## Prompt\n\ndo the thing\n\n## Response\n\nOLDER-REAL-ANSWER\n",
        )
        _write_archive(
            cron_env, job["id"], "2026-09-19_08-00-00.md",
            "# Cron Job: probe\n\n## Prompt\n\ndo the thing\n\n## Response\n\n[SILENT]\n",
        )

        prompt, injected = _inject_context_from(job, "Report")

        assert injected is True
        assert "OLDER-REAL-ANSWER" in prompt
        assert "[SILENT]" not in prompt

class TestScriptModeArchives:
    """Archives without a ## Response heading (script-mode) stay whole-document."""

    def test_headingless_archive_injects_whole_document(self, cron_env):
        from cron.jobs import create_job
        from cron.scheduler_prompt import _inject_context_from

        job = create_job(prompt="Report", schedule="0 8 * * *", context_from="self")
        _write_archive(cron_env, job["id"], "2026-09-19_08-00-00.md",
                       "\n\nplain script payload\nline two\n\n")

        prompt, injected = _inject_context_from(job, "Report")

        assert injected is True
        # Whole document, but trimmed like every other archive answer.
        assert "```\nplain script payload\nline two\n```" in prompt


def test_writer_reader_preserve_response_with_nested_frames(cron_env, monkeypatch, cron_owner):
    from cron.jobs import create_job, save_job_output
    from cron.scheduler_prompt import _inject_context_from

    answer = "摘要 before heading\r\n\r\n## Response\nsubsection\n**Response Characters:** 4\n## Response\n\nbody\n\n  "

    job = create_job(prompt="Original prompt noise\r\n**Response Characters:** 4\n## Response\n\nbody",
                     schedule="0 8 * * *", context_from="self")
    success, archive, _final, error = _run_stub_job(monkeypatch, job, answer)
    assert success, error
    save_job_output(job["id"], archive)
    prompt, injected = _inject_context_from(job, "Next task")
    assert injected
    assert answer.replace("\r\n", "\n").strip() in prompt
    assert "Original prompt noise" not in prompt


def test_truncated_outer_frame_cannot_promote_a_quoted_inner_frame(cron_env, monkeypatch, cron_owner):
    import os
    from cron.jobs import create_job, save_job_output, OUTPUT_DIR
    from cron.scheduler_prompt import _inject_context_from

    quoted = "QUOTED INNER ANSWER"
    suffix = "\nThis tail will be lost."
    answer = ("Outer response introduction\n"
              f"**Response Characters:** {len(quoted)}\n## Response\n\n{quoted}"
              + suffix)

    job = create_job(prompt="Report", schedule="0 8 * * *", context_from="self")
    success, archive, _final, error = _run_stub_job(monkeypatch, job, answer)
    assert success, error
    save_job_output(job["id"], archive)
    saved = next((OUTPUT_DIR / job["id"]).glob("*.md"))
    complete = saved.read_text(encoding="utf-8")
    assert complete.endswith(suffix + "\n")
    # Simulate a partial write: the quoted inner frame now reaches EOF exactly,
    # but the enclosing writer-owned response is missing its declared suffix.
    saved.write_text(complete[:-len(suffix + "\n")] + "\n", encoding="utf-8")
    os.utime(saved, (2, 2))
    prompt, injected = _inject_context_from(job, "Next task")
    assert not injected
    assert prompt == "Next task"

    _write_archive(cron_env, job["id"], "older.md", "## Response\n\nOLDER COMPLETE ANSWER\n")
    os.utime(OUTPUT_DIR / job["id"] / "older.md", (1, 1))
    prompt, injected = _inject_context_from(job, "Next task")
    assert injected
    assert "OLDER COMPLETE ANSWER" in prompt
    assert quoted not in prompt

    # Losing the response boundary itself is also unusable, not a script archive.
    saved.write_text(complete.split("**Response Characters:**", 1)[0], encoding="utf-8")
    os.utime(saved, (2, 2))
    prompt, injected = _inject_context_from(job, "Next task")
    assert injected and "OLDER COMPLETE ANSWER" in prompt
    assert "## Prompt" not in prompt


@pytest.mark.parametrize("prompt_delta", [0, 1, 9, 14])
def test_response_frame_anchor_tolerates_prompt_length_drift(prompt_delta):
    from cron.scheduler_prompt import _archive_answer

    archive = _framed_archive("prompt body", "CURRENT FRAMED ANSWER",
                              prompt_delta=prompt_delta)

    assert _archive_answer(archive) == "CURRENT FRAMED ANSWER"


def test_quoted_prompt_frame_cannot_capture_drifted_response_boundary():
    from cron.scheduler_prompt import _archive_answer

    prompt = (
        "Instructions may quote a frame:\n"
        "**Response Characters:** 4\n## Response\n\nbody\n"
        "and a bare heading:\n## Response\n"
    )
    archive = _framed_archive(prompt, "REAL OUTER ANSWER", prompt_delta=9)

    assert _archive_answer(archive) == "REAL OUTER ANSWER"


def test_overclaimed_quoted_prompt_frame_does_not_hide_real_response():
    from cron.scheduler_prompt import _archive_answer

    prompt = "Quoted example:\n**Response Characters:** 999999\n## Response\n\nnot the boundary"
    archive = _framed_archive(prompt, "REAL OUTER ANSWER")

    assert _archive_answer(archive) == "REAL OUTER ANSWER"


def test_exact_tail_quoted_prompt_frame_does_not_capture_real_response():
    from cron.scheduler_prompt import _archive_answer

    answer = "REAL OUTER ANSWER"
    outer = f"**Response Characters:** {len(answer)}\n## Response\n\n{answer}\n"
    prompt_tail = (
        f"PROMPT-ONLY TEXT\n\n**Response Characters:** {len(answer)}\n"
        f"## Response\n\n{answer}"
    )
    quoted_tail = prompt_tail + "\n\n" + outer
    prompt = (
        f"**Response Characters:** {len(quoted_tail) - 1}\n## Response\n\n{prompt_tail}"
    )
    archive = _framed_archive(prompt, answer)

    assert _archive_answer(archive) == answer


def test_exact_tail_quoted_answer_frame_does_not_replace_outer_response():
    from cron.scheduler_prompt import _archive_answer

    answer = "OUTER INTRO\n**Response Characters:** 5\n## Response\n\nINNER"
    archive = _framed_archive("prompt body", answer)

    assert _archive_answer(archive) == answer


def test_drifted_quoted_answer_frame_is_ambiguous_not_promoted():
    from cron.scheduler_prompt import _archive_answer

    answer = "OUTER INTRO\n**Response Characters:** 5\n## Response\n\nINNER"
    archive = _framed_archive("prompt body", answer, prompt_delta=27)

    assert _archive_answer(archive) is None


def test_drifted_truncated_outer_cannot_promote_quoted_answer_frame():
    from cron.scheduler_prompt import _archive_answer

    answer = "OUTER INTRO\n**Response Characters:** 5\n## Response\n\nINNER"
    archive = _framed_archive("prompt body", answer, prompt_delta=27)

    assert _archive_answer(archive[:-1]) is None


@pytest.mark.parametrize("response_delta", [-1, 1])
def test_invalid_response_length_stamp_is_rejected(response_delta):
    from cron.scheduler_prompt import _archive_answer

    archive = _framed_archive("prompt body", "answer with a bad stamp",
                              response_delta=response_delta)

    assert _archive_answer(archive) is None


def test_truncated_drifted_frame_is_rejected_without_legacy_fallback():
    from cron.scheduler_prompt import _archive_answer

    archive = _framed_archive("prompt body", "complete answer that loses its tail",
                              prompt_delta=14)

    assert _archive_answer(archive[:-5]) is None


def test_context_from_prefers_newest_drifted_frame_over_older_legacy(cron_env):
    import os
    from cron.jobs import OUTPUT_DIR, create_job
    from cron.scheduler_prompt import _inject_context_from

    job = create_job(prompt="Report", schedule="0 8 * * *", context_from="self")
    _write_archive(cron_env, job["id"], "older.md",
                   "## Response\n\nDAYS OLD LEGACY ANSWER\n")
    _write_archive(cron_env, job["id"], "newer.md",
                   _framed_archive("latest prompt", "LATEST FRAMED ANSWER",
                                   prompt_delta=14))
    os.utime(OUTPUT_DIR / job["id"] / "older.md", (1, 1))
    os.utime(OUTPUT_DIR / job["id"] / "newer.md", (2, 2))

    prompt, injected = _inject_context_from(job, "Next report")

    assert injected
    assert "LATEST FRAMED ANSWER" in prompt
    assert "DAYS OLD LEGACY ANSWER" not in prompt
