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
