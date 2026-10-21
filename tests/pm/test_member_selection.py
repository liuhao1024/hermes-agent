"""The workspace and admission paths agree on scoped keys and disabled plugins."""
from pm.workspace import enabled_member_dirs


def test_scoped_members_respect_disabled_and_keep_other_profiles(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    current = home / "plugins" / "group" / "plugin"
    other_home = home / "profiles" / "other"
    other = other_home / "plugins" / "same"
    for member in (current, other):
        member.mkdir(parents=True)
        (member / "pyproject.toml").write_text('[project]\nname="test"\nversion="1"\n')
    (home / "config.yaml").write_text('plugins:\n  enabled: [group/plugin]\n  disabled: []\n')
    (other_home / "config.yaml").write_text('plugins:\n  enabled: [same]\n')
    assert set(enabled_member_dirs()) == {current, other}
    (home / "config.yaml").write_text('plugins:\n  enabled: [group/plugin]\n  disabled: [group/plugin]\n')
    assert enabled_member_dirs() == [other]


def test_manifest_only_member_is_named_after_its_plugin_dir_and_stays_unique(tmp_path):
    """uv's conflict text names the workspace member (``hermes-plugin-<key> depends on …``);
    a bare path hash left the user with nothing to disable. Two same-named plugins from
    different homes must still be distinct members."""
    import tomllib
    from pm.workspace import _workspace_member

    members = []
    for home in ("home-a", "home-b"):
        plugin = tmp_path / home / "plugins" / "My Plugin!"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: my-plugin\npip_dependencies: [left-pad-py]\n", encoding="utf-8")
        root = tmp_path / f"gen-{home}"
        root.mkdir()
        members.append(_workspace_member(plugin, root, identity=plugin))
    names = [tomllib.loads((m / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"] for m in members]
    assert all(name.startswith("hermes-plugin-my-plugin-") for name in names), names
    assert names[0] != names[1]
    assert members[0].name != members[1].name


def test_pyproject_member_is_renamed_by_its_key_and_stays_unique(tmp_path):
    """A plugin that ships its own pyproject declares its real [project].name, and
    uv identifies workspace members by that name — enabling the plugin in two
    profiles then fails ``uv lock`` with "Two workspace members are both named
    …". Rename metadata-only members by their unique key, like manifest-only
    ones; a buildable member keeps the name its package metadata reports."""
    import tomllib
    from pm.workspace import _workspace_member

    members = []
    for home in ("home-a", "home-b"):
        plugin = tmp_path / home / "plugins" / "hindsight"
        plugin.mkdir(parents=True)
        (plugin / "pyproject.toml").write_text(
            '[project]\nname = "hermes-plugin-hindsight"\nversion = "1.0.0"\n'
            'dependencies = ["hindsight-client>=0.10.1"]\n',
            encoding="utf-8",
        )
        root = tmp_path / f"gen-{home}"
        root.mkdir()
        members.append(_workspace_member(plugin, root, identity=plugin))
    names = [tomllib.loads((m / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"] for m in members]
    assert all(name.startswith("hermes-plugin-hindsight-") for name in names), names
    assert names[0] != names[1]
    document = tomllib.loads((members[0] / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["project"]["version"] == "1.0.0"
    assert document["project"]["dependencies"] == ["hindsight-client>=0.10.1"]


def test_buildable_pyproject_member_keeps_its_declared_name(tmp_path):
    """uv verifies a buildable member's [project].name against the package metadata
    its backend produces, so renaming it breaks the build ("Package metadata name
    … does not match given name"); only metadata-only members may be renamed."""
    import tomllib
    from pm.workspace import _workspace_member

    plugin = tmp_path / "home" / "plugins" / "replay"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "replay-plugin"\nversion = "1.0"\n'
        '[build-system]\nrequires = []\nbuild-backend = "backend"\n',
        encoding="utf-8",
    )
    root = tmp_path / "gen"
    root.mkdir()
    member = _workspace_member(plugin, root, identity=plugin)
    assert (member / "pyproject.toml").read_text(encoding="utf-8") == (
        plugin / "pyproject.toml").read_text(encoding="utf-8")


def test_virtual_member_without_project_metadata_gets_a_version(tmp_path):
    """A metadata-only member whose pyproject declares no [project] (lint config
    only) is emitted as ``[project] name = …``; uv rejects a [project] table
    without ``version`` (or ``dynamic``) at ``uv lock`` time, aborting the whole
    update. Generated virtual members carry the same inert version plugin-deps
    use."""
    import tomllib
    from pm.workspace import _workspace_member

    plugin = tmp_path / "home" / "plugins" / "hermes-lcm"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text('[tool.ruff]\ntarget-version = "py311"\n', encoding="utf-8")
    root = tmp_path / "gen"
    root.mkdir()
    member = _workspace_member(plugin, root, identity=plugin)
    project = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == "0.0.0"


def test_virtual_member_declaring_dynamic_version_is_left_alone(tmp_path):
    """PEP 621 forbids supplying ``project.version`` while it is listed under
    ``project.dynamic`` ("You cannot provide a value for project.version and
    list it under project.dynamic at the same time"). A metadata-only member
    may legitimately declare ``dynamic = ["version"]``; the generated member
    must not gain a static version in that case, or ``uv lock`` rejects the
    conflicted manifest."""
    import tomllib
    from pm.workspace import _workspace_member

    plugin = tmp_path / "home" / "plugins" / "dynver"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "dynver"\ndynamic = ["version"]\n', encoding="utf-8"
    )
    root = tmp_path / "gen"
    root.mkdir()
    member = _workspace_member(plugin, root, identity=plugin)
    project = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "version" not in project
    assert project["dynamic"] == ["version"]


def test_buildable_member_without_a_version_gets_one_and_keeps_its_name(tmp_path):
    """uv rejects a [project] table with no version at parse time, buildable or not.
    A buildable member still keeps its declared name (uv checks it against its backend)."""
    import tomllib
    from pm.workspace import _workspace_member

    plugin = tmp_path / "home" / "plugins" / "noversion"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "noversion-plugin"\n'
        '[build-system]\nrequires = []\nbuild-backend = "backend"\n',
        encoding="utf-8",
    )
    root = tmp_path / "gen"
    root.mkdir()
    member = _workspace_member(plugin, root, identity=plugin)
    project = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project == {"name": "noversion-plugin", "version": "0.0.0"}


def test_duplicate_buildable_names_across_homes_are_demoted_to_unique_members(tmp_path):
    """The same buildable plugin installed in the shared home and a profile home
    declares one [project].name twice; uv aborts the lock ("Two workspace members
    are both named …") and every profile sharing the environment loses its plugins.
    Demote the colliding members to metadata-only ones named by their unique key —
    the snapshot is loaded by path, so nothing loses the build (#142712)."""
    import tomllib

    from pm.workspace import _duplicate_member_names, _workspace_member

    plugins = []
    for home in ("home-a", "home-b"):
        plugin = tmp_path / home / "plugins" / "replay"
        plugin.mkdir(parents=True)
        (plugin / "pyproject.toml").write_text(
            '[project]\nname = "replay-plugin"\nversion = "1.0"\n'
            '[build-system]\nrequires = []\nbuild-backend = "backend"\n',
            encoding="utf-8",
        )
        plugins.append(plugin)
    duplicates = _duplicate_member_names([(plugin, plugin) for plugin in plugins])
    assert duplicates == frozenset({"replay-plugin"})

    root = tmp_path / "gen"
    root.mkdir()
    members = [_workspace_member(plugin, root, identity=plugin, duplicate_names=duplicates)
               for plugin in plugins]
    names = [tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
             for member in members]
    assert all(name.startswith("hermes-plugin-replay-") for name in names), names
    assert names[0] != names[1]
    for member in members:
        document = tomllib.loads((member / "pyproject.toml").read_text(encoding="utf-8"))
        assert document["tool"]["uv"]["package"] is False


def test_duplicate_scan_ignores_metadata_only_and_lone_buildable_members(tmp_path):
    """Only buildable members can collide: a metadata-only member is born renamed to
    its unique key, and a single buildable one owns its declared name."""
    from pm.workspace import _duplicate_member_names

    virtual = tmp_path / "home" / "plugins" / "hindsight"
    virtual.mkdir(parents=True)
    (virtual / "pyproject.toml").write_text(
        '[project]\nname = "shared-name"\nversion = "1"\ndependencies = []\n', encoding="utf-8")
    buildable = tmp_path / "home" / "profiles" / "p" / "plugins" / "replay"
    buildable.mkdir(parents=True)
    (buildable / "pyproject.toml").write_text(
        '[project]\nname = "lone-plugin"\nversion = "1"\n'
        '[build-system]\nrequires = []\nbuild-backend = "backend"\n', encoding="utf-8")
    assert _duplicate_member_names([(virtual, virtual), (buildable, buildable)]) == frozenset()


def test_generate_pyproject_demotes_duplicate_buildable_names(tmp_path):
    """The generation path itself must demote the colliding copies, or the shared
    workspace root still names two members alike and `uv lock` exits 2."""
    import tomllib

    from pm.workspace import _generate_pyproject

    core = tmp_path / "core"
    core.mkdir()
    (core / "pyproject.toml").write_text(
        '[project]\nname = "hermes-agent"\nversion = "1.0"\n', encoding="utf-8")
    plugins = []
    for home in ("home-a", "home-b"):
        plugin = tmp_path / home / "plugins" / "replay"
        plugin.mkdir(parents=True)
        (plugin / "pyproject.toml").write_text(
            '[project]\nname = "replay-plugin"\nversion = "1.0"\n'
            '[build-system]\nrequires = []\nbuild-backend = "backend"\n', encoding="utf-8")
        plugins.append(plugin)

    root = tmp_path / "gen"
    _generate_pyproject(plugins, root, source=core)
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    members = sorted(document["tool"]["uv"]["workspace"]["members"])
    assert len(members) == 2
    assert all(member.startswith("plugin-sources/replay-") for member in members), members
    names = []
    for member in members:
        member_document = tomllib.loads((root / member / "pyproject.toml").read_text(encoding="utf-8"))
        assert member_document["project"]["name"].startswith("hermes-plugin-replay-")
        assert member_document["tool"]["uv"]["package"] is False
        names.append(member_document["project"]["name"])
    assert names[0] != names[1]
