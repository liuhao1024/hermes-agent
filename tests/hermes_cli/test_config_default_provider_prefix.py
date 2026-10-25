"""Regression for #142839: a slash-form ``model.default`` must not forward its provider
prefix verbatim upstream.

``model.default: china/deepseek-v4`` with ``model.provider: china`` (a named custom
provider) used to reach the wire as ``china/deepseek-v4``; exact-name OpenAI-compatible
routers (New API / one-api style) reject it with ``503 model_not_found``, Hermes retries
and silently fails over to the fallback model — a local config typo dressed up as a
provider outage. The load-path chokepoint (``_normalize_root_model_keys``) normalizes the
redundant prefix away, so ``load_config()`` — the runtime's exact entry — is the surface
under test.
"""

import os
from unittest.mock import patch

from hermes_cli.config import load_config


def _load(tmp_path, config_yaml: str):
    (tmp_path / "config.yaml").write_text(config_yaml, encoding="utf-8")
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        return load_config()


_REPORTER_SHAPE = """\
model:
  default: china/deepseek-v4-flash
  provider: china
  base_url: http://feiniu:3001/v1
  key_env: HERMES_CUSTOM_CHINA_API_KEY
providers:
  china:
    base_url: http://feiniu:3001/v1
    models:
      deepseek-v4-flash: {}
"""


def test_issue_repro_prefixed_default_with_matching_provider_is_stripped(tmp_path):
    config = _load(tmp_path, _REPORTER_SHAPE)
    assert config["model"]["default"] == "deepseek-v4-flash"
    # The rest of the section is untouched.
    assert config["model"]["provider"] == "china"
    assert config["model"]["base_url"] == "http://feiniu:3001/v1"


def test_prefix_strips_with_bare_providers_key_only(tmp_path):
    # No ``model.provider`` at all: the prefix alone names the routable custom provider.
    config = _load(tmp_path, """\
model:
  default: china/deepseek-v4-flash
providers:
  china:
    base_url: http://feiniu:3001/v1
    models:
      deepseek-v4-flash: {}
""")
    assert config["model"]["default"] == "deepseek-v4-flash"


def test_unmatched_prefix_is_forwarded_verbatim(tmp_path):
    # ``cn/...`` names no configured provider: stripping would corrupt an id the user's
    # router may legitimately expect, so the qualified form passes through unchanged.
    config = _load(tmp_path, """\
model:
  default: cn/deepseek-v4-flash
  provider: china
providers:
  china:
    base_url: http://feiniu:3001/v1
    models:
      deepseek-v4-flash: {}
""")
    assert config["model"]["default"] == "cn/deepseek-v4-flash"


def test_full_registered_model_id_is_not_stripped(tmp_path):
    # The provider's model table registers the *qualified* id itself: that is the model's
    # real name on this endpoint, not a redundant route prefix.
    config = _load(tmp_path, """\
model:
  default: china/deepseek-v4-flash
  provider: china
providers:
  china:
    base_url: http://feiniu:3001/v1
    models:
      china/deepseek-v4-flash: {}
""")
    assert config["model"]["default"] == "china/deepseek-v4-flash"


def test_canonical_builtin_prefix_is_kept_even_when_shadowed(tmp_path):
    # ``nous`` is a canonical built-in id: the runtime defers to the built-in over a
    # same-named entry, and aggregator-facing providers accept slash-bearing ids
    # (mirrors runtime_provider_custom._shadowed_by_builtin).
    config = _load(tmp_path, """\
model:
  default: nous/research/some-model
  provider: nous
providers:
  nous:
    base_url: http://relay:3001/v1
    models:
      research/some-model: {}
""")
    assert config["model"]["default"] == "nous/research/some-model"


def test_legacy_custom_providers_name_strips(tmp_path):
    # Legacy ``custom_providers:`` list entries are addressed by their ``name:`` too.
    config = _load(tmp_path, """\
model:
  default: legacy-net/deepseek-v4-flash
  provider: legacy-net
custom_providers:
  - name: legacy-net
    base_url: http://relay:3001/v1
    model: deepseek-v4-flash
""")
    assert config["model"]["default"] == "deepseek-v4-flash"


def test_models_list_form_strips(tmp_path):
    # ``models`` may be a list of ids (or {id, name} dicts) rather than a mapping.
    config = _load(tmp_path, """\
model:
  default: china/deepseek-v4-flash
  provider: china
providers:
  china:
    base_url: http://feiniu:3001/v1
    models:
      - deepseek-v4-flash
""")
    assert config["model"]["default"] == "deepseek-v4-flash"


def test_aggregator_style_default_without_custom_entry_untouched(tmp_path):
    # No ``providers:`` section at all: behaviour is unchanged for every existing config.
    config = _load(tmp_path, """\
model:
  default: anthropic/claude-opus-4-6
  provider: openrouter
""")
    assert config["model"]["default"] == "anthropic/claude-opus-4-6"


def test_empty_prefix_or_model_part_is_untouched(tmp_path):
    config = _load(tmp_path, """\
model:
  default: /deepseek-v4-flash
  provider: china
providers:
  china:
    base_url: http://feiniu:3001/v1
""")
    assert config["model"]["default"] == "/deepseek-v4-flash"
    config = _load(tmp_path, """\
model:
  default: china/
  provider: china
providers:
  china:
    base_url: http://feiniu:3001/v1
""")
    assert config["model"]["default"] == "china/"
