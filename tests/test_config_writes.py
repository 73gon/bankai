"""Config changes go to the file the settings actually came from."""

from __future__ import annotations

import tomllib

from bankai.cli.main import _set_config_value
from bankai.config import active_config_path, reset_settings_cache


def test_a_write_lands_in_the_working_directory_config_in_use(tmp_path, monkeypatch):
    """seireitei reads config.toml from the repo, with no user config.

    Writing to the user path created a file holding only the changed key,
    which then took precedence over the real config on the next start.
    """
    work = tmp_path / "repo"
    work.mkdir()
    (work / "config.toml").write_text('[qbittorrent]\nurl = "http://127.0.0.1:8080"\n', encoding="utf-8")
    monkeypatch.chdir(work)
    monkeypatch.delenv("BANKAI_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_settings_cache()

    assert active_config_path() == work / "config.toml"
    path, _ = _set_config_value("shoko.api_key", "secret")

    assert path == work / "config.toml"
    assert not (tmp_path / "xdg" / "bankai" / "config.toml").exists()
    written = tomllib.loads((work / "config.toml").read_text(encoding="utf-8"))
    # The existing settings survive beside the new one.
    assert written["qbittorrent"]["url"] == "http://127.0.0.1:8080"
    assert written["shoko"]["api_key"] == "secret"
    reset_settings_cache()


def test_with_no_config_anywhere_the_user_config_is_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BANKAI_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_settings_cache()

    assert active_config_path() == tmp_path / "xdg" / "bankai" / "config.toml"
    reset_settings_cache()
