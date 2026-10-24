"""Read-only probes of the two standard container-discovery files are a note (#144123).

``proc_access`` flags any ``/proc/self`` or ``/proc/<pid>`` reference at high, which gave a
plugin that sizes work against its container's CPU quota a CAUTION verdict — an unattended
catalog update needs ``safe``. Reading exactly ``/proc/self/cgroup`` or
``/proc/self/mountinfo`` (static literal, read action, no write marker) steps down to a
medium note; writes, other proc paths, dynamic destinations and unrecognized shapes keep
the high finding.
"""

from pathlib import Path

import pytest

from tools.skills_guard import format_scan_report
from tools.plugin_guard import should_allow_plugin_install, scan_plugin


def _mk_plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()
    for rel, content in files.items():
        p = plugin / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return plugin


BASE_FILES = {
    "plugin.yaml": "name: test-plugin\nmanifest_version: 1\n",
    "__init__.py": (
        "def register(ctx):\n"
    ),
    "README.md": "# Test plugin\n\nA simple test plugin.\n",
}

# The issue's reproducer: a CPU-quota detector reading exactly the two discovery files.
ISSUE_REPRO = (
    "from pathlib import Path\n"
    "\n"
    "def container_layout():\n"
    "    return (\n"
    '        Path("/proc/self/cgroup").read_text(),\n'
    '        Path("/proc/self/mountinfo").read_text(),\n'
    "    )\n"
)


class TestContainerProbeRead:
    @pytest.mark.parametrize("rel,text", [
        ("quota.py", ISSUE_REPRO),
        ("probe.py", 'with open("/proc/self/mountinfo") as fh:\n    layout = fh.read()\n'),
        ("probe.py", 'data = open("/proc/self/cgroup").read()\n'),
        ("probe.py", 'data = open("/proc/self/cgroup", "r").read()\n'),
        ("probe.js", 'const cgroup = readFileSync("/proc/self/cgroup", "utf8");\n'),
        ("probe.sh", "cat /proc/self/mountinfo\n"),
        ("probe.sh", "grep cgroup /proc/self/cgroup\n"),
    ])
    def test_read_only_probe_is_a_note_and_installs_safe(self, tmp_path, rel, text):
        result = scan_plugin(_mk_plugin(tmp_path, {**BASE_FILES, rel: text}), source="owner/repo")
        sevs = {f.severity for f in result.findings if f.pattern_id == "proc_access"}
        assert sevs == {"medium"}, format_scan_report(result)
        assert result.verdict == "safe", format_scan_report(result)
        allowed, _reason = should_allow_plugin_install(result)
        assert allowed is True

    def test_issue_reproducer_ends_the_update_block(self, tmp_path):
        """The exact plugin from the issue: verdict safe, findings still visible."""
        result = scan_plugin(_mk_plugin(tmp_path, {**BASE_FILES, "detect.py": ISSUE_REPRO}),
                             source="owner/repo")
        assert result.verdict == "safe"
        assert len([f for f in result.findings if f.pattern_id == "proc_access"]) == 2

    @pytest.mark.parametrize("rel,text", [
        ("evil.py", 'Path("/proc/self/cgroup").write_text("x")\n'),
        ("evil.py", 'fh = open("/proc/self/cgroup", "w")\n'),
        ("evil.py", 'Path("/proc/self/status").read_text()\n'),
        ("evil.py", 'open("/proc/1/maps").read()\n'),
        ("evil.py", 'open(f"/proc/self/{name}").read()\n'),
        ("evil.sh", "echo x > /proc/self/cgroup\n"),
    ])
    def test_write_other_paths_and_dynamic_shapes_keep_high(self, tmp_path, rel, text):
        result = scan_plugin(_mk_plugin(tmp_path, {**BASE_FILES, rel: text}), source="owner/repo")
        sevs = {f.severity for f in result.findings if f.pattern_id == "proc_access"}
        assert sevs == {"high"}, format_scan_report(result)
        assert result.verdict == "caution"
