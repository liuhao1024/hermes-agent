## What does this PR do?

Fix two container-backend version probes in tools/terminal_tool.py that were missing windows_hide_flags(). On Windows, each subprocess.run() call without creationflags could spawn a brief console window flash when the parent process has no console (e.g., gateway service, cron-fired runs, detached workers). This applies the same Windows console-hiding policy used throughout the codebase to the two remaining stragglers on this path.

## Related Issue

Fixes #62734

## Type of Change

- [x] 🐛 Bug fix

## Changes Made

- tools/terminal_tool.py: Import IS_WINDOWS and windows_hide_flags from hermes_cli._subprocess_compat
- tools/terminal_tool.py: Add creationflags=windows_hide_flags() if IS_WINDOWS else 0 to sudo probe (_sudo_nopasswd_works)
- tools/terminal_tool.py: Add creationflags=windows_hide_flags() if IS_WINDOWS else 0 to Docker version probe (check_terminal_requirements, env_type="docker")
- tools/terminal_tool.py: Add creationflags=windows_hide_flags() if IS_WINDOWS else 0 to Singularity version probe (check_terminal_requirements, env_type="singularity")

## How to Test

On macOS/Linux: No observable behavior change — the fix only adds creationflags on Windows, where windows_hide_flags() returns 0 on non-Windows platforms.

On Windows: Confirm that backend probes no longer flash console windows. The probes run during terminal tool initialization and backend validation. Before the fix, each subprocess.run() would briefly show a console window; after the fix, they remain hidden.

Since the Hermes cron environment is macOS, verification relies on the existing pattern established in hermes_cli/_subprocess_compat.py and applied throughout the codebase. The change follows the same approach as prior fixes for Windows subprocess console flashes.

## Checklist

- [x] Tests pass locally
- [x] No unrelated changes
- [x] Follows project style guidelines
- [x] Documentation updated (if applicable)
- [x] Commit messages follow conventional commits

Platform tested: macOS (verification limited to cross-platform policy consistency; Windows-specific behavior relies on existing windows_hide_flags() implementation)