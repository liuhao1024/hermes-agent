"""The model-facing `computer_use` schema must stay backend-neutral.

`COMPUTER_USE_SCHEMA` is sent to the model every turn regardless of which
`ComputerUseProvider` backend is selected, so it must not name a specific
implementation (cua-driver) or promise backend-specific capabilities (#145883).
"""

import json

from tools.computer_use.schema import COMPUTER_USE_SCHEMA


def _schema_text() -> str:
    return json.dumps(COMPUTER_USE_SCHEMA)


def test_schema_does_not_name_a_specific_backend():
    assert "cua-driver" not in _schema_text()
    assert "cua_driver" not in _schema_text()


def test_schema_refers_to_configured_backend():
    text = _schema_text()
    assert "computer-use backend" in text
    # The window_id and bring_to_front property descriptions previously named
    # cua-driver; they should now speak of the backend generically.
    assert (
        "backend"
        in COMPUTER_USE_SCHEMA["parameters"]["properties"]["window_id"]["description"]
    )
    assert (
        "backend"
        in COMPUTER_USE_SCHEMA["parameters"]["properties"]["bring_to_front"][
            "description"
        ]
    )
