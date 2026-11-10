# AGENTS.md — bankai developer/agent reference

This file is the authoritative quick-reference for any agent (AI or human) working
on this codebase. Read it in full before making changes.

---

## 1. What bankai does

bankai fuses the **German dub audio** scraped from a streaming site (filmpalast,
aniworld, …) with the **HQ English video** obtained via torrent into a single
Plex/Jellyfin-ready MKV. It is operated through:

- a **CLI** (`bankai …`)
- a **web control panel** (`bankai web serve`) — served on port 9988

---

## 2. Repository layout

```
config.example.toml          Template for every config key (authoritative docs)
pyproject.toml               Python package / deps / entry point
scripts/install.sh           One-liner installer (sets up venv + Playwright)
src/bankai/
  cli/
    main.py                  Typer CLI entry point (all subcommands)
    bgjobs.py                Background-job registry (detached processes)
  config.py                  Pydantic-settings config model
  logging.py                 Rich + ANSI logging setup
  notify.py                  Discord/webhook notifications
  backend/
    services.py              qBittorrent + Prowlarr API clients
    transfer.py              rsync → native-move fallback
  db/
    schema.sql               SQLite schema for the job queue
    state.py                 DB helpers
  metadata/
    tmdb_titles.py           TMDB titles: search, German names, a show's episodes (Movies & Shows; TVDB gone Oct 2026)
    tmdb.py                  TMDB artwork + genres
    anilist.py               Anime identity: AniList API cache, title index, resolve/settle
    offline.py               anime-offline-database per AniList id (titles, AniDB cross map)
  processor/
    extractor.py             Playwright / yt-dlp German stream downloader
    pipeline.py              Orchestrates all four stages end-to-end
    remux.py                 mkvmerge remux stage
    sync.py                  Audio-offset / atempo stage (alass + visual)
    visual_sync.py           Frame-hash visual alignment (coarse→fine)
  queue/
    models.py                Job dataclass
    worker.py                Async job runner
  scraper/
    base.py                  Scraper ABC
    http.py                  HTTP helper
    registry.py              Scraper registry
    backends/
      aniworld.py
      bs_to.py
      filmpalast.py          Primary German source — quote() URL-encoding fix applied
      kinox.py
  torrent/
    matcher.py               Fuzzy title matching
    prowlarr.py              Prowlarr search client (TorrentCandidate + info_hash)
    qbittorrent.py           qBittorrent API wrapper
    selector.py              Quality / size / seeder filter + leading-words guard
    worker.py                Download + poll loop
  web/
    app.py                   FastAPI app factory (all API endpoints)
    availability.py          Background availability checker (filmpalast cache)
    discover.py              TMDB popular/trending + search + poster cache
    jobs.py                  Web-job queue (pending.json + snapshot)
    media.py                 ffprobe probe + repack helpers
    review.py                Per-file review state (stage / delay_ms / fps / drift)
    server.py                Static-file / SPA serving
    frontend/                Vite + React + Tailwind v4 SPA
      src/
        App.tsx              App shell: zon Sidebar (icon rail when collapsed, Ctrl+B; a sheet on mobile)
        pages/
          Library.tsx        Queue table + WaveformReview studio (~1800 lines)
          Discover.tsx       TMDB popular grid
          Search.tsx         TMDB search; German dub queue, or Torrent (follow a show / download a film)
          Server.tsx         Media-server browser
          Settings.tsx
        components/          bankai's own parts (StatusTag, SortHeader, CollectionGraph, …)
        lib/api.ts           Typed fetch wrappers for every API endpoint
      index.html             <html class="dark"> — dark mode always on
      tailwind.config.js     DELETED — v4 uses CSS-only config
      postcss.config.js      @tailwindcss/postcss (v4)
tests/                       pytest — always target 107 pass
```

---

## 3. Two servers

### keller (primary — runs the web UI + pipeline jobs)

> **Moved to WSL (Sept 2026).** bankai no longer runs as the Windows service
> `bankai-web`; that service is stopped and disabled. It runs inside the WSL2
> distro **seireitei** under systemd, together with qBittorrent, so the whole
> pipeline sits on one machine and off the laptop. The Windows rows below are
> kept because the media drives are still NTFS, reached via `/mnt/g`, `/mnt/e`.

| Property     | Value                                                                |
| ------------ | -------------------------------------------------------------------- |
| Distro       | `wsl -d seireitei` (Ubuntu 24.04, systemd, WSL2)                      |
| Repo / venv  | `/home/malik/projects/bankai` , `.venv` installed as `.[web]`         |
| Config       | `/home/malik/projects/bankai/config.toml`                             |
| State        | `/home/malik/.local/state/bankai/`                                    |
| Services     | `systemctl {status,restart} bankai-web` / `bankai-automation` / `qbittorrent` |
| Logs         | `journalctl -u bankai-web -f`                                         |
| Listens      | bankai `127.0.0.1:3003`, qBittorrent `127.0.0.1:8080`                 |
| Prowlarr     | `127.0.0.1:9696`, docker `deploy/seireitei/prowlarr/compose.yaml` (+ FlareSolverr), config `~/.config/prowlarr`; public indexers Knaben, The Pirate Bay, YTS, LimeTorrents, and through FlareSolverr (tag 1) 1337x, EZTV, kickasstorrents.to/.ws, ExtraTorrent.st, Uindex. A site behind Cloudflare passes Prowlarr's test when added and then fails every search: give it the tag (EZTV failed so from Oct 27 to 31 2026). Every German indexer Prowlarr knows is private. Moved here Oct 2026 when the mediaserver went dark |
| Reachable    | `http://seireitei:7003` , qBittorrent `http://seireitei:7004`         |
| Downloads    | `/mnt/g/qbit` (G:\qbit) — **not** inside the WSL image, see below    |
| Staging      | `/mnt/g/bankai/staging` — same volume as the library, so publish renames |
| Media roots  | `/mnt/g/media/{movies,shows,shows_anime}` — 9p, measured 88.9 MB/s    |
| Boot         | Windows task "Seireitei WSL Server" starts the distro and holds it open |
| Setup        | `deploy/seireitei/setup.sh` (idempotent, run as root)                 |

**Two processes.** `bankai-web` serves the UI; `bankai-automation` (`bankai anime
worker`) runs the anime automation cycle. The web unit sets
`BANKAI_ANIME_WORKER=external`, so it never runs the cycle itself: its Retry /
Run-now buttons write `erai_cycle_request.json`, and the worker's status comes
from its heartbeat `erai_worker.json`, both in the state directory. Without the
variable (tests, Windows) the web process runs the cycle as before. The two
share the release state under a file lock (`erai_state.lock`).

**The library worker** (Nov 2026): `bankai-library-worker` (`bankai library worker`, `web/library_worker.py`)
runs the passes that rewrite library files -- encode, browser audio, track cleanup -- when bankai-web is started
with `BANKAI_LIBRARY_WORKER=external`, so a restart of bankai-web (any deploy) no longer cuts an encode off. They
talk through files: the queues and rules where they always were, written by the web UI (a change to one makes its
pass due at once in the worker); the worker's heartbeat `library_worker.json` (every 2 s) carries each pass's
`progress` -- read through `encode_library` / `browser_audio` / `track_cleanup.current_progress()`, never the
module's `progress` dict from outside the pass -- and each pass's run for the Dashboard (`pass_row`). Shared state
files are written under `web/state_lock.StateLock` (a thread lock plus an flock), the four modules' `_LOCK`. One
process has the passes at a time: whoever runs them holds `library_passes.lock` (`claim`); bankai-web without the
variable takes it at startup, the worker waits for it (its recovery would throw away the half-written result of an
encode running in bankai-web), and a bankai-web finding it taken leaves the passes alone. On a change to bankai's
code the worker starts no new file, lets the running ones finish and exits 3; systemd (`Restart=always`) brings it
back on the new code -- nobody waits for an encode to end to update. Restarting the worker itself still stops its
encode (it goes on from its last finished step).

**Tabs answer from snapshots** (`web/snapshots.py`): each tab's response is
kept built and gzipped, rebuilt in the background when its input files change,
and invalidated by any successful non-GET `/api/` request so the page's next
load waits for fresh data. A new tab endpoint that is slow should be a snapshot.

Downloads sit on G:, not on ext4 inside the image. The image lives on C:,
grows with everything written into it and never shrinks back, and reports its
virtual maximum as free space -- so the 100 GiB reserve never fired and 233 GB
of torrents filled C: to zero (Sept 2026). On G: the free space is real, and
download, staging and library share one volume.

#### Windows side (drives only, service disabled)


| Property            | Value                                                                                              |
| ------------------- | -------------------------------------------------------------------------------------------------- |
| Host                | `192.168.178.27`                                                                                   |
| SSH                 | `ssh keller` (alias; lands in Windows PowerShell)                                                  |
| OS                  | Windows 11 native                                                                                  |
| Repo                | `C:\bankai`                                                                                        |
| venv                | `C:\bankai\.venv`                                                                                  |
| Config              | `C:\bankai\config.toml` (env: `BANKAI_CONFIG=C:\bankai\config.toml`)                               |
| Web service         | nssm `bankai-web` (Automatic / LocalSystem)                                                        |
| Web URL             | `http://192.168.178.27:9988`                                                                       |
| Logs                | `C:\bankai\logs\web.log`                                                                           |
| ffmpeg/ffprobe      | `C:\bankai\bin\`                                                                                   |
| mkvmerge            | `C:\Program Files\MKVToolNix\`                                                                     |
| Library (staging)   | `C:\bankai\library\`                                                                               |
| Approved media      | `G:\media\movies` / `G:\media\shows`                                                               |
| Pending jobs JSON   | `C:\Windows\System32\config\systemprofile\AppData\Local\bankai\web_pending.json`                   |
| Review state JSON   | same `AppData\Local\bankai\review.json`                                                            |
| Downloads (local)   | `C:\bankai\downloads\`                                                                             |
| path_map            | `/downloads` → `C:/bankai/downloads`                                                               |
| Selector config     | `preferred_resolutions=["1080p","2160p"]`, `min_seeders=15`, `min_size_gib=1.0`, `max_size_gib=25` |
| max_concurrent_jobs | `1` (string coerced to int by Pydantic)                                                            |

**CRITICAL — never restart the service while a job is running.** nssm's job
object kills all detached child processes → job fails with empty reason.

### mediaserver (Linux — qBittorrent + Prowlarr + extraction)

> **Dark since Oct 2026** (no answer to ARP on .29). Prowlarr runs on seireitei now (see above);
> `[prowlarr] url` is `http://127.0.0.1:9696`. qBittorrent sits inside gluetun and cannot reach
> Prowlarr's loopback links, so bankai fetches each `.torrent` itself (`torrent.worker.fetch_torrent`)
> and hands qBittorrent the magnet it redirects to or the file.
> The German stream extraction ran here over SSH (`scraper.remote_extract_ssh`) for its display; it is
> empty since, and seireitei extracts itself (Playwright + its own Xvfb). Every filmpalast job failed with
> "No route to host" until then. The pipeline's HQ torrents go into qBittorrent's `bankai-dub` category
> (`torrent.worker.CATEGORY`): under `bankai` the anime automation, while off, paused them as its backlog.

| Property        | Value                                                                                                                   |
| --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Host            | `192.168.178.29`                                                                                                        |
| SSH             | `ssh malik@192.168.178.29` (`ssh mediaserver` alias NOT configured on dev box)                                          |
| OS              | Ubuntu/Debian                                                                                                           |
| Repo            | `/home/malik/bankai`                                                                                                    |
| venv            | `/home/malik/bankai/.venv`                                                                                              |
| Config          | `/home/malik/bankai/config.toml`                                                                                        |
| qBittorrent     | `http://localhost:8080` (admin/437581), category=`bankai`, saves to `/downloads/bankai` → `/mnt/media/downloads/bankai` |
| Prowlarr        | `http://localhost:9696`, api_key=`a9f6b9c1fb0949598060ea0724cd79d5`, 7 indexers                                         |
| Xvfb            | `:99`–`:102` (headful Playwright extraction)                                                                            |
| Downloads mount | `/mnt/media/downloads`                                                                                                  |

---

## 4. Deploy sequence (standard)

```powershell
# 1. Build frontend (run from repo root on dev box)
cd src/bankai/web/frontend
npm run build

# 2. Run tests
cd d:\projects\mov_scraper
poetry run pytest -q          # must be 107 passed, 0 failed

# 3. Commit
git add -A
git commit -m "…"
git push origin main

# 4. Deploy to keller (now WSL under systemd, not the Windows service)
ssh keller "wsl -d seireitei -- git -C /home/malik/projects/bankai pull --ff-only"
ssh keller "wsl -d seireitei -u root -- systemctl restart bankai-web bankai-automation"
ssh keller "wsl -d seireitei -- curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3003/api/health"
# Expected output: 200

# Quoting: nesting ssh -> PowerShell -> wsl -> bash strips $variables and eats
# & and quotes. For anything non-trivial, base64 the script and decode it on
# the far side -- several silent failures came from exactly that.

# 5. Sync mediaserver (when pipeline/scraper/torrent code changed)
ssh malik@192.168.178.29 "cd /home/malik/bankai && git fetch --all && git reset --hard origin/main && git rev-parse --short HEAD"
```

**The web service on keller must always respond 200 after deploy.** If it does
not, check `C:\bankai\logs\web.log` via:

```powershell
ssh keller "Get-Content C:\bankai\logs\web.log -Tail 50"
```

---

## 5. Key operational gotchas

### PowerShell remote execution pitfalls

- **Never** use `&&` in ssh commands from local PowerShell — use `;` instead.
- `$_`, `$var` in double-quoted strings are interpolated by local PowerShell.
  Escape them with a backtick (`` `$var ``) or ship as base64.
- Multi-line `run_in_terminal` often doesn't show output — prefer
  `execution_subagent` for reliable output capture, or use single-line commands.

### Service restart kills jobs

Restarting `bankai-web` terminates all detached child jobs (nssm job object).
**Always confirm no job is running before deploying.**

### Frontend after a deploy

The SPA bundles are hash-named and `index.html` is served with
`Cache-Control: no-cache`, so a normal reload picks up a new build. (Before
that header, a hard refresh was needed once more after this change.)

### Rate-limited Prowlarr indexers

Rapid repeated searches (e.g. 5+ within 2 minutes) trigger Prowlarr's per-indexer
rate-limit. The indexer's `disabledTill` timestamp shows when it recovers. Wait
before re-running the torrent step.

### ffprobe encoding on Windows

All `subprocess.run` calls that invoke `ffprobe`/`ffmpeg`/`mkvmerge` on Windows
**must** pass `encoding="utf-8", errors="replace"` — otherwise the default cp1252
codec silently corrupts non-ASCII output (e.g. subtitle titles with 0x8D bytes),
yielding empty `MediaInfo` with 0 audio tracks. This is already applied in
`media.py` and `remux.py`.

---

## 6. Frontend tech stack

| Thing             | Detail                                                                               |
| ----------------- | ------------------------------------------------------------------------------------ |
| Framework         | React 19 + TypeScript                                                                |
| Build             | Vite 6                                                                               |
| CSS               | **Tailwind v4** (`@import 'tailwindcss'`) — no `tailwind.config.js`                  |
| PostCSS           | `@tailwindcss/postcss` (replaces v3's `tailwindcss: {}`)                             |
| Component library | zon/ui (`@zonsh/ui` from npm, on Base UI); `render` prop, no `asChild`               |
| Theme             | zon tokens + material utilities (`@zonsh/ui`, trimmed in `src/zon.css`); status colours kept in `index.css`; the mark (`components/bankai-mark.tsx`, `public/favicon.svg`) is crimson, `--brand` / `text-brand` (#e0283f; #b3122e on a light tab) |
| Dark mode         | Always on (`<html class="dark">`)                                                    |
| Fonts             | Licht (zon.sh/licht), self-hosted in `public/fonts/licht`: Licht Serif (headings), Licht Sans (body/UI), Licht Mono (mono) |
| State             | React hooks only (no Redux/Zustand)                                                  |
| API               | `src/lib/api.ts` — typed `fetch` wrappers, all endpoints relative to window origin   |

**zon/ui.** From npm since Nov 10 2026: `@zonsh/ui` (0.5.0), imported as `@zonsh/ui` (`@zonsh/ui/resizable` for the
split view's panes); the sources copied into `src/zon/` before are gone. Styles: not `@zonsh/ui/styles.css` but
`src/zon.css`, generated from it by `npm run zon:styles` (`scripts/zon-styles.mjs`, run it after updating the
package): the same entry less the parts bankai uses none of (foundations, brand, editorial, isometric, pdf, ai,
graph -- their selectors were matched against every element at every style recalculation; code blocks alone
400,000 tries a page), and Tailwind pointed only at the components bankai imports and what they import (all
102 scanned, button-group's `:nth-child(… of …)` selectors were generated and matched against every element).
Fonts: the package asks for `/licht/fonts/*.woff2`; bankai serves its own copies there (`public/licht/fonts`),
no `zonFonts()` proxy to zon.sh. bankai's patches -- a fit table's measurements kept across a hide and show,
at most 48 rows measured, only rows near the view glide, the sidebar rule without `:has()`, ToggleGroup items
`flex-auto` -- are a patch-package patch (`patches/@zonsh+ui+0.5.0.patch`, applied on `npm install` by
`postinstall`) until zon/ui 0.5.1, which takes all but the toggle sizing (zonsh/ui PR #24); drop it then.
React 19 is needed (`ref` as a prop). Status labels use `StatusTag` (`components/status-tag.tsx`), counts use
zon `Badge`, toasts come from `@/lib/toast` (`inBackground`: a dialog's work after it closes, a spinner toast
turning into the outcome). Run the dev server against live data with
`BANKAI_API=http://seireitei:7003 npm run dev`.

**One library, three times** (Nov 2026): what the anime, Movies & Shows and manga libraries share lives in
`components/library/library-page.tsx` -- `useLibraryView(scope)` (grid / table / split, kept in the browser as
`bankai.<scope>.library.view`; split only from 64rem, `useWide`), `useLibraryAddress(scope, params, {split})` (the
open title in the address, open/close/Back and the split view's remembered title -- `useRememberedOpen` -- for
one or several parameters: Movies & Shows' `movie` and `show`), `LibraryHeader` (title, search, the page's own
buttons, `ViewSwitch`, Rescan), `LibraryBody` (spinner, empty state, `PosterGrid` of `PosterCard`s, the table,
or the table beside the open title in `LibrarySplit`), `TitlePage` ("← Library" over a title opened outside the
split view), `LibraryFooter` / `FooterStat`, `formatSize`. Each page brings its rows, columns, card contents and
title page. Written three times they had drifted: Movies & Shows' view switch was three icon buttons, manga's
cards another design. A new library uses these.

**Library titles open as pages, not drawers** (Oct 30 2026). The anime, Movies & Shows and manga
libraries read the open title from the address -- `/a/library?show=<key>`, `/mas/library?movie=<key>` /
`?show=<key>`, `/m/library?series=<id>` -- and show it as its own page ("← Library"; opened from the list,
Back closes it). The view toggle's **Split** (64rem and wider, an experiment) is `components/library-split.tsx`
(zon `Resizable`, `react-resizable-panels`): where its divider sits is a UI setting kept for every device
(`split.<id>`; `lib/prefs.ts` keeps it in the browser at once and PUTs it to `/api/ui/prefs/<key>` a moment
later, `web/ui_prefs.py` -> `ui_prefs.json`; `/api/ui/` writes invalidate no snapshot), and the title open in
it is opened again on coming back from another tab (`useRememberedOpen`). The split takes the height the
scrolling area shows below its top (`useFillHeight`, less what follows it, measured by its size, not where it sits: `spaceBelow` -- a footer pinned to the page's bottom, Movies & Shows' counts, moved with nothing and kept the split at its first height, half the screen (Nov 2026); the area's scrollHeight is never under its height, so on a page shorter than a tall screen it gave back the split's own height and the split stayed at its 400 px floor), so each pane scrolls on its own: the group sets
`width`/`height: 100%` inline, which beat any class on it, and the page scrolled both panes (Nov 2026). Where AGENTS.md still says
"drawer" it means that view. The library tables are zon `<Table fit>`: as they narrow, lesser columns move
into the title cell (`inline`, by `priority`) before any goes. Episode columns are one `EpisodeBar`
(`components/episode-bar.tsx`): a show's episodes end to end, season after season (`strip`, from
`anime_library.episode_strip` / the MAS library), each stretch lit where the library has them and coloured
by encode (anime) or language (shows).

**Speed** (Oct 30 2026, measured tab by tab in the browser; most of the lag was the browser, not the
server). Pages are kept once visited (`App.tsx` `KeptPages`: React `<Activity>`, each page reading the
address it was last shown at, memoised so a hidden page never renders again on someone else's click, its
scroll position kept): coming back is instant, effects (fetching, polling) rest while hidden and run again
when shown. A GET whose answer text is unchanged returns the same object (`lib/api.ts` `request`), so a
page refreshing or coming back sets state it already has and React does nothing; keep it so -- derive
lists with `useMemo`, not into state (a `filter` into state made the anime library lay out 243 rows on
every visit), and show a spinner only while there is nothing to show yet. Posters: `lib/posters.ts`
(`srcset` of AniList's small/medium/large or TMDB's widths, `sizes="auto"`, lazy, async decoding) -- a
32px thumbnail took the 460px cover, and Blacklist spent 3 s decoding covers; `poster-card` /
`offscreen-skip` cards skip rendering out of view (`content-visibility`; not on fit-table rows: a
contained row cannot be a subgrid). Slow tab endpoints are snapshots (`/api/genres`, `/api/tracks`,
`/api/encode` too now).
Scrolling areas paint zon's page ground themselves (`page-ground` in `index.css`: the app's `SidebarInset`, the
libraries' grids and table bodies, both split panes): the grain is baked once into a tile by `lib/ground.ts`, the
light from above drawn over it, `background-attachment: local`. See-through, they let body's ground show, but on a
screen at 100% scaling Chrome then painted the whole page again every scroll frame and blended both grain layers anew
(20-30 fps on the manga review); opaque, they scroll on the compositor (60). Flat `bg-background` hid the ground and
the page looked black -- give a new page-level scroller `page-ground`. A long list of heavy cards is virtualized (`@tanstack/react-virtual`; the manga review: 178 cards,
30,000 elements, a tick or a letter typed redrew them all in a third of a second), and search fields filter
through `useDeferredValue` (Nov 2026).

**Tailwind v4 deprecated aliases still work** (e.g. `bg-gradient-to-b`) but the
linter will suggest `bg-linear-to-b`. The build succeeds either way.

---

## 7. API quick-reference (web)

All endpoints are served by the FastAPI app at `/api/…`. Base URL on keller:
`http://192.168.178.27:9988`.

| Method | Path                                                       | Purpose                                                 |
| ------ | ---------------------------------------------------------- | ------------------------------------------------------- |
| GET    | `/api/health`                                              | Liveness check — returns `{"ok":true}`                  |
| GET    | `/api/titles`                                              | Unified queue+library row list                          |
| POST   | `/api/queue`                                               | Enqueue a new movie `{title, year, url?, site?}`        |
| DELETE | `/api/queue/{job_id}`                                      | Cancel / delete a queued/running job                    |
| GET    | `/api/jobs/{job_id}/log`                                   | Last N log lines for a job                              |
| GET    | `/api/media/info?path=…`                                   | ffprobe result + review state for a library file        |
| GET    | `/api/media/waveform?path=…&stream=…&start=…&dur=…&bins=…` | Peak envelope (max 4000 bins)                           |
| GET    | `/api/media/audioclip?path=…&stream=…&start=…&dur=…`       | Short MP3 clip                                          |
| GET    | `/api/media/videoclip?path=…&start=…&dur=…&height=…`       | Short H.264 clip                                        |
| POST   | `/api/review/repack`                                       | Remux with delay (+ optional `atempo` drift correction) |
| POST   | `/api/review/approve`                                      | Mark library file approved for transfer                 |
| POST   | `/api/library/delete`                                      | Delete library file (writes tombstone, shows "Deleted") |
| GET    | `/api/discover`                                            | TMDB popular / search results                           |

**Waveform endpoint hard cap:** `bins` parameter capped at 4000 by the backend
(`le=4000` Query constraint). Requesting more returns 422 and silently leaves
the canvas blank. Always pass `Math.min(4000, …)`.

**Concurrent ffmpeg cap:** `_FFMPEG_SLOTS = threading.BoundedSemaphore(4)` in
`app.py`. A 5th concurrent waveform/audioclip/videoclip request gets a 503
`{"detail":"transcoder busy"}`. The frontend retries up to 4× at 600 ms intervals.

---

## 8. Pipeline stages

```
extract  →  torrent  →  sync  →  remux  →  library
```

1. **extract** (`processor/extractor.py`) — Playwright/yt-dlp downloads the German
   audio (or video when visual sync is enabled) from filmpalast/aniworld/etc.
2. **torrent** (`torrent/worker.py`) — Prowlarr search → qBittorrent download →
   poll until complete. Prefers `candidate.info_hash` as the tracking key.
3. **sync** (`processor/sync.py` + `visual_sync.py`) — aligns German dub to the
   HQ reference using frame-hash visual matching (coarse→fine, median offset,
   Theil-Sen drift, confidence). Low confidence sets `needs_sync_review = True`.
4. **remux** (`processor/remux.py`) — `mkvmerge` merges HQ video + German audio
   with `--sync` delay and optional `--default-track`.

0. **German release first** (`selector.german_release_first`, on; Nov 2026) -- before any of it, Prowlarr is
   searched for "<title> German" (and by the German title TMDB gives, `_german_title`; episodes: the season pack
   first). A release whose name says German audio (`german_audio_named`: German / Deutsch / GER, but not
   "Subbed" / "GerSub" unless it also says DL or Dubbed), ranked by the selector with German preferred, is
   downloaded through the torrent stage (`manual_candidate`); if its tracks hold German (`direct.has_german`,
   ffprobe) it is copied losslessly into the library MKV (`_copy_into_mkv`, every track kept) with nothing to
   sync and no review flag; otherwise it is removed and the stages below run as before. The German track synced
   from a stream often needed its delay set by hand.

The pipeline is orchestrated in `processor/pipeline.py`. The web UI enqueues
jobs via `web/jobs.py` → `web/app.py`. Jobs are dispatched by `web/app.py`'s
lifespan thread-pool (96 tokens, `_FFMPEG_SLOTS = 4`).

---

## 9. Audio drift correction

When a German dub runs at a different speed than the reference (classic PAL 25fps
vs NTSC 23.976fps), a constant delay alone cannot fix the sync.

**How it's detected:** `visual_sync.py` computes a `drift_ratio` (Theil-Sen slope).
`pipeline.py` also probes the German source video fps (`_probe_fps`) and stores
`source_fps`, `reference_fps`, and `drift_ratio` in `review.py`'s `ReviewState`.

**How it's corrected in the web review player:**

- The review studio (`Library.tsx` → `WaveformReview`) shows a **Drift** row
  under the German waveform.
- If `drift_ratio` was measured by the pipeline, a **Suggest ×factor** button
  appears (preferred over the crude duration ratio).
- The user can fine-tune with `−/+` (±0.05% steps) and preview the waveform in
  real time.
- On **Approve**, if `stretch ≠ 1`, `api.repack()` is called with `{atempo, track_index}`.
- The backend (`media.py → repack_audio_drift`) runs:
  1. `ffmpeg -filter:a "atempo=…" -c:a aac -b:a 384k` → `stretched.mka`
  2. `mkvmerge --audio-tracks !{original_idx} <orig> --sync 0:<delay_ms> --default-track 0:yes <stretched.mka>` → overwrites the library file.

---

## 10. Review state

Stored in a JSON file at `LOCALAPPDATA\bankai\review.json` (keller) or
`~/.local/state/bankai/review.json` (Linux).

Key fields per path entry:

| Field               | Meaning                                                          |
| ------------------- | ---------------------------------------------------------------- |
| `stage`             | `review` → `approved` → `transferred` → `deleted` (tombstone)    |
| `delay_ms`          | Constant audio delay already applied to the file                 |
| `needs_sync_review` | True when visual-sync confidence was low                         |
| `sync_confidence`   | 0..1 confidence from visual alignment                            |
| `auto_delay_ms`     | Offset the pipeline auto-applied (baseline shown in review)      |
| `source_fps`        | German source video fps (captured during pipeline, if available) |
| `reference_fps`     | HQ reference fps (captured during pipeline)                      |
| `drift_ratio`       | Measured drift slope (source speed / reference speed)            |
| `transfer_status`   | `idle` / `transferring` / `done` / `failed`                      |

`library_delete` writes `stage="deleted"` (tombstone) instead of removing the
entry — this makes deleted movies show a **Deleted** badge in the queue table even
after the file is gone. Re-downloading the same title resets the tombstone.

---

## 11. `_is_german` heuristic

`media.py → _is_german(lang, title)` decides which audio track is the German dub.

- **Language tag** (`lang`) is matched **exactly** against `{"ger", "deu", "de",
"german", "deutsch"}`.
- **Title string** is split into whole words (`re.findall(r"[a-z]+", title.lower())`)
  and each token is checked for membership. This avoids false positives from
  English track titles like _"Audio Description"_ or _"Extended"_ that contain
  the substring `"de"`.

---

## 12. Torrent selection guards

`selector.py → _filter_by_query` rejects candidates with **extra leading content
words** beyond the query title (e.g. the query "Obsession" would match "Toxic
Obsession" without the guard). The set difference `(cand_words − query_words −
stop_words)` must be empty.

`prowlarr.py` stores `info_hash` from the Prowlarr API `infoHash` field.
`torrent/worker.py` uses it as the primary tracking key (`known_hash`), and the
fuzzy fallback explicitly excludes `before_hashes` to avoid locking onto a
leftover torrent from a previous run of the same title.

---

## 13. Running tests

```bash
poetry run pytest -q          # fast; target: 107 passed, 0 failed
poetry run pytest -x          # stop on first failure
poetry run pytest tests/test_web_api.py -v   # specific file
```

Tests are pure-Python (no live servers required). They mock HTTP, ffprobe, and
filesystem calls. Always run before committing.

On seireitei the repo **is** the live instance, and bankai reads `config.toml`
from the working directory. `tests/conftest.py` (`_never_the_live_instance`)
gives every test its own config, state directory and an unreachable
qBittorrent; before it existed, running the suite there paused every real
download and wrote into the live config (Oct 2026). Never remove it, and a new
test that needs a config sets its own.

---

## 14. Common one-liners

```powershell
# Check keller health
ssh keller "(Invoke-WebRequest -UseBasicParsing http://localhost:9988/api/health).StatusCode"

# Tail keller web log
ssh keller "Get-Content C:\bankai\logs\web.log -Tail 80"

# List running ffmpeg processes on keller
ssh keller "(Get-Process ffmpeg -ErrorAction SilentlyContinue).Count"

# Fetch last 40 lines of a specific job log (replace JOB_ID)
# From dev box PowerShell:
$log = (Invoke-RestMethod 'http://192.168.178.27:9988/api/jobs/JOB_ID/log').log
($log -split "`n" | Select-Object -Last 40) -join "`n"

# Kill all stuck ffmpeg on keller (use only when no job is running)
ssh keller "Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force"

# Rebuild frontend only
cd src/bankai/web/frontend; npm run build

# Reset keller to latest without restarting the service (read-only sync)
ssh keller "cd C:\bankai; git fetch --all; git reset --hard origin/main"
```

---

## 15. Anime library (AniList)

Anime is identified by **AniList** alone (AniDB, Shoko and TVDB are gone from the
anime side since Oct 2026). An AniList entry is one season or cour; a release is
an entry plus its own episode number.

| Piece | Where | What |
| ----- | ----- | ---- |
| Identity | `metadata/anilist.py` | title index over anime-offline-database + AniList API cache (`anime_metadata/anilist_media.json`, batches of 50, weekly); `resolve_in` (season markers walk SEQUEL chains), `settle_in` (episode past an entry's end -> its sequel) |
| File index | `web/anime_files.py` | `anime_files.json`: path relative to the anime root (same under `<staging>/Shows`) -> `[(anilist_id, episode)]`. Written on publish, layout moves, deletes, manual match. Never guess identity from folder names. |
| Relation graph | `components/CollectionGraph.tsx` | Lines carry no labels (stacked on one shared line they could not be read): a side entry says on its card what it is to the entry it hangs off (`Layout.relation`); links the layout hangs nothing by are faint. The manga and light novels stand framed on the left ("Manga & light novels"), each card saying its anime, with no line to it (those crossed every row) |
| Collections | `web/anime_collections.py` | `anime_collections.json`, bankai-owned: members, title entry, custom name, Season 00 order. New entries join by SEQUEL/PREQUEL; side stories by hand in the relation graph. |
| Disk layout | `web/anime_layout.py` | `<root>/<Collection>/Season NN/<Collection> - SxxEyy.mkv`; main line = Season 1..n, every other member a Season 00 block. Writes `tvshow.nfo`, `season.nfo`, episode `.nfo`, posters (skipped under 2 GiB free). Renames are journaled in `anime_layout_moves/`; `bankai anime layout-undo <file>` reverts one run. An entry that is a season alone can be split into named arcs, a season each (`Collection.arcs`, drawer → Seasons; `web/anime_arcs.py` holds suggestions, Bleach's 16): episodes keep their numbers (S02E21), the seasons after move up, arc names go into `season.nfo`. A rename carries the file's encode records, track inventory and probe along (`_follow`), and never moves the file being encoded: its show's fingerprint holds it until the encode is done. Season tabs read "Season 2" where the entry's name says no more once the show's name is off (`short_label` compares letters and digits only, so a collection named like its folder -- "ReZero - …" -- still matches "Re:ZERO -…-"; else "S4 · Final Season"). A season can be named by hand (`Collection.season_names`, by the entry it opens with; drawer → Rename season, `POST /api/anime/collections/{cid}/season-name`): the tab and `season.nfo` show it. |
| Upkeep | `web/anime_maintenance.py` | scheduler pass every 5 min: offline DB weekly, AniList refresh a few batches per pass, joins/extends collections, re-lays out collections whose fingerprint changed. |
| Missing episodes | `web/anime_missing.py` | pass "Look for missing episodes" (10 min): German-subtitled releases for aired episodes not on disk -> `anime_missing_checks.json`; library tag "N available", drawer "Download N remaining episodes" (queued only on approval). **New episodes by themselves** (`anime.auto_download_new`, on; Nov 2026): an episode aired in the last 14 days of a show being followed -- its episode before on disk, or for an episode 1 the season before has files (`_new_episodes`) -- is searched again every 3 h instead of weekly, and a single-episode release found for it is queued at once (`queue_releases`, also what the dialog's download uses; `enqueue_download` is the drawer's one-release queueing, moved out of `app.py`). Older missing episodes and batches still wait for a click |
| Late German subs | `web/erai_subs.py` | Erai-raws uploads `.ger.ass` files after release in a members-only folder (`/subs/?dir=Sub/<year>/<season>/<show>/<NN>`, `HEVC/<NN>` for encodes). Needs `anime.erai_username`/`erai_password` (a real login; the feed token gets 403). A release qualifies through it (`--german-sub-url`, staged as `<stem>.de.ass`); pass "Add German subtitles from Erai-raws" (15 min) adds them beside library episodes without German. Cache: `erai_subs.json` |
| Artwork | `web/anime_artwork.py`, `metadata/tmdb.py`, `metadata/animelists.py` | Per-collection slots (poster, fanart, landscape, logo, banner, `season:N`) written as Jellyfin local images. Automatic: AniList poster/banner, TMDB backdrop/thumb/logo/Specials poster; AniList→TMDB ids from Fribb's anime-lists (weekly, `anime_metadata/anilist_external.json`), TMDB images cached a month (`tmdb_images.json`). Hand choices in `anime_artwork.json`, from the drawer's Artwork dialog. Needs `metadata.tmdb_api_key`, toggle `anime.artwork_tmdb` |
| Episode pictures | `web/anime_thumbs.py` | `<episode>-thumb.jpg` from the video at `anime.thumb_mode` (`percent` / `time`), `thumb_percent`, `thumb_time_seconds`, `thumb_width`; each picture's setting in `anime_thumb_specs.json`, so a changed setting (or "Take all pictures again") retakes them, 20 a minute |
| Files to match | `web/anime_unsorted.py` | A download's files that are no episode of its entry -- marked another season (`S00E01` OVAs and films beside `S01E..`), or with no number (NCOP) -- are not filed and do not fail the job: hard-linked to `<staging>/../anime_unsorted/<release>/` (beside staging, which Library and Transfer read whole), rows in `anime_unsorted.json`. Review → "Files to match": tick files, pick an entry (the release's, its relations, its collection's; or search), adjust numbers; filed through the layout, published, and an entry in no collection joins the release's show. Over an episode the library has: `/check` first, then `replace` |
| Episode check | `web/anime_verify.py` | Whether each file is the episode it is filed as, without AniDB (Shoko's hash lookup needs a login, and a file Tracks or Encode rewrote has no release's hash). Picture: the file's keyframes (read end to end through `dd`, 32x18 RGB; stops once two of the expected episode's stills are seen) against TMDB's stills of every episode of its show (`anime_metadata/tmdb_episodes.json`, stills scaled down in `tmdb_stills/<tv>.json`, 6 an episode): an episode scores by its second-best still, right ones 0.89-1.0 (`MATCH` 0.86). Called **wrong** only when nothing else explains it (`certainly_wrong`): another episode's stills at 0.9 or more, its own at 0.6 or less, neither a special, more than two episodes apart -- of the first 1066 files every looser "wrong" was right: next-episode previews and recaps (AoT S03E19 showed S03E20 at 0.997), recap specials, flashback episodes. Anything undecided is shown as nothing. The user's word overrides it: "It's the right episode" (`POST /api/anime/library/verify/confirm` {paths, right}; `confirmed` in the store, kept while the file's size and time stay, follows renames; the file is not checked again). Which TMDB episode is expected comes from air dates (the AniList entry's start against TMDB's episodes, within Fribb's season): TMDB keeps Re:Zero as one 85-episode season and Fribb names season 1 for all five entries. Length (`judge_length`): the picture's length (a track running past it made SPY x FAMILY S02E11 "60 minutes") against TMDB's runtime of the episode, taken as fitting when AniList's per-episode length fits instead (TMDB counts some specials as one) unless TMDB gives the episode another length than its season's others; AniList's alone judges only far off (0.5-2x), an episode 0 not at all. A length off is shown whatever the picture says (`length_off`: a row tag "Length off", the library's "N length off", "It's the right episode" dismisses it) -- Re:Zero S01E01, 24 of its 50 minutes, sat unshown as "unsure" (Nov 2026). Count (index facts, shown whatever the picture says): two files on one episode, past the entry's end. Pass `anime_verify` (2 min, ~4 min of files each), results in `anime_verify.json` by path (size+mtime; follows layout renames). Library: "N wrong episodes" tag; drawer: only what is wrong -- a line saying how many files are another episode, and per wrong row (no mark on files found right or confirmed, and no Check again button since Nov 2026: the pass checks in the background; `POST /api/anime/library/verify` stays) its findings, and for a wrong one "Make it <entry> ep N" (assign), "Delete this copy" or "It's the right episode". Found Re:Zero "Season 01" E01-13 to be copies of Season 2 (Nov 2026) |
| Migration | `web/anilist_migration.py` | `bankai anime migrate-anilist [--dry-run]` — one-off from AniDB/Shoko; backups in `anilist_migration_backup/`. |

In the anime library a missing episode that has aired can be ticked too (`tickKey`: `missing:<aid>:<ep>`; only
Replace with magnet link uses them, every other action takes the ticked files): one batch magnet then replaces the
episodes on disk and fills the missing ones (`missing` on `/api/anime/replace-magnet` and `/api/magnet/preview`,
`{anilist_id, anilist_episode, season_number, episode}` into the same `--episode-map`; Nov 2026) -- before, one
episode on disk and ten not took a Replace and a separate download of the same batch.

Episode names come from AniList's streaming list (Crunchyroll's titles), else MyAnimeList's (`jikan`, fetched by
the maintenance pass for entries left untitled). AniList sometimes hangs one season's streaming list on several
entries -- Dr. STONE's first season carried SCIENCE FUTURE's, so both tabs read "Ryusui vs. Senku" (Nov 2026):
`anilist._streaming_owner` gives an identical list only to the newest entry that is a season (not a film or
special: One Piece's list is the series'), the others take MyAnimeList's names. 303 entries shared a list, 58 of
them in the library.

Replace with magnet link (anime, Movies & Shows) shows what the magnet is before anything is queued (`web/magnet_preview.py`, `POST /api/magnet/preview`): name, size and files read from the swarm by qBittorrent's `torrents/fetchMetadata` (5.2+; nothing is added or downloaded; a live torrent answers in seconds, the dialog asks again for up to two minutes), what bankai knows of it (Erai-raws' listing: subtitles, resolution; a release it had), and per file the ticked file it would replace -- the replace's own matching (anime: the file's episode number onto SxxE<N>; shows: SxxEyy; a film: the largest video). The qBittorrent tab's context menu runs an errored torrent again (Retry, and Retry all errored: `POST /api/qbittorrent/torrents/{hash}/retry`, a recheck then a start -- qBittorrent leaves one in error stopped until then).

The Dashboard's rows, after the numbers: Workers, Recently added, then Up next and System sharing a row (3:2 from 80rem, System's two halves stacked there; System's Swap turns amber only while pages move, `swap_in/out_per_second` from `/proc/vmstat`: a full swap of idle programs' pages is no trouble). Recently added is a row of its own (three libraries side by side need the width): Movies & Shows and Anime each switch between Episodes and Series (`recent.mas_episodes`, `recent.anime_episodes`), Manga lists downloads filed, newest first (`dashboard.recent_manga`: "Vol 01-12, Chapter 0191", and the chapter files a volume replaced).

**Upgrade to HEVC** (a show's drawer, or the library's "Upgrade all") replaces watchable files, so it is approved first: the dialog shows each HEVC release found (Erai-raws, German subtitles), the AniList entry it is taken for, and every file it would replace (`POST /api/anime/library/upgrade-hevc/preview`; `erai.upgrade_show_to_hevc(queue=False)` returns `plans`, kept in `anime_hevc_plans.json` so "Upgrade all" shows them without asking Nyaa again, while every file is unchanged). Queueing (`POST /api/anime/library/upgrade-hevc`, bulk with `approved`) takes only ticked releases, `approved: {info_hash: [paths]}`, worked out afresh; a release whose files are not exactly the approved ones is left out (`changed`); without `approved` it is refused. And a replacing download (`--replace-existing`, no `--episode-map`) never writes a file whose own name is another AniList entry over this one (`processor.anime._named_entry`; it goes to Files to match). Both came of Re:Zero (Nov 4 2026): `anilist_match` read a batch's name with the cleaner alone, "2nd Season - 01 ~ 13" lost its season, and the second season's batch replaced Season 01 E01-13; it reads batches as Erai names now (`_erai_identity_name`), and a season number the name carries is looked for ("Show Season 2").

Download on the Erai-raws page (no card) shows the entry the name was matched to, with its year and a **Change** button
(`AniListPickDialog`; `anilist_id` on `replace-plan` / `replace`): the pick is for that download alone, the saved
choice for the name stays. A saved choice is not applied to a release uploaded more than two weeks before its entry
began airing (`erai._chosen_after`, the Erai row's `published`): Erai names a new season as plainly as the first, and
the choice made for War of the Three Titans Arc's "Tokyo Revengers - 01" took the first season's 2024 batch for it
(Nov 2026).

Replace (a review card's Erai-raws search, or Download on the Erai-raws page without a card: the entry from the release name) takes a hand-picked release as it is, but one listing no German never replaces files that can be watched in German unless that is confirmed (`replaces_german`, `lose_german`). A downloaded release whose episodes another release holds now (`erai.superseded`: an upgrade or a Replace took them) shows "Replaced since" and may be taken again -- Mushoku Tensei's German batch, swapped for HEVC singles without German (Oct 2026).

The Erai-raws tab's search suggests shows as one types (`/api/anime/erai/suggest`, `erai_site.suggest`): Erai's show names from the releases bankai has read (its anime-list page carries no names in its HTML, and asking the site more risks WP Defender's lockout), and every other name the anime-offline-database gives those shows, so "solo lev" finds Ore dake Level Up na Ken. Each row has its magnet link (copy button; Nyaa's trackers, `anime.magnet_uri`).

**Which entry a release is** (`anime.anilist_match`, offline; search rows, the missing-episode pass, the library's
Replace, filing): Erai's own name shapes first, then `release_readings` -- the names any uploader's title may go by,
best first, each with the season, episode and year it says: "Title - S04E02v2", "Romaji (English) - 05", "Title
S01E06 1080p ... (Other Name, Multi-Subs)", "Title Season 2 (BD ...) | Other Name", "標題 / Title S2 - 18",
"Movie (2019)", "Title 01-13 (...)", "Title - Ep03", "Title 08 (1080p ...)" (a number ending the title is tried as
the title's first: "Mob Psycho 100"). `_season_entry`: season N is "Name Season N" walked by AniList's sequels
(`anilist._resolve_season`, stepping past a season's later parts -- "Season 3 Part 2" -- unless that reaches no
season: Chitose's "S2" is its "2nd Cour"), else the name itself only if it is a later season; season 0 (S00Exx)
only an entry that is no season, else unmatched -- a special was shown as the show's episode of its number; a year
picks "Name (2011)" or among same-named entries. A number past a later season's end counts from the show's first
episode ("Black Clover Season 2 - 176" is its 6). A user's saved choice whose key lost the name's season
("honzuki no gekokujou" for "S04E23") comes after what the name itself says. Search (`anilist_matches`) first asks
AniList for the sequel chains such names walk -- kept only for entries bankai had to do with (1,900 of 20,700) --
a few requests, kept a week. Of 2,334 nyaa (1_2, 1_3, 1_4) and nekobt names, 882 were unmatched and several
dozen wrong (Mushoku Tensei S3 as S2, AoT S04 as S3 Part 2, specials); 406 are left, 166 of them in Chinese or Japanese script only,
raws and non-anime (Nov 2026).

Search (`/a/search`, the old `/a/discover` and `/anime` lead there) has two sources, switched at the top right and remembered (`bankai:anime-discover` in the browser; the search
and the filters both have -- quality, publisher, title/description terms, minimum seeders -- go along on a switch,
the other source showing the same search; Nyaa's category and nekobt's languages and batches stay each its own): **nyaa.si** (`web/anime.py`, RSS + listing) and **nekobt.to** (`web/nekobt.py`, its JSON API `/api/v1/torrents/search`; rows in Nyaa's shape plus source type, codec and languages). nekobt reads a list of languages as all of them, so subtitles (a release's own or a fansub's) and audio are asked one language per request and merged: any of them; the choices come from `anime.nekobt_languages`. A publisher nekobt knows as a group is asked by `group_id`. Batches alone or single episodes alone: nekobt's own `batch=true|false`. Each page shows the requests it made (`sent`). nekobt downloads go by magnet alone (`source: nekobt`, empty torrent/detail links). Rows say what the library has of their entry (`anime.mark_owned`: `owned`, `episodes`; "In library", "5/12 in library"), and so does the download dialog (`/api/anime/owned`). With it the audio and subtitle languages of those episodes (`languages`: the `track_cleanup` inventory's tracks plus subtitle files beside them, `<stem>.de.ass`; per language how many episodes have it), and `gains`: German audio or subtitles a nekobt release lists that some of them lack (`GAIN_LANGUAGES`) -- worth a Replace, since a download leaves episodes the library has out. Minimum seeders on both sources (nekobt has no such filter: bankai's own).

**One file per episode** (`processor.anime._one_file_per_episode`): files of one download that are one episode's parts ("01 (Part One)", "01 (Part Two)", `part_number`) are joined into one (`_join_parts`: mkvmerge, lossless, ASS subtitles joined script by script with each part's styles, `merge_ass`; checked by length); any other second file of an episode goes to Files to match. Erai's Re:Zero batch staged both parts of its double-length first episode as S01E01, the second over the first (Nov 2026). A Replace with an episode map downloads only the ticked episodes' files.

A download that is no replacement (no `replace_existing`, no `--episode-map`) never takes an episode the library has (`anime_files.in_library`: the library's own copy, staging not counted -- a staged one is a download to carry on with): once qBittorrent knows the torrent's files, those episodes and their subtitles get priority 0 (`_leave_out_owned`), a torrent with nothing new is removed undownloaded (`already_in_library` in the result), and filing leaves out whatever got through (`skip_owned`), so no second copy waits in staging beside the library's. Swapping an episode for another release is Replace.

**Every torrent is some job's** (`web/torrent_ties.py`, Oct 30 2026). Each pass of the release reconciler (`erai`, every 20 s from startup) reads bankai's anime category from saved state alone -- qBittorrent's list, the job records (`--info-hash`, a running job's active torrent), the queue, the release table -- so no restart or crash loses a torrent: one finished with no owner and an episode the library lacks is taken up after 15 min as a "complete" release and filed by the usual job (`--publish`); one whose episodes are all in the library goes after 2 h (`cleanup_after_success`); anything else (still downloading, held on purpose, no episode in its name, blacklisted) is only counted ("in qBittorrent with no job" on the Dashboard). Deleting a job from the queue takes its torrent (bankai's categories only, unless another job or a driven release still reads it). The queue's tidy no longer takes a release marked `duplicate`/`done` for finished work: My Hero Academia S07E05-08 were tidied away as "duplicate" on Oct 29 and their torrents sat seeding unfiled. `processor/direct.sweep` also removes unfinished `bankai-direct` torrents no running job reads after 2 h.

A blacklisted entry is never downloaded, whoever queued it: `anime-download` refuses it (`Series blacklisted`, also through `--episode-map`) and the queue's tidy removes its failed jobs instead of running them again (Utawarerumono, run again the night after it was blacklisted, Oct 2026). Its folder still shows in the library, with a Blacklisted badge: the library shows everything on disk.

Keys: canonical `anilist:<id>|<ep>`, blacklist/review cards `anilist:<id>`, job
args `--anilist-id/--anilist-title`. Jellyfin's anime library reads the NFO files
(NFO reader only, no online metadata fetchers).

## 15b. Library upkeep: Tracks and Shrink

| Piece | Where | What |
| ----- | ----- | ---- |
| Tracks | `web/track_cleanup.py`, page `/tracks` | Per library (anime, shows, movies; roots as the Server page has them, a root listed twice belongs to the first) the audio and subtitle languages switched off are taken out, one file at a time, by mkvmerge (lossless); a non-MKV video is put into MKV. Pass `track_read` reads every video's tracks with ffprobe, 150 a minute, into `track_inventory.json`; pass `track_clean` writes the next file anew beside the original (`.<name>.tracks.part`), checks track counts and duration, and replaces it only if the original did not change meanwhile. A shorter container alone is no failure: a track taken out may have run past the picture (a subtitle with a late line), so then the picture's end is compared (`_picture_end`, its last frames read with ffprobe). Tracks queued by hand (`POST /api/tracks/remove`) end a failed file's day of waiting. Never the last audio track; files hard-linked to a seeding torrent (`st_nlink > 1`) and files changed in the last hour wait. The replaced file's mtime is the old one + 1 s: Jellyfin re-reads its tracks, recency order stays. Rules in `track_rules.json`, results in `track_cleanup.json`; a copy cut off by a restart is removed at startup (`recover`) |
| Browser audio | `web/browser_audio.py`, card on `/tracks` | Chrome and Firefox decode no AC3, E-AC3, DTS or TrueHD, so Jellyfin converted the sound of every such play (no device in the house passes Dolby/DTS through). A lossy track (AC3, E-AC3, DTS without MA) is **replaced** by AAC: its channels (written as the standard layout -- AC3's `5.1(side)` came out of the encoder as six channels of unknown layout; relabelled, each channel's level is the same), a bitrate no lower than its own (at least 192k 2.0, 384k 5.1; at most 256k a channel), its language, default/forced/commentary flags and place, and its name with the codec word made AAC (`aac_name`: "DD+ 5.1 @ 640 kbps" -> "AAC 5.1 @ …", the kbps the AAC's own; a name with no codec word stays). Added tracks are named "AAC 5.1" / "AAC 2.0". Lossless (TrueHD, DTS-HD MA -- `dts` too, told apart by ffprobe's `profile`, kept in the inventory since Nov 2026 -- PCM) is **kept**; a language whose main sound (commentaries and audio descriptions aside, by name) is lossless alone gets an AAC from it (5.1 at most, 448k), before it and default when it was. A lossless default whose language has an AAC (or gets one from its AC3) gives that track the default: browsers play the default, and a default TrueHD beside an AC3 played no AC3. Almost every lossless film carries a Dolby Digital track of its language, so few AAC are added (about +2 GB in all, not 40-60). Native ffmpeg `aac` (no libfdk on seireitei). ffmpeg encodes the tracks (`-copyts`: a dub 40 ms late stays so) into `.<stem>.audio.part.mka`, mkvmerge writes the file anew beside it (`--track-order`), checked: track counts, length, each AAC's start (50 ms), length (0.5 s), channels and layout; replaced only if the original did not change, mtime + 1 s, Jellyfin re-probes (its subtitle cache per track index forgotten). MKV only. Pass `browser_audio` (60 s), one file at a time; off until switched on (`browser_audio.json`), files tried by hand (`POST /api/browser-audio/try`) done even while off. Seeding, changed in the last hour, being encoded or having tracks taken out: waits (and Encode and Tracks wait for it) |
| Shrink | `processor/shrink.py`, `bankai shrink run/check` | One file's picture encoded again with NVENC (RTX 2060 SUPER: ~32 fps 4K, ~100 fps 1080p), HEVC or H.264, peaks capped at 40 Mbps; HDR10 metadata on the MKV track (ffmpeg 6.1's NVENC writes no mastering SEI), Dolby Vision 7/8 kept as 8.1 via `dovi_tool -m 2` (`~/.local/bin`). Steps leave files in a work folder (`encode.json` marks the finished encode; other settings start over) and resume. Profile 5 is refused |
| Encode | `web/encode_library.py`, page `/encode` | Not automatic: the page lists per library (anime, shows, movies) the files worth encoding, from the `track_cleanup` inventory (which keeps each file's video: codec, size, bit depth, `BPS`, DV profile, interlacing), shows grouped by show; the ticked ones join `encode_queue.json` and the pass `encode` takes them in order, one at a time, a file that waits (seeding, changed in the last hour, having tracks taken out) keeping its place. Listed: HEVC pictures >= 30 Mbps 4K, 15 Mbps 1080p, 10 below, and in every library each non-HEVC file (H.264, VC-1, MPEG-2/4) whatever its bitrate; the result is always HEVC (quality 24 4K, 22 below; 22 / 20 until Oct 30 2026): HEVC stays HEVC, H.264 becomes HEVC; AV1 never. Work folder `<drive>/bankai/encode/<sha1>` on the file's own drive. Replaces the original only after `verify`, SSIM >= 0.95 at three places (catches broken encodes), >= 20% smaller and an unchanged original; mtime + 1 s. The encode must hold the picture's own frames: mkvmerge's `NUMBER_OF_FRAMES` (or MP4's `nb_frames`), else the video track's `DURATION`, counted with ffprobe when that disagrees -- never the container's length, which an audio track starting late makes longer (GoT S01, Oct 2026: ten good encodes thrown away). The raw encode has no frame times of its own: mkvmerge lays its frames on an even grid at the source's rate, unless the source's frames are not evenly spaced (its frames against its picture's length at its rate) -- then they get the source's own times (`frames.txt`, timestamp format v2, read with ffprobe) -- and `verify` refuses a picture not as long as the source's (3 frames; the container's length is its longest track's and hid it). Naruto Shippuden's stream downloads hold a jump or two of seconds in their frame times: on the grid their picture ran up to 4 s ahead of the sound, and only some failed the SSIM check (Oct 30 2026). The two that passed it (S12E302, S12E326) got their picture's hole back by a remux: the sound keeps its holes (a cut ad break, 48 frames of 1001/24000) where the picture lost its time (Oct 31 2026). A step after the encode that fails keeps the encode in the work folder; queued again, the file goes on from it Files done are kept with their size in `encode_state.json` (never listed twice); not worth it and failed too (failed ones can be queued again). The Dashboard's Encoding lane shows the file and the queue. Measured: GoT 4K and Ice Age 1080p at half the size, SSIM 0.995 / 0.987 |

## 15c. Manga and light novels

The anime flow's review, without anything automatic at the end: found on nyaa
(Literature, English-translated, `3_1`), matched to AniList, downloaded only once
approved, filed for **OpenPanel** (the user's own reader, `~/projects/openpanel`,
CBZ; EPUB only as page-less entries so far).

| Piece | Where | What |
| ----- | ----- | ---- |
| Identity | `metadata/anilist_manga.py` | AniList `type: MANGA` (formats MANGA/ONE_SHOT = "manga", NOVEL = "novel"), 50 a batch, kept a week in `anime_metadata/anilist_manga.json`, every relation kept `[id, relation, type, format]`. The anime cache keeps each anime's manga links apart in `manga` (`relations` stays anime only); entries cached without it are fetched again by the maintenance pass |
| Names | `web/manga_names.py` | A release name into series names (split on ` \| ` / ` / `), volumes (`v01-14`, `Vol.10 - Vol.11`), chapters (`CH001-140`, a bare `001-166` but not years `{2021-2023}`, `+ 138-147`), tags (`()`, `[]`, `{}`; the last one cut off with the name too); light novel told by what it says, else size per volume (< 25 MiB). `file_number` / `target_name`: files are filed as `Vol 01.cbz`, `Chapter 0138.cbz`; a bare number of three digits or more ending a name is a chapter (1r0n's weekly `Jujutsu Kaisen 191 (2022) (Digital) (1r0n).cbz`) |
| Library | `web/manga_library.py` | `manga.library_dir` / `novels_dir` (`D:\books\manga`, OpenPanel's). Which entry each folder is, in `manga_library.json`: an id in its name (5-6 digits), else OpenPanel's own database (read only, `C:\ProgramData\openpanel\openpanel.db`), else an exact AniList title + year; otherwise matched by hand (Library -> To match). New folders: `Title (Year) - <id>` -- OpenPanel reads the id and hides it from the shown name. `owned` reads volumes/chapters from file names, cached by folder mtime |
| Followed | `web/manga_series.py` | manga/novels an anime on disk links to as ADAPTATION / SOURCE / ALTERNATIVE (spin-offs only in the graph), + library folders, + followed by hand; minus `manga_policies.json` blacklist (`anilist:<id>`) |
| Releases | `web/manga_releases.py` | pass `manga_discover` (`manga.poll_interval_seconds`): adopts folders, reads the newest `3_1` uploads, searches `searches_per_pass` series due (each every `search_interval_days`, by English/romaji title up to the subtitle); a finished series the library has whole -- every volume, or Complete in its drawer (`whole`) -- is never searched again, and its drawer shows no Releases button unless some still wait (Nov 2026). Kept only when a name is a followed series' title (manga vs novel by kind; searched for, a name that starts the title is a guess); audiobooks and month-old 0-seeder torrents skipped; "owned" when it adds nothing (`adds`: volumes the folder has no book of, whole chapters it lacks by `Holdings.lacks` -- read from names and pages, or where nothing else can be; a FINISHED series with every volume lacks only the holes between the chapters its books name, Jujutsu Kaisen's 191). A release whose tags say novel (`(Novel)`, `[WN ...]`; `tagged_novel`, not a title with the word in it) is never a manga. Every release's name is read again each pass (`tidy`), so a better reading reaches rows found before it; a waiting one it no longer matches goes, as if never found. `manga_releases.json` statuses: review -> queued -> downloading -> publishing -> done; discarded, owned, failed. Pass `manga_reconcile` (30 s, only with approved ones): adds to qBittorrent (category `bankai-manga`, `<save_path>/manga`); once qBittorrent knows the torrent's files, those the library does not need get priority 0 (`_pick`; a torrent with nothing needed is removed undownloaded); files only the finished books that fill a gap (`wanted`: a volume the folder has no book of, a chapter it lacks -- not a name it has, one moved from it to another series, an in-between number, a second copy, or a book without a number beside numbered ones; `.part` then rename), removes the torrent with the rest, asks OpenPanel to scan (`web/openpanel.py`, `manga.openpanel_*`). The row keeps `copied` and `left` (name, why), shown on Downloads and Releases |
| Chapters | `web/manga_contents.py` (`holdings`, cached by folder mtime), `manga_views.breakdown` | Which chapters each CBZ holds, read from its pages' names (`c0036 (v005)`, `Chapter 001 - .../`, `v01c01/`, `001/01.jpg`; a ZIP's name list is one small read), kept per file by size+mtime in `manga_contents.json` and warmed 400 archives per discovery pass. A volume holds its pages' longest stretch of chapters without a gap over 10, every chapter from its first to its last (`own_chapters`: pages named for the chapter before; a number far off is another numbering or a slip -- JoJo Part 5 v01 opens with Part 4's 172-174, TG:re v15's "c197"); two volumes one after the other hold the chapters between them (JJK v22 names 191-199 "c192"-"c200"). The drawer's Chapters tab: every chapter in runs -- in which volume file, as chapter files (with the volume their pages name), or missing; volumes whose pages name nothing share the chapters between the volumes around them by volume number (a volume not on disk keeps its share missing), trailing ones the rest of a finished series or the average ("worked out"), a row each; a FINISHED series with every volume is whole whatever AniList counts. Every file of the folder is in a row, so the tab is the file list too: a file that holds no chapter of its own (Vol 0 with its own numbering, a second copy, an in-between number, chapters another file holds, no number at all) stands beside where it belongs, saying why. A light novel's rows count volumes (an EPUB names no chapters) |
| Pages | `/m/search`, `/m/library`, `/m/review`, `/m/blacklist`, `/m/settings` | Review: a card per series, Download / Discard per release, Change series (ticked releases), Blacklist series. Library: series on disk, followed ones, downloads, folders to match, as posters or a sortable table like the anime library's (Volumes and Chapters as `have/total` meters -- the card's `count` is the drawer's -- State a badge: Complete, Up to date (not finished), Partial, Unknown, Not in library; the drawer's Chapters tab shows the same badge); a drawer per series (`?series=<id>`, the State badge beside its title): the AniList link beside the title; Relations (a header button, the graph in a dialog: `MangaGraph`: the collection's entries in a row, what relates to them in bands -- one per entry and relation, anime above, manga/novels below, each joined to its entry by one line and named once (a line and a label per card crossed each other and the cards, Nov 2026) -- what the library has first in full colour with a green frame, the rest greyed with a dashed frame; pointer events stop at the graph so a pan does not swipe the drawer shut); Remove and blacklist with books on disk, else Blacklist (`POST /api/manga/series/{id}/remove`, `manga_releases.remove_series`: its downloads stopped and their torrents removed, its folder deleted -- only the one `manga_library.json` knows, straight under its root -- and blacklisted; refused while one of its downloads is being filed; the downloads pass never brings a stopped one back, `_change(only=ACTIVE)`). The page (Nov 2026): a collection's entries as tabs above the chapters (where Chapters / Releases were); the chapters are the page itself (a tick box and a Move button, shown on hover, per row), the releases a dialog (Releases, beside Relations). Entries already opened, and a collection's others fetched behind the open one, show at once from memory while their fresh answer comes. Books that are another series (Jujutsu Kaisen's Vol 00 is Jujutsu Kaisen 0) move there: a row's Move, or tick rows and use the bar that then floats at the drawer's bottom (as the anime library's). `manga_library.move` checks every name first, makes the target's folder, renames (Vol 00 -> Vol 01 suggested) and keeps what left in the series' `moved` (`manga_library.json`), so a later download of it does not file the book back; `POST /api/manga/series/{id}/move`. The dialog numbers each book instead of a free name (Volume / Chapter / Name as is, and a number): `POST .../move/plan` (`manga_views.move_plan`) suggests its own number where it fits the target (whole, from 1, within AniList's count, not on disk), else the target's first missing one, and each row says what it does (fills a gap, past the count, a second copy, taken: refused). **Renumber** (a row's button beside Move, and the ticked bar's) is the same with the series itself as target: renamed in place, through names of their own so two may swap (Jujutsu Kaisen's bonus chapter, filed as "Chapter 0168.1" in its own entry, is Chapter 0001; Nov 2026). The anime drawer lists an anime's manga (`LinkedManga`, `/api/manga/linked`) |
| Auto review | `manga_releases.auto_review` | `manga.auto_review` (on): after each discovery pass and each finished download, per series in the review -- its plan approved (as Download plan); once nothing of it downloads, "covered" releases (nothing the library lacks, chapters inside its volumes) set aside as owned; then one "unsure"/"extra" release, the best seeded, approved -- a download takes only the files the library lacks (`_pick`), one with none is removed undownloaded, so trying costs its file list; the next on a later turn. Guessed matches, and what there is no room for, wait for the user. Rows it approved carry `auto`. A bare number of three digits or more ending a release's name is its chapter ("A Couple of Cuckoos 206 (2024)", `_read_numbers`) unless the series' title ends so ("Mob Psycho 100"). 54 series waited for a click before it (Nov 2026) |
| Plan | `manga_releases.plan`, `take_plan` | Per series in the review: the fewest waiting releases that bring everything they offer -- volumes before chapters, one pack before its pieces, then trusted, then seeded; downloads under way count as had; chapters inside volumes are no gap (a name holding both, "v01-14 + 138-147", says so, else 6 a volume, only where every volume up to there is had or offered); a guessed match is never taken. Each release's `role`: plan, covered (another version of books the plan brings), extra, unsure. The card: Download plan (`POST /api/manga/series/{id}/plan`), the other versions folded away; bankai downloads only a torrent's files the library lacks, so a plan taking one volume of a pack downloads that one. **Volumes where they exist, chapters only past the last volume** (Nov 2026): a volume the folder holds only as chapter files is still wanted (`adds`; the review says `replaces`), and once filed it takes their place -- `file_release` deletes the chapter files its pages name, or whose pages name it (`replaced_by`), after the volume is on disk; a volume stays out only when other volumes hold its chapters (`wanted`). Chapter releases bring only what no volume holds; a finished series with every volume needs no chapter (`complete`). Bankai's own changes to a folder clear its caches (`manga_library.forget`): a book filed and the files it replaced gone within one clock tick left the folder's time as it was |
| Collections | `web/manga_collections.py`, `web/manga_layout.py` | `manga_collections.json`: the entries of one story the library has or follows -- AniList's main line (sequels, prequels) among one kind, followed through entries not on disk -- refreshed where read; the rest by hand in the relation graph, as the anime library's (drawer -> Relations: the collection whole and framed, AniList's links between its entries; click a manga to pick it, Shift adds; then split off, pull in, merge a whole collection in, or move an entry in no collection into one -- `/api/manga/collections/{include,split,merge,exclude}`; one split off alone is `alone`, never joined again on its own). Library: one row per collection (its first entry's place, every entry counted); the drawer's tabs are its entries, the pen renames it. On disk (Nov 2026): a collection with two or more entries on disk gets a folder, the entries' folders inside it named "Title (Year) - id" (OpenPanel takes the folder holding the books as the series and reads the id from it); books get the library's names (`Vol 01-02.cbz` for one of two volumes). Nothing moves on its own but a change by hand (its entries' folders follow) and a download filed into a collection (`ensure_folder` gathers the entry already on disk); the rest waits for the library's banner (`GET /api/manga/layout`, `POST /api/manga/layout/apply`). Every run is journaled in `manga_layout_moves/`, `POST /api/manga/layout/undo` puts one back. `manga_library.folders` lists the series folders inside a collection's |

## 15d. Curated genres

bankai's own genre list, written into Jellyfin's normal Genres field so every client shows it (no client integration).
`web/genres.py`: the pure rules engine -- vocabulary in priority order (English + German names, no `/` or `|`), raw genre /
`#tag` / library mappings, `yields` (Anime takes Animation's place) -- with stores `genre_rules.json`, per-title
`genre_overrides.json` (keys `anime:<cid>`, `tmdb:movie:<id>`, `tmdb:tv:<id>`; never Jellyfin item ids),
`genre_sources.json` (Jellyfin's raw values, frozen once bankai writes a title) and `genre_state.json`. `web/genre_sync.py`:
the dry run. Raw genres: TMDB's by TMDB id (`metadata.tmdb.ensure_genres`, English, a month in
`anime_metadata/tmdb_genres.json`; a wrong TVDB match gave Avatar: The Way of Water a documentary's genre), Jellyfin's
where TMDB has none, AniList's for anime. Page `/genres`: Vocabulary (drag to order, sources per genre), Titles (filter,
add / remove per title), Unmapped. The writer (full DTO round trip without `Trickplay`, Genres locked; anime via
tvshow.nfo, never locked) runs as the hourly pass `genre_sync` once switched on (Oct 2026: on for movies, shows and anime).

Titles tab: a genre the title's Jellyfin genres gave before bankai first wrote it and the rules do not give
(`jellyfin_only`) is shown struck through only until bankai writes the title -- after that Jellyfin no longer has
it, and struck through it read as "removed by hand". Clicked back, it is kept as a hand add but shown grey, the
title's own (`kept_from_jellyfin`), not blue "added by hand".

**Age ratings** (Genres page, tab Age ratings; `web/age_ratings.py`, Nov 2026): an FSK rating for every anime
collection and show, as Jellyfin gives German films (`OfficialRating` "FSK-16"). From TMDB's German rating
(`/tv/{id}/content_ratings`, a month in `anime_metadata/tmdb_ratings.json`), else 18 for an entry AniList files
under Hentai, else an estimate from another country's (`ESTIMATE`: US, AU, BR, KR), marked so, else set by hand.
Which TMDB show: a show's own (Jellyfin's id); an anime collection's from Fribb's anime-lists by its title entry
(177 of 179 in Nov 2026), else another member, else TMDB's search by name and year, Animation first -- shown as a
guess, kept until set by hand. Store `age_ratings.json`: `hand`, `tmdb` (matches and names), `written`. Nothing
is written until approved (Write ticked / Write all): anime get `<mpaa>` and `<uniqueid type="tmdb">` in
tvshow.nfo through the layout (the rating is in the collection's fingerprint only once there is one, so the rest
keep theirs; never locked), shows their Jellyfin item as genre_sync writes genres -- the whole DTO,
`OfficialRating` set and locked, what it had kept for undo (`POST /api/age-ratings/undo`).

## 15e. Torrents (Movies & Shows)

Page `/mas/torrents`: any release Prowlarr finds (Movies or TV categories; quality, title has / lacks,
minimum seeders -- Prowlarr gives no descriptions to filter on), downloaded **as it is** into a library
root picked per download (`web/direct.py` `targets()`: every Movies and Shows root, "English" when the
folder ends in `_en`; the anime roots are left out -- anime is filed by AniList from the Anime page).
No dub, sync or review. Job `direct-download` (`processor/direct.py`): its own lane, no pipeline slot
(`jobs._is_direct_job`), and its own qBittorrent category `bankai-direct` -- under `bankai` the anime automation,
while off, paused it with the rest of its backlog. A movie is its largest video, `<root>/<Title (Year)>/<Title (Year)>.ext`; a
show's files go by their SxxEyy into `<show>/Season NN/<show> - SxxEyy.ext` -- the root's own folder
for the show when it has one (matched with or without its year), flat when that folder is flat. A file
already there is never replaced. Search rows keep their links server-side (`direct.found`): Prowlarr's
download links carry its API key.

## 15f. Followed shows (English, from torrents)

Search → a show → **Torrent** tab → Follow (`web/mas_follow.py`, store `mas_follow.json`): every aired
episode TMDB lists is downloaded, no review. The dialog shows seasons, what the library has and an estimate
(median per-episode size of the indexers' releases at the chosen quality, else running time). Pass
`mas_follow` (10 min, 3 Prowlarr searches a pass, at most 6 follow downloads at once, 20 GiB reserve): a season
mostly missing → its pack, else single episodes (a pack search that found nothing turns into episode searches);
retried after 12 h; a season held only in English is searched with "German" every 3 days. Picking: right show
(`same_title`, "The Office US" counts), right season, no multi-season packs, German first, then quality (never
above the one chosen), then seeders; releases that failed before are skipped. Jobs are `direct-download` with
`--root-german/--root-english --follow <tmdb> --episodes S01E01,…`: each file goes by its **own audio**
(ffprobe; release name only if unreadable) into the German or English folder, an English file never beside a
German or English copy, a German one replaces the English-only copy (`direct.remove_with_sidecars`). Library
page: followed shows get cards even with nothing on disk; their drawer (`FollowedShowView`) lists every
episode German / English / Downloading / Missing / Not aired, with Search per episode or season (a Prowlarr
picker, filed the same way) and Look now. An English episode's subtitles count when they are a file beside
it (`<stem>.srt`, `.en.srt`, a `Subs` folder: `direct.english_subtitle_file`), in the drawer and on the library's
episode bar alike: the bar read only the file's own tracks and called Dexter S03-S05 "without English subtitles"
(Nov 2026). The bar reads the names from the held tree (`library_walk` keeps subtitle names beside the videos,
`lister`), no disk read per file. Films: the Torrent tab lists releases best first, Download files by
language the same way. TMDB numbering is used; a show the release groups number differently shows episodes
missing that are on disk under another number.

## 16. Things that must NOT happen

- **Never push code that breaks `poetry run pytest -q`** (target 107 pass).
- **Never restart `bankai-web` while a real job is running** — it kills the job. That includes the
  Dashboard's **Encoding** lane: an encode cut off restarts its picture from zero (up to an hour of GPU work).
  With the library worker on (`BANKAI_LIBRARY_WORKER=external`) encodes, browser audio and track cleanup run
  there and a bankai-web restart leaves them; never restart `bankai-library-worker` by hand mid-encode -- it
  restarts itself between files when the code changes.
- **Never request more than 4000 waveform bins** from the API — the backend
  rejects it with 422 and the frontend silently shows a blank canvas.
- **Never use `&&` in ssh commands from PowerShell** — use `;`.
- **Never call `review_mod.forget(path)` on a deleted file** — use
  `review_mod.set_stage(path, "deleted")` so the Deleted badge persists.
- **Never use bare string substrings to detect German audio tracks** — always
  use whole-word tokenisation (see §11).
- **Never identify an anime file by its folder name** — the file index
  (`anime_files.json`) says what each file is; the layout decides folders.
- **Never download a manga or light novel release the user did not approve** — discovery
  only ever adds "review" rows; `approve`, the Search page's Download and `auto_review` (the user's
  `manga.auto_review`, on since Nov 2026) are the only ways in, and `auto_review` never approves a
  guessed match.
- **Never add `autoprefixer` or `tailwindcss` v3 back** — the project is on
  Tailwind v4 (`@tailwindcss/postcss`); a `tailwind.config.js` would conflict
  with `@theme inline`.
