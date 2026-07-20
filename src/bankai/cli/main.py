"""Bankai CLI \u2014 Typer entry point with interactive menu, config, doctor.

Top-level commands::

    bankai                     # interactive menu (search / run / queue / config)
    bankai shell               # REPL
    bankai run "Title Year"    # auto-search + pipeline (--url to bypass search)
    bankai search "Title"      # show matches, no download
    bankai shows "Show" -s 1   # whole season
    bankai config get/set/list/path/edit/init
    bankai doctor              # check ffmpeg, mkvmerge, alass, qbit, prowlarr
    bankai update              # update this install from git
    bankai jobs list/show/retry/cancel
    bankai history
    bankai daemon
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from bankai import __version__
from bankai.backend import (
    BatchMovie,
    TransferError,
    TransferKind,
    build_movie_args,
    format_transfer_summary,
    list_series_episodes,
    parse_movie_batch,
    plan_transfer,
    search_stream_sources,
    title_aliases,
    transfer_with_rsync,
)
from bankai.config import (
    get_settings,
    load_settings,
    reset_settings_cache,
    user_config_path,
)
from bankai.logging import configure_logging, get_logger
from bankai.metadata.tvdb import TitleAlias, get_title_aliases
from bankai.queue.models import MediaKind
from bankai.theme import make_console
from bankai.web.server import SERVICE_NAME

app = typer.Typer(
    name="bankai",
    help="Extract German dub audio from web streams + HQ video from torrents into one MKV.",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
)
jobs_app = typer.Typer(name="jobs", help="Inspect and manage queued jobs.", no_args_is_help=True)
config_app = typer.Typer(name="config", help="View/edit configuration.", no_args_is_help=True)
background_app = typer.Typer(name="background", help="Inspect detached background jobs.", no_args_is_help=True)
metadata_app = typer.Typer(name="metadata", help="Inspect metadata providers.", no_args_is_help=True)
web_app = typer.Typer(name="web", help="Run and manage the web UI.", no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")
app.add_typer(config_app, name="config")
app.add_typer(background_app, name="background")
app.add_typer(metadata_app, name="metadata")
app.add_typer(web_app, name="web")

console = make_console()
log = get_logger(__name__)


def _bool_text(value: bool) -> str:
    return "[green]True[/green]" if value else "[red]False[/red]"


def _format_value(value: Any, *, key: str | None = None) -> str:
    if key and _is_secret_key(key) and value:
        return "[dim]<set>[/dim]"
    if isinstance(value, bool):
        return _bool_text(value)
    if value is None:
        return "[dim]null[/dim]"
    if isinstance(value, int | float):
        return f"[cyan]{value}[/cyan]"
    if isinstance(value, list):
        if not value:
            return "[dim][][/dim]"
        return ", ".join(_format_value(v) for v in value)
    if isinstance(value, dict):
        if not value:
            return "[dim]{}[/dim]"
        return json.dumps(value, ensure_ascii=False)
    if value == "":
        return "[dim]<empty>[/dim]"
    return str(value)


def _plain_value(value: Any, *, key: str | None = None) -> str:
    if key and _is_secret_key(key) and value:
        return "<set>"
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "null"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if value == "":
        return "<empty>"
    return str(value)


def _is_secret_key(key: str) -> bool:
    return any(part in key.casefold() for part in ("password", "api_key", "pin", "webhook"))


def _flatten_settings(data: dict[str, Any], *, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(_flatten_settings(value, prefix=full_key))
        else:
            rows.append((full_key, value))
    return rows


def _status_color(status: str) -> str:
    return {
        "queued": "cyan",
        "running": "yellow",
        "done": "green",
        "failed": "red",
        "cancelled": "magenta",
    }.get(status, "white")


def _rich_status(status: str) -> str:
    color = _status_color(status)
    return f"[{color}]{status.upper()}[/{color}]"


_BANNER = r"""[bold magenta]
  ____    _    _   _ _  __    _    ___
 | __ )  / \  | \ | | |/ /   / \  |_ _|
 |  _ \ / _ \ |  \| | ' /   / _ \  | |
 | |_) / ___ \| |\  | . \  / ___ \ | |
 |____/_/   \_\_| \_|_|\_\/_/   \_\___|
[/bold magenta][dim]                                  v{ver}[/dim]"""


def _print_banner() -> None:
    console.print(_BANNER.format(ver=__version__))
    console.print()


def _ask_select(message: str, choices: list[str], default: str | None = None) -> str | None:
    """Arrow-key menu via questionary, with a number-prompt fallback.

    Returns the chosen string, or ``None`` on Ctrl-C.
    """
    try:
        import questionary
    except ImportError:
        # graceful degrade if dep missing (older installs)
        for i, c in enumerate(choices, 1):
            console.print(f"  [cyan]{i}[/cyan]  {c}")
        try:
            idx = IntPrompt.ask(message, default=1)
        except (KeyboardInterrupt, EOFError):
            return None
        if 1 <= idx <= len(choices):
            return choices[idx - 1]
        return None
    try:
        return cast(
            str | None,
            questionary.select(
                message,
                choices=choices,
                default=default or choices[0],
                qmark="\u276f",
                instruction="(\u2191/\u2193 to move, enter to pick)",
                use_indicator=False,
                use_arrow_keys=True,
                style=questionary.Style(
                    [
                        # Kill the inverted-background highlight on the focused
                        # row \u2014 we rely solely on the arrow pointer to show
                        # which line is selected.
                        ("highlighted", "noinherit"),
                        ("selected", "noinherit"),
                        ("pointer", "fg:ansimagenta bold"),
                        ("qmark", "fg:ansimagenta bold"),
                        ("question", "bold"),
                    ]
                ),
            ).unsafe_ask(),
        )
    except (KeyboardInterrupt, EOFError):
        return None


def _ask_table_select(
    message: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    back_label: str = "\u2190 Back",
) -> int | None:
    """Render an arrow-pickable table with box-drawing borders.

    Each ``rows[i]`` is a list of column strings (already coloured if
    desired). Returns the selected row index, or ``None`` on cancel.
    """
    if not rows:
        console.print("[dim](empty)[/dim]")
        return None
    # Compute column widths (visual width, ignoring ANSI markup).
    import re as _re

    def _vis(s: str) -> int:
        return len(_re.sub(r"\x1b\[[0-9;]*m", "", s))

    widths = [max(_vis(headers[i]), max(_vis(r[i]) for r in rows)) for i in range(len(headers))]

    def _row(parts: list[str]) -> str:
        cells = []
        for i, p in enumerate(parts):
            pad = widths[i] - _vis(p)
            cells.append(p + " " * pad)
        return "  ".join(cells)

    header_line = _row(headers)
    sep_line = "\u2500" * _vis(header_line)
    # questionary draws a 2-char left margin ("❯ " or "  ") in front
    # of every choice; pad the header by the same amount so columns line
    # up vertically with the rendered rows.
    console.print(f"\n  [bold magenta]{header_line}[/bold magenta]")
    console.print(f"  [dim]{sep_line}[/dim]")
    labels = [_row(r) for r in rows]
    labels.append(back_label)
    choice = _ask_select(message, labels)
    if choice is None or choice == back_label:
        return None
    return labels.index(choice)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"bankai {__version__}")
        raise typer.Exit()


@dataclass(frozen=True, slots=True)
class MediaIdentity:
    """Normalised result of an interactive TheTVDB lookup.

    ``original`` is whatever the user typed at the prompt; the other
    fields come from TheTVDB and may be empty when an alias is missing
    or when the user chose to keep their original query (no internet
    / TVDB key not configured).
    """

    original: str
    english: str | None = None
    german: str | None = None
    year: int | None = None
    tvdb_id: int | None = None
    kind: MediaKind | None = None


def _format_identity_row(alias: TitleAlias, fallback_query: str) -> list[str]:
    """Build a row for the TVDB picker table."""
    en = alias.english_title or alias.name or fallback_query
    de = alias.german_title or "[muted]\u2014[/muted]"
    year = str(alias.year) if alias.year else "[muted]?[/muted]"
    kind = alias.kind.value if alias.kind else "[muted]?[/muted]"
    return [en, de, year, kind]


def _identify_via_tvdb(query: str, *, kind: MediaKind) -> MediaIdentity | None:
    """Search TheTVDB and let the user pick the correct title.

    The picker shows English title, German title, year and kind. The
    chosen entry is converted into a :class:`MediaIdentity` so callers
    can pre-fill year / German alias without asking the user again.

    Returns ``None`` if the user cancels. If TVDB is unreachable or no
    matches are found the function falls back to a single
    ``MediaIdentity(original=query)`` so the rest of the flow still
    works.
    """
    try:
        results: list[TitleAlias] = asyncio.run(get_title_aliases(query, kind=kind))
    except Exception as exc:
        log.warning("tvdb lookup failed: %s", exc)
        results = []

    if not results:
        console.print(f"[warn]TheTVDB returned no matches for[/warn] [accent]{query}[/accent] [muted]\u2014 continuing without metadata.[/muted]")
        return MediaIdentity(original=query, kind=kind)

    # Cap the list so the picker stays readable.
    results = results[:10]
    rows = [_format_identity_row(alias, query) for alias in results]
    # Add a "None of these" sentinel so the user can still proceed
    # when TVDB has the wrong entry.
    rows.append([f"[muted]Use my query verbatim:[/muted] [accent]{query}[/accent]", "", "", ""])
    idx = _ask_table_select(
        "Pick the correct title (TheTVDB):",
        headers=["English", "German", "Year", "Kind"],
        rows=rows,
    )
    if idx is None:
        return None
    if idx == len(results):
        return MediaIdentity(original=query, kind=kind)
    alias = results[idx]
    return MediaIdentity(
        original=query,
        english=alias.english_title or alias.name,
        german=alias.german_title,
        year=alias.year,
        tvdb_id=alias.tvdb_id,
        kind=alias.kind or kind,
    )


@app.callback()
def _root(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to config.toml.")] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Global options applied to every subcommand."""
    configure_logging(level="DEBUG" if verbose else "INFO")
    if config is not None:
        reset_settings_cache()
        load_settings(config)
    if ctx.invoked_subcommand is None:
        _interactive_menu()
        raise typer.Exit()


# ---------------------------------------------------------------------------
# interactive menu / shell
# ---------------------------------------------------------------------------


def _interactive_menu() -> None:
    _print_banner()
    while True:
        choice = _ask_select(
            "What do you want to do?",
            [
                "Run a movie",
                "Run movie batch",
                "Run a show",
                "Transfer files",
                "Search",
                "Queue / history",
                "Config",
                "Doctor",
                "Quit",
            ],
        )
        if choice is None or choice == "Quit":
            console.print("[dim]bye[/dim]")
            return
        try:
            if choice == "Run a movie":
                _menu_run_movie()
            elif choice == "Run movie batch":
                _menu_run_batch()
            elif choice == "Run a show":
                _menu_run_show()
            elif choice == "Transfer files":
                _menu_transfer()
            elif choice == "Search":
                _menu_search()
            elif choice == "Queue / history":
                _menu_queue()
            elif choice == "Config":
                _menu_config()
            elif choice == "Doctor":
                _doctor()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]bye[/dim]")
            return
        console.print()


def _menu_run_movie() -> None:
    from bankai.cli import bgjobs

    title = Prompt.ask("[bold]Movie title[/bold] (any language)")
    if not title.strip():
        return
    identity = _identify_via_tvdb(title, kind=MediaKind.MOVIE)
    if identity is None:
        # User cancelled the picker.
        return
    en_title = identity.english or identity.original
    de_title = identity.german or en_title
    year = identity.year
    # Build the canonical "English Title YYYY" query used for torrent
    # search; downstream code parses the year off the end.
    en_query = f"{en_title} {year}" if year else en_title
    url = Prompt.ask("Stream URL (blank = auto-search filmpalast)", default="").strip()
    if not url:
        # Prefer the German title for the filmpalast lookup; fall back to
        # English when no German alias is known.
        picked_url = _interactive_pick_movie(de_title)
        if not picked_url:
            console.print("[error]no stream URL found[/error]")
            return
        url = picked_url
    args = build_movie_args(
        BatchMovie(title=en_query, german_title=de_title, url=url),
        site="filmpalast",
    )
    job = bgjobs.spawn(kind="movie", title=en_query, args=args)
    console.print(f"[success]queued[/success] job [accent]{job.id}[/accent] \u2014 \u2018Queue / history\u2019 to watch / cancel.")


def _menu_run_batch() -> None:
    path = Path(Prompt.ask("Batch file path")).expanduser()
    if not path.exists():
        console.print(f"[red]not found:[/red] {path}")
        return
    site = Prompt.ask("Stream site", default="filmpalast")
    _queue_movie_batch(path, site=site, dry_run=False)


def _parse_episode_selector(spec: str, available: list[int]) -> list[int]:
    """Parse an episode selector like ``all`` / ``1-5`` / ``1,3,7-9``.

    Returns the subset of ``available`` episode numbers matching the
    spec, preserving the order of ``available``. Unknown numbers are
    silently dropped so the user can paste a range that overshoots the
    actual season length.
    """
    spec = (spec or "").strip().lower()
    if not spec or spec in {"all", "*", "a"}:
        return list(available)
    picked: set[int] = set()
    for token in spec.replace(" ", "").split(","):
        if not token:
            continue
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            picked.update(range(lo, hi + 1))
        else:
            try:
                picked.add(int(token))
            except ValueError:
                continue
    return [n for n in available if n in picked]


def _menu_run_show() -> None:
    from bankai.cli import bgjobs

    query = Prompt.ask("[bold]Show name[/bold]")
    if not query.strip():
        return
    identity = _identify_via_tvdb(query, kind=MediaKind.EPISODE)
    if identity is None:
        return
    show = identity.english or identity.german or identity.original
    season = IntPrompt.ask("Season", default=1)
    site_choice = Prompt.ask(
        "Site (auto/filmpalast/burningseries/aniworld/bs.to/kinox)", default="auto"
    )
    site_id = None if site_choice.casefold() in {"", "auto"} else site_choice

    result = asyncio.run(list_series_episodes(show, season=season, site=site_id))
    if result is None:
        console.print(f"[warn]no episodes found for[/warn] [accent]{show}[/accent] S{season:02d} [muted](tried {site_choice if site_id else 'all sites'})[/muted]")
        return

    episodes = sorted(result.episodes, key=lambda e: e.episode)
    table = Table(
        title=f"{show} \u2014 Season {season}  [muted]({result.site}, query={result.query!r})[/muted]",
        header_style="table.header",
    )
    table.add_column("#", justify="right")
    table.add_column("Title", overflow="fold")
    table.add_column("URL", overflow="fold", style="muted")
    for ep in episodes:
        table.add_row(
            f"[accent]S{season:02d}E{ep.episode:02d}[/accent]",
            ep.title or "",
            ep.url,
        )
    console.print(table)

    spec = Prompt.ask("Which episodes? [muted](all / 1-5 / 1,3,7-9)[/muted]", default="all")
    wanted = _parse_episode_selector(spec, [e.episode for e in episodes])
    if not wanted:
        console.print("[warn]no episodes selected[/warn]")
        return
    selected = [e for e in episodes if e.episode in wanted]
    if not Confirm.ask(f"Queue pipeline for {len(selected)} episode(s)?", default=True):
        return
    for ep in selected:
        q = f"{show} S{season:02d}E{ep.episode:02d}"
        args = [
            "run",
            q,
            "--url",
            ep.url,
            "--site",
            result.site,
            "--kind",
            "episode",
            "--season",
            str(season),
            "--episode",
            str(ep.episode),
            "--series-title",
            show,
            "--auto",
        ]
        if ep.title:
            args.extend(["--episode-title", ep.title])
        job = bgjobs.spawn(kind="show", title=q, args=args)
        console.print(f"[success]queued[/success] {q} \u2014 job [accent]{job.id}[/accent] [muted]({result.site})[/muted]")


def _menu_transfer() -> None:
    choice = _ask_select(
        "Transfer what?",
        [
            "Library output folder",
            "Specific files/folders",
            "\u2190 Back",
        ],
    )
    if choice is None or choice.startswith("\u2190"):
        return
    if choice.startswith("Library"):
        paths = [Path(get_settings().output.directory)]
    else:
        raw = Prompt.ask("Path(s), separated by comma")
        paths = [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]
    if not paths:
        return
    kind = _transfer_kind(Prompt.ask("Kind", default="auto"))
    _queue_transfer(paths, kind=kind, yes=False, title_library=choice.startswith("Library"))


def _menu_queue() -> None:
    """Background-job queue browser: list, watch logs, cancel."""
    from bankai.cli import bgjobs

    while True:
        jobs = bgjobs.list_jobs()
        if not jobs:
            console.print("[dim]no background jobs yet[/dim]")
            return
        counts = _job_status_counts(jobs)
        console.print(
            "[dim]Background jobs:[/dim] "
            f"[yellow]{counts.get('running', 0)} running[/yellow]  "
            f"[green]{counts.get('done', 0)} done[/green]  "
            f"[red]{counts.get('failed', 0)} failed[/red]"
        )
        rows: list[list[str]] = []
        for j in jobs:
            snapshot = bgjobs.progress_snapshot(j)
            rows.append(
                [
                    j.id,
                    _format_job_status(j.status),
                    _truncate(snapshot.step_label, 24),
                    _progress_text(snapshot.overall_percent),
                    _truncate(j.title, 42),
                    _humanize_age(j.started_at),
                    _format_job_result(j),
                ]
            )
        can_clear = any(j.status in {"done", "failed", "cancelled"} for j in jobs)
        if can_clear:
            rows.append(
                [
                    "-",
                    "action",
                    "Cleanup",
                    "",
                    "Clear finished background jobs",
                    "",
                    "done/failed/cancelled",
                ]
            )
        idx = _ask_table_select(
            "Background jobs (newest first)",
            ["ID", "Status", "Step", "Progress", "Title", "Age", "Result"],
            rows,
        )
        if idx is None:
            return
        if can_clear and idx == len(jobs):
            count = bgjobs.clear_jobs(statuses={"done", "failed", "cancelled"})
            console.print(f"[green]cleared[/green] {count} background job(s)")
            continue
        _job_detail_menu(jobs[idx])


def _job_status_counts(jobs: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    return counts


def _format_job_status(status: str) -> str:
    labels = {
        "running": "RUNNING",
        "done": "DONE",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }
    return labels.get(status, status.upper())


def _format_job_result(job: Any) -> str:
    if job.status == "running":
        return "open log"
    if job.final_path:
        return _truncate(Path(str(job.final_path)).name, 30)
    if job.exit_code is not None:
        return f"exit {job.exit_code}"
    return "-"


def _progress_text(percent: float | None) -> str:
    if percent is None:
        return "[dim]pending[/dim]"
    clamped = max(0.0, min(100.0, percent))
    filled = round(clamped / 10)
    bar = f"[green]{'#' * filled}[/green][dim]{'-' * (10 - filled)}[/dim]"
    return f"{bar} [cyan]{clamped:5.1f}%[/cyan]"


def _format_speed(value: int | None) -> str:
    if value is None or value <= 0:
        return "-"
    units = ["B/s", "KiB/s", "MiB/s", "GiB/s"]
    n = float(value)
    unit = units[0]
    for unit in units:
        if n < 1024 or unit == units[-1]:
            break
        n /= 1024
    return f"{n:.1f} {unit}"


def _format_eta(value: int | None) -> str:
    if value is None or value < 0 or value >= 8_640_000:
        return "-"
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m{value % 60:02d}s"
    return f"{value // 3600}h{(value % 3600) // 60:02d}m"


def _render_background_progress(job: Any) -> None:
    from bankai.cli import bgjobs

    snapshot = bgjobs.progress_snapshot(job)
    console.print(f"[bold]Step:[/bold] [magenta]{snapshot.step_label}[/magenta]  [bold]Overall:[/bold] {_progress_text(snapshot.overall_percent)}")
    if not snapshot.parts:
        return
    table = Table(title="Downloads / transfer", show_lines=False)
    for col in ("Item", "Progress", "Speed", "ETA", "Status"):
        table.add_column(col)
    for part in snapshot.parts.values():
        table.add_row(
            part.label,
            _progress_text(part.percent),
            _format_speed(part.speed),
            _format_eta(part.eta),
            part.status or "-",
        )
    console.print(table)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return f"{value[: width - 1]}…"


def _job_detail_menu(job: Any) -> None:
    from bankai.cli import bgjobs

    while True:
        job = job.refresh()
        console.rule(f"[bold]{job.title}[/bold]  \u00b7  {job.id}  \u00b7  status={_rich_status(job.status)}")
        _render_background_progress(job)
        if job.final_path:
            console.print(f"  [green]final:[/green] {job.final_path}")
        actions = []
        if job.status == "running":
            actions.append("Watch live (Ctrl-C to detach)")
            actions.append("Cancel job")
        actions.extend(
            [
                "Show last 50 log lines",
                "Show full log",
                "\u2190 Back",
            ]
        )
        choice = _ask_select("Action", actions)
        if choice is None or choice.startswith("\u2190"):
            return
        if choice.startswith("Watch"):
            console.print("[dim]\u2014 streaming log (Ctrl-C to detach) \u2014[/dim]")
            bgjobs.watch(job)
        elif choice.startswith("Cancel"):
            if Confirm.ask("Send SIGTERM to job?", default=False):
                ok = job.cancel()
                console.print("[green]cancelled[/green]" if ok else "[yellow]could not cancel[/yellow]")
        elif choice.startswith("Show last"):
            console.print(bgjobs.render_tail(job, lines=50))
        elif choice.startswith("Show full"):
            console.print(bgjobs.render_tail(job, lines=10_000))


@background_app.command("list")
def background_list() -> None:
    """List detached background jobs, including transfers."""
    from bankai.cli import bgjobs

    jobs = bgjobs.list_jobs()
    table = Table(title=f"Background jobs ({len(jobs)})", show_lines=False)
    for col in ("ID", "Kind", "Status", "Step", "Progress", "Title", "Age", "Result"):
        if col in {"Title", "Result"}:
            table.add_column(col, overflow="fold")
        else:
            table.add_column(col)
    for job in jobs:
        snapshot = bgjobs.progress_snapshot(job)
        table.add_row(
            job.id,
            job.kind,
            _rich_status(job.status),
            snapshot.step_label,
            _progress_text(snapshot.overall_percent),
            job.title,
            _humanize_age(job.started_at),
            _format_job_result(job),
        )
    console.print(table)


@background_app.command("watch")
def background_watch(job_id: str = typer.Argument(...)) -> None:
    """Stream a background job log until it finishes."""
    from bankai.cli import bgjobs

    job = bgjobs.get_job(job_id)
    if job is None:
        console.print(f"[red]no such background job: {job_id}[/red]")
        raise typer.Exit(code=1)
    bgjobs.watch(job)


@background_app.command("log")
def background_log(
    job_id: str = typer.Argument(...),
    lines: int = typer.Option(80, "--lines", "-n", min=1),
) -> None:
    """Print recent background job log lines."""
    from bankai.cli import bgjobs

    job = bgjobs.get_job(job_id)
    if job is None:
        console.print(f"[red]no such background job: {job_id}[/red]")
        raise typer.Exit(code=1)
    console.print(bgjobs.render_tail(job, lines=lines))


@background_app.command("status")
def background_status(job_id: str = typer.Argument(...)) -> None:
    """Show current parsed stage/download progress for one background job."""
    from bankai.cli import bgjobs

    job = bgjobs.get_job(job_id)
    if job is None:
        console.print(f"[red]no such background job: {job_id}[/red]")
        raise typer.Exit(code=1)
    console.rule(f"[bold]{job.title}[/bold]  \u00b7  {job.id}  \u00b7  {_rich_status(job.status)}")
    _render_background_progress(job)


@background_app.command("clear")
def background_clear() -> None:
    """Delete finished background jobs."""
    from bankai.cli import bgjobs

    count = bgjobs.clear_jobs(statuses={"done", "failed", "cancelled"})
    console.print(f"[green]cleared[/green] {count} background job(s)")


def _humanize_age(ts: float) -> str:
    import time as _t

    delta = max(0, int(_t.time() - ts))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h{(delta % 3600) // 60}m"
    return f"{delta // 86400}d"


def _menu_search() -> None:
    query = Prompt.ask("Search")
    if not query.strip():
        return
    _do_search(query, site=None, limit=15)


# Each entry: (label, dotted-key, type, description). The dotted-key MUST
# match the actual pydantic Settings model field path.
_QUICK_SETTINGS: list[tuple[str, str, str, str]] = [
    (
        "Interactive Pick",
        "scraper.interactive_pick",
        "bool",
        "Ask which result to pick instead of auto-picking #1",
    ),
    (
        "TVDB Metadata",
        "metadata.tvdb_enabled",
        "bool",
        "Use TVDB aliases when an API key is configured",
    ),
    (
        "TVDB API Key",
        "metadata.tvdb_api_key",
        "str",
        "TheTVDB v4 API key for title aliases",
    ),
    (
        "TVDB PIN",
        "metadata.tvdb_pin",
        "str",
        "Optional subscriber PIN for user-supported keys",
    ),
    (
        "Min Torrent Size (GiB)",
        "selector.min_size_gib",
        "float",
        "Minimum acceptable torrent size in GiB",
    ),
    (
        "Max Torrent Size (GiB)",
        "selector.max_size_gib",
        "float",
        "Maximum acceptable torrent size in GiB",
    ),
    (
        "Min Seeders",
        "selector.min_seeders",
        "int",
        "Minimum seeders for a torrent to be considered",
    ),
    (
        "Preferred Resolutions",
        "selector.preferred_resolutions",
        "list",
        "Comma-sep resolutions in priority order",
    ),
    ("Preferred Codecs", "selector.preferred_codecs", "list", "Comma-sep codecs in priority order"),
    ("Library Directory", "output.directory", "str", "Where final remuxed MKVs go"),
    ("Transfer Movies Dir", "transfer.movies_dir", "str", "Mounted movie destination"),
    ("Transfer Shows Dir", "transfer.shows_dir", "str", "Mounted show destination"),
    ("Rsync Binary", "transfer.rsync_binary", "str", "Command used for safe transfers"),
    ("Movie Filename Template", "output.filename_template", "str", "Plex movie filename template"),
    (
        "Show Filename Template",
        "output.series_filename_template",
        "str",
        "Plex show filename template",
    ),
    (
        "Cleanup After Success",
        "paths.cleanup_after_success",
        "bool",
        "Remove intermediates + qBit torrent on success",
    ),
    (
        "Discord Webhook URL",
        "notifications.webhook_url",
        "str",
        "Discord webhook URL (blank = none)",
    ),
    (
        "Notify On Success",
        "notifications.on_success",
        "bool",
        "Send a Discord notification on success",
    ),
    (
        "Notify On Failure",
        "notifications.on_failure",
        "bool",
        "Send a Discord notification on failure",
    ),
]


def _menu_config_quick() -> None:
    """Quick edit of frequently-changed settings — shows current value
    and prompts for a new one (blank to keep)."""

    while True:
        dump = get_settings().model_dump(mode="json")
        rows = []
        for label, key, typ, desc in _QUICK_SETTINGS:
            cur = _resolve_key(dump, key)
            cur_str = _plain_value(cur, key=key)
            rows.append([label, cur_str[:32], typ, desc[:50]])
        idx = _ask_table_select(
            "Common settings",
            ["Setting", "Current", "Type", "Description"],
            rows,
        )
        if idx is None:
            return
        label, key, typ, _desc = _QUICK_SETTINGS[idx]
        cur = _resolve_key(get_settings().model_dump(mode="json"), key)
        # Prompt by type: bool gets arrow-pick, list gets csv prompt.
        if typ == "bool":
            cur_b = bool(cur)
            choice = _ask_select(
                f"{label} (current: {cur_b})",
                ["true", "false"],
                default="true" if cur_b else "false",
            )
            if choice is None:
                continue
            new = choice
        else:
            new = Prompt.ask(
                f"  {label} (current: [bold]{cur}[/bold]) — new value (blank to keep)",
                default="",
            ).strip()
            if not new:
                continue
        try:
            if typ == "list":
                # write list values one element at a time using nested keys
                # is awkward; use config_set with a json-ish parsed value
                items = [s.strip() for s in new.split(",") if s.strip()]
                _set_raw(key, items)
            else:
                path, written = _set_config_value(key, _coerce(new))
                console.print(f"[dim]wrote {key} to {path}[/dim]")
                new = str(written)
            console.print(f"  [green]saved[/green] {label} = {new}")
        except Exception as exc:
            console.print(f"  [red]failed:[/red] {exc}")


def _set_raw(key: str, value: Any) -> None:
    """Write a non-string value (e.g. a list) into the active config file."""
    _set_config_value(key, value)


def _menu_config() -> None:
    while True:
        choice = _ask_select(
            "Config",
            [
                "Quick edit (common settings)",
                "List all keys",
                "Get a key",
                "Set a key (advanced)",
                "Edit in $EDITOR",
                "Print path",
                "Run init wizard",
                "\u2190 Back",
            ],
        )
        if choice is None or choice.startswith("\u2190"):
            return
        try:
            if choice.startswith("Quick"):
                _menu_config_quick()
            elif choice.startswith("List"):
                config_list()
            elif choice.startswith("Get"):
                k = Prompt.ask("key (e.g. output.directory)")
                config_get(k)
            elif choice.startswith("Set"):
                k = Prompt.ask("key")
                v = Prompt.ask("value")
                config_set(k, v)
            elif choice.startswith("Edit"):
                config_edit()
            elif choice.startswith("Print"):
                config_path()
            elif choice.startswith("Run init"):
                config_init(force=False)
        except (KeyboardInterrupt, EOFError):
            return


@app.command()
def shell() -> None:
    """Interactive REPL: type commands like ``run "Title"`` without leaving bankai."""
    _print_banner()
    console.print("[dim]REPL \u2014 type 'help' or 'exit'.[/dim]\n")
    import shlex

    while True:
        try:
            line = Prompt.ask("[bold magenta]bankai[/bold magenta]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        if not line:
            continue
        if line in ("exit", "quit", "q"):
            return
        if line == "help":
            console.print("Commands: search QUERY, run QUERY [URL], shows SHOW SEASON, queue, config, doctor, exit")
            continue
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            console.print(f"[red]parse error: {exc}[/red]")
            continue
        cmd, *args = parts
        try:
            if cmd == "search" and args:
                _do_search(" ".join(args), site=None, limit=15)
            elif cmd == "run" and args:
                q = args[0]
                u = args[1] if len(args) > 1 else _interactive_pick_movie(q)
                if u:
                    _run_pipeline(query=q, url=u, kind="movie")
            elif cmd in {"shows", "series"} and len(args) >= 2:
                asyncio.run(_run_show(show=args[0], season=int(args[1]), site="auto", episode=None))
            elif cmd == "queue":
                jobs_list(status=None, kind=None, limit=20)
            elif cmd == "config":
                _menu_config()
            elif cmd == "doctor":
                _doctor()
            else:
                console.print(f"[yellow]unknown command: {cmd}[/yellow]")
        except Exception as exc:
            console.print(f"[red]error: {exc}[/red]")


@app.command()
def update(
    ref: str = typer.Option("main", "--ref", help="Git branch/tag/commit to install."),
) -> None:
    """Update this bankai install by rerunning the bundled installer."""
    script = _install_script_path()
    if script is None:
        console.print("[red]could not find scripts/install.sh[/red]\n[dim]Install once with the curl command from the README, then use `bankai update`.[/dim]")
        raise typer.Exit(code=1)

    prefix = script.parent.parent
    bin_dir = _current_bin_dir()
    env = os.environ.copy()
    env["BANKAI_PREFIX"] = str(prefix)
    env["BANKAI_BIN"] = str(bin_dir)
    env["BANKAI_REF"] = ref

    console.print(f"[bold]Updating bankai[/bold] from [cyan]{ref}[/cyan]")
    console.print(f"[dim]prefix:[/dim] {prefix}")
    console.print(f"[dim]bin:[/dim]    {bin_dir}")
    proc = subprocess.run(["bash", str(script)], env=env, check=False)
    if proc.returncode != 0:
        raise typer.Exit(code=proc.returncode)


def _install_script_path() -> Path | None:
    for root in _candidate_install_roots():
        candidate = root / "scripts" / "install.sh"
        if candidate.exists():
            return candidate
    return None


def _candidate_install_roots() -> list[Path]:
    roots: list[Path] = [Path.cwd()]
    for path in (Path(__file__).resolve(), Path(sys.executable).resolve()):
        roots.extend(path.parents)
    command = shutil.which("bankai")
    if command:
        resolved = Path(command).resolve()
        roots.append(resolved.parent)
        roots.extend(resolved.parents)

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _current_bin_dir() -> Path:
    command = shutil.which("bankai")
    if command:
        return Path(command).resolve().parent
    return Path.home() / ".local" / "bin"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(..., help="Title to search."),
    site: str | None = typer.Option(None, "--site", help="Restrict to one backend."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50),
) -> None:
    """Query stream-site backends and print a Rich table."""
    _do_search(query, site=site, limit=limit)


def _do_search(query: str, *, site: str | None, limit: int, render: bool = True) -> list[Any]:
    async def go() -> list[Any]:
        return await search_stream_sources(query, site=site, limit=limit)

    results = asyncio.run(go())
    if not render:
        return results
    table = Table(title=f"Search results for {query!r}", show_lines=False)
    for col in ("#", "Site", "Title", "Year", "Kind", "URL"):
        if col == "URL":
            table.add_column(col, overflow="fold")
        else:
            table.add_column(col)
    for i, r in enumerate(results, 1):
        kind = str(r.kind)
        table.add_row(
            f"[cyan]{i}[/cyan]",
            f"[magenta]{r.site}[/magenta]",
            r.title,
            f"[cyan]{r.year}[/cyan]" if r.year else "",
            f"[green]{kind}[/green]" if kind == MediaKind.MOVIE.value else f"[blue]{kind}[/blue]",
            r.url,
        )
    if not results:
        console.print("[yellow]no results[/yellow]")
    else:
        console.print(table)
    return results


@app.command("extract-audio")
def extract_audio(
    url: str = typer.Argument(..., help="Stream URL to extract audio from."),
    out_dir: str = typer.Option(..., "--out-dir", help="Directory to write the audio into."),
    site: str = typer.Option("unknown", "--site", help="Backend name (metadata only)."),
    hint: str = typer.Option("ytdlp", "--hint", help="'ytdlp' or 'playwright'."),
    want_video: bool = typer.Option(False, "--want-video", help="Download video too (for visual sync)."),
    max_height: int | None = typer.Option(None, "--max-height", help="Cap video height when --want-video."),
    as_json: bool = typer.Option(False, "--json", help="Print a single JSON result line on stdout."),
) -> None:
    """Extract the German audio for ONE stream URL into --out-dir.

    Standalone (no pipeline/DB) so it can be invoked over SSH on a host with a
    real display (Xvfb) to delegate extraction from a headless machine.
    """
    import json as _json
    from pathlib import Path as _Path

    from bankai.processor.extractor import extract_url

    outp = _Path(out_dir)
    outp.mkdir(parents=True, exist_ok=True)

    async def go() -> Any:
        return await extract_url(url, outp, site=site, hint=hint, want_video=want_video, max_height=max_height)

    result = asyncio.run(go())
    payload = {
        "path": str(result.path),
        "codec": result.codec,
        "extractor": result.extractor,
        "has_video": result.has_video,
        "duration_ms": result.duration_ms,
    }
    if as_json:
        print(_json.dumps(payload))
    else:
        console.print(payload)


@metadata_app.command("search")
def metadata_search(
    query: str = typer.Argument(..., help="Movie or show title to look up."),
    kind: str = typer.Option("show", "--kind", help="show | movie"),
) -> None:
    """Show TVDB title aliases used by Bankai lookups."""
    media_kind = _metadata_kind(kind)
    aliases = asyncio.run(get_title_aliases(query, kind=media_kind))
    if not aliases:
        console.print("[yellow]no metadata results[/yellow]\n[dim]Check `metadata.tvdb_api_key` and `metadata.tvdb_enabled`.[/dim]")
        return
    table = Table(title=f"TVDB metadata for {query!r}", show_lines=False)
    for col in ("#", "TVDB ID", "Name", "English", "German", "Year"):
        if col in {"Name", "English", "German"}:
            table.add_column(col, overflow="fold")
        else:
            table.add_column(col)
    for index, alias in enumerate(aliases, 1):
        table.add_row(
            f"[cyan]{index}[/cyan]",
            str(alias.tvdb_id or ""),
            alias.name or "",
            alias.english_title or "",
            alias.german_title or "",
            str(alias.year or ""),
        )
    console.print(table)


def _metadata_kind(kind: str) -> MediaKind:
    clean = kind.casefold()
    if clean in {"movie", "movies"}:
        return MediaKind.MOVIE
    if clean in {"show", "shows", "series", "episode", "episodes"}:
        return MediaKind.EPISODE
    console.print(f"[red]invalid kind:[/red] {kind!r} (use movie or show)")
    raise typer.Exit(code=1)


async def _movie_lookup_queries(query: str, german_title: str | None) -> list[str]:
    # Even when a caller supplied a title, include TVDB's aliases/translations.
    # Web jobs historically always passed --de (falling back to the English
    # title), which prevented titles such as "El Hoyo" from ever trying the
    # Filmpalast name "Der Schacht".
    values = [german_title] if german_title else []
    values.extend(await title_aliases(query, kind=MediaKind.MOVIE))
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = (value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _interactive_pick_movie(query: str) -> str | None:
    """Search filmpalast and let the user pick (or auto-pick top by setting).

    Strips a trailing year (``"Cars 3 2017"`` \u2192 ``"Cars 3"``) before
    searching since filmpalast indexes on title only and a year token in
    the query reduces hit-count to zero.

    Auto-pick mode re-ranks results by token overlap with the cleaned
    query because filmpalast's own ranking is unreliable (e.g. searching
    ``"Cars"`` returns the wanted ``"Cars 3: Evolution"`` at row 10).
    """
    settings = get_settings()
    cleaned = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$", "", query).strip()

    # filmpalast's search is finicky — multi-token queries with numbers
    # (e.g. ``"Cars 3"``) often return zero hits while the bare first
    # token (``"Cars"``) returns the wanted title at row 10. Probe with
    # progressively shorter prefixes until something comes back.
    tokens = cleaned.split()
    results: list[Any] = []
    for n in range(len(tokens), 0, -1):
        attempt = " ".join(tokens[:n])
        results = _do_search(attempt, site="filmpalast", limit=30, render=False)
        if results:
            break
    if not results:
        console.print("[yellow]no results[/yellow]")
        return None
    if not settings.scraper.interactive_pick:
        from bankai.scraper import SearchResult

        def _tokens(s: str) -> set[str]:
            return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t}

        want = _tokens(cleaned or query)

        def _score(r: SearchResult) -> tuple[int, int]:
            cand = _tokens(r.title)
            overlap = len(want & cand)
            extra = len(cand - want)
            return (overlap, -extra)  # max overlap, then min extra tokens

        ranked = sorted(results, key=_score, reverse=True)
        pick = ranked[0]
        console.print(f"[green]auto-picked:[/green] {pick.title}")
        return str(pick.url)
    # Interactive: arrow-key pick rendered as a single aligned table.
    rows = [[str(i), r.title[:60], str(r.year or ""), r.url[:60]] for i, r in enumerate(results, 1)]
    idx = _ask_table_select(
        f"Pick a stream for {query!r}",
        ["#", "Title", "Year", "URL"],
        rows,
    )
    if idx is None:
        return None
    return str(results[idx].url)


# ---------------------------------------------------------------------------
# config commands
# ---------------------------------------------------------------------------


@config_app.command("path")
def config_path() -> None:
    """Print the active config file path."""
    console.print(str(user_config_path()))


@config_app.command("list")
def config_list() -> None:
    """Print current effective settings."""
    s = get_settings().model_dump(mode="json")
    table = Table(title="Bankai config", show_lines=False)
    table.add_column("Key", style="bold")
    table.add_column("Value", overflow="fold")
    for key, value in _flatten_settings(s):
        table.add_row(key, _format_value(value, key=key))
    console.print(table)


@config_app.command("get")
def config_get(key: str) -> None:
    """Get a single key (dotted path, e.g. ``output.directory``)."""
    val = _resolve_key(get_settings().model_dump(mode="json"), key)
    if val is None:
        console.print(f"[red]no such key: {key}[/red]")
        raise typer.Exit(code=1)
    console.print(_format_value(val, key=key))


@config_app.command("set")
def config_set(
    key: str,
    value: str,
    config_file: Annotated[
        Path | None,
        typer.Option("--file", help="Override config path."),
    ] = None,
) -> None:
    """Set a key in the user config file (creates it if missing)."""
    path, written = _set_config_value(key, _coerce(value), config_file=config_file)
    console.print(f"[green]set[/green] {key} = {written!r}  \u2192  {path}")


def _set_config_value(
    key: str,
    value: Any,
    *,
    config_file: Path | None = None,
) -> tuple[Path, Any]:
    """Set a key in the active TOML config and return the written value."""
    path = config_file or user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_toml(path) if path.exists() else {}
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    _write_toml(path, data)
    reset_settings_cache()
    return path, cur[parts[-1]]


@config_app.command("edit")
def config_edit() -> None:
    """Open the config in $EDITOR."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# bankai config\n", encoding="utf-8")
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    subprocess.call([editor, str(path)])
    reset_settings_cache()


@config_app.command("init")
def config_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing config."),
) -> None:
    """First-run wizard: ask the essentials and write ``~/.config/bankai/config.toml``."""
    path = user_config_path()
    if path.exists() and not force and not Confirm.ask(f"{path} exists. Overwrite?", default=False):
        return
    _print_banner()
    console.print("[bold]Welcome \u2014 a few quick questions.[/bold]\n")
    library = Prompt.ask("Library directory (final MKVs)", default="/mnt/media/bankai")
    work = Prompt.ask("Work directory (intermediate files)", default=str(Path.home() / ".bankai/work"))
    state = Prompt.ask("State DB path", default=str(Path.home() / ".bankai/state.sqlite3"))
    downloads = Prompt.ask("qBittorrent downloads dir (host path)", default="/mnt/media/downloads/bankai")
    transfer_root = Prompt.ask("Mounted media server root", default="/mnt/media12")
    qbit_url = Prompt.ask("qBittorrent URL", default="http://localhost:8080")
    qbit_user = Prompt.ask("qBittorrent username", default="admin")
    qbit_pass = Prompt.ask("qBittorrent password", default="adminadmin", password=True)
    prow_url = Prompt.ask("Prowlarr URL", default="http://localhost:9696")
    prow_key = Prompt.ask("Prowlarr API key", default="")
    tvdb_key = Prompt.ask("TheTVDB API key (blank = disabled)", default="")
    tvdb_pin = Prompt.ask("TheTVDB PIN (blank = none)", default="", password=True)
    discord = Prompt.ask("Discord webhook URL (blank = none)", default="")
    interactive = Confirm.ask(
        "When you run a movie without --url, ask before picking the search hit?",
        default=False,
    )
    parent_host = downloads.rsplit("/", 1)[0] if "/" in downloads else "/downloads"
    data: dict[str, Any] = {
        "paths": {
            "state_db": state,
            "work_dir": work,
            "downloads_dir": downloads,
        },
        "output": {"directory": library},
        "qbittorrent": {
            "url": qbit_url,
            "username": qbit_user,
            "password": qbit_pass,
            "category": "bankai",
            "save_path": "/downloads/bankai",
            "path_map": {"/downloads": parent_host},
        },
        "prowlarr": {"url": prow_url, "api_key": prow_key},
        "scraper": {"interactive_pick": interactive},
        "metadata": {
            "tvdb_enabled": bool(tvdb_key),
            "tvdb_api_key": tvdb_key,
            "tvdb_pin": tvdb_pin,
            "tvdb_languages": ["deu", "eng"],
        },
        "transfer": {
            "root": transfer_root,
            "movies_dir": f"{transfer_root.rstrip('/')}/movies",
            "shows_dir": f"{transfer_root.rstrip('/')}/shows",
            "rsync_binary": "rsync",
        },
    }
    if discord:
        data["notifications"] = {"webhook_url": discord}
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_toml(path, data)
    reset_settings_cache()
    console.print(f"\n[green]wrote[/green] {path}")
    console.print(f"[dim]Add `export BANKAI_CONFIG={path}` to ~/.bashrc to make it the default.[/dim]")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Check that all external dependencies and services are reachable."""
    _doctor()


def _doctor() -> None:
    settings = get_settings()
    table = Table(title="bankai doctor", show_lines=False)
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    def row(name: str, ok: bool, detail: str) -> None:
        mark = "[green]\u2713[/green]" if ok else "[red]\u2717[/red]"
        table.add_row(name, mark, detail)

    for binary in ("ffmpeg", "ffprobe", "mkvmerge", "yt-dlp"):
        path = _find_runtime_binary(binary)
        row(binary, path is not None, path or "not on PATH")
    alass = shutil.which(settings.sync.alass_binary)
    row("alass", alass is not None, alass or f"not on PATH ({settings.sync.alass_binary})")
    rsync = shutil.which(settings.transfer.rsync_binary)
    row("rsync", rsync is not None, rsync or f"not on PATH ({settings.transfer.rsync_binary})")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                _ = pw.chromium.executable_path
                row("playwright chromium", True, "installed")
            except Exception as exc:
                row("playwright chromium", False, f"{exc}; run: playwright install chromium")
    except Exception as exc:
        row("playwright", False, str(exc))

    for label, p in (
        ("state_db parent", settings.paths.state_db.parent),
        ("work_dir", settings.paths.work_dir),
        ("output.directory", settings.output.directory),
        ("transfer.movies_dir", settings.transfer.movies_dir),
        ("transfer.shows_dir", settings.transfer.shows_dir),
    ):
        row(label, p.exists() or _can_create(p), str(p))

    import httpx

    try:
        r = httpx.get(f"{settings.qbittorrent.url}/api/v2/app/version", timeout=3.0)
        row("qBittorrent", r.status_code in (200, 403), f"HTTP {r.status_code}")
    except Exception as exc:
        row("qBittorrent", False, str(exc))

    try:
        r = httpx.get(
            f"{settings.prowlarr.url}/api/v1/health",
            headers={"X-Api-Key": settings.prowlarr.api_key},
            timeout=3.0,
        )
        row("Prowlarr", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as exc:
        row("Prowlarr", False, str(exc))

    if settings.notifications.webhook_url:
        row("Discord webhook", True, "configured")

    try:
        from bankai.web.server import service_status

        status = service_status()
        if status["available"]:
            row(
                "web service",
                status["active"],
                f"{SERVICE_NAME}: {status['detail']} (port {settings.web.port})",
            )
        else:
            row("web service", False, status["detail"])
    except Exception as exc:
        row("web service", False, str(exc))

    console.print(table)


def _can_create(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def _find_runtime_binary(binary: str) -> str | None:
    """Find a shell binary, including console scripts in bankai's venv."""
    path = shutil.which(binary)
    if path:
        return path
    name = f"{binary}.exe" if os.name == "nt" else binary
    candidate = Path(sys.executable).with_name(name)
    return str(candidate) if candidate.exists() else None


# ---------------------------------------------------------------------------
# web UI
# ---------------------------------------------------------------------------


@web_app.command("serve")
def web_serve(
    host: str | None = typer.Option(None, "--host", help="Bind host (default web.host)."),
    port: int | None = typer.Option(None, "--port", help="Bind port (default web.port)."),
) -> None:
    """Run the web UI / HTTP API in the foreground (blocking)."""
    from bankai.web.server import run_server

    run_server(host=host, port=port)


@web_app.command("install-service")
def web_install_service(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    no_enable: bool = typer.Option(False, "--no-enable", help="Write the unit but don't start it."),
) -> None:
    """Install (and start) the bankai-web systemd user service."""
    from bankai.web.server import install_service

    try:
        unit = install_service(host=host, port=port, enable=not no_enable)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    settings = get_settings()
    console.print(f"[green]wrote[/green] {unit}")
    if not no_enable:
        console.print(f"[green]started[/green] {SERVICE_NAME} \u2014 open [accent]http://{settings.web.host}:{settings.web.port}[/accent]")


@web_app.command("status")
def web_status() -> None:
    """Show the web service status."""
    from bankai.web.server import service_status

    status = service_status()
    settings = get_settings()
    if not status["available"]:
        console.print(f"[yellow]{status['detail']}[/yellow]")
        return
    mark = "[green]active[/green]" if status["active"] else f"[red]{status['detail']}[/red]"
    console.print(f"{SERVICE_NAME}: {mark}  (port {settings.web.port})")


# ---------------------------------------------------------------------------
# extract / sync / remux (single-stage commands)
# ---------------------------------------------------------------------------


@app.command()
def extract(
    url: str = typer.Option(..., "--url"),
    out: Path = typer.Option(..., "--out"),
    hint: str = typer.Option("ytdlp", "--hint"),
    site: str = typer.Option("manual", "--site"),
) -> None:
    """Extract audio from a stream URL."""
    from bankai.processor.extractor import ExtractWorker

    out.mkdir(parents=True, exist_ok=True)
    worker = ExtractWorker()

    async def go() -> None:
        result = await _run_worker_once(
            worker,
            out,
            payload={"url": url, "hint": hint, "site": site},
        )
        console.print_json(data=result or {})

    asyncio.run(go())


@app.command()
def sync(
    audio: Path = typer.Option(..., "--audio", exists=True),
    reference: Path = typer.Option(..., "--reference", exists=True),
    out: Path = typer.Option(..., "--out"),
    offset: float | None = typer.Option(None, "--offset"),
) -> None:
    """Align ``audio`` to ``reference`` (or apply manual offset)."""
    from bankai.processor.sync import SyncWorker

    out.parent.mkdir(parents=True, exist_ok=True)
    worker = SyncWorker()
    payload: dict[str, Any] = {"audio": str(audio), "reference": str(reference)}
    if offset is not None:
        payload["offset_seconds"] = offset

    async def go() -> None:
        result = await _run_worker_once(worker, out.parent, payload=payload)
        console.print_json(data=result or {})

    asyncio.run(go())


@app.command()
def remux(
    video: Path = typer.Option(..., "--video", exists=True),
    audio: Path = typer.Option(..., "--audio", exists=True),
    out: Path = typer.Option(..., "--out"),
    language: str = typer.Option("ger", "--language"),
    track_name: str = typer.Option("German (Web-DL)", "--track-name"),
    default_track: bool = typer.Option(True, "--default/--no-default"),
) -> None:
    """Remux HQ ``video`` + dub ``audio`` into a single MKV."""
    from bankai.processor.remux import RemuxWorker

    worker = RemuxWorker()
    payload = {
        "video": str(video),
        "audio": str(audio),
        "out": str(out),
        "language": language,
        "track_name": track_name,
        "default_track": default_track,
    }

    async def go() -> None:
        result = await _run_worker_once(worker, out.parent, payload=payload)
        console.print_json(data=result or {})

    asyncio.run(go())


# ---------------------------------------------------------------------------
# run / shows
# ---------------------------------------------------------------------------


@app.command()
def batch(
    file: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, readable=True),
    site: str = typer.Option("filmpalast", "--site", help="Stream site for auto-search."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show parsed jobs without queueing."),
) -> None:
    """Queue a batch of movie downloads from a text file."""
    _queue_movie_batch(file, site=site, dry_run=dry_run)


def _queue_movie_batch(path: Path, *, site: str, dry_run: bool) -> None:
    from bankai.cli import bgjobs

    movies = parse_movie_batch(path)
    if not movies:
        console.print("[yellow]no movies found in batch file[/yellow]")
        return

    table = Table(title=f"Movie batch ({len(movies)})", show_lines=False)
    table.add_column("#")
    table.add_column("Title", overflow="fold")
    table.add_column("German title", overflow="fold")
    table.add_column("URL", overflow="fold")
    for index, movie in enumerate(movies, 1):
        table.add_row(
            f"[cyan]{index}[/cyan]",
            movie.title,
            movie.german_title or "[dim]auto[/dim]",
            movie.url or "[dim]auto-search[/dim]",
        )
    console.print(table)

    if dry_run:
        return

    jobs = [bgjobs.spawn(kind="movie", title=movie.title, args=build_movie_args(movie, site=site)) for movie in movies]
    ids = ", ".join(job.id for job in jobs)
    console.print(f"[green]queued[/green] {len(jobs)} movie job(s): {ids}")


@app.command()
def transfer(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="Files or folders to move. Defaults to output.directory with --library."),
    ] = None,
    kind: str = typer.Option("auto", "--kind", help="auto | movie | show"),
    library: bool = typer.Option(False, "--library", help="Transfer the configured output directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned moves without queueing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Queue without confirmation."),
) -> None:
    """Queue a safe rsync move to the mounted media server."""
    selected_paths = list(paths or [])
    if library:
        selected_paths.insert(0, Path(get_settings().output.directory))
    if not selected_paths:
        console.print("[red]no paths given[/red] [dim](pass paths or use --library)[/dim]")
        raise typer.Exit(code=1)
    transfer_kind = _transfer_kind(kind)
    if dry_run:
        _render_transfer_plan(selected_paths, kind=transfer_kind)
        return
    _queue_transfer(selected_paths, kind=transfer_kind, yes=yes, title_library=library)


def _queue_transfer(
    paths: list[Path],
    *,
    kind: TransferKind,
    yes: bool,
    title_library: bool = False,
) -> None:
    planned = _render_transfer_plan(paths, kind=kind)
    if planned == 0:
        return
    if not yes and not Confirm.ask("Queue this transfer?", default=True):
        return
    from bankai.cli import bgjobs

    args = ["transfer-run", "--kind", kind, *[str(p) for p in paths]]
    title = "Transfer library" if title_library else f"Transfer {len(paths)} path(s)"
    job = bgjobs.spawn(kind="transfer", title=title, args=args)
    console.print(f"[green]queued[/green] transfer job [bold]{job.id}[/bold]\n[dim]Watch it with `bankai background watch {job.id}`.[/dim]")


@app.command("transfer-run", hidden=True)
def transfer_run(
    paths: Annotated[list[Path], typer.Argument(help="Files or folders to move.")],
    kind: str = typer.Option("auto", "--kind", help="auto | movie | show"),
) -> None:
    """Foreground transfer worker used by the background supervisor."""
    from bankai.notify import notify_transfer_summary

    transfer_kind = _transfer_kind(kind)
    result = transfer_with_rsync(paths, kind=transfer_kind, progress=console.print)
    summary = format_transfer_summary(result)
    console.print(summary)
    asyncio.run(notify_transfer_summary(summary=summary, ok=result.ok))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("review-repack", hidden=True)
def review_repack(
    path: Path = typer.Argument(...),
    delay_ms: int = typer.Option(..., "--delay-ms"),
    atempo: float | None = typer.Option(None, "--atempo"),
    track_index: int | None = typer.Option(None, "--track-index"),
) -> None:
    """Detached review repack used by the web UI."""
    from bankai.web import media as media_mod
    from bankai.web import review as review_mod

    review_mod.set_repack(path, "repacking", percent=0.0, kind="audio")
    log.info("BANKAI_PROGRESS stage=repack pct=1 status=running")
    try:
        if atempo is not None and abs(atempo - 1.0) > 1e-4:
            result = media_mod.repack_audio_drift(
                path,
                delay_ms=delay_ms,
                atempo=atempo,
                track_index=track_index,
            )
        else:
            result = media_mod.repack_audio_delay(path, delay_ms=delay_ms)
        if not result.ok:
            raise RuntimeError(result.message)
        review_mod.set_delay(path, delay_ms)
        review_mod.set_stage(path, "approved")
        review_mod.set_repack(path, "done", percent=100.0, kind="audio")
        log.info("BANKAI_PROGRESS stage=repack pct=100 status=done")
        console.print_json(data={"final_path": str(path), "message": result.message})
    except Exception as exc:
        review_mod.set_repack(path, "failed", kind="audio", note=str(exc))
        print(f"{type(exc).__name__}: {exc}", flush=True)
        raise typer.Exit(code=1) from exc


@app.command("review-replace-torrent", hidden=True)
def review_replace_torrent(
    path: Path = typer.Argument(...),
    query: str = typer.Option(..., "--query"),
    target_runtime_seconds: float | None = typer.Option(None, "--target-runtime-seconds"),
    candidate_json: str | None = typer.Option(None, "--candidate-json"),
) -> None:
    """Download another HQ release and remux it with the reviewed German dub."""
    from bankai.torrent.qbittorrent import QBittorrentClient
    from bankai.torrent.worker import TorrentWorker
    from bankai.web import media as media_mod
    from bankai.web import review as review_mod

    review_mod.set_repack(path, "repacking", percent=0.0, kind="torrent")
    # The review dialog already provides an explicit Manual mode. Automatic
    # replacement should fail visibly on the same row when policy rejects all
    # releases, rather than entering the pipeline-only interactive wait state
    # with no standalone job row to host its picker.
    payload: dict[str, Any] = {"query": query, "kind": "movie", "wait_for_manual": False}
    if target_runtime_seconds is not None:
        payload["target_runtime_seconds"] = target_runtime_seconds
    if candidate_json:
        try:
            payload["manual_candidate"] = json.loads(candidate_json)
        except ValueError as exc:
            review_mod.set_repack(path, "failed", kind="torrent", note="invalid torrent choice")
            raise typer.BadParameter("invalid candidate JSON") from exc

    async def go() -> dict[str, Any] | None:
        log.info("BANKAI_PROGRESS stage=replace pct=2 status=searching")
        result = await _run_worker_once(TorrentWorker(), get_settings().paths.work_dir, payload=payload)
        if not result or not result.get("path"):
            raise RuntimeError("replacement torrent produced no video file")
        log.info("BANKAI_PROGRESS stage=replace pct=92 status=remuxing")
        state = review_mod.get_state(path)
        remuxed = await asyncio.to_thread(
            media_mod.replace_video_source,
            path,
            Path(result["path"]),
            delay_ms=state.delay_ms,
        )
        if not remuxed.ok:
            raise RuntimeError(remuxed.message)
        torrent_hash = result.get("torrent_hash")
        if torrent_hash:
            qbit = QBittorrentClient()
            try:
                await qbit.remove(str(torrent_hash), delete_files=True)
            finally:
                await qbit.aclose()
        return result

    try:
        asyncio.run(go())
        review_mod.set_stage(path, "review")
        review_mod.set_repack(path, "done", percent=100.0, kind="torrent")
        log.info("BANKAI_PROGRESS stage=replace pct=100 status=done")
        console.print_json(data={"final_path": str(path), "message": "torrent replaced"})
    except Exception as exc:
        review_mod.set_repack(path, "failed", kind="torrent", note=str(exc))
        print(f"{type(exc).__name__}: {exc}", flush=True)
        raise typer.Exit(code=1) from exc


def _transfer_kind(kind: str) -> TransferKind:
    clean = kind.casefold()
    if clean in {"auto", "movie", "show"}:
        return cast(TransferKind, clean)
    if clean in {"movies"}:
        return "movie"
    if clean in {"shows", "series"}:
        return "show"
    console.print(f"[red]invalid transfer kind:[/red] {kind!r}")
    raise typer.Exit(code=1)


def _render_transfer_plan(paths: list[Path], *, kind: TransferKind) -> int:
    try:
        items = plan_transfer(paths, kind=kind)
    except TransferError as exc:
        console.print(f"[red]transfer plan failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if not items:
        console.print("[yellow]no transfer files found[/yellow]")
        return 0
    table = Table(title=f"Transfer plan ({len(items)})", show_lines=False)
    for col in ("#", "Kind", "Source", "Destination", "Status"):
        if col in {"Source", "Destination"}:
            table.add_column(col, overflow="fold")
        else:
            table.add_column(col)
    for index, item in enumerate(items, 1):
        table.add_row(
            f"[cyan]{index}[/cyan]",
            item.kind,
            str(item.source),
            str(item.destination),
            "[yellow]skip exists[/yellow]" if item.destination.exists() else "[green]move[/green]",
        )
    console.print(table)
    return len(items)


@app.command()
def run(
    query: str = typer.Argument(..., help="English title for torrent search (include year)."),
    de: str | None = typer.Option(
        None,
        "--de",
        "--german",
        help="German title for filmpalast lookup (defaults to QUERY).",
    ),
    url: str | None = typer.Option(None, "--url", help="Stream URL (skip search)."),
    site: str | None = typer.Option(None, "--site", help="Stream site id."),
    hint: str = typer.Option("ytdlp", "--hint"),
    out: Path | None = typer.Option(None, "--out"),
    kind: str = typer.Option("movie", "--kind", help="movie | episode"),
    offset: float | None = typer.Option(None, "--offset"),
    season_number: int | None = typer.Option(None, "--season", help="Episode season metadata."),
    episode_number: int | None = typer.Option(None, "--episode", help="Episode number metadata."),
    episode_title: str | None = typer.Option(None, "--episode-title", help="Episode title metadata."),
    series_title: str | None = typer.Option(None, "--series-title", help="Show title metadata."),
    interactive: bool | None = typer.Option(None, "--interactive/--auto", help="Override scraper.interactive_pick."),
) -> None:
    """End-to-end pipeline: extract dub, fetch video, sync, remux.

    QUERY is the English title used for the torrent search. Pass --de
    with the German title to look the dub up on filmpalast (which indexes
    German titles only).
    """
    if not url:
        if interactive is not None:
            os.environ["BANKAI_SCRAPER__INTERACTIVE_PICK"] = "true" if interactive else "false"
            reset_settings_cache()
        for lookup_query in asyncio.run(_movie_lookup_queries(query, de)):
            url = _interactive_pick_movie(lookup_query)
            if url:
                break
        if not url:
            console.print("[red]no stream URL found \u2014 aborting[/red]")
            raise typer.Exit(code=1)
        if not site:
            site = "filmpalast"
    extra_payload: dict[str, Any] = {}
    if season_number is not None:
        extra_payload["season"] = season_number
    if episode_number is not None:
        extra_payload["episode"] = episode_number
    if episode_title:
        extra_payload["episode_title"] = episode_title
    if series_title:
        extra_payload["series_title"] = series_title
    _run_pipeline(
        query=query,
        url=url,
        site=site,
        hint=hint,
        out=out,
        kind=kind,
        offset=offset,
        extra_payload=extra_payload or None,
    )


@app.command("shows")
def shows(
    show: str = typer.Argument(..., help="Show name."),
    season: int = typer.Option(..., "--season", "-s", help="Season number."),
    episode: int | None = typer.Option(None, "--episode", "-e", help="Single episode (else all)."),
    site: str = typer.Option("auto", "--site", help="Stream site id, or auto."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Queue without confirmation."),
) -> None:
    """Queue the pipeline for an entire season (or one episode)."""
    asyncio.run(_run_show(show=show, season=season, site=site, episode=episode, yes=yes))


@app.command("series", hidden=True)
def series(
    show: str = typer.Argument(..., help="Show name."),
    season: int = typer.Option(..., "--season", "-s", help="Season number."),
    episode: int | None = typer.Option(None, "--episode", "-e", help="Single episode (else all)."),
    site: str = typer.Option("auto", "--site", help="Stream site id, or auto."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Queue without confirmation."),
) -> None:
    """Backward-compatible alias for ``shows``."""
    asyncio.run(_run_show(show=show, season=season, site=site, episode=episode, yes=yes))


async def _run_show(
    *,
    show: str,
    season: int,
    site: str,
    episode: int | None,
    yes: bool = False,
) -> None:
    from bankai.cli import bgjobs

    site_id = None if site.casefold() in {"", "auto"} else site
    result = await list_series_episodes(show, season=season, site=site_id)
    if result is None:
        console.print("[yellow]no episodes found[/yellow]")
        return
    episodes = result.episodes
    if episode is not None:
        episodes = [e for e in episodes if e.episode == episode]
    if not episodes:
        console.print("[yellow]no matching episode found[/yellow]")
        return
    table = Table(title=f"{show} season {season} ({result.site}, query={result.query!r})")
    table.add_column("Episode")
    table.add_column("Title", overflow="fold")
    table.add_column("URL", overflow="fold")
    for ep in episodes:
        table.add_row(f"[cyan]S{season:02d}E{ep.episode:02d}[/cyan]", ep.title or "", ep.url)
    console.print(table)
    if not yes and not Confirm.ask(f"Queue pipeline for {len(episodes)} episode(s)?", default=True):
        return
    for ep in episodes:
        q = f"{show} S{season:02d}E{ep.episode:02d}"
        args = [
            "run",
            q,
            "--url",
            ep.url,
            "--site",
            result.site,
            "--kind",
            "episode",
            "--season",
            str(season),
            "--episode",
            str(ep.episode),
            "--series-title",
            show,
            "--auto",
        ]
        if ep.title:
            args.extend(["--episode-title", ep.title])
        job = bgjobs.spawn(kind="show", title=q, args=args)
        console.print(f"[green]queued[/green] {q} as job [bold]{job.id}[/bold] ({result.site})")


def _run_pipeline(
    *,
    query: str,
    url: str,
    site: str | None = None,
    hint: str = "ytdlp",
    out: Path | None = None,
    kind: str = "movie",
    offset: float | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    from bankai.processor.pipeline import PipelineWorker

    worker = PipelineWorker()
    payload: dict[str, Any] = {
        "query": query,
        "stream_url": url,
        "stream_site": site or "unknown",
        "stream_hint": hint,
        "kind": kind,
    }
    if out:
        payload["out"] = str(out)
    if offset is not None:
        payload["offset_seconds"] = offset
    if extra_payload:
        payload.update(extra_payload)

    async def go() -> None:
        settings = get_settings()
        try:
            result = await _run_worker_once(worker, settings.paths.work_dir, payload=payload)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            # Keep the full stack trace only at DEBUG (visible with --verbose)
            # and surface a single clean line for humans and the UI parser.
            log.debug("pipeline traceback", exc_info=True)
            log.error("Job failed \u2014 %s", reason)
            # Bare reason on its own unwrapped line so the Reason column and
            # log parser can pick it up cleanly (no box, no wrapping).
            print(reason, flush=True)
            raise typer.Exit(code=1) from exc
        console.print_json(data=result or {})

    asyncio.run(go())


# ---------------------------------------------------------------------------
# daemon
# ---------------------------------------------------------------------------


@app.command()
def daemon() -> None:
    """Run the dispatcher in the foreground."""
    from bankai.db import initialize
    from bankai.processor.extractor import ExtractWorker
    from bankai.processor.pipeline import PipelineWorker
    from bankai.processor.remux import RemuxWorker
    from bankai.processor.sync import SyncWorker
    from bankai.queue.worker import Dispatcher
    from bankai.torrent.worker import TorrentWorker

    settings = get_settings()
    initialize(settings.paths.state_db)
    workers = {
        ExtractWorker.kind: ExtractWorker(),
        TorrentWorker.kind: TorrentWorker(),
        SyncWorker.kind: SyncWorker(),
        RemuxWorker.kind: RemuxWorker(),
        PipelineWorker.kind: PipelineWorker(),
    }
    dispatcher = Dispatcher(
        db_path=settings.paths.state_db,
        work_dir=settings.paths.work_dir,
        workers=workers,
        queue_settings=settings.queue,
    )
    console.print("[green]bankai daemon started[/green] \u2014 Ctrl+C to stop")

    async def go() -> None:
        try:
            await dispatcher.run()
        except KeyboardInterrupt:
            await dispatcher.stop()

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        console.print("\n[yellow]shutting down[/yellow]")


# ---------------------------------------------------------------------------
# jobs subcommands
# ---------------------------------------------------------------------------


@jobs_app.command("list")
def jobs_list(
    status: str | None = typer.Option(None, "--status"),
    kind: str | None = typer.Option(None, "--kind"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """List jobs in the queue."""
    from bankai.db import StateRepository
    from bankai.queue.models import JobKind, JobStatus

    settings = get_settings()
    repo = StateRepository(settings.paths.state_db)
    rows = repo.list_jobs(
        status=JobStatus(status) if status else None,
        kind=JobKind(kind) if kind else None,
        limit=limit,
    )
    table = Table(title=f"Jobs ({len(rows)})", show_lines=False)
    for col in ("ID", "Kind", "Status", "Attempts", "Updated"):
        table.add_column(col)
    for j in rows:
        table.add_row(
            str(j.id),
            str(j.kind),
            _rich_status(str(j.status)),
            f"{j.attempts}/{j.max_attempts}",
            j.updated_at or "-",
        )
    console.print(table)


@jobs_app.command("show")
def jobs_show(job_id: int = typer.Argument(...)) -> None:
    """Show one job's full payload + result + artifacts."""
    from bankai.db import StateRepository

    repo = StateRepository(get_settings().paths.state_db)
    job = repo.get_job(job_id)
    if job is None:
        console.print(f"[red]no such job: {job_id}[/red]")
        raise typer.Exit(code=1)
    console.print_json(data=json.loads(job.model_dump_json()))
    arts = repo.list_artifacts(job_id)
    if arts:
        table = Table(title="Artifacts")
        for col in ("ID", "Kind", "Path", "Size"):
            table.add_column(col)
        for a in arts:
            table.add_row(str(a.id), a.kind, str(a.path), str(a.size_bytes or "-"))
        console.print(table)


@jobs_app.command("retry")
def jobs_retry(job_id: int = typer.Argument(...)) -> None:
    """Reset a failed job to ``queued`` so the dispatcher picks it up."""
    from bankai.db import StateRepository
    from bankai.queue.models import Job, JobStatus

    repo = StateRepository(get_settings().paths.state_db)
    job = repo.get_job(job_id)
    if job is None:
        console.print(f"[red]no such job: {job_id}[/red]")
        raise typer.Exit(code=1)
    new_payload = job.model_dump(exclude={"id", "created_at", "updated_at", "started_at", "finished_at"})
    new_payload["status"] = JobStatus.QUEUED
    new_payload["attempts"] = 0
    new_payload["error"] = None
    repo.create_job(Job(**new_payload))
    console.print(f"[green]requeued job {job_id}[/green]")


@jobs_app.command("cancel")
def jobs_cancel(job_id: int = typer.Argument(...)) -> None:
    """Cancel a job (running or queued)."""
    from bankai.db import StateRepository

    repo = StateRepository(get_settings().paths.state_db)
    repo.cancel_job(job_id)
    console.print(f"[yellow]cancelled {job_id}[/yellow]")


@jobs_app.command("clear")
def jobs_clear(
    status: Annotated[
        list[str] | None,
        typer.Option("--status", help="Status to delete; repeat for multiple statuses."),
    ] = None,
    all_jobs: Annotated[
        bool,
        typer.Option("--all", help="Delete jobs in every status, including queued/running."),
    ] = False,
) -> None:
    """Delete queue rows. Defaults to completed, failed, and cancelled jobs."""
    from bankai.db import StateRepository
    from bankai.queue.models import JobStatus

    if all_jobs:
        statuses = list(JobStatus)
    else:
        raw = status or [JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]
        try:
            statuses = [JobStatus(s) for s in raw]
        except ValueError as exc:
            console.print(f"[red]invalid status:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    with StateRepository(get_settings().paths.state_db) as repo:
        count = repo.clear_jobs(statuses)
    console.print(f"[green]cleared[/green] {count} job(s)")


@app.command()
def history(limit: int = typer.Option(20, "--limit")) -> None:
    """Show recently completed pipeline jobs."""
    from bankai.db import StateRepository
    from bankai.queue.models import JobKind, JobStatus

    repo = StateRepository(get_settings().paths.state_db)
    rows = repo.list_jobs(status=JobStatus.DONE, kind=JobKind.PIPELINE, limit=limit)
    table = Table(title="History")
    for col in ("ID", "Finished", "Result"):
        if col == "Result":
            table.add_column(col, overflow="fold")
        else:
            table.add_column(col)
    for j in rows:
        result_summary = j.result.get("final_path", "?") if isinstance(j.result, dict) else "?"
        table.add_row(str(j.id), j.finished_at or "-", str(result_summary))
    console.print(table)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _run_worker_once(
    worker: Any,
    work_dir: Path,
    *,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Run a worker from a foreground CLI command and persist final status."""
    ctx = _make_ctx(work_dir, payload=payload, kind=worker.kind)
    assert ctx.job.id is not None
    try:
        result = await worker.run(ctx)
    except Exception as exc:
        ctx.repo.fail_job(ctx.job.id, f"{type(exc).__name__}: {exc}", retry=False)
        raise
    else:
        ctx.repo.complete_job(ctx.job.id, result)
        return cast(dict[str, Any] | None, result)
    finally:
        ctx.repo.close()


def _make_ctx(work_dir: Path, *, payload: dict[str, Any], kind: Any | None = None) -> Any:
    """Build an ad-hoc :class:`WorkerContext` for one-shot CLI commands."""
    import asyncio as _a

    from bankai.db import StateRepository, initialize
    from bankai.queue.models import Job, JobKind
    from bankai.queue.worker import WorkerContext

    settings = get_settings()
    settings.paths.state_db.parent.mkdir(parents=True, exist_ok=True)
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(Job(kind=kind or JobKind.PIPELINE, payload=payload))
    assert job.id is not None
    job = repo.start_job(job.id)
    return WorkerContext(
        job=job,
        repo=repo,
        work_dir=work_dir,
        cancel_token=_a.Event(),
    )


def _resolve_key(data: dict[str, Any], key: str) -> Any:
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _coerce(value: str) -> Any:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    import tomllib

    with path.open("rb") as f:
        return tomllib.load(f)


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    try:
        import tomli_w
    except ImportError:
        path.write_text(_dump_toml_basic(data), encoding="utf-8")
        return
    with path.open("wb") as f:
        tomli_w.dump(data, f)


def _dump_toml_basic(data: dict[str, Any]) -> str:
    out: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    sections = {k: v for k, v in data.items() if isinstance(v, dict)}
    for k, v in scalars.items():
        out.append(f"{k} = {_toml_val(v)}")
    for sec, body in sections.items():
        out.append(f"\n[{sec}]")
        for k, v in body.items():
            if isinstance(v, dict):
                inner = ", ".join(f'"{ik}" = "{iv}"' for ik, iv in v.items())
                out.append(f"{k} = {{ {inner} }}")
            else:
                out.append(f"{k} = {_toml_val(v)}")
    return "\n".join(out) + "\n"


def _toml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    return f'"{v}"'


if __name__ == "__main__":
    app()
