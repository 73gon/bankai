"""CLI integration smoke tests using Typer's CliRunner.

These exercise the help text, sub-app routing, and the ``jobs list``
command against an isolated SQLite DB. Heavy commands (``run``,
``extract``, ``daemon``, ``tui``) are exercised at the help-only level
to confirm import/argument wiring; their actual behavior is tested via
the unit tests for each worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bankai.cli.main import app, config_set
from bankai.config import get_settings, load_settings, reset_settings_cache, user_config_path
from bankai.db import StateRepository
from bankai.queue.models import Job, JobKind, JobStatus


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point all paths at tmp dirs so we never hit /config or /work."""
    monkeypatch.setenv("BANKAI_PATHS__STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BANKAI_PATHS__WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("BANKAI_PATHS__DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "library"))
    reset_settings_cache()
    # Touch settings to materialize.
    _ = get_settings()


def test_root_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Extract German dub audio" in result.stdout
    assert "search" in result.stdout
    assert "remux" in result.stdout
    assert "daemon" in result.stdout


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "bankai" in result.stdout


def test_jobs_subcommand_help() -> None:
    result = CliRunner().invoke(app, ["jobs", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "retry" in result.stdout
    assert "clear" in result.stdout


def test_jobs_list_empty() -> None:
    result = CliRunner().invoke(app, ["jobs", "list"])
    assert result.exit_code == 0
    assert "Jobs" in result.stdout


def test_jobs_show_missing() -> None:
    result = CliRunner().invoke(app, ["jobs", "show", "9999"])
    assert result.exit_code == 1
    assert "no such job" in result.stdout


def test_jobs_clear_defaults_to_finished_statuses() -> None:
    settings = get_settings()
    with StateRepository(settings.paths.state_db) as repo:
        done = repo.create_job(Job(kind=JobKind.PIPELINE))
        queued = repo.create_job(Job(kind=JobKind.PIPELINE))
        failed = repo.create_job(Job(kind=JobKind.PIPELINE))
        assert done.id is not None
        assert queued.id is not None
        assert failed.id is not None
        repo.complete_job(done.id)
        repo.fail_job(failed.id, "boom", retry=False)

    result = CliRunner().invoke(app, ["jobs", "clear"])
    assert result.exit_code == 0
    assert "cleared" in result.stdout

    with StateRepository(settings.paths.state_db) as repo:
        assert repo.get_job(done.id) is None
        assert repo.get_job(failed.id) is None
        assert repo.get_job(queued.id) is not None
        assert repo.get_job(queued.id).status is JobStatus.QUEUED  # type: ignore[union-attr]


def test_history_command() -> None:
    result = CliRunner().invoke(app, ["history"])
    assert result.exit_code == 0
    assert "History" in result.stdout


def test_config_set_direct_call_uses_default_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    reset_settings_cache()

    config_set("scraper.interactive_pick", "true")

    assert user_config_path().exists()
    assert load_settings(user_config_path()).scraper.interactive_pick is True
