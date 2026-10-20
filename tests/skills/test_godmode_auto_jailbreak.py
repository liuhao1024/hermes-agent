"""Config/model detection tests for the godmode auto_jailbreak helper (#142286)."""

import importlib.util
from pathlib import Path

import pytest

repo_root = Path(__file__).parent.parent.parent
script_path = (
    repo_root
    / "optional-skills"
    / "security"
    / "godmode"
    / "scripts"
    / "auto_jailbreak.py"
)

spec = importlib.util.spec_from_file_location(
    "auto_jailbreak_under_test", str(script_path)
)
auto_jailbreak_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto_jailbreak_mod)

_KEY_VARS = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(auto_jailbreak_mod, "CONFIG_PATH", path)
    return path


@pytest.fixture
def no_api_keys(monkeypatch):
    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_current_model_reads_model_default(config_path):
    config_path.write_text(
        "model:\n  default: fixture-model\n  provider: openrouter\n", encoding="utf-8"
    )
    model, base_url = auto_jailbreak_mod._get_current_model()
    assert model == "fixture-model"
    assert base_url == "https://openrouter.ai/api/v1"


def test_current_model_keeps_legacy_name(config_path):
    config_path.write_text("model:\n  name: legacy-model\n", encoding="utf-8")
    assert auto_jailbreak_mod._get_current_model()[0] == "legacy-model"


def test_current_model_keeps_string_form(config_path):
    config_path.write_text("model: string-model\n", encoding="utf-8")
    assert auto_jailbreak_mod._get_current_model()[0] == "string-model"


def test_current_model_default_wins_over_empty_name(config_path):
    config_path.write_text("model:\n  default: primary\n  name: ''\n", encoding="utf-8")
    assert auto_jailbreak_mod._get_current_model()[0] == "primary"


def test_current_model_missing_file(config_path):
    assert auto_jailbreak_mod._get_current_model() == (None, None)


def test_current_model_unparsable_yaml(config_path):
    config_path.write_text("model: [broken\n", encoding="utf-8")
    assert auto_jailbreak_mod._get_current_model() == (None, None)


def test_current_model_non_mapping_document(config_path):
    config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert auto_jailbreak_mod._get_current_model() == (None, None)


def test_current_model_readable_but_no_model_entry(config_path):
    config_path.write_text("agent: {}\n", encoding="utf-8")
    model, base_url = auto_jailbreak_mod._get_current_model()
    assert model == ""
    assert base_url == "https://openrouter.ai/api/v1"


def test_dry_run_reports_missing_model_field(config_path, no_api_keys):
    config_path.write_text("agent: {}\n", encoding="utf-8")
    result = auto_jailbreak_mod.auto_jailbreak(dry_run=True, verbose=False)
    assert result["success"] is False
    assert "model.default" in result["error"]


def test_dry_run_reports_unreadable_config(config_path, no_api_keys):
    result = auto_jailbreak_mod.auto_jailbreak(dry_run=True, verbose=False)
    assert result["success"] is False
    assert "couldn't read or parse config.yaml" in result["error"]


def test_api_key_error_names_required_env_var(no_api_keys):
    result = auto_jailbreak_mod.auto_jailbreak(
        model="gpt-5.3",
        base_url="https://api.openai.com/v1",
        dry_run=True,
        verbose=False,
    )
    assert result["success"] is False
    assert "OPENAI_API_KEY" in result["error"]


def test_api_key_error_explains_oauth_subscription_endpoint(no_api_keys):
    result = auto_jailbreak_mod.auto_jailbreak(
        model="gpt-5.3-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        dry_run=True,
        verbose=False,
    )
    assert result["success"] is False
    assert "OAuth" in result["error"]


def test_openrouter_default_error_names_openrouter_var(no_api_keys):
    result = auto_jailbreak_mod.auto_jailbreak(
        model="fixture-model", dry_run=True, verbose=False
    )
    assert result["success"] is False
    assert "OPENROUTER_API_KEY" in result["error"]
