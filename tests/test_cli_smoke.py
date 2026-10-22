"""Smoke tests for the CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from bankai import __version__
from bankai.cli.main import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "daemon", "shell"):
        assert command in result.stdout


def test_anime_download_passes_publish_on(monkeypatch) -> None:
    """--publish takes a hand-picked episode straight into the library, as the automation's go."""
    seen: dict = {}

    async def fake_download(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("bankai.processor.anime.download_anime", fake_download)
    base = [
        "anime-download", "--release-title", "[Erai-raws] Show - 09 [1080p]",
        "--torrent-url", "https://nyaa.si/download/1.torrent", "--detail-url", "https://nyaa.si/view/1",
        "--magnet-uri", "magnet:?xt=urn:btih:" + "a" * 40, "--info-hash", "a" * 40,
        "--kind", "show", "--anilist-id", "1", "--english-title", "Show", "--episode", "9",
    ]
    assert runner.invoke(app, [*base, "--publish"]).exit_code == 0
    assert seen["publish"] is True
    assert runner.invoke(app, base).exit_code == 0
    assert seen["publish"] is False
