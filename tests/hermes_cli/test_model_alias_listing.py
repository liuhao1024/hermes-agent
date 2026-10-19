"""``hermes model aliases`` — the alias discovery surface (#141409).

Both alias tables were write-only: resolution consulted them, but nothing
printed them, so a user who half-remembered an alias had to read config.yaml
or the source to find out what exists. ``format_model_alias_listing`` renders
both tables and ``cmd_model`` routes the new ``aliases`` subcommand to it
before the TTY guard (enumerating aliases is read-only and is exactly what a
piped caller needs).
"""

import argparse
from types import SimpleNamespace

from hermes_cli import main as hermes_main
from hermes_cli import model_switch
from hermes_cli.model_switch import DirectAlias, format_model_alias_listing


USER_ALIASES = {
    "kabeuchi": DirectAlias(
        model="ling-3.1-flash", provider="openrouter", base_url=""),
    # Same name as a built-in: user aliases win during resolution, so the
    # built-in row must be marked shadowed.
    "kimi": DirectAlias(
        model="kimi-k2", provider="moonshotai", base_url=""),
    "theta": DirectAlias(
        model="theta-1", provider="custom", base_url="https://theta.example.com/v1"),
}


class TestFormatModelAliasListing:
    def test_builtin_table_lists_every_short_name_with_vendor_and_family(self):
        out = format_model_alias_listing(direct_aliases={})
        assert "Built-in short names (catalog-resolved):" in out
        for name, identity in model_switch.MODEL_ALIASES.items():
            assert f"{name}" in out
            assert f"{identity.vendor}/{identity.family}" in out

    def test_user_aliases_list_with_provider_and_model(self):
        out = format_model_alias_listing(direct_aliases=USER_ALIASES)
        assert "User-defined aliases (model_aliases: / model.aliases: in config.yaml):" in out
        assert "kabeuchi" in out
        assert "openrouter/ling-3.1-flash" in out

    def test_base_url_carrying_alias_shows_its_endpoint(self):
        out = format_model_alias_listing(direct_aliases=USER_ALIASES)
        assert "custom/theta-1" in out
        assert "https://theta.example.com/v1" in out

    def test_builtin_shadowed_by_user_alias_is_marked(self):
        out = format_model_alias_listing(direct_aliases=USER_ALIASES)
        shadowed_lines = [ln for ln in out.splitlines() if "shadowed by your alias" in ln]
        assert len(shadowed_lines) == 1
        assert "kimi" in shadowed_lines[0]

    def test_no_shadow_marker_without_user_aliases(self):
        out = format_model_alias_listing(direct_aliases={})
        assert "shadowed" not in out

    def test_empty_user_table_prints_the_config_pointer(self):
        out = format_model_alias_listing(direct_aliases={})
        assert "No user-defined aliases configured" in out
        assert "model_aliases:" in out


class TestAliasesSubcommandWiring:
    def test_parser_routes_the_aliases_subcommand(self):
        parser = argparse.ArgumentParser(prog="hermes")
        subparsers = parser.add_subparsers(dest="command")
        hermes_main.build_model_parser(subparsers, cmd_model=hermes_main.cmd_model)
        args = parser.parse_args(["model", "aliases"])
        assert args.model_command == "aliases"
        assert args.func is hermes_main.cmd_model

    def test_plain_model_invocation_keeps_the_interactive_default(self):
        parser = argparse.ArgumentParser(prog="hermes")
        subparsers = parser.add_subparsers(dest="command")
        hermes_main.build_model_parser(subparsers, cmd_model=hermes_main.cmd_model)
        args = parser.parse_args(["model", "--refresh"])
        assert getattr(args, "model_command", None) is None
        assert args.func is hermes_main.cmd_model
        assert args.refresh is True

    def test_cmd_model_prints_listing_before_the_tty_guard(self, monkeypatch, capsys):
        # stdin under pytest is not a TTY: reaching the listing proves the
        # aliases branch runs before _require_tty (which would sys.exit(1)).
        monkeypatch.setattr(model_switch, "_load_direct_aliases", lambda cfg=None: dict(USER_ALIASES))
        hermes_main.cmd_model(SimpleNamespace(model_command="aliases", refresh=False))
        out = capsys.readouterr().out
        assert "Built-in short names" in out
        assert "openrouter/ling-3.1-flash" in out
        assert "shadowed by your alias" in out
