"""Doctor npm-audit incompleteness — a failed audit must never read as "clean".

Regression for #142635: ``hermes doctor``'s npm-audit check treated any audit
failure (timeout, non-zero exit with empty stdout, partial JSON) as zero
vulnerabilities, printing ``✓ … (no known vulnerabilities)`` while the tree
actually held ``high`` findings, and the 30 s timeout was swallowed entirely by
the blanket ``except Exception: pass``. An audit that did not complete must say
so and surface as an issue instead of a false clean bill.
"""

import json
import subprocess
from unittest.mock import patch

from hermes_cli import doctor_tools


INCOMPLETE_DETAIL = "audit did not complete"


def _audit_json(critical=0, high=0, moderate=0):
    return json.dumps(
        {
            "metadata": {
                "vulnerabilities": {
                    "critical": critical,
                    "high": high,
                    "moderate": moderate,
                    "low": 0,
                    "info": 0,
                    "total": critical + high + moderate,
                }
            }
        }
    )


def _run(capsys, completed=None, raises=None):
    manual_issues: list[str] = []
    with patch.object(
        doctor_tools.subprocess, "run", side_effect=raises, return_value=completed
    ):
        doctor_tools._audit_one(
            "npm", "/fake/root", "web workspace", ["--workspace", "web"], manual_issues
        )
    return capsys.readouterr().out, manual_issues


def test_empty_stdout_audit_is_not_reported_clean(capsys):
    """A registry/network failure with empty stdout used to print
    ``✓ (no known vulnerabilities)``; it must warn that the audit did not
    complete and count as an issue so ``Found N issue(s)`` can see it."""
    out, issues = _run(
        capsys, subprocess.CompletedProcess([], 1, stdout="", stderr="ECONNRESET")
    )
    assert INCOMPLETE_DETAIL in out
    assert "no known vulnerabilities" not in out
    assert issues and INCOMPLETE_DETAIL in issues[0]


def test_partial_json_audit_is_not_reported_clean(capsys):
    """Truncated stdout (killed run / registry hiccup) fails json.loads into
    the same false-clean path."""
    out, issues = _run(
        capsys,
        subprocess.CompletedProcess([], 1, stdout='{"metadata": {"vuln', stderr=""),
    )
    assert INCOMPLETE_DETAIL in out
    assert "no known vulnerabilities" not in out
    assert issues and INCOMPLETE_DETAIL in issues[0]


def test_error_json_without_metadata_is_not_reported_clean(capsys):
    """npm writes ``{"error": ...}`` on registry failures; a payload with no
    metadata.vulnerabilities block used to fall through to a clean bill too."""
    out, issues = _run(
        capsys,
        subprocess.CompletedProcess(
            [], 1, stdout='{"error": {"code": "ETIMEDOUT"}}', stderr=""
        ),
    )
    assert INCOMPLETE_DETAIL in out
    assert "no known vulnerabilities" not in out
    assert issues and INCOMPLETE_DETAIL in issues[0]


def test_timeout_audit_is_not_reported_clean(capsys):
    """The 30 s cap used to raise TimeoutExpired into the blanket ``except`` —
    nothing was printed at all. It must surface the same incomplete warning."""
    out, issues = _run(
        capsys, raises=subprocess.TimeoutExpired(cmd=["npm", "audit"], timeout=30)
    )
    assert INCOMPLETE_DETAIL in out
    assert issues and INCOMPLETE_DETAIL in issues[0]


def test_vuln_findings_with_nonzero_exit_still_count(capsys):
    """npm exits 1 when it *finds* vulnerabilities while stdout stays valid
    JSON: that run completed and must be counted, never read as incomplete."""
    out, issues = _run(
        capsys,
        subprocess.CompletedProcess([], 1, stdout=_audit_json(high=1), stderr=""),
    )
    assert INCOMPLETE_DETAIL not in out
    assert "1 high" in out
    assert issues and "1 npm vulnerability" in issues[0]
