"""``hermes doctor`` names cache-root dirs that no pruner covers once they are big enough."""

from hermes_cli import doctor_state
from hermes_cli.doctor_state import unpruned_cache_hogs


def test_unpruned_cache_hogs_skips_pruned_dirs_and_small_entries(tmp_path):
    cache = tmp_path / "cache"
    for name in ("scratch", "terminal", "campaign-x", "web"):
        (cache / name).mkdir(parents=True)
        with open(cache / name / "blob", "wb") as fh:
            fh.truncate(2048)
            fh.write(b"x" * 2048)
    hogs = unpruned_cache_hogs(tmp_path, min_bytes=1024)
    assert {name for name, _ in hogs} == {"campaign-x", "web"}
    assert not unpruned_cache_hogs(tmp_path, min_bytes=1 << 20)
    assert all(size >= 2048 for _, size in hogs)


def test_pm_owned_cache_dirs_get_their_own_advice(tmp_path, monkeypatch, capsys):
    """#143577: uv/ and partials/ are pm-owned, so their hog warning says what they are instead
    of telling the user to move task files under scratch/ (advice that cannot apply to them)."""
    cache = tmp_path / "cache"
    for name in ("uv", "partials", "campaign-x"):
        (cache / name).mkdir(parents=True)
        # Sparse files: st_size crosses doctor's 1 GiB hog threshold without occupying disk.
        with open(cache / name / "blob", "wb") as fh:
            fh.truncate((1 << 30) + 1)

    warns: list[tuple[str, str]] = []
    monkeypatch.setattr(doctor_state, "check_warn", lambda text, detail="": warns.append((text, detail)))
    doctor_state._check_scratch_dir(tmp_path, "~/.hermes")
    capsys.readouterr()  # the scratch-dir and TMPDIR lines still print

    def warn_for(dir_name):
        assert any(f"/cache/{dir_name}/" in text for text, _ in warns), warns
        return next((text, detail) for text, detail in warns if f"/cache/{dir_name}/" in text)

    uv_text, uv_detail = warn_for("uv")
    assert "pm's shared uv cache" in uv_detail
    assert "move task files" not in uv_text and "move task files" not in uv_detail

    partials_text, partials_detail = warn_for("partials")
    assert "pm's download partials" in partials_detail
    assert "move task files" not in partials_text and "move task files" not in partials_detail

    campaign_text, _campaign_detail = warn_for("campaign-x")
    assert "move task files" in campaign_text  # the generic advice is unchanged
    assert "outside every pruner" in campaign_text
