## What does this PR do?

Fixes #61294: Stdio MCP subprocess environment scrubbing silently discards essential configuration env vars (like `CBM_CACHE_DIR`).

The `_build_safe_env()` function was filtering out all environment variables except a hardcoded whitelist and `XDG_*` prefix. This prevented MCP servers like `codebase-memory-mcp` from receiving legitimate configuration variables from the parent process environment.

This PR adds a `pass_env_prefixes` config option that allows users to whitelist additional safe environment variable prefixes (e.g., `["CBM_"]`) that should be passed through to MCP server subprocesses.

The default behavior remains secure—without `pass_env_prefixes`, the function behaves exactly as before. Users must explicitly opt-in to pass additional prefixes, and the variable names must start with the specified prefix.

## Related Issue

Fixes #61294

## Type of Change

- [x] 🐛 Bug fix (non-breaking change that fixes an issue)
- [ ] ✨ New feature (non-breaking change that adds functionality)
- [ ] 🔒 Security fix
- [ ] 📝 Documentation update
- [ ] ✅ Tests (adding or improving test coverage)
- [ ] ♻️ Refactor (no behavior change)
- [ ] 🎯 New skill (bundled or hub)

## Changes Made

- `tools/mcp_tool.py`:
  - Modified `_build_safe_env()` to accept optional `pass_prefixes` parameter
  - Updated `_run_stdio()` to read `pass_env_prefixes` from server config and pass it to `_build_safe_env()`
- `tests/tools/test_mcp_tool.py`:
  - Added 4 new tests for the `pass_prefixes` feature:
    - `test_pass_prefixes_allows_custom_prefixes`: verifies single prefix whitelist
    - `test_pass_prefixes_multiple_prefixes`: verifies multiple prefixes
    - `test_pass_prefixes_empty_list`: verifies empty list behaves like default
    - `test_pass_prefixes_none`: verifies None behaves like default

## How to Test

### Unit tests
Run the new tests:
```bash
pytest tests/tools/test_mcp_tool.py::TestBuildSafeEnv -v
```

All 10 tests should pass (6 existing + 4 new).

### Integration test
1. Export a custom environment variable:
   ```bash
   export CBM_CACHE_DIR="/mnt/data/cache"
   ```

2. Configure an MCP server in `~/.hermes/config.yaml` with the new `pass_env_prefixes` option:
   ```yaml
   mcp_servers:
     codebase-memory:
       command: "npx"
       args: ["-y", "codebase-memory-mcp"]
       pass_env_prefixes:
         - "CBM_"
   ```

3. Start Hermes and verify the MCP server receives the `CBM_CACHE_DIR` variable by inspecting the spawned process environment (e.g., via `/proc/<PID>/environ` on Linux or `ps eww <PID>` on macOS).

The `CBM_CACHE_DIR` variable should be present in the MCP server subprocess environment.

## Checklist

### Code

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`fix(scope):`, `feat(scope):`, etc.)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (no unrelated commits)
- [x] I've run `pytest tests/tools/test_mcp_tool.py::TestBuildSafeEnv -v` and all tests pass
- [x] I've added tests for my changes (required for bug fixes, strongly encouraged for features)
- [x] I've tested on my platform: macOS 15.2

### Documentation & Housekeeping

- [x] I've updated relevant documentation (README, `docs/`, docstrings) — or N/A
- [x] I've updated `cli-config.yaml.example` if I added/changed config keys — or N/A
- [x] I've updated `CONTRIBUTING.md` or `AGENTS.md` if I changed architecture or workflows — or N/A
- [x] I've considered cross-platform impact (Windows, macOS) per the [compatibility guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) — or N/A
- [x] I've updated tool descriptions/schemas if I changed tool behavior — or N/A

## For New Skills

<!-- Only fill this out if you're adding a skill. Delete this section otherwise. -->

- [ ] This skill is **broadly useful** to most users (if bundled) — see [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#should-the-skill-be-bundled)
- [ ] SKILL.md follows the [standard format](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#skillmd-format) (frontmatter, trigger conditions, steps, pitfalls)
- [ ] No external dependencies that aren't already available (prefer stdlib, curl, existing Hermes tools)
- [ ] I've tested the skill end-to-end: `hermes --toolsets skills -q "Use the X skill to do Y"`

## Screenshots / Logs

N/A (behavioral change)