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


def test_anime_download_refuses_a_blacklisted_entry(monkeypatch) -> None:
    """However it was queued, nothing of a blacklisted entry is downloaded."""
    called: list = []

    async def fake_download(**kwargs):
        called.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("bankai.processor.anime.download_anime", fake_download)
    monkeypatch.setattr("bankai.web.erai._policy_anilist_ids", lambda: {112640})
    base = [
        "anime-download", "--release-title", "[Erai-raws] Utawarerumono - Futari no Hakuoro - 22 [1080p]",
        "--torrent-url", "https://nyaa.si/download/1.torrent", "--detail-url", "https://nyaa.si/view/1",
        "--magnet-uri", "magnet:?xt=urn:btih:" + "a" * 40, "--info-hash", "a" * 40,
        "--kind", "show", "--english-title", "Utawarerumono", "--episode", "22",
    ]
    result = runner.invoke(app, [*base, "--anilist-id", "112640"])
    assert result.exit_code == 1 and "Series blacklisted" in result.stdout and not called
    # One magnet over several entries: blocked when any of them is.
    assert runner.invoke(app, [*base, "--anilist-id", "1", "--episode-map", "22=112640:22"]).exit_code == 1
    assert runner.invoke(app, [*base, "--anilist-id", "1"]).exit_code == 0 and called
