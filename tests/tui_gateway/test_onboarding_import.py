"""Import-safety regression for the onboarding handler split module.

Background
----------
``methods_onboarding``'s ``_shared_nous_store`` annotates its return type as
``tuple[Path, ...]``, which evaluates ``Path`` at function-definition time.
The v0.22.2 release shipped that annotation without importing ``Path``, so on
pre-3.14 interpreters (eager annotation evaluation) importing
``tui_gateway.methods_onboarding`` raised ``NameError`` at import time. The
failure aborted ``tui_gateway.server``'s split-module initialization and broke
unrelated backend consumers such as ``tests/hermes_cli/test_plugin_events.py``
(#138516, fixed on main by #138324).

Evicting the cached ``sys.modules`` entry first guarantees the import below
re-executes the module top level instead of returning the object the pytest
process (or ``tui_gateway.server``) already loaded. On Python 3.14's deferred
annotations a broken module would import cleanly, so this check only bites on
pre-3.14 interpreters — exactly the lanes that must stay import-safe.
"""

import importlib
import sys


def test_onboarding_module_imports_on_eager_annotation_interpreters(monkeypatch):
    """A clean re-import of tui_gateway.methods_onboarding must succeed."""
    monkeypatch.delitem(sys.modules, "tui_gateway.methods_onboarding", raising=False)
    module = importlib.import_module("tui_gateway.methods_onboarding")

    assert module.__name__ == "tui_gateway.methods_onboarding"
    # The fix under test: the module must import the annotation names it uses.
    assert module.Path is not None
