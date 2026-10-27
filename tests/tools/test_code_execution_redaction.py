"""Regression tests for execute_code output redaction (#95509).

``execute_code`` output used the plain ``code_file=True`` pass, which skips the
ENV-assignment rules — so a script that printed ``.env``-shaped ``KEY=value``
lines passed opaque secret values through verbatim (measured live on main by
the reporter: ``HERMES_WEBUI_PASSWORD=<value>`` delivered raw). The fix routes
exec output through ``redact_env_shaped_lines``: the ENV-assignment rules run on
top of ``code_file=True`` with code-shape values excluded, so parameter defaults
and constants in echoed source stay readable (measured trade-off: the
unconditional ``code_file=False`` flip altered 13/101 real secret-keyword
source lines; this gate alters 2, both doc examples). All secret-shaped
literals below are synthetic and non-usable.
"""

import inspect

from agent.redact import redact_env_shaped_lines
from tools.code_execution_tool import _clean_output, _truncate_stdout_text


def test_env_shaped_dump_is_masked():
    """The reporter's failure shape: .env lines printed by script logic leak."""
    out = redact_env_shaped_lines(
        "HERMES_WEBUI_PASSWORD=Sup3rS3cretValue!\n"
        "OPENROUTER_API_KEY=sk-or-v1-synthetic0123456789\n"
        "GITHUB_TOKEN=ghp_SYNTHETICxyz\n"
        "GEMINI_API_KEY=AQ.Synthetic123xyz\n"
        "INTERNAL_SECRET=opaque_random_48char_value_no_known_prefix\n"
        "DB_PASSWORD=hunter2style"
    )
    for secret in ("Sup3rS3cretValue!", "sk-or-v1-synthetic0123456789", "ghp_SYNTHETICxyz",
                   "AQ.Synthetic123xyz", "opaque_random_48char_value_no_known_prefix",
                   "hunter2style"):
        assert secret not in out, secret
    assert out.count("=") == 6  # keys survive, values masked


def test_quoted_env_values_are_masked():
    """.env values are commonly quoted; the quote must not shield the secret."""
    out = redact_env_shaped_lines('PASSWORD="quoted-secret-123"')
    assert "quoted-secret-123" not in out


def test_code_shaped_assignments_stay_readable():
    """Echoed source must survive: parameter defaults, comparisons, constants,
    attribute paths, call syntax (review-measured false-positive shapes)."""
    lines = [
        "def gql(q, v=None, token=None):",
        "if token == expected:",
        "TOKEN=MAX_RETRIES",
        'config = load(api_key=os.getenv("K"))',
        "x = module.helper.path",
        "total=42",
        "author=Smith",
    ]
    text = "\n".join(lines)
    assert redact_env_shaped_lines(text) == text


def test_programmatic_env_lookup_stays_readable():
    """The #2852 guard: KEY=os.getenv('X') references a name, not a secret."""
    out = redact_env_shaped_lines('GITHUB_TOKEN=os.getenv("GITHUB_TOKEN")')
    assert out == 'GITHUB_TOKEN=os.getenv("GITHUB_TOKEN")'


def test_url_query_params_unchanged():
    """Web-URL passthrough (#77484): token= in a query string is preserved."""
    text = "GET https://example.com/cb?token=abc123&state=xyz"
    assert redact_env_shaped_lines(text) == text


def test_line_count_is_preserved():
    """Masking must never swallow text across newlines (an unbounded quote
    branch in a hand-rolled assignment regex silently deletes lines)."""
    text = "\n".join(
        ["plain line", "GITHUB_TOKEN=ghp_SYNTHETICxyz", "if token == expected:",
         'say("KEY=hello world")', "last"]
    )
    out = redact_env_shaped_lines(text)
    assert len(out.split("\n")) == len(text.split("\n"))
    assert "plain line" in out and "last" in out


def test_clean_output_masks_env_shaped_dump():
    """The shared pipeline masks .env-shaped values while keeping code reads."""
    out, _ = _clean_output("HERMES_WEBUI_PASSWORD=Sup3rS3cretValue!\nMAX_TOKENS=100\n")
    assert "Sup3rS3cretValue!" not in out
    assert "MAX_TOKENS=100" in out  # code_file carve-out intact


def test_clean_output_redacts_before_spilling(tmp_path, monkeypatch):
    """Spilled full output must carry redacted text: the result teaches the
    model to read_file the spill, and read_file implies code_file=True, which
    would skip the ENV pass on spilled plaintext."""
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_dir",
                        lambda *a, **k: tmp_path, raising=True)
    payload = ("padding line that is not secret\n" * 3000) + "GITHUB_TOKEN=ghp_SYNTHETICxyz\n"
    out, metadata = _clean_output(payload)
    assert metadata.get("stdout_spill_path"), "expected truncation to spill"
    spilled = open(metadata["stdout_spill_path"], encoding="utf-8").read()
    assert "ghp_SYNTHETICxyz" not in spilled
    assert "ghp_SYNTHETICxyz" not in out


def test_clean_output_redaction_precedes_truncation():
    """Pin the ordering inside the shared pipeline: redaction before the
    byte-cap, because the spill file is written by the cap step."""
    src = inspect.getsource(_clean_output)
    assert src.index("redact_env_shaped_lines(") < src.index("_truncate_stdout_text(")


def test_session_kernel_routes_through_the_env_pass():
    """The session-kernel cell path must use the same supplement — no bare
    code_file-only clean(), and the cell-side spill is scrubbed in place."""
    import tools.code_kernel as ck

    src = inspect.getsource(ck._cell_result)
    assert "redact_env_shaped_lines(" in src
    assert "os.replace(tmp_spill, cell_spill)" in src


def test_cell_side_spill_file_is_scrubbed(tmp_path):
    """The runner writes raw stdout to the cell spill; the host must scrub it
    before surfacing the path (read_file would skip the ENV pass)."""
    from types import SimpleNamespace

    import tools.code_kernel as ck

    spill = tmp_path / "cell_spill.out"
    spill.write_text("head output\nINTERNAL_SECRET=opaque_random_48char_value\n", encoding="utf-8")
    kernel = SimpleNamespace(stderr=SimpleNamespace(drain=lambda: ""),
                             raw=SimpleNamespace(drain=lambda: ""),
                             tool_call_counter=[0], execution_count=0,
                             cell_log_start=0, tool_call_log=[])
    payload = {"status": "ok", "stdout": "head output\n", "execution_count": 1,
               "stdout_spill_path": str(spill), "stdout_clipped": True}
    result = ck._cell_result(kernel, ("o", "m", "py", "/tmp", ()), "success", payload,
                             timeout=10, sandbox_tools=frozenset(), reused=False,
                             state_reset=False, exec_start=0.0)
    assert result["stdout_spill_path"] == str(spill)
    scrubbed = spill.read_text(encoding="utf-8")
    assert "opaque_random_48char_value" not in scrubbed
    assert "INTERNAL_SECRET=" in scrubbed  # key survives, value masked
    assert "opaque_random_48char_value" not in result["output"]
