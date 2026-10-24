"""Display-time redaction for ``hermes gateway status`` output.

systemctl's status rendering prints the unit's whole CGroup tree with every child's full
command line, so an MCP server launched with a credential argument (``--api-key <key>``)
would surface the live key in shared status output (#143938). Lives in a topical sibling
of the gateway CLI facade: the facade file is over its line cap and may only shrink.
"""

import re

# Flag names are an exact list so neighbours like ``--token-count`` / ``--api-key-file``
# stay intact; the operand is masked whether it is space- or ``=``-separated, quoted or bare.
_CREDENTIAL_FLAG_OPERAND_RE = re.compile(
    r"""(?<![\w-])(--(?:api-key|api_key|apikey|token|secret|password|passwd))(=|[ \t]+)"""
    r"""("[^"\n]*"|'[^'\n]*'|[^\s"']+)""",
    re.IGNORECASE,
)


def mask_credential_flags(text: str) -> str:
    """Replace a credential flag's operand with ``***`` before display."""
    return _CREDENTIAL_FLAG_OPERAND_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
