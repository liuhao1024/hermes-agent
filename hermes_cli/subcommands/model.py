"""``hermes model`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_model_parser(subparsers, *, cmd_model: Callable) -> None:
    """Attach the ``model`` subcommand to ``subparsers``."""
    model_parser = subparsers.add_parser(
        "model", help="Select default model and provider",
        description="Interactively select your inference provider and default model")
    model_parser.add_argument(
        "--refresh", action="store_true",
        help="Wipe the model picker disk cache and re-fetch every provider's live /v1/models list.")
    model_parser.add_argument(
        "--portal-url", help="Portal base URL for Nous login (default: production portal)")
    model_parser.add_argument(
        "--inference-url",
        help="Inference API base URL for Nous login (default: production inference API)")
    model_parser.add_argument(
        "--client-id", default=None,
        help="OAuth client id to use for Nous login (default: hermes-cli)")
    model_parser.add_argument("--scope", default=None, help="OAuth scope to request for Nous login")
    model_parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not attempt to open the browser automatically during Nous login")
    model_parser.add_argument(
        "--timeout", type=float, default=15.0,
        help="HTTP request timeout in seconds for Nous login (default: 15)")
    model_parser.add_argument(
        "--ca-bundle", help="Path to CA bundle PEM file for Nous TLS verification")
    model_parser.add_argument(
        "--insecure", action="store_true",
        help="Disable TLS verification for Nous login (testing only)")
    model_subparsers = model_parser.add_subparsers(dest="model_command", metavar="<subcommand>")
    aliases_parser = model_subparsers.add_parser(
        "aliases", help="List built-in short names and user-defined model aliases",
        description="Print every model alias that -m/--model and /model can resolve: the "
        "built-in short names (sonnet, kimi, glm, …) with the vendor/family each resolves to, "
        "plus your model_aliases: / model.aliases: entries with the provider and model they "
        "point at. Built-ins shadowed by a user alias of the same name are marked.")
    aliases_parser.set_defaults(func=cmd_model)
    model_parser.set_defaults(func=cmd_model)
