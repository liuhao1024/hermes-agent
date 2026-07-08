"""Tests for _is_ollama_glm_backend stop misreport detection.

Covers run_agent.py _is_ollama_glm_backend() exclusion of Ollama Cloud.

Issue: #60928 - Ollama Cloud (ollama.com) triggers false-positive truncation.
"""


def test_ollama_cloud_excluded_from_glm_stop_misreport():
    """Ollama Cloud reports finish_reason correctly and should be excluded.

    Ollama Cloud (https://ollama.com/v1) is not affected by the stop misreport
    issue that affects local Ollama instances hosting GLM models. Excluding
    it prevents false-positive truncation continuations that corrupt MEDIA/voice
    tags when concatenated without separators.
    """
    from run_agent import AIAgent
    from unittest.mock import MagicMock

    # Mock a minimal AIAgent instance
    agent = MagicMock(spec=AIAgent)
    agent._base_url_lower = "https://ollama.com/v1"
    agent.provider = "ollama"
    agent.model = "llama3.1"

    # Bind the actual method to the mock
    agent._is_ollama_glm_backend = AIAgent._is_ollama_glm_backend.__get__(agent, AIAgent)

    # Ollama Cloud should NOT be detected as a GLM backend
    assert not agent._is_ollama_glm_backend()


def test_local_ollama_glm_detected_correctly():
    """Local Ollama instances hosting GLM models should still be detected.

    The original #13971 fix must continue working for affected backends.
    """
    from run_agent import AIAgent
    from unittest.mock import MagicMock

    agent = MagicMock(spec=AIAgent)
    agent._base_url_lower = "http://localhost:11434/v1"
    agent.provider = "zai"
    agent.model = "glm-4"

    agent._is_ollama_glm_backend = AIAgent._is_ollama_glm_backend.__get__(agent, AIAgent)

    # Local Ollama with GLM should be detected
    assert agent._is_ollama_glm_backend()


def test_ollama_local_url_detected_correctly():
    """Ollama URLs with 'ollama' in the path/host should be detected when appropriate."""
    from run_agent import AIAgent
    from unittest.mock import MagicMock

    # Case 1: ollama.local with GLM
    agent = MagicMock(spec=AIAgent)
    agent._base_url_lower = "http://ollama.local:11434/v1"
    agent.provider = "zai"
    agent.model = "glm-4"
    agent._is_ollama_glm_backend = AIAgent._is_ollama_glm_backend.__get__(agent, AIAgent)
    assert agent._is_ollama_glm_backend()

    # Case 2: /ollama/ path with GLM
    agent._base_url_lower = "https://proxy.example.com/ollama/v1"
    assert agent._is_ollama_glm_backend()


def test_non_glm_models_excluded():
    """Non-GLM models on Ollama backends should not trigger the misreport guard."""
    from run_agent import AIAgent
    from unittest.mock import MagicMock

    agent = MagicMock(spec=AIAgent)
    agent._base_url_lower = "http://localhost:11434/v1"
    agent.provider = "ollama"
    agent.model = "llama3.1"

    agent._is_ollama_glm_backend = AIAgent._is_ollama_glm_backend.__get__(agent, AIAgent)

    # Non-GLM model on local Ollama should NOT trigger
    assert not agent._is_ollama_glm_backend()