## What does this PR do?

Fixes a resource leak in the gateway's agent cache soft eviction path. When a cached AIAgent instance is softly evicted (LRU cap or idle TTL), the agent's LLM client is closed but the memory provider remains running. If the session later resumes, a fresh agent and memory provider are created while the old provider continues running, accumulating leaked threads, file descriptors, and HTTP connections.

## Related Issue

Fixes #64278

## Type of Change

- [x] 🐛 Bug fix

## Changes Made

- `gateway/run.py`: Added `shutdown_memory_provider()` call to `_release_evicted_agent_soft()`. The evicted agent's memory provider is now properly shut down to prevent resource leaks. The provider will be recreated when the session resumes, maintaining session tool state (terminal, browser, background processes).

## How to Test

1. Configure Hermes gateway with the Hindsight memory provider.
2. Process a turn to trigger provider initialization.
3. Trigger agent cache eviction (wait for idle TTL or fill LRU cap).
4. Verify the evicted agent's memory provider is shut down (writer thread stopped, HTTP client closed).
5. Resume the same session to verify a fresh provider is created and works correctly.

To verify without running gateway:
```bash
# Test the soft eviction path with a mock agent
python3 << 'EOF'
from unittest.mock import MagicMock
from gateway.run import GatewayRunner

agent = MagicMock()
agent._memory_manager = MagicMock()
agent._session_messages = ["msg1", "msg2"]
shutdown_called = []

def mock_shutdown(messages=None):
    shutdown_called.append(True)
    agent._memory_manager.shutdown_all()

agent.shutdown_memory_provider = mock_shutdown

gw = GatewayRunner.__new__(GatewayRunner)
gw._release_evicted_agent_soft(agent)

assert len(shutdown_called) == 1, "shutdown_memory_provider should be called"
assert agent._session_messages == [], "_session_messages should be cleared"
print("✓ Test passed: shutdown_memory_provider is called during soft eviction")
EOF
```

Expected output: `✓ Test passed: shutdown_memory_provider is called during soft eviction`

## Checklist

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] The code follows the project's style guidelines
- [x] The code compiles without errors
- [x] The code passes existing tests
- [x] The code is documented
- [x] The code is tested on the local platform (macOS)