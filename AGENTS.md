# AGENTS.md — bankai developer/agent reference

This file is the authoritative quick-reference for any agent (AI or human) working
on this codebase. Read it in full before making changes.

---

## 1. What bankai does

bankai fuses the **German dub audio** scraped from a streaming site (filmpalast,
aniworld, …) with the **HQ English video** obtained via torrent into a single
Plex/Jellyfin-ready MKV. It is operated through:

* a **CLI** (`bankai …`)
* a **web control panel** (`bankai web serve`) — served on port 9988

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
    tvdb.py                  TVDB v4 metadata / alias resolver
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
    discover.py              TVDB trending + poster cache
    jobs.py                  Web-job queue (pending.json + snapshot)
    media.py                 ffprobe probe + repack helpers
    review.py                Per-file review state (stage / delay_ms / fps / drift)
    server.py                Static-file / SPA serving
    frontend/                Vite + React + Tailwind v4 SPA
      src/
        App.tsx              Collapsible sidebar (dark, icons-only when collapsed)
        pages/
          Library.tsx        Queue table + WaveformReview studio (~1800 lines)
          Discover.tsx       TVDB trending grid
          Search.tsx         TVDB search + queue
          Server.tsx         Media-server browser
          Settings.tsx
        components/ui/       shadcn/ui + Radix components
        lib/api.ts           Typed fetch wrappers for every API endpoint
      index.html             <html class="dark"> — dark mode always on
      tailwind.config.js     DELETED — v4 uses CSS-only config
      postcss.config.js      @tailwindcss/postcss (v4)
tests/                       pytest — always target 107 pass
```

---

## 3. Two servers

### keller (primary — runs the web UI + pipeline jobs)

| Property | Value |
|---|---|
| Host | `192.168.178.27` |
| SSH | `ssh keller` (alias; lands in Windows PowerShell) |
| OS | Windows 11 native |
| Repo | `C:\bankai` |
| venv | `C:\bankai\.venv` |
| Config | `C:\bankai\config.toml` (env: `BANKAI_CONFIG=C:\bankai\config.toml`) |
| Web service | nssm `bankai-web` (Automatic / LocalSystem) |
| Web URL | `http://192.168.178.27:9988` |
| Logs | `C:\bankai\logs\web.log` |
| ffmpeg/ffprobe | `C:\bankai\bin\` |
| mkvmerge | `C:\Program Files\MKVToolNix\` |
| Library (staging) | `C:\bankai\library\` |
| Approved media | `G:\media\movies` / `G:\media\shows` |
| Pending jobs JSON | `C:\Windows\System32\config\systemprofile\AppData\Local\bankai\web_pending.json` |
| Review state JSON | same `AppData\Local\bankai\review.json` |
| Downloads (local) | `C:\bankai\downloads\` |
| path_map | `/downloads` → `C:/bankai/downloads` |
| Selector config | `preferred_resolutions=["1080p","2160p"]`, `min_seeders=15`, `min_size_gib=1.0`, `max_size_gib=25` |
| max_concurrent_jobs | `1` (string coerced to int by Pydantic) |

**CRITICAL — never restart the service while a job is running.** nssm's job
object kills all detached child processes → job fails with empty reason.

### mediaserver (Linux — qBittorrent + Prowlarr + extraction)

| Property | Value |
|---|---|
| Host | `192.168.178.29` |
| SSH | `ssh malik@192.168.178.29` (`ssh mediaserver` alias NOT configured on dev box) |
| OS | Ubuntu/Debian |
| Repo | `/home/malik/bankai` |
| venv | `/home/malik/bankai/.venv` |
| Config | `/home/malik/bankai/config.toml` |
| qBittorrent | `http://localhost:8080` (admin/437581), category=`bankai`, saves to `/downloads/bankai` → `/mnt/media/downloads/bankai` |
| Prowlarr | `http://localhost:9696`, api_key=`a9f6b9c1fb0949598060ea0724cd79d5`, 7 indexers |
| Xvfb | `:99`–`:102` (headful Playwright extraction) |
| Downloads mount | `/mnt/media/downloads` |

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

# 4. Deploy to keller
ssh keller "cd C:\bankai; git fetch --all; git reset --hard origin/main; Restart-Service bankai-web; Start-Sleep -Seconds 2; (Invoke-WebRequest -UseBasicParsing http://localhost:9988/api/health).StatusCode"
# Expected output: 200

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

### Frontend requires a hard-refresh
The SPA bundles are hash-named. After deploy the browser must hard-refresh
(`Ctrl+Shift+R`) to load the new JS/CSS.

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

| Thing | Detail |
|---|---|
| Framework | React 18 + TypeScript |
| Build | Vite 6 |
| CSS | **Tailwind v4** (`@import 'tailwindcss'`) — no `tailwind.config.js` |
| PostCSS | `@tailwindcss/postcss` (replaces v3's `tailwindcss: {}`) |
| Component library | shadcn/ui (Radix Primitives) |
| Theme | Eigenpair mono design system — oklch tokens, `@theme inline`, `@custom-variant dark` |
| Dark mode | Always on (`<html class="dark">`) |
| Fonts | Geist Variable (body/UI), Geist Mono Variable (mono), Libre Baskerville (headings) |
| State | React hooks only (no Redux/Zustand) |
| API | `src/lib/api.ts` — typed `fetch` wrappers, all endpoints relative to window origin |

**Embossed button/input styling** uses `data-slot` / `data-variant` HTML attributes
emitted by `button.tsx`, `input.tsx`, and `select.tsx`. Without those attributes
the CSS gradient rules in `index.css` will not apply.

**Tailwind v4 deprecated aliases still work** (e.g. `bg-gradient-to-b`) but the
linter will suggest `bg-linear-to-b`. The build succeeds either way.

---

## 7. API quick-reference (web)

All endpoints are served by the FastAPI app at `/api/…`. Base URL on keller:
`http://192.168.178.27:9988`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness check — returns `{"ok":true}` |
| GET | `/api/titles` | Unified queue+library row list |
| POST | `/api/queue` | Enqueue a new movie `{title, year, url?, site?}` |
| DELETE | `/api/queue/{job_id}` | Cancel / delete a queued/running job |
| GET | `/api/jobs/{job_id}/log` | Last N log lines for a job |
| GET | `/api/media/info?path=…` | ffprobe result + review state for a library file |
| GET | `/api/media/waveform?path=…&stream=…&start=…&dur=…&bins=…` | Peak envelope (max 4000 bins) |
| GET | `/api/media/audioclip?path=…&stream=…&start=…&dur=…` | Short MP3 clip |
| GET | `/api/media/videoclip?path=…&start=…&dur=…&height=…` | Short H.264 clip |
| POST | `/api/review/repack` | Remux with delay (+ optional `atempo` drift correction) |
| POST | `/api/review/approve` | Mark library file approved for transfer |
| POST | `/api/library/delete` | Delete library file (writes tombstone, shows "Deleted") |
| GET | `/api/discover` | TVDB trending / search results |

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

| Field | Meaning |
|---|---|
| `stage` | `review` → `approved` → `transferred` → `deleted` (tombstone) |
| `delay_ms` | Constant audio delay already applied to the file |
| `needs_sync_review` | True when visual-sync confidence was low |
| `sync_confidence` | 0..1 confidence from visual alignment |
| `auto_delay_ms` | Offset the pipeline auto-applied (baseline shown in review) |
| `source_fps` | German source video fps (captured during pipeline, if available) |
| `reference_fps` | HQ reference fps (captured during pipeline) |
| `drift_ratio` | Measured drift slope (source speed / reference speed) |
| `transfer_status` | `idle` / `transferring` / `done` / `failed` |

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
  English track titles like *"Audio Description"* or *"Extended"* that contain
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

## 15. Things that must NOT happen

- **Never push code that breaks `poetry run pytest -q`** (target 107 pass).
- **Never restart `bankai-web` while a real job is running** — it kills the job.
- **Never request more than 4000 waveform bins** from the API — the backend
  rejects it with 422 and the frontend silently shows a blank canvas.
- **Never use `&&` in ssh commands from PowerShell** — use `;`.
- **Never call `review_mod.forget(path)` on a deleted file** — use
  `review_mod.set_stage(path, "deleted")` so the Deleted badge persists.
- **Never use bare string substrings to detect German audio tracks** — always
  use whole-word tokenisation (see §11).
- **Never add `autoprefixer` or `tailwindcss` v3 back** — the project is on
  Tailwind v4 (`@tailwindcss/postcss`); a `tailwind.config.js` would conflict
  with `@theme inline`.
