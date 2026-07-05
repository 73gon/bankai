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


def test_sync_visual_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BANKAI_CONFIG", raising=False)
    sync = load_settings().sync
    assert sync.visual is True
    assert sync.visual_max_height == 480
    assert sync.visual_min_confidence == 0.6
    assert sync.visual_apply_drift is False


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


def test_metadata_settings_can_be_configured_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BANKAI_METADATA__TVDB_API_KEY", "secret")
    monkeypatch.setenv("BANKAI_METADATA__TVDB_ENABLED", "true")

    settings = load_settings()

    assert settings.metadata.tvdb_enabled is True
    assert settings.metadata.tvdb_api_key == "secret"
