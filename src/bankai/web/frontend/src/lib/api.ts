// Thin typed API client for the bankai backend.

export interface UpdateStatus {
  phase: 'idle' | 'waiting' | 'applying' | 'restarting' | 'done' | 'failed';
  available: boolean;
  supported: boolean;
  checking: boolean;
  current_commit: string | null;
  latest_commit: string | null;
  commits_behind: number;
  checked_at: number | null;
  active_jobs: number;
  detail: string | null;
  error: string | null;
  unavailable_reason: string | null;
}

export interface ApiError {
  detail: string;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: any) => d.msg).join(', ');
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Remembered answers -------------------------------------------------
// The last answer to each page's main request, so coming back to a tab shows
// it at once while a fresh one loads behind it. Held in memory for tab
// switches and, where it fits, in localStorage so a reload starts from it too.
const REMEMBER_PREFIX = 'bankai:answer:';
// localStorage holds about five million characters per site; no one answer
// may take a third of that.
const REMEMBER_MAX_CHARS = 1_500_000;
const remembered = new Map<string, unknown>();

function remember(path: string, value: unknown): void {
  remembered.set(path, value);
  try {
    const text = JSON.stringify(value);
    if (text.length <= REMEMBER_MAX_CHARS) localStorage.setItem(REMEMBER_PREFIX + path, text);
    else localStorage.removeItem(REMEMBER_PREFIX + path);
  } catch {
    /* storage full or unavailable: memory alone still serves tab switches */
  }
}

/** The last answer to a GET of ``path``, if this browser has one. */
export function recall<T>(path: string): T | undefined {
  if (remembered.has(path)) return remembered.get(path) as T;
  try {
    const text = localStorage.getItem(REMEMBER_PREFIX + path);
    if (text) {
      const value = JSON.parse(text) as T;
      remembered.set(path, value);
      return value;
    }
  } catch {
    /* unreadable: as if never stored */
  }
  return undefined;
}

/** The requests whose answers pages start from; see ``recall``. */
export const pagePaths = {
  dashboard: '/api/dashboard',
  qbittorrent: '/api/qbittorrent/torrents',
  animeReview: '/api/anime/review',
  animeBlacklist: '/api/anime/blacklist',
  animeLibrary: '/api/anime/library',
  animeAutomation: '/api/anime/automation',
  sidebarCounts: '/api/sidebar/counts',
  titles: '/api/titles',
  masLibrary: '/api/mas/library?include_episodes=true&rescan=false',
} as const;

export function animeQueuePath(page = 0, pageSize = 100, includeDone = false, filters: AnimeQueueFilters = {}): string {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    include_done: String(includeDone),
  });
  if (filters.q?.trim()) params.set('q', filters.q.trim());
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.sort) {
    params.set('sort', filters.sort);
    params.set('direction', filters.dir ?? 'desc');
  }
  return `/api/anime/queue?${params}`;
}

async function remembering<T>(path: string): Promise<T> {
  const value = await request<T>(path);
  remember(path, value);
  return value;
}

// ---- Erai-raws ----------------------------------------------------------

export interface EraiRelease {
  info_hash: string;
  name: string;
  title: string;
  /** Erai's subtitle codes, the flags it shows: "us", "de", ... */
  subs: string[];
  german: boolean;
  resolution: string;
  size: string;
  category: string;
  published: number;
  page: string;
  /** bankai's status for the torrent, when it has one. */
  status: string | null;
  reason: string | null;
  /** The AniList episodes it covers: [n, n] for one episode, a batch's first and last. */
  episodes: [number, number] | null;
  /** From the name: Erai marks HEVC, and a release without the mark is AVC. */
  codec: 'hevc' | 'avc';
}

/** What taking an Erai-raws release for a review card would change. */
export interface ReplacementPlan {
  info_hash: string;
  name: string;
  batch: boolean;
  first: number;
  last: number;
  german: boolean;
  anilist_id: number;
  anilist_title: string;
  held_count: number;
  file_count: number;
  german_dubs_kept: number[];
}

export interface EraiPage {
  /** The show's page on the site when it has one but lists no release there. */
  empty_show?: string | null;
  configured: boolean;
  known: number;
  newest: number | null;
  complete: boolean;
  updated_at: number | null;
  /** Why the last read of the feeds failed, if it did. */
  error?: string | null;
  total: number;
  page: number;
  page_size: number;
  languages: Record<string, string>;
  items: EraiRelease[];
}

export type EraiResolution = '1080p' | '720p' | 'SD' | 'all';

export type EraiCodec = 'all' | 'hevc' | 'avc';

export function eraiPath(
  page = 0,
  q = '',
  german: 'all' | 'yes' | 'no' = 'all',
  res: EraiResolution = '1080p',
  codec: EraiCodec = 'all',
): string {
  const params = new URLSearchParams({ page: String(page), german, res, codec });
  if (q.trim()) params.set('q', q.trim());
  return `/api/anime/erai?${params}`;
}

// ---- Disk usage -------------------------------------------------------------

/** One part of an area: a kind of file, or one of bankai's own folders. */
export interface StoragePart {
  label: string;
  bytes: number;
  files: number;
}

/** A folder bankai knows of: a library, staging, the downloads, bankai's data. */
export interface StorageArea {
  key: string;
  label: string;
  path: string;
  /** The mount it is on; matches a volume's ``path``. */
  volume: string;
  bytes: number;
  files: number;
  /** Bytes of files counted in another area already: hard links, no space of their own. */
  shared_bytes: number;
  parts: StoragePart[];
  /** The downloads only: qBittorrent's torrents by how far they got. */
  /** ``bytes`` is what their files take on disk; ``downloaded`` what has arrived of them. */
  torrents?: { count: number; groups: Array<{ label: string; bytes: number; torrents: number; downloaded?: number }> } | null;
}

export interface StorageVolume {
  path: string;
  total: number;
  used: number;
  free: number;
  /** Used, but by nothing bankai knows of. */
  other: number;
}

export interface StorageUsage {
  scanned_at: number | null;
  duration_seconds?: number;
  scanning: boolean;
  volumes: StorageVolume[];
  areas: StorageArea[];
}

// ---- Dashboard ----------------------------------------------------------

export interface DashboardTask {
  title: string;
  detail: string | null;
  percent: number | null;
  started_at: number | null;
  href: string | null;
}

export interface DashboardWorker {
  id: string;
  name: string;
  busy: boolean;
  /** Running outside the lane's limit (qBittorrent's slow torrents). */
  extra?: boolean;
  task: DashboardTask | null;
}

export interface DashboardLane {
  key: string;
  label: string;
  description: string;
  /** null: no limit (transfers), or unknown (qBittorrent unreachable). */
  capacity: number | null;
  busy: number;
  workers: DashboardWorker[];
  /** "down" when the lane's worker is not reporting. */
  state: string | null;
  note?: string | null;
}

export interface DashboardDueItem {
  title: string;
  detail?: string | null;
  /** When it is due (scheduled work), or how long it has waited (queued work). */
  due_at?: number | null;
  since?: number | null;
  position?: number | null;
  count?: number | null;
  running?: boolean;
  href?: string | null;
}

export interface DashboardDueGroup {
  lane: string;
  label: string;
  items: DashboardDueItem[];
  more: number;
}

export interface DashboardRecent {
  key: string;
  title: string;
  poster_url: string | null;
  kind: 'movie' | 'show' | 'episode';
  href: string;
  added_at: number;
  count: number;
  episode_label: string | null;
}

export interface Dashboard {
  generated_at: number;
  summary: {
    workers_total: number;
    workers_busy: number;
    due_total: number;
    downloading: number;
    download_speed: number;
    automation: 'running' | 'idle' | 'down';
  };
  lanes: DashboardLane[];
  due: DashboardDueGroup[];
  recent: { mas: DashboardRecent[]; anime: DashboardRecent[]; anime_episodes?: DashboardRecent[] };
}

// ---- Background actions -------------------------------------------------

export interface ActionStatus {
  id: string;
  kind: string;
  title: string;
  detail: string | null;
  status: 'running' | 'done' | 'failed';
  started_at: number;
  finished_at: number | null;
  result: any;
  error: string | null;
}

/**
 * POST an action to run in the background, then follow it until it is done.
 * The server answers at once; the page stays usable, the Dashboard shows it
 * under "Your actions", and this resolves with its result (or throws its error).
 */
async function runAction<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const started = await request<{ action_id: string }>(path, {
    method: 'POST',
    body: JSON.stringify({ ...body, background: true }),
  });
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 700));
    const action = await request<ActionStatus>('/api/actions/' + started.action_id);
    if (action.status === 'done') return action.result as T;
    if (action.status === 'failed') throw new Error(action.error || 'The action failed');
  }
}

export interface HealthResponse {
  status: string;
  version: string;
  library: string;
  ffprobe: boolean;
  ffmpeg: boolean;
  mkvmerge: boolean;
  tvdb_configured: boolean;
}

export interface DiscoverItem {
  name: string;
  kind: string;
  tvdb_id: number | null;
  year: number | null;
  release_date?: string | null;
  poster_url: string | null;
  overview: string | null;
  is_new?: boolean;
  available?: boolean;
  checked?: boolean;
  added?: boolean;
  in_library?: boolean;
  filmpalast_url?: string;
}

export interface VpnStatus {
  connected: boolean;
  status: 'connected' | 'disconnected' | 'unavailable';
  detail: string;
}

export interface PagedDiscover {
  configured: boolean;
  items: DiscoverItem[];
  page: number;
  page_size: number;
  total: number | null;
  has_next: boolean;
}

export interface TorrentCandidate {
  id: string;
  title: string;
  indexer: string;
  indexer_id: number | null;
  download_url: string;
  info_url: string | null;
  magnet_uri: string | null;
  info_hash: string | null;
  size_bytes: number;
  seeders: number;
  leechers: number;
  publish_date: string | null;
  runtime_seconds: number | null;
  eligible: boolean;
}

export interface TorrentSearchOptions {
  kind?: 'movie' | 'episode';
  seriesTitle?: string | null;
  season?: number | null;
  episode?: number | null;
  minSeeders?: number | null;
  maxSeeders?: number | null;
  minSizeGib?: number | null;
  maxSizeGib?: number | null;
}

export interface TorrentPolicy {
  min_seeders: number;
  max_seeders: number | null;
  min_size_gib: number;
  max_size_gib: number;
}

/** The AniList entry and episode a Nyaa release is, as told from its title. */
export interface AnimeAniListMatch {
  anilist_id: number;
  /** The romanised title, as a release would be named. */
  title: string;
  /** How it is shown: English, else romaji. */
  name: string | null;
  /** The AniList episode; null for a batch or when the title carries none. */
  episode: number | null;
  cover: string | null;
  /** Not in the contract: shown, and used to default to a movie, only when sent. */
  format?: string | null;
  year?: number | null;
}

export interface AnimeEntry {
  id: number;
  title: string;
  download_url: string;
  detail_url: string;
  magnet_uri: string;
  info_hash: string;
  category_id: string;
  category: string;
  size: string;
  size_bytes: number;
  seeders: number;
  leechers: number;
  downloads: number;
  comments: number;
  trusted: boolean;
  remake: boolean;
  published_at: string | null;
  publisher: string | null;
  quality: string | null;
  season: number | null;
  episode: number | null;
  description: string;
  anilist: AnimeAniListMatch | null;
}

export interface AnimeSearchOptions {
  category?: string;
  page?: number;
  quality?: string;
  publisher?: string;
  titleFilters?: string;
  descriptionFilters?: string;
  minSeeders?: number;
}

export interface AnimeSearchPage {
  items: AnimeEntry[];
  page: number;
  has_next: boolean;
  aliases: string[];
}

export interface HeldRelease {
  info_hash: string;
  title: string;
  reason: string;
  detail_url?: string | null;
  quality?: string | null;
  /** The release names German among its own language tags. */
  german_in_title: boolean;
  hevc: boolean;
  season: number | null;
  episode: number | null;
  /** A batch's first and last episode; a single release names one. */
  episodes: [number, number] | null;
}

export interface AnimeReviewItem {
  key: string;
  info_hash?: string;
  release_title?: string;
  source_title: string;
  title: string;
  year: number | null;
  poster_url: string | null;
  detail_url?: string;
  quality?: string | null;
  reason?: string;
  reasons?: string[];
  release_count?: number;
  /** Seasons held under this one card; a card is a whole show. */
  seasons?: number[];
  /** The per-season decision keys merged into the card. */
  keys?: string[];
  /** Blacklist: the AniList entry the card is linked to, if any. */
  anilist_id?: number | null;
  anilist_title?: string | null;
  linked?: boolean;
  updated_at: number;
}

/** An AniList entry, as the pick-an-anime dialog lists it. */
export interface AniListAnime {
  anilist_id: number;
  /** How it is shown: English, else romaji. */
  name: string | null;
  romaji: string | null;
  english: string | null;
  /** AniList's format: TV, TV_SHORT, MOVIE, OVA, ONA, SPECIAL, MUSIC. */
  format: string | null;
  episodes: number | null;
  year: number | null;
  cover: string | null;
}

export interface AnimeAutomationStatus {
  retry_pending: number;
  rss_url: string;
  feed_source: string;
  series_indexes: Array<{ title: string; complete: boolean; phase: string; error: string | null }>;
  enabled: boolean;
  running: boolean;
  paused: boolean;
  pause_reason: string | null;
  free_space_gib: number | null;
  download_free_space_gib: number | null;
  min_free_space_gib: number;
  poll_interval_seconds: number;
  settle_minutes: number;
  last_poll: number | null;
  last_success: number | null;
  last_error: string | null;
  last_enqueued: number;
  counts: Record<string, number>;
  held: Array<{
    info_hash: string;
    title: string;
    detail_url: string;
    quality: string | null;
    reason: string;
    updated_at: number;
  }>;
  backfill: {
    enabled: boolean;
    phase: string;
    page: number;
    complete: boolean;
    found_1080: number;
    fallback_720: number;
    queries_completed: number;
    queries_split: number;
    queries_pending: number;
    error: string | null;
  };
}

export interface AnimeLibraryEntry {
  path: string;
  rel_path: string;
  name: string;
  series: string;
  season: string | null;
  size: number;
  mtime: number;
  staged: boolean;
  stage: string;
  transfer_status: string;
}

export interface AnimeLibraryEpisode extends AnimeLibraryEntry {
  /** Encode the episode was downloaded as; null when missing or unknown. */
  codec?: 'hevc' | 'avc' | null;
  /** Carries a German dub, which Erai-raws never ships — never replaced. */
  german_dub?: boolean;
  /** Carries German subtitles; null while the file is not probed for them yet. */
  german_subs?: boolean | null;
  missing?: boolean;
  tba?: boolean;
  aired?: string | null;
  episode_title?: string | null;
  season_number: number | null;
  episode: number | null;
  /** The AniList entry a matched file is, on cards made of AniList entries. */
  anilist_id?: number | null;
  /** That entry's episode the file is. */
  anilist_episode?: number | null;
}

/** The three kinds of root the server library scans. */
export type ServerDirKind = 'movie' | 'show' | 'anime';

/** How a card's episodes are laid out: by its AniList entries, or -- folders no
 *  AniList entry answers for -- by its files' own numbers. */
export type CardNumbering = 'anilist' | 'files';

export interface AnimeLibraryShow {
  numbering?: CardNumbering;
  /** The AniList entry the card is (the first, when a folder holds several). */
  anilist_id?: number | null;
  anilist_ids?: number[];
  /** Files no AniList entry is known for: offer "Match on AniList". */
  unmatched?: boolean;
  /** AVC episodes an upgrade could actually replace (dubs are excluded). */
  avc_count?: number;
  hevc_count?: number;
  german_dub_count?: number;
  /** Episodes on disk with neither German subtitles nor a German dub. */
  no_german_count?: number;
  /** AVC episodes an HEVC upgrade is on its way for. */
  upgrading_count?: number;
  /** AVC episodes a search found an HEVC release with German subtitles for. */
  upgrade_available_count?: number;
  /** AVC episodes an upgrade search found no HEVC release with German for. */
  no_hevc_count?: number;
  /** When that search last ran (seconds since the epoch). */
  hevc_checked_at?: number | null;
  /** Missing episodes the background search found a release with German subtitles for, not queued yet. */
  missing_available_count?: number;
  /** Blacklisted by the user, yet its files are still in the library. */
  blacklisted?: boolean;
  /** Each tab's AniList entries. */
  season_anilist?: Record<string, Array<{ anilist_id: number; title: string }>>;
  /** The bankai collection the card is; null for a card that is in none. */
  collection_id?: number | null;
  /** The collection was renamed by hand, not named after an entry. */
  collection_name_custom?: boolean;
  /** The collection's entries in order; the owned ones can name it. */
  collection_entries?: Array<{ anilist_id: number; title: string; owned: boolean; season?: string | null }>;
  downloaded_count: number;
  total_count: number;
  completion_state: 'empty' | 'upcoming' | 'partial' | 'complete' | 'unknown';
  finished: boolean;
  metadata_available: boolean;
  key: string;
  /** Every library folder behind this one card; more than one once merged. */
  folders: string[];
  title: string;
  /** AniList's romaji title, when the card is shown under another. */
  source_title: string;
  year: number | null;
  poster_url: string | null;
  episode_count: number;
  season_count: number;
  size: number;
  staged_count: number;
  episodes: AnimeLibraryEpisode[];
}

/** A release the background search found for missing episodes of a show; a batch once. */
export interface MissingRelease {
  info_hash: string;
  title: string;
  size_bytes: number;
  seeders: number;
  quality: string | null;
  hevc: boolean;
  batch: boolean;
  first: number;
  last: number;
  anilist_id: number;
  detail_url: string;
  /** The show's missing episodes it covers, by the drawer's own tabs and numbers. */
  episodes: Array<{ anilist_id: number; anilist_episode: number; season: string | null; season_number: number | null; episode: number | null }>;
}

/** What the library's bulk buttons act on: HEVC upgrades found, or releases found for missing episodes. */
export type AnimeBulkKind = 'upgrade' | 'missing';

/** One show a bulk action would take, with the episodes it covers there. */
export interface AnimeBulkShow {
  key: string;
  title: string;
  poster_url: string | null;
  anilist_ids: number[];
  episodes: Array<{ anilist_id: number; anilist_episode: number; season: string | null; season_number: number | null; episode: number | null }>;
  /** Releases to download; only for missing episodes. */
  downloads: number;
  size_bytes: number;
}

export interface AnimeBulkResult {
  ok: boolean;
  shows: number;
  /** Episodes queued. */
  queued: number;
  /** Upgrade: downloads queued and episodes no replacement was found for on the second look. */
  downloads?: number;
  no_replacement?: number;
  /** Missing: releases already in the queue, and the episodes the queued ones cover. */
  duplicates?: number;
  episodes?: number;
  /** Shows whose queueing failed. */
  failed: string[];
}

/** A picture an artwork slot can show. */
export interface ArtworkCandidate {
  url: string;
  preview: string;
  source: 'anilist' | 'tmdb';
  label: string;
  lang: string | null;
  width?: number | null;
  height?: number | null;
}

/** One artwork slot of a collection: poster, backdrop, thumb, logo, banner, a season's poster. */
export interface ArtworkSlot {
  slot: string;
  label: string;
  /** The picture it shows now; null when there is none. */
  url: string | null;
  /** Chosen by hand rather than automatically. */
  chosen: boolean;
  candidates: ArtworkCandidate[];
}

/** One AniList entry in a collection's relation graph. */
export interface CollectionNode {
  anilist_id: number;
  title: string;
  /** AniList's romaji title. */
  main_title: string | null;
  /** AniList's format: TV, TV_SHORT, MOVIE, OVA, ONA, SPECIAL, MUSIC. */
  type: string | null;
  year: number | null;
  first_aired: string | null;
  episodes: number | null;
  /** Episodes of it on disk. */
  on_disk: number;
  /** Some of it is on disk. */
  owned: boolean;
  poster_url: string | null;
  in_collection: boolean;
  /** The entry the collection is named and pictured after. */
  title_entry: boolean;
  /** Where a member is filed: "Season 2", "Specials"; null for a suggestion. */
  season: string | null;
  /** Where a suggestion is now: another collection, or none. */
  collection_id: number | null;
  collection_name: string | null;
  /** Taken out of every collection by hand. */
  alone: boolean;
}

export interface CollectionEdge {
  from: number;
  to: number;
  /** "sequel", "side story", "summary", "alternative", "spin-off", ... */
  label: string;
  main_line: boolean;
}

export interface CollectionGraph {
  /** The collection shown; null for the graph of an entry in no collection. */
  collection_id: number | null;
  /** That entry, when the graph is of one entry in no collection. */
  root_aid: number | null;
  name: string;
  title_entry: number | null;
  nodes: CollectionNode[];
  edges: CollectionEdge[];
  /** How the collection's entries make up its seasons; absent for an entry in no collection. */
  arrangement?: CollectionArrangement | null;
}

/** A collection's seasons: ``seasons[i]`` are the AniList ids of Season i+1 in
 *  order (later ones continue its numbering); ``specials`` make up Season 00. */
export interface CollectionArrangement {
  seasons: number[][];
  specials: number[];
  /** Arranged by hand rather than by the sequel chain. */
  custom: boolean;
}

/** A film on the server, as the Movies & Shows library presents it. */
export interface MasMovie {
  key: string;
  folders: string[];
  title: string;
  tvdb_id: number | null;
  year: number | null;
  poster_url: string | null;
  metadata_available: boolean;
  file_count: number;
  size: number;
  /** Configured roots it was found under; more than one means it is stored twice. */
  roots: string[];
  files: AnimeLibraryEntry[];
}

/** A series on the server, merged against its TVDB roster. */
export interface MasShow {
  key: string;
  folders: string[];
  title: string;
  tvdb_id: number | null;
  year: number | null;
  poster_url: string | null;
  episode_count: number;
  season_count: number;
  size: number;
  roots: string[];
  downloaded_count: number;
  total_count: number;
  completion_state: 'empty' | 'upcoming' | 'partial' | 'complete' | 'unknown';
  finished: boolean;
  metadata_available: boolean;
  episodes: AnimeLibraryEpisode[];
}

export type DiscoverSearchBy = 'title' | 'person' | 'studio';

export interface PersonSuggestion {
  name: string;
  tvdb_id: number | null;
}

export interface SearchResult {
  site: string;
  title: string;
  year: number | null;
  kind: string;
  url: string;
  release_name?: string | null;
  poster_url?: string | null;
  runtime_minutes?: number | null;
  in_library?: boolean;
}

export interface RecentRelease {
  site: string;
  title: string;
  url: string;
  kind: 'movie' | 'episode';
  year: number | null;
  poster_url: string | null;
  release_name: string | null;
  runtime_minutes: number | null;
  in_library: boolean;
}

export interface RecentReleasePage {
  items: RecentRelease[];
  page: number;
  feed: FilmpalastFeed;
  source_page_start: number;
  source_page_end: number;
  has_next: boolean;
}

export type FilmpalastFeed = 'new' | 'movies' | 'shows' | 'top';

export interface FilmpalastMirror {
  url: string;
  host: string;
  hint: 'ytdlp' | 'playwright' | 'direct';
  supported: boolean;
}

export interface FilmpalastDetails {
  title: string;
  url: string;
  kind: 'movie' | 'episode';
  year: number | null;
  poster_url: string | null;
  release_name: string | null;
  runtime_minutes: number | null;
  mirrors: FilmpalastMirror[];
  episodes: EpisodeItem[];
}

export interface PurgeResult {
  ok: boolean;
  blacklisted?: number;
  removed_torrents: number;
  deleted_files: number;
  deleted_folders: string[];
  freed_bytes: number;
}

export interface AnimeQueueFilters {
  q?: string;
  status?: string;
  /** Sorted on the server: a page is a slice, so sorting it here would only
   *  order the slice. */
  sort?: string;
  dir?: 'asc' | 'desc';
}

export interface AnimeQueuePage {
  jobs: Job[];
  total: number;
  /** Status -> row count for the current search, before the status filter. */
  counts: Record<string, number>;
  page: number;
  page_size: number;
}

export interface QBittorrentItem {
  hash: string;
  name: string;
  state: string;
  progress: number;
  size_bytes: number;
  seeds: number;
  peers: number;
  seeds_total: number;
  peers_total: number;
  dlspeed: number;
  upspeed: number;
  eta: number;
  added_on: number;
}

export interface EpisodeItem {
  season: number;
  episode: number;
  title: string | null;
  url: string;
}

export interface Job {
  tvdb_id?: number | string | null;
  /** Anime jobs: the AniList entry the job files under. */
  anilist_id?: number | string | null;
  poster_url?: string | null;
  series_title?: string | null;
  id: string;
  kind: string;
  title: string;
  status: string;
  started_at: number;
  updated_at: number | null;
  finished_at: number | null;
  exit_code: number | null;
  final_path: string | null;
  step: number | null;
  total_steps: number | null;
  step_key: string | null;
  step_label: string;
  overall_percent: number | null;
  /** "running" narrowed to downloading | organizing | transferring; the status otherwise. */
  phase: string;
  /** Byte progress of the library copy, present once the transfer stage starts. */
  transfer_percent: number | null;
  pending: boolean;
  action_required: boolean;
  reason?: string | null;
  reason_detail?: string | null;
  queue_position: number | null;
  queue_total: number | null;
}

export interface LibraryEntry {
  kind: string;
  path: string;
  rel_path: string;
  name: string;
  size: number;
  mtime: number;
  series: string | null;
  season: number | null;
  stage: string;
  delay_ms: number;
  needs_sync_review: boolean;
  sync_confidence: number | null;
  sync_user_approved: boolean;
  duration_delta_seconds: number | null;
  duration_compatible: boolean | null;
  auto_delay_ms: number;
  transfer_status: string;
  transfer_percent: number;
  repack_status: string;
  repack_percent: number;
  repack_kind: string | null;
  german_source_url: string | null;
  torrent_source_url: string | null;
  torrent_source_title: string | null;
}

/** Unified one-row-per-title view (library file OR in-flight/failed job). */
export interface TitleRow {
  row_kind: 'library' | 'job';
  id: string;
  title: string;
  kind: string;
  year: number | null;
  poster: string | null;
  created_at: number | null;
  updated_at: number | null;
  done_at: number | null;
  path: string | null;
  rel_path: string | null;
  name: string;
  size: number | null;
  mtime: number | null;
  series: string | null;
  season: number | null;
  stage: string | null;
  reason: string | null;
  reason_code?: string | null;
  reason_detail?: string | null;
  delay_ms: number;
  needs_sync_review: boolean;
  sync_confidence: number | null;
  sync_user_approved: boolean;
  duration_delta_seconds: number | null;
  duration_compatible: boolean | null;
  auto_delay_ms: number;
  transfer_status: string;
  transfer_percent: number;
  job_id: string | null;
  job_status: string | null;
  step_label: string | null;
  overall_percent: number | null;
  total_steps: number | null;
  pending: boolean;
  action_required: boolean;
  repack_status: string;
  repack_percent: number;
  repack_kind: string | null;
  repack_label: string | null;
  queue_position: number | null;
  queue_total: number | null;
  german_source_url: string | null;
  torrent_source_url: string | null;
  torrent_source_title: string | null;
}

export interface AudioTrack {
  index: number;
  order: number;
  language: string | null;
  title: string | null;
  codec: string | null;
  channels: number | null;
  default: boolean;
  is_german: boolean;
  sample_rate: number | null;
  duration: number | null;
}

export interface MediaInfo {
  path: string;
  size: number;
  duration: number | null;
  video_codec: string | null;
  width: number | null;
  height: number | null;
  video_fps: number | null;
  has_german: boolean;
  browser_playable: boolean;
  stage: string;
  delay_ms: number;
  needs_sync_review: boolean;
  sync_confidence: number | null;
  sync_user_approved: boolean;
  duration_delta_seconds: number | null;
  duration_compatible: boolean | null;
  auto_delay_ms: number;
  source_fps: number | null;
  source_video_fps: number | null;
  reference_fps: number | null;
  drift_ratio: number | null;
  german_source_url: string | null;
  torrent_source_url: string | null;
  torrent_source_title: string | null;
  audio_tracks: AudioTrack[];
}

export interface ServerTitle {
  name: string;
  present: boolean;
  location: string | null;
  directory: string | null;
}

export interface ServerEpisode {
  name: string;
  path: string;
  size: number;
}

export interface ServerSeason {
  name: string;
  season: number | null;
  episodes: ServerEpisode[];
}

export interface SettingRow {
  key: string;
  value: any;
  secret: boolean;
  is_set: boolean;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  vpnStatus: () => request<VpnStatus>('/api/vpn/status'),
  vpnConnect: () => request<VpnStatus>('/api/vpn/connect', { method: 'POST' }),

  discoverTrending: (kind: string, page = 0, pageSize = 50) => request<PagedDiscover>(`/api/discover/trending?kind=${kind}&page=${page}&page_size=${pageSize}`),
  discoverSearch: (q: string, kind: string, by: DiscoverSearchBy = 'title', page = 0, pageSize = 50) =>
    request<PagedDiscover>(
      `/api/discover/search?q=${encodeURIComponent(q)}&kind=${kind}&by=${by}&page=${page}&page_size=${pageSize}`,
    ),
  discoverPeopleSuggest: (q: string, limit = 8) =>
    request<{ configured: boolean; items: PersonSuggestion[] }>(
      `/api/discover/people/suggest?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  discoverGerman: (id: number, kind: string) =>
    request<{ tvdb_id: number; kind: string; english: string | null; german: string | null; year: number | null; release_date: string | null }>(
      `/api/discover/german?id=${id}&kind=${kind}`,
    ),
  // TVDB artwork is a public CDN resource; sending every image through the
  // single Bankai process created 50-100 avoidable backend requests per page.
  // Retain the proxy only for legacy/non-HTTPS artwork URLs.
  posterUrl: (url: string) =>
    // bankai's own images load as they are: the poster proxy fetches only
    // http(s) addresses. AniList's covers (s4.anilist.co) load directly.
    url.startsWith('/')
      ? url
      : url.includes('filmpalast.to/') || !url.startsWith('https://')
      ? `/api/discover/poster?url=${encodeURIComponent(url)}`
      : url,

  search: (q: string, kind: string, site?: string) =>
    request<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}&kind=${kind}${site ? `&site=${site}` : ''}`),
  recentReleases: (page = 0, feed: FilmpalastFeed = 'new') =>
    request<RecentReleasePage>(`/api/releases/recent?page=${page}&feed=${feed}`),
  filmpalastDetails: (url: string) =>
    request<FilmpalastDetails>(`/api/filmpalast/detail?url=${encodeURIComponent(url)}`),
  qbittorrentTorrents: () =>
    remembering<{ items: QBittorrentItem[] }>(pagePaths.qbittorrent),
  qbittorrentStart: (hash: string) =>
    request<{ ok: boolean }>(`/api/qbittorrent/torrents/${encodeURIComponent(hash)}/start`, { method: 'POST' }),
  qbittorrentStop: (hash: string) =>
    request<{ ok: boolean }>(`/api/qbittorrent/torrents/${encodeURIComponent(hash)}/stop`, { method: 'POST' }),
  qbittorrentRemove: (hash: string, deleteFiles = false) =>
    request<{ ok: boolean }>(
      `/api/qbittorrent/torrents/${encodeURIComponent(hash)}?delete_files=${deleteFiles}`,
      { method: 'DELETE' },
    ),
  torrentSearch: (q: string, runtimeSeconds?: number | null, options: TorrentSearchOptions = {}) => {
    const params = new URLSearchParams({ q });
    if (runtimeSeconds) params.set('runtime_seconds', String(runtimeSeconds));
    if (options.kind) params.set('kind', options.kind);
    if (options.seriesTitle) params.set('series_title', options.seriesTitle);
    if (options.season != null) params.set('season', String(options.season));
    if (options.episode != null) params.set('episode', String(options.episode));
    if (options.minSeeders != null) params.set('min_seeders', String(options.minSeeders));
    if (options.maxSeeders != null) params.set('max_seeders', String(options.maxSeeders));
    if (options.minSizeGib != null) params.set('min_size_gib', String(options.minSizeGib));
    if (options.maxSizeGib != null) params.set('max_size_gib', String(options.maxSizeGib));
    return request<{
      query: string;
      target_runtime_seconds: number | null;
      policy: TorrentPolicy;
      candidates: TorrentCandidate[];
    }>(`/api/torrents/search?${params.toString()}`);
  },
  animeSearch: (q: string, options: AnimeSearchOptions = {}) => {
    const params = new URLSearchParams({ q });
    if (options.category) params.set('category', options.category);
    if (options.page != null) params.set('page', String(options.page));
    if (options.quality) params.set('quality', options.quality);
    if (options.publisher) params.set('publisher', options.publisher);
    if (options.titleFilters) params.set('title_filters', options.titleFilters);
    if (options.descriptionFilters) params.set('description_filters', options.descriptionFilters);
    if (options.minSeeders != null) params.set('min_seeders', String(options.minSeeders));
    return request<AnimeSearchPage>(`/api/anime?${params.toString()}`);
  },
  animeDetail: (url: string) =>
    request<{ description: string; magnet_uri: string | null; publisher: string | null }>(
      `/api/anime/detail?url=${encodeURIComponent(url)}`,
    ),
  /** Download a Nyaa release as one AniList entry; ``episode`` null for a batch or a movie.
   *  ``publish``: straight into the library, not left staged for Transfer. */
  animeDownload: (entry: AnimeEntry, anilistId: number, kind: 'show' | 'movie', episode: number | null = null, publish = false) =>
    request<Job>('/api/anime/download', {
      method: 'POST',
      body: JSON.stringify({
        release_title: entry.title,
        torrent_url: entry.download_url,
        detail_url: entry.detail_url,
        magnet_uri: entry.magnet_uri,
        info_hash: entry.info_hash,
        anilist_id: anilistId,
        kind,
        publish,
        episode,
      }),
    }),
  torrentAction: (jobId: string) =>
    request<{ job_id: string; query: string; target_runtime_seconds: number | null; candidates: TorrentCandidate[] }>(
      `/api/torrent-actions/${jobId}`,
    ),
  chooseTorrent: (jobId: string, candidate: TorrentCandidate) =>
    request(`/api/torrent-actions/${jobId}`, { method: 'POST', body: JSON.stringify({ candidate }) }),
  chooseTorrentMagnet: (jobId: string, magnetUri: string, title?: string) =>
    request(`/api/torrent-actions/${jobId}`, {
      method: 'POST',
      body: JSON.stringify({ magnet_uri: magnetUri, title }),
    }),
  episodes: (show: string, season: number, site?: string) =>
    request<{ found: boolean; site: string | null; episodes: EpisodeItem[] }>(
      `/api/series/episodes?show=${encodeURIComponent(show)}&season=${season}${site ? `&site=${site}` : ''}`,
    ),

  queue: () => request<{ jobs: Job[] }>('/api/queue'),
  animeQueue: (page = 0, pageSize = 100, includeDone = false, filters: AnimeQueueFilters = {}) =>
    remembering<AnimeQueuePage>(animeQueuePath(page, pageSize, includeDone, filters)),
  /** Releases of one AniList episode, each checked to be that episode. */
  animeEpisodeSearch: (anilistId: number, episode: number, q?: string) => {
    const params = new URLSearchParams({ anilist_id: String(anilistId), episode: String(episode) });
    if (q) params.set('q', q);
    return request<{ items: AnimeEntry[]; queries: string[] }>(`/api/anime/episode/search?${params}`);
  },
  animeReviewReleases: (key: string) =>
    request<{ items: HeldRelease[] }>('/api/anime/review/' + encodeURIComponent(key) + '/releases'),
  markAnimeOwned: (payload: { key?: string; info_hashes?: string[] }, label = '') =>
    runAction<{ ok: boolean; cleared: number }>('/api/anime/review/owned', { ...payload, label }),
  animeReview: () => remembering<{ items: AnimeReviewItem[] }>(pagePaths.animeReview),
  animeBlacklist: () => remembering<{ items: AnimeReviewItem[] }>(pagePaths.animeBlacklist),
  reviewAnime: (infoHash: string, action: 'recheck' | 'allow_german' | 'blacklist', label = '') =>
    runAction<{ ok: boolean; requested: number; blacklisted?: number }>('/api/anime/review/' + infoHash, { action, label }),
  purgeAnimeSeries: (infoHash: string, deleteFiles: boolean, label = '') =>
    runAction<PurgeResult>('/api/anime/review/' + infoHash + '/purge', { delete_files: deleteFiles, label }),
  removeAnimeBlacklist: (key: string, label = '') =>
    runAction<{ ok: boolean; requested: number }>('/api/anime/blacklist/remove', { key, label }),
  /** Tie a blacklist card to its AniList entry, so releases match by any of its titles. */
  linkAnimeBlacklist: (key: string, anilistId: number) =>
    request<{ ok: boolean; keys: string[]; caught: number }>('/api/anime/blacklist/link', {
      method: 'POST', body: JSON.stringify({ key, anilist_id: anilistId }),
    }),
  /** Remember which AniList entry a held show name is; its releases are rechecked. */
  saveAnilistMapping: (releaseTitle: string, anilistId: number) =>
    request<{ ok: boolean; requested: number; title: string }>('/api/anime/mapping/anilist', {
      method: 'POST', body: JSON.stringify({ release_title: releaseTitle, anilist_id: anilistId }),
    }),
  /** An Unmatched card's files become that entry's episodes (by the number in each name), moved into place. */
  matchLibraryFolders: (folders: string[], anilistId: number) =>
    request<{ ok: boolean; anilist_id?: number; moved?: number; matched?: string[]; skipped?: string[] }>('/api/anime/library/match', {
      method: 'POST', body: JSON.stringify({ folders, anilist_id: anilistId }),
    }),
  /** Every file of entry ``fromId`` becomes entry ``toId`` (same episode numbers), moved into place. */
  rematchEntry: (fromId: number, toId: number) =>
    request<{ ok: boolean; moved?: number }>('/api/anime/library/rematch', {
      method: 'POST', body: JSON.stringify({ from_id: fromId, to_id: toId }),
    }),
  /** AniList entries by title; also takes an anilist.co link or a numeric id. */
  anilistSearch: (q: string) =>
    request<{ items: AniListAnime[] }>('/api/anime/anilist/search?q=' + encodeURIComponent(q)),
  /** A collection's entries and the ones related to them. */
  collectionGraph: (collectionId: number) => request<CollectionGraph>(`/api/anime/collections/${collectionId}/graph`),
  /** An entry in no collection, and what it is related to. */
  entryGraph: (anilistId: number) => request<CollectionGraph>(`/api/anime/entries/${anilistId}/graph`),
  /** An entry in no collection into a collection it is related to. */
  entryMove: (anilistId: number, collectionId: number) =>
    request<{ ok: boolean }>(`/api/anime/entries/${anilistId}/move`, {
      method: 'POST', body: JSON.stringify({ collection_id: collectionId }),
    }),
  /** Entries of a collection into a new one of their own. */
  collectionSplit: (collectionId: number, anilistIds: number[]) =>
    request<{ ok: boolean; result: number | null }>(`/api/anime/collections/${collectionId}/split`, {
      method: 'POST', body: JSON.stringify({ anilist_ids: anilistIds }),
    }),
  /** One entry out of a collection. */
  collectionExclude: (collectionId: number, anilistId: number) =>
    request<{ ok: boolean }>(`/api/anime/collections/${collectionId}/exclude`, {
      method: 'POST', body: JSON.stringify({ anilist_id: anilistId }),
    }),
  /** A related entry into a collection. */
  collectionInclude: (collectionId: number, anilistId: number) =>
    request<{ ok: boolean }>(`/api/anime/collections/${collectionId}/include`, {
      method: 'POST', body: JSON.stringify({ anilist_id: anilistId }),
    }),
  /** Every entry of another collection into this one. */
  collectionMerge: (collectionId: number, otherId: number) =>
    request<{ ok: boolean; result: number }>(`/api/anime/collections/${collectionId}/merge`, {
      method: 'POST', body: JSON.stringify({ other_id: otherId }),
    }),
  /** Name and picture a collection after one of its entries. */
  collectionTitle: (collectionId: number, anilistId: number) =>
    request<{ ok: boolean }>(`/api/anime/collections/${collectionId}/title`, {
      method: 'POST', body: JSON.stringify({ anilist_id: anilistId }),
    }),
  /** Rename a collection by hand; null gives it back AniList's name. */
  collectionName: (collectionId: number, name: string | null) =>
    request<{ ok: boolean; name?: string | null }>(`/api/anime/collections/${collectionId}/name`, {
      method: 'POST', body: JSON.stringify({ name }),
    }),
  /** Name and picture a library card's collection after one of its entries, here and in Jellyfin. */
  setCollectionTitle: (key: string, anilistId: number) =>
    request<{ ok: boolean; name: string | null; anilist_id: number }>('/api/anime/collections/title', {
      method: 'POST', body: JSON.stringify({ key, anilist_id: anilistId }),
    }),
  /** Delete a show's folders and torrents and stop following it; blacklist it too unless ``blacklist`` is false. */
  removeAnimeLibraryShow: (key: string, label = '', blacklist = true) =>
    runAction<PurgeResult & { anilist_id: number | null }>('/api/anime/library/remove', { key, label, blacklist }),
  /** Files of a card become episodes of an AniList entry (``episodes[i]`` for ``paths[i]``, else the
   *  number in each name), moved into place; ``anilistId`` null un-matches them. */
  assignFiles: (key: string, paths: string[], anilistId: number | null, episodes?: number[]) =>
    request<{ ok: boolean; assigned?: number; moved?: number; unmatched?: number; anilist_id?: number | null }>(
      '/api/anime/library/assign',
      {
        method: 'POST',
        body: JSON.stringify(
          episodes ? { key, paths, anilist_id: anilistId, episodes } : { key, paths, anilist_id: anilistId },
        ),
      },
    ),
  /** A collection's whole season arrangement, or back to the automatic one; files are renamed into place. */
  arrangeCollection: (collectionId: number, body: { seasons: number[][]; specials: number[] } | { reset: true }) =>
    request<{ ok: boolean; moved: number; result: CollectionArrangement }>(
      `/api/anime/collections/${collectionId}/arrange`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  /** Delete episode files of one show, subtitles with them; only files the library lists under it. */
  deleteAnimeEpisodes: (key: string, paths: string[]) =>
    request<Pick<PurgeResult, 'deleted_files' | 'deleted_folders' | 'freed_bytes'>>('/api/anime/library/episodes/delete', {
      method: 'POST', body: JSON.stringify({ key, paths }),
    }),
  /** Queue HEVC replacements for the AVC episodes of a card's AniList entries. */
  upgradeShowToHevc: (anilistIds: number[]) =>
    request<{
      ok: boolean;
      queued: number;
      no_replacement: number;
      already_hevc: number;
      german_dub_kept: number;
      /** Episodes whose files the codec sweep has not probed yet. */
      not_probed?: number;
    }>(
      '/api/anime/library/upgrade-hevc',
      { method: 'POST', body: JSON.stringify({ anilist_ids: anilistIds }) },
    ),
  /** What the background search found for a show's missing episodes. */
  missingReleases: (key: string) =>
    request<{ key: string; items: MissingRelease[] }>('/api/anime/library/missing?' + new URLSearchParams({ key })),
  /** Queue the picked ones of those releases, straight into the library. */
  downloadMissingReleases: (key: string, infoHashes: string[]) =>
    request<{ ok: boolean; queued: number; duplicates: number; episodes: number }>('/api/anime/library/missing/download', {
      method: 'POST', body: JSON.stringify({ key, info_hashes: infoHashes }),
    }),
  /** What "Upgrade all" or "Download all available" would take, show by show; nothing is queued. */
  animeBulkPlan: (kind: AnimeBulkKind) =>
    request<{ kind: AnimeBulkKind; shows: AnimeBulkShow[] }>('/api/anime/library/bulk?' + new URLSearchParams({ kind })),
  /** Queue it for the confirmed shows, in the background. */
  runAnimeBulk: (kind: AnimeBulkKind, keys: string[]) =>
    runAction<AnimeBulkResult>('/api/anime/library/bulk', { kind, keys }),
  /** A collection's artwork slots and what each can show; TMDB's is fetched when not kept yet. */
  collectionArtwork: (collectionId: number, refresh = false) =>
    request<{ collection_id: number; tmdb: boolean; tmdb_error: { error?: string } | null; slots: ArtworkSlot[] }>(
      `/api/anime/collections/${collectionId}/artwork` + (refresh ? '?refresh=true' : ''),
    ),
  /** Choose a slot's picture by URL; null gives it back to the automatic choice. */
  chooseArtwork: (collectionId: number, slot: string, url: string | null) =>
    request<{ ok: boolean; pictures: number }>(`/api/anime/collections/${collectionId}/artwork`, {
      method: 'POST', body: JSON.stringify({ slot, url }),
    }),
  /** Take every episode picture again at the saved setting. */
  regenerateThumbs: () => request<{ ok: boolean }>('/api/anime/thumbs/regenerate', { method: 'POST' }),
  /** A frame of a library episode as these (unsaved) settings would take it. */
  thumbPreview: async (params: { mode: string; percent: number; time_seconds: number; width: number; sample: number }) => {
    const query = new URLSearchParams(Object.entries(params).map(([key, value]) => [key, String(value)]));
    const res = await fetch('/api/anime/thumbs/preview?' + query);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return {
      url: URL.createObjectURL(await res.blob()),
      episode: decodeURIComponent(res.headers.get('X-Episode') ?? ''),
      position: Number(res.headers.get('X-Position') ?? 0),
    };
  },
  /** Every sidebar badge in one call; see /api/sidebar/counts. */
  dashboard: () => remembering<Dashboard>(pagePaths.dashboard),
  storage: () => request<StorageUsage>('/api/storage'),
  refreshStorage: () => request<{ started: boolean }>('/api/storage/refresh', { method: 'POST' }),
  eraiReleases: (page = 0, q = '', german: 'all' | 'yes' | 'no' = 'all', res: EraiResolution = '1080p', codec: EraiCodec = 'all') =>
    remembering<EraiPage>(eraiPath(page, q, german, res, codec)),
  refreshErai: () => request<{ refreshing: boolean }>('/api/anime/erai/refresh', { method: 'POST' }),
  replacementPlan: (key: string, infoHash: string) =>
    request<ReplacementPlan>('/api/anime/review/replace-plan?' + new URLSearchParams({ key, info_hash: infoHash })),
  replaceFromErai: (key: string, infoHash: string, label = '') =>
    runAction<{ ok: boolean; queued: string; held_replaced: number; files_to_replace: number; german_dubs_kept: number[] }>(
      '/api/anime/review/replace', { key, info_hash: infoHash, label },
    ),
  sidebarCounts: () => remembering<{ counts: Record<string, number | null> }>(pagePaths.sidebarCounts),
  /** `rescan` checks the library against the disk first instead of the held tree. */
  animeLibrary: async (rescan = false) => {
    type Library = { root: string; entries: AnimeLibraryEntry[]; shows: AnimeLibraryShow[] };
    if (!rescan) return remembering<Library>(pagePaths.animeLibrary);
    const fresh = await request<Library>('/api/anime/library?rescan=true');
    remember(pagePaths.animeLibrary, fresh);
    return fresh;
  },
  animeLibraryShow: (key: string) =>
    request<{ root: string; entries: AnimeLibraryEntry[]; shows: AnimeLibraryShow[] }>(
      `/api/anime/library?show=${encodeURIComponent(key)}`,
    ),
  animeAutomation: () => remembering<AnimeAutomationStatus>(pagePaths.animeAutomation),
  runAnimeAutomation: () => request<AnimeAutomationStatus>('/api/anime/automation/run', { method: 'POST' }),
  retryHeldAnime: () => request<AnimeAutomationStatus & { requested: number }>('/api/anime/automation/retry-held', { method: 'POST' }),
  queueMovie: (body: { title: string; german?: string; url?: string; site?: string; year?: number }) =>
    request('/api/queue/movie', { method: 'POST', body: JSON.stringify(body) }),
  queueShow: (body: {
    show: string;
    season: number;
    episodes?: number[];
    site?: string;
    custom_episodes?: { episode: number; title?: string; url: string }[];
  }) =>
    request('/api/queue/show', { method: 'POST', body: JSON.stringify(body) }),
  cancelJob: (id: string) => request(`/api/queue/${id}/cancel`, { method: 'POST' }),
  stopJob: (id: string) => request(`/api/queue/${id}/stop`, { method: 'POST' }),
  continueJob: (id: string) => request(`/api/queue/${id}/continue`, { method: 'POST' }),
  forceJob: (id: string) => request(`/api/queue/${id}/force`, { method: 'POST' }),
  setJobPriority: (id: string, position: number) =>
    request<{ id: string; position: number }>(`/api/queue/${id}/priority`, {
      method: 'POST',
      body: JSON.stringify({ position }),
    }),
  retryJob: (id: string) => request(`/api/queue/${id}/retry`, { method: 'POST' }),
  retryJobWithSource: (id: string, url: string) =>
    request(`/api/queue/${id}/retry-with-source`, {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),
  deleteJob: (id: string) =>
    request<{ deleted: boolean; pending: boolean }>(`/api/queue/${id}`, { method: 'DELETE' }),
  jobLog: (id: string) => request<{ id: string; status: string; log: string }>(`/api/jobs/${id}/log`),

  library: () => request<{ entries: LibraryEntry[]; library: string }>('/api/library'),
  titles: () => remembering<{ rows: TitleRow[]; library: string }>(pagePaths.titles),
  redoTitle: (path: string) =>
    request<{ redo: any; title: string; fresh: boolean; stages: string[] }>('/api/titles/redo', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  mediaInfo: (path: string) => request<MediaInfo>(`/api/media/info?path=${encodeURIComponent(path)}`),
  deleteFile: (path: string) => request('/api/library/file', { method: 'DELETE', body: JSON.stringify({ path }) }),
  streamUrl: (path: string) => `/api/media/stream?path=${encodeURIComponent(path)}`,
  transcodeUrl: (path: string, audio = 0, t = 0) => `/api/media/transcode?path=${encodeURIComponent(path)}&audio=${audio}&t=${t}`,
  waveform: (path: string, stream: number, start: number, dur: number, bins: number) =>
    request<{ start: number; dur: number; bins: number; peaks: string }>(
      `/api/media/waveform?path=${encodeURIComponent(path)}&stream=${stream}&start=${start}&dur=${dur}&bins=${bins}`,
    ),
  audioClipUrl: (path: string, stream: number, start: number, dur: number, lead = 0, rate = 1) =>
    `/api/media/audioclip?path=${encodeURIComponent(path)}&stream=${stream}&start=${start}&dur=${dur}&lead=${lead}&rate=${rate}`,
  audioClipCache: (path: string, stream: number, segment: number, delayMs = 0, rate = 1) =>
    request<{ ranges: Array<{ start: number; end: number }> }>(
      `/api/media/audioclip/cache?path=${encodeURIComponent(path)}&stream=${stream}&segment=${segment}&delay_ms=${delayMs}&rate=${rate}`,
    ),
  videoClipUrl: (path: string, start: number, dur: number, height = 480, audio?: number | null) =>
    `/api/media/videoclip?path=${encodeURIComponent(path)}&start=${start}&dur=${dur}&height=${height}${audio == null ? '' : `&audio=${audio}`}`,
  videoClipCache: (path: string, segment: number, height = 480, audio?: number | null) =>
    request<{ ranges: Array<{ start: number; end: number }> }>(
      `/api/media/videoclip/cache?path=${encodeURIComponent(path)}&segment=${segment}&height=${height}${audio == null ? '' : `&audio=${audio}`}`,
    ),

  setDelay: (path: string, delay_ms: number) => request('/api/review/delay', { method: 'POST', body: JSON.stringify({ path, delay_ms }) }),
  repack: (path: string, delay_ms: number, opts?: { atempo?: number; track_index?: number | null }) =>
    request<{ ok: boolean; message: string; delay_ms: number }>('/api/review/repack', {
      method: 'POST',
      body: JSON.stringify({ path, delay_ms, atempo: opts?.atempo ?? null, track_index: opts?.track_index ?? null }),
    }),
  approve: (path: string, opts?: { delay_ms?: number; atempo?: number; track_index?: number | null }) =>
    request<{ background: boolean }>('/api/review/approve', {
      method: 'POST',
      body: JSON.stringify({ path, ...opts }),
    }),
  replaceTorrent: (body: {
    path: string;
    query: string;
    target_runtime_seconds?: number | null;
    candidate?: TorrentCandidate;
    magnet_uri?: string;
    kind?: 'movie' | 'episode';
    series_title?: string | null;
    season?: number | null;
    episode?: number | null;
  }) =>
    request<{ background: boolean }>('/api/review/replace-torrent', { method: 'POST', body: JSON.stringify(body) }),
  transfer: (path: string) => request('/api/review/transfer', { method: 'POST', body: JSON.stringify({ path }) }),
  approveBatch: (paths: string[]) =>
    request<{ approved: any[]; count: number; errors: any[] }>('/api/review/approve-batch', {
      method: 'POST',
      body: JSON.stringify({ paths }),
    }),
  transferBatch: (paths: string[]) =>
    request<{ transferred: any[]; count: number; skipped: any[]; errors: any[] }>('/api/review/transfer-batch', {
      method: 'POST',
      body: JSON.stringify({ paths }),
    }),

  masLibrary: async (includeEpisodes = false, rescan = false) => {
    type Library = { movies: MasMovie[]; shows: MasShow[] };
    const fresh = await request<Library>(`/api/mas/library?include_episodes=${includeEpisodes}&rescan=${rescan}`);
    if (includeEpisodes) remember(pagePaths.masLibrary, fresh);
    return fresh;
  },
  serverContents: (rescan = false) => request<{ movies: ServerTitle[]; shows: ServerTitle[]; anime: ServerTitle[] }>(`/api/server/contents${rescan ? '?rescan=true' : ''}`),

  serverShow: (path: string) => request<{ path: string; seasons: ServerSeason[] }>(`/api/server/show?path=${encodeURIComponent(path)}`),
  renameServerItem: (kind: 'movie' | 'episode', path: string, title: string) =>
    request<{ renamed: boolean; kind: string; path: string; name: string; folder_renamed: boolean }>(
      '/api/server/rename',
      { method: 'POST', body: JSON.stringify({ kind, path, title }) },
    ),

  serverDirs: () => request<{ movie_dirs: string[]; show_dirs: string[]; anime_dirs: string[] }>('/api/server/dirs'),
  addServerDir: (kind: ServerDirKind, path: string) =>
    request<{ kind: string; dirs: string[] }>('/api/server/dirs', {
      method: 'POST',
      body: JSON.stringify({ kind, path }),
    }),
  removeServerDir: (kind: ServerDirKind, path: string) =>
    request<{ kind: string; dirs: string[] }>('/api/server/dirs', {
      method: 'DELETE',
      body: JSON.stringify({ kind, path }),
    }),

  updateStatus: () => request<UpdateStatus>('/api/update/status'),
  checkUpdate: () => request<UpdateStatus>('/api/update/check', { method: 'POST' }),
  applyUpdate: () => request<UpdateStatus>('/api/update/apply', { method: 'POST' }),

  settings: () => request<{ settings: SettingRow[] }>('/api/settings'),
  setSetting: (key: string, value: any) => request('/api/settings', { method: 'POST', body: JSON.stringify({ key, value }) }),
};
