# bankai

Terminal-first pipeline that fuses the **German dub audio** from a streaming
site with the **HQ video** from a torrent into a single Plex/Jellyfin-ready MKV.

```
filmpalast / aniworld   ──►  Playwright + yt-dlp  ──┐
                                                    ├──► alass sync ──► mkvmerge ──► /mnt/media/bankai/Movies/Title (Year)/Title (Year).mkv
Prowlarr ──► qBittorrent ──► HQ video  ─────────────┘
```

## Install

```bash
curl -sSfL https://raw.githubusercontent.com/73gon/bankai/main/scripts/install.sh | bash
```

The installer creates a venv in `~/.local/share/bankai`, installs `bankai`,
downloads Chromium for Playwright, symlinks `bankai` into `~/.local/bin`,
and prints a hint to add it to `$PATH` if needed. After install:

```bash
bankai config init      # interactive wizard
bankai doctor           # verify ffmpeg, mkvmerge, alass, qbit, prowlarr
bankai                  # interactive menu
```

External binaries needed at runtime: `ffmpeg`, `mkvmerge` (mkvtoolnix),
`alass-cli`, plus Playwright Chromium (installed automatically).

## Update

Run the same installer again. It fetches `main`, resets the checkout, updates
dependencies, refreshes Playwright Chromium, and keeps your config/state files
outside the install directory.

```bash
curl -sSfL https://raw.githubusercontent.com/73gon/bankai/main/scripts/install.sh | bash
```

After that first install, update with:

```bash
bankai update
```

Useful overrides:

```bash
BANKAI_REF=v0.1.1 bash scripts/install.sh
BANKAI_PREFIX=/opt/bankai BANKAI_BIN=/usr/local/bin bash scripts/install.sh
BANKAI_FORCE_REINSTALL=1 bash scripts/install.sh
```

## Daily use

```bash
bankai                           # ASCII banner + menu
bankai run "Finding Nemo 2003"   # auto-search filmpalast, pipeline through
bankai run "Inception 2010" --url https://filmpalast.to/stream/inception
bankai batch movies.txt          # queue many movie downloads in the background
bankai search "matrix"
bankai metadata search "Arcane" --kind show
bankai shows "Arcane" -s 1       # auto-detect show site, queue episodes
bankai transfer --library        # background rsync move to /mnt/media12
bankai jobs list
bankai background list
bankai jobs clear                # delete done/failed/cancelled queue rows
bankai history
bankai doctor
```

Toggle interactive picking:

```bash
bankai config set scraper.interactive_pick true
```

## CLI map

| Command                                    | Purpose                                                             |
| ------------------------------------------ | ------------------------------------------------------------------- |
| `bankai`                                   | Interactive menu (default when no subcommand).                      |
| `bankai shell`                             | REPL mode \u2014 type `run "X"`, `search Y`, etc.                   |
| `bankai run QUERY [--url URL]`             | End-to-end pipeline. Auto-searches filmpalast when `--url` omitted. |
| `bankai batch FILE`                        | Queue movie downloads from a text file.                             |
| `bankai shows SHOW -s N [-e M]`            | Queue a whole show season (or one episode).                         |
| `bankai search QUERY [--site filmpalast]`  | List matches as a table.                                            |
| `bankai metadata search QUERY`             | Show TVDB aliases used by lookups.                                  |
| `bankai transfer PATH...`                  | Background rsync move to the mounted media server.                  |
| `bankai background list/watch/log/clear`   | Inspect detached movie/show/transfer jobs.                          |
| `bankai config init`                       | First-run wizard; writes `~/.config/bankai/config.toml`.            |
| `bankai config get/set KEY [VALUE]`        | Read or write one key.                                              |
| `bankai config list/path/edit`             | Dump effective config / print path / open in `$EDITOR`.             |
| `bankai doctor`                            | Check every dep + service.                                          |
| `bankai jobs list/show/retry/cancel/clear` | Queue inspection and cleanup.                                       |
| `bankai history`                           | Recently completed pipelines.                                       |
| `bankai daemon`                            | Run the dispatcher in the foreground.                               |
| `bankai extract / sync / remux`            | Per-stage debugging entry points.                                   |
| `bankai web serve [--host H] [--port N]`   | Run the web control panel (default `0.0.0.0:9988`).                 |
| `bankai web install-service`               | Install + start the `bankai-web` systemd user service.              |
| `bankai web status`                        | Show whether the web service is active.                             |

## Configuration

Layered: **CLI args > env vars (`BANKAI_*` with `__` for nesting) > `config.toml` > defaults**.

The config file lives at `~/.config/bankai/config.toml` (override with
`$BANKAI_CONFIG`). See [config.example.toml](config.example.toml) for every key.

Highlights:

```toml
[paths]
state_db      = "/home/you/.bankai/state.sqlite3"
work_dir      = "/home/you/.bankai/work"
downloads_dir = "/mnt/media/downloads/bankai"

[output]
directory = "/mnt/media/bankai"   # Movies/Shows folders are created automatically

[audio]
language_tag  = "ger"
track_name    = "German (Web-DL)"
default_track = true              # German is the default audio track

[scraper]
interactive_pick = false          # true = ask before picking from search results

[metadata]
tvdb_enabled = true               # no effect until tvdb_api_key is set
tvdb_api_key = ""                 # TheTVDB v4 API key
tvdb_pin = ""                     # optional subscriber PIN
tvdb_languages = ["deu", "eng"]

[transfer]
root = "/mnt/media12"
movies_dir = "/mnt/media12/movies"
shows_dir = "/mnt/media12/shows"
rsync_binary = "rsync"

[selector]
preferred_resolutions    = ["1080p", "720p"]
preferred_audio_languages = ["english"]   # prefer English HQ video
min_size_gib             = 5.0
max_size_gib             = 80.0

[notifications]
webhook_url = "https://discord.com/api/webhooks/..."
```

## Queue cleanup

There are two ledgers:

- `bankai jobs ...` reads the SQLite pipeline queue at `paths.state_db`.
  `bankai jobs clear` removes `done`, `failed`, and `cancelled` rows. Use
  `bankai jobs clear --status running` only after confirming no bankai process
  is still active.
- The interactive menu's background-job view stores detached process logs in
  `$XDG_STATE_HOME/bankai/jobs` (usually `~/.local/state/bankai/jobs`). Open
  `bankai` -> `Queue / history` and select `Clear finished background jobs`.

## Library layout migration

v0.2 changes the default output layout to:

```text
Movies/Title (Year)/Title (Year).mkv
Shows/Show/Season XX/Show - SXXEXX.mkv
```

Existing files are not renamed automatically during `bankai update`. Preview
the migration first, then apply it after checking the proposed moves:

```bash
cd /home/malik/bankai
.venv/bin/python scripts/migrate_layout.py --dry-run
.venv/bin/python scripts/migrate_layout.py --apply
```

Use `--library /path/to/library` if your `output.directory` is not the library
you want to migrate. Add `--infer-movie-years` when old movie folders were
created without a year, for example `Movies/Cars/Cars.mkv`.

## Movie batches

`bankai batch FILE` queues one background job per non-empty line. Lines use:

```text
English Title 2010
English Title 2010 | German Title
English Title 2010 | German Title | https://filmpalast.to/stream/title
```

Use `bankai batch FILE --dry-run` to preview the parsed jobs.

## Transfers

`bankai transfer PATH...` queues a detached background transfer. It moves files
with `rsync --ignore-existing --remove-source-files`, so existing destination
files are skipped and successful source files are removed only after rsync
finishes. Watch progress any time with:

```bash
bankai background list
bankai background watch JOB_ID
```

Use `bankai transfer --library` to transfer the configured output directory.
Movies go to `transfer.movies_dir`; shows go to `transfer.shows_dir`. The
Discord completion message includes moved files and skipped-existing files.
By default the CLI prints the transfer plan first and asks for confirmation;
use `--yes` to queue immediately.

## Background progress

Detached movie/show/transfer jobs expose parsed progress in:

```bash
bankai background list
bankai background status JOB_ID
bankai background watch JOB_ID
```

Pipeline steps are:

1. Extract stream audio
2. Download HQ video
3. Sync audio
4. Write final MKV

When available, Bankai shows separate progress rows for the Filmpalast audio
download and the HQ qBittorrent download.

## Web control panel

Bankai ships a dark, mobile-responsive web UI for driving downloads and
manually QC'ing the German dub before anything is transferred to the media
server.

```bash
pip install -e '.[web]'          # install web extras (FastAPI + uvicorn)
bankai web serve                 # http://0.0.0.0:9988
bankai web install-service       # run as a persistent systemd user service
bankai web status                # check the service
```

Open `http://<host>:9988`. Pages:

- **Discover** \u2014 trending movies/shows from TheTVDB; click a poster to queue.
- **Search** \u2014 site search; pick a season and episode range for shows.
- **Queue** \u2014 live job progress + colored log tail over a WebSocket; cancel/retry.
- **Library** \u2014 review studio: play in-browser (with ffmpeg transcode fallback
  for 4K/HEVC), switch audio tracks, nudge the German audio delay in
  milliseconds, repack the MKV in place, then **Approve** and **Transfer**.
  Nothing moves to the media server without explicit approval.
- **Server** \u2014 two columns showing which titles already exist on the server.
- **Settings** \u2014 edit safe config keys; secrets are masked (set/unset only).

Configure defaults under the `[web]` section of `config.toml` (port, host,
`max_concurrent_jobs`, `transcode_fallback`, server scan dirs, cache TTL).

## Architecture

- **`bankai.cli`** \u2014 Typer command tree, interactive menu, REPL.
- **`bankai.backend`** \u2014 application services shared by the CLI today and a
  future HTTP/web UI.
- **`bankai.metadata`** \u2014 optional TVDB title aliases and language lookup.
- **`bankai.backend.transfer`** \u2014 rsync-backed safe moves to the mounted
  media server.
- **`bankai.processor`** \u2014 Stage workers: `extractor` (yt-dlp +
  Playwright capture-all + pick-longest), `sync` (alass / ffmpeg
  `-itsoffset`), `remux` (mkvmerge with track tagging),
  `pipeline` (orchestrator + cleanup + notifications).
- **`bankai.scraper`** \u2014 Backend `Protocol` + auto-discovering registry.
  Drop a `@register` class into `src/bankai/scraper/backends/` to add a site.
- **`bankai.torrent`** \u2014 `prowlarr` (search), `selector` (scored
  ranking with English-audio preference + size band), `qbittorrent`
  (Web API), `worker` (pipeline stage with path translation).
- **`bankai.queue`** \u2014 Async dispatcher polling SQLite. Per-stage
  concurrency limits, retries, cancellation, artifact ledger.
- **`bankai.notify`** \u2014 Discord webhook on success/failure.

## Adding a new scraper backend

1. Create `src/bankai/scraper/backends/yoursite.py`.
2. Define a class with `site_id`, `display_name`, `supports_movies`,
   `supports_series` class attributes and async `search`,
   `list_episodes`, `resolve_stream`, `aclose` methods.
3. Decorate with `@register`. Add `list_season(show, season)` if you want
   `bankai shows` to work.
4. Drop a fixture HTML under `tests/fixtures/yoursite/` and write a test.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```
