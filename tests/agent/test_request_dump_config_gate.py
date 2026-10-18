"""``logging.request_dumps: false`` gates provider-error request dumps (#141277).

Regression: a deployment that keeps prompts only in ``state.db`` under a retention
policy must be able to stop ``sessions/request_dump_*.json`` from writing another
full-prompt copy on provider errors.
"""

from types import SimpleNamespace

from agent.agent_runtime_helpers import dump_api_request_debug


def _minimal_agent(logs_dir):
    return SimpleNamespace(
        api_mode="openai_completions",
        client=None,
        session_id="sess-1",
        base_url="https://api.example.com/v1",
        logs_dir=logs_dir,
        log_prefix="",
        verbose_logging=False,
        _mask_api_key_for_logs=lambda key: "sk-***" if key else None,
        _vprint=lambda *a, **kw: None,
    )


_API_KWARGS = {"model": "test-model", "messages": [{"role": "user", "content": "secret prompt"}]}


class TestDumpApiRequestDebugConfigGate:
    def test_dump_written_by_default(self, tmp_path):
        agent = _minimal_agent(tmp_path)

        path = dump_api_request_debug(agent, dict(_API_KWARGS), reason="non_retryable_client_error")

        assert path is not None
        assert path.exists()
        assert list(tmp_path.glob("request_dump_*.json")) == [path]

    def test_dump_skipped_when_request_dumps_false(self, tmp_path):
        from hermes_constants import get_config_path

        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("logging:\n  request_dumps: false\n", encoding="utf-8")
        try:
            agent = _minimal_agent(tmp_path)

            path = dump_api_request_debug(agent, dict(_API_KWARGS), reason="non_retryable_client_error")

            assert path is None
            assert not list(tmp_path.glob("request_dump_*"))
        finally:
            config_path.unlink(missing_ok=True)

    def test_dump_written_when_request_dumps_explicitly_true(self, tmp_path):
        from hermes_constants import get_config_path

        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("logging:\n  request_dumps: true\n", encoding="utf-8")
        try:
            agent = _minimal_agent(tmp_path)

            path = dump_api_request_debug(agent, dict(_API_KWARGS), reason="non_retryable_client_error")

            assert path is not None and path.exists()
        finally:
            config_path.unlink(missing_ok=True)
