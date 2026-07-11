"""Test that vision_analyze auto-activates SSH/Docker sandboxes on demand.

Issue #62825: vision_analyze cannot activate SSH connection to read remote
files. The fix changes image_source.py to use _get_or_create_env (which
auto-creates the sandbox if needed) instead of get_active_env (which only
returns existing sandboxes).
"""
import pytest
from pathlib import Path
from tools.image_source import ResolveContext, _resolve_container_fallback, _get_or_create_env


class MockEnv:
    """Mock sandbox environment."""
    def __init__(self, success: bool = True, output: str = ""):
        self._success = success
        self._output = output

    def execute(self, cmd: str) -> dict:
        # Simulate a successful base64 encode of a minimal PNG (1x1 pixel)
        png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
        if self._success:
            return {"returncode": 0, "output": png_base64}
        return {"returncode": 1, "output": self._output}


def test_get_or_create_env_returns_existing_env(monkeypatch):
    """When a sandbox already exists for task_id, return it."""
    mock_env = MockEnv()

    def mock_get_or_create(task_id: str):
        return mock_env, "docker"

    monkeypatch.setattr("tools.code_execution_tool._get_or_create_env", mock_get_or_create)

    env = _get_or_create_env("test-task-123")
    assert env is mock_env


def test_get_or_create_env_returns_none_on_import_error(monkeypatch):
    """When code_execution_tool is unavailable, return None (graceful degradation)."""
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "tools.code_execution_tool":
            raise ImportError("unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    env = _get_or_create_env("test-task-123")
    assert env is None


def test_resolve_container_fallback_creates_env_if_needed(monkeypatch):
    """When no active sandbox exists, _get_or_create_env is called to create one."""
    mock_env = MockEnv(success=True)

    def mock_get_or_create(task_id: str):
        return mock_env, "docker"

    monkeypatch.setattr("tools.code_execution_tool._get_or_create_env", mock_get_or_create)

    import asyncio

    async def run_test():
        ctx = ResolveContext(task_id="test-task-456")
        result = await _resolve_container_fallback(
            Path("/tmp/test.png"), ctx, "/tmp/test.png"
        )
        assert result.origin == "container"
        assert len(result.data) > 0
        # Minimal PNG is 70 bytes after base64 decode
        assert 60 <= len(result.data) <= 80

    asyncio.run(run_test())


def test_resolve_container_fallback_raises_when_env_cannot_be_created(monkeypatch):
    """When sandbox creation fails, raise SourceNotFound with clear message."""
    def mock_get_or_create(task_id: str):
        return None, None  # Simulate creation failure

    monkeypatch.setattr("tools.code_execution_tool._get_or_create_env", mock_get_or_create)

    from tools.image_source import SourceNotFound
    import asyncio

    async def run_test():
        ctx = ResolveContext(task_id="test-task-789")
        with pytest.raises(SourceNotFound) as exc_info:
            await _resolve_container_fallback(
                Path("/tmp/test.png"), ctx, "/tmp/test.png"
            )
        assert "no sandbox session could be activated" in str(exc_info.value)

    asyncio.run(run_test())


def test_resolve_container_fallback_propagates_execute_errors(monkeypatch):
    """When env.execute fails (returncode != 0), raise SourceNotFound with error details."""
    mock_env = MockEnv(success=False, output="permission denied")

    def mock_get_or_create(task_id: str):
        return mock_env, "ssh"

    monkeypatch.setattr("tools.code_execution_tool._get_or_create_env", mock_get_or_create)

    from tools.image_source import SourceNotFound
    import asyncio

    async def run_test():
        ctx = ResolveContext(task_id="test-task-999")
        with pytest.raises(SourceNotFound) as exc_info:
            await _resolve_container_fallback(
                Path("/tmp/secret.png"), ctx, "/tmp/secret.png"
            )
        assert "could not read '/tmp/secret.png' inside the sandbox" in str(exc_info.value)

    asyncio.run(run_test())


def test_resolve_container_fallback_rejects_non_image_output(monkeypatch):
    """When sandbox returns non-image data (base64 decode succeeds but magic-byte fails), raise NotAnImage."""
    # "Hello world!" base64 = 12 bytes, decode to ASCII
    mock_env = MockEnv(success=False, output="SGVsbG8gd29ybGQh")

    def mock_get_or_create(task_id: str):
        return mock_env, "docker"

    # Override execute to return returncode 0 but non-PNG data
    def mock_execute(cmd: str) -> dict:
        return {"returncode": 0, "output": "SGVsbG8gd29ybGQh"}

    mock_env.execute = mock_execute

    monkeypatch.setattr("tools.code_execution_tool._get_or_create_env", lambda tid: (mock_env, "docker"))

    from tools.image_source import NotAnImage
    import asyncio

    async def run_test():
        ctx = ResolveContext(task_id="test-task-000")
        with pytest.raises(NotAnImage):
            await _resolve_container_fallback(
                Path("/tmp/text.txt"), ctx, "/tmp/text.txt"
            )

    asyncio.run(run_test())