"""The aiohttp-optional gateway platform modules import without aiohttp.

These adapters import aiohttp defensively (``web = None`` on ImportError), but the ruff UP037
autofix (2ff58849f4) unquoted their ``"web.Response"`` return annotations. On Python 3.11-3.13 a
function annotation is evaluated when the ``def`` runs, so at module import the ``-> web.Response``
expressions evaluated against ``None`` and raised ``AttributeError``, which the ImportError-only
adapter guard in ``gateway/run.py`` did not catch — the gateway crash-looped on any install whose
environment lacks aiohttp (#144560). PEP 563 lazy annotations (``from __future__ import
annotations``, the pattern ``msgraph_webhook.py`` already used) restore the degraded mode on every
supported interpreter.

Two pins per module: a static one for the 3.11-3.13 eager-evaluation semantics (a runtime import
cannot reproduce the failure on 3.14, where PEP 649 already defers annotations), and a runtime one
that exercises the degraded import with aiohttp actually blocked.
"""

import ast
import importlib
import sys
from pathlib import Path

import pytest

_MODULES = [
    "api_server",
    "api_server_runs",
    "api_server_openai_routes",
    "webhook",
    "api_server_room_grants",
    "api_server_room_dispatch",
]
_PLATFORMS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "platforms"


def _has_lazy_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _eager_web_annotation_count(tree: ast.Module) -> int:
    """Count unquoted annotations referencing ``web.<attr>`` — Attribute nodes survive only when
    the annotation is evaluated at definition time; a quoted one parses as a string constant."""
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        annotations = [node.returns] + [arg.annotation for arg in node.args.args if arg.annotation]
        for annotation in annotations:
            if any(
                isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "web"
                for sub in ast.walk(annotation)
            ):
                found += 1
    return found


@pytest.mark.parametrize("module_name", _MODULES)
def test_platform_module_annotations_are_lazy(module_name):
    tree = ast.parse((_PLATFORMS_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
    assert _has_lazy_annotations(tree) or _eager_web_annotation_count(tree) == 0, (
        f"gateway/platforms/{module_name}.py evaluates web.* annotations at import time; with "
        "aiohttp absent (web = None) that raises AttributeError on Python 3.11-3.13 and "
        "crash-loops the gateway (#144560). Add `from __future__ import annotations`."
    )


class _BlockedAiohttpFinder:
    """Meta-path finder that reports aiohttp as unavailable, simulating an install without it."""

    def find_spec(self, name, path=None, target=None):
        if name == "aiohttp" or name.startswith("aiohttp."):
            raise ImportError(f"No module named {name!r} (blocked by test)")
        return None


@pytest.fixture()
def aiohttp_blocked():
    """Hide aiohttp and the target modules from sys.modules, block aiohttp imports, then restore."""
    blocker = _BlockedAiohttpFinder()
    hidden = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "aiohttp"
        or name.startswith("aiohttp.")
        or name in {f"gateway.platforms.{module}" for module in _MODULES}
    }
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(hidden)


@pytest.mark.parametrize("module_name", _MODULES)
def test_platform_module_imports_with_aiohttp_absent(aiohttp_blocked, module_name):
    module = importlib.import_module(f"gateway.platforms.{module_name}")
    assert getattr(module, "web", "missing") is None
