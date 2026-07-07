"""Regression tests for the ``hermes --yolo`` CLI flag timing fix.

Pre-fix bug (issue #60328): ``HERMES_YOLO_MODE`` was set inside ``cmd_chat()``,
but ``_prepare_agent_startup()`` was called earlier from ``main()``. This created
an import-time race condition:

    main()
      args = parser.parse_args(...)
      │
      ├── _prepare_agent_startup(args)              ← called at ~line 14119
      │     discover_plugins()
      │       from tools.terminal_tool import ...
      │         from tools.approval import ...
      │           _YOLO_MODE_FROZEN = os.getenv("HERMES_YOLO_MODE")  ← STILL EMPTY
      │
      └── cmd_chat(args)                            ← called at ~line 14149
            os.environ["HERMES_YOLO_MODE"] = "1"    ← TOO LATE

Once frozen, ``_YOLO_MODE_FROZEN`` never re-reads the env var, so ``--yolo``
was silently ignored and approval prompts appeared despite the flag.

The fix moves the env var assignment from ``cmd_chat()`` to ``main()``,
between ``parse_args()`` and ``_prepare_agent_startup()``. This ensures
the env var is set before the first import of ``tools.approval``.

These tests verify that the frozen constant is set from the env var at
module import time and never changes afterward.
"""
import os
import sys


def test_yolo_frozen_constant_set_at_import_time():
    """Verify that ``_YOLO_MODE_FROZEN`` is set from env var at import time.

    This confirms the import-time freeze design:
    - The constant is set once when the module is first imported
    - It reads the current value of ``HERMES_YOLO_MODE`` env var
    - It does NOT change if the env var changes after import
    """
    # Ensure we start with a clean state
    if "tools.approval" in sys.modules:
        del sys.modules["tools.approval"]

    # Import without env var set
    os.environ.pop("HERMES_YOLO_MODE", None)
    from tools.approval import _YOLO_MODE_FROZEN

    frozen_without_env = _YOLO_MODE_FROZEN
    assert frozen_without_env is False, (
        "Without HERMES_YOLO_MODE env var, _YOLO_MODE_FROZEN should be False"
    )

    # Set env var AFTER import
    os.environ["HERMES_YOLO_MODE"] = "1"

    # The frozen constant should NOT have changed — it was captured at import time
    # We read it again to confirm (not reimport)
    from tools.approval import _YOLO_MODE_FROZEN as frozen_still_same

    assert frozen_still_same is False, (
        "Frozen constant should not change after env var is set post-import"
    )
    assert frozen_still_same == frozen_without_env, (
        "Frozen constant should remain unchanged after env var mutation"
    )


def test_yolo_frozen_constant_set_correctly_with_env_var():
    """Verify that setting ``HERMES_YOLO_MODE`` before import makes frozen True."""
    # Ensure we start with a clean state
    if "tools.approval" in sys.modules:
        del sys.modules["tools.approval"]

    # Set env var BEFORE import
    os.environ["HERMES_YOLO_MODE"] = "1"
    from tools.approval import _YOLO_MODE_FROZEN

    assert _YOLO_MODE_FROZEN is True, (
        "With HERMES_YOLO_MODE='1' set before import, "
        "_YOLO_MODE_FROZEN should be True"
    )

    # Clean up for other tests
    os.environ.pop("HERMES_YOLO_MODE", None)


def test_yolo_frozen_constant_uses_is_truthy_value():
    """Verify that ``_YOLO_MODE_FROZEN`` uses ``is_truthy_value()`` correctly.

    ``is_truthy_value()`` treats '1', 'true', 'yes' (case-insensitive) as truthy.
    """
    # Ensure we start with a clean state
    if "tools.approval" in sys.modules:
        del sys.modules["tools.approval"]

    from utils import is_truthy_value

    # Test that is_truthy_value works as expected
    assert is_truthy_value("1") is True
    assert is_truthy_value("true") is True
    assert is_truthy_value("True") is True
    assert is_truthy_value("yes") is True
    assert is_truthy_value("YES") is True
    assert is_truthy_value("0") is False
    assert is_truthy_value("") is False
    assert is_truthy_value("false") is False
    assert is_truthy_value("no") is False

    # Now test with actual env var
    os.environ["HERMES_YOLO_MODE"] = "1"
    from tools.approval import _YOLO_MODE_FROZEN as frozen_1

    assert frozen_1 is True

    # Clean up
    os.environ.pop("HERMES_YOLO_MODE", None)
    if "tools.approval" in sys.modules:
        del sys.modules["tools.approval"]

    # Test with another truthy value
    os.environ["HERMES_YOLO_MODE"] = "yes"
    from tools.approval import _YOLO_MODE_FROZEN as frozen_yes

    assert frozen_yes is True

    # Clean up
    os.environ.pop("HERMES_YOLO_MODE", None)