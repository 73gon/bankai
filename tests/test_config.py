"""Smoke tests for the configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.config import Settings, load_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_settings_cache()


def test_defaults_load_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BANKAI_CONFIG", raising=False)
    settings = load_settings()
    assert isinstance(settings, Settings)
    assert settings.audio.codec == "copy"
    assert settings.queue.remux_workers == 1


def test_toml_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
        [audio]
        codec = "eac3"
        track_name = "German"

        [queue]
        extract_workers = 7
        """,
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.audio.codec == "eac3"
    assert settings.audio.track_name == "German"
    assert settings.queue.extract_workers == 7


def test_env_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[audio]\ncodec = "ac3"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BANKAI_AUDIO__CODEC", "eac3")
    settings = load_settings()
    assert settings.audio.codec == "eac3"
