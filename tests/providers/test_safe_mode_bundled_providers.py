"""Safe mode must resolve bundled model-provider plugins (#143527).

``--safe-mode`` bypasses user customizations, but ``plugins/model-providers/`` ships with
hermes-agent: a troubleshooting worker still has to resolve those first-party providers, or a
pinned ``--provider commandcode`` run dies with ``AuthError: Unknown provider`` while built-ins
work. ``$HERMES_HOME`` plugins stay off — importing them is the user customization safe mode
exists to bypass. The policy binds once before runtime imports, so probes reuse the
``run_worker`` fresh-exec harness from the safe-worker policy tests.
"""
from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from tests.agent.test_safe_worker_policy import run_worker

_PROBE = '''
    import json, os
    mode = os.environ["PROBE_MODE"]
    if mode == "safe":
        from agent.safe_worker_policy import _bind_safe_worker_policy
        _bind_safe_worker_policy(safe_mode=True, ignore_user_config=True, config={})
    from providers import get_provider_profile, list_providers
    names = {p.name for p in list_providers()}
    result = {
        "bundled_resolves": get_provider_profile("commandcode") is not None,
        "bundled_listed": "commandcode" in names,
        "user_resolves": get_provider_profile("safeuser") is not None,
        "user_listed": "safeuser" in names,
        "custom_route": get_provider_profile("custom:route") is not None,
    }
    if mode == "safe":
        from hermes_cli.auth import resolve_provider
        result["resolve_provider"] = resolve_provider("commandcode")
    print(json.dumps(result))
'''

_USER_PLUGIN_INIT = textwrap.dedent('''
    import os
    from pathlib import Path
    # Prove whether safe mode imported this user plugin at all.
    (Path(os.environ["HERMES_HOME"]) / "user-plugin-imported.flag").write_text("x")
    from providers import register_provider
    from providers.base import ProviderProfile
    register_provider(ProviderProfile(name="safeuser", env_vars=("SAFEUSER_API_KEY",)))
''')


@pytest.mark.parametrize("mode", ["safe", "ordinary"])
def test_safe_mode_resolves_bundled_but_not_user_providers(tmp_path, mode):
    home = tmp_path / mode
    plugin_dir = home / "plugins" / "model-providers" / "safeuser"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text(_USER_PLUGIN_INIT, encoding="utf-8")
    (plugin_dir / "plugin.yaml").write_text(
        "name: safeuser\nkind: model-provider\nversion: 0.0.1\n", encoding="utf-8")

    result = run_worker(tmp_path, _PROBE, mode=mode)
    # Bundled first-party providers resolve and list in BOTH modes — safe mode
    # previously returned None/[] here, which surfaced as "Unknown provider".
    assert result["bundled_resolves"], result
    assert result["bundled_listed"], result
    assert result["custom_route"], result
    if mode == "safe":
        assert result["resolve_provider"] == "commandcode", result
        assert not result["user_resolves"], result
        assert not result["user_listed"], result
        assert not (home / "user-plugin-imported.flag").exists(), (
            "safe mode imported a $HERMES_HOME user plugin"
        )
    else:
        assert result["user_resolves"], result
        assert result["user_listed"], result
        assert (home / "user-plugin-imported.flag").exists()
