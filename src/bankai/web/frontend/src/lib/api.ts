// Thin typed API client for the bankai backend.

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

export interface AnimeMetadataMatch {
  tmdb_id: number;
  kind: 'show' | 'movie';
  english_title: string;
  japanese_title: string | null;
  year: number | null;
  poster_url: string | null;
  aliases: string[];
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
  tmdb: AnimeMetadataMatch | null;
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
  configured: boolean;
  items: AnimeEntry[];
  page: number;
  has_next: boolean;
  aliases: string[];
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
  step_label: string;
  overall_percent: number | null;
  pending: boolean;
  action_required: boolean;
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
    url.includes('filmpalast.to/') || !url.startsWith('https://')
      ? `/api/discover/poster?url=${encodeURIComponent(url)}`
      : url,

  search: (q: string, kind: string, site?: string) =>
    request<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}&kind=${kind}${site ? `&site=${site}` : ''}`),
  recentReleases: (page = 0, feed: FilmpalastFeed = 'new') =>
    request<RecentReleasePage>(`/api/releases/recent?page=${page}&feed=${feed}`),
  filmpalastDetails: (url: string) =>
    request<FilmpalastDetails>(`/api/filmpalast/detail?url=${encodeURIComponent(url)}`),
  qbittorrentTorrents: () =>
    request<{ items: QBittorrentItem[] }>('/api/qbittorrent/torrents'),
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
  animeMetadata: (q: string) =>
    request<{ configured: boolean; items: AnimeMetadataMatch[] }>(
      `/api/anime/metadata?q=${encodeURIComponent(q)}`,
    ),
  animeDetail: (url: string) =>
    request<{ description: string; magnet_uri: string | null; publisher: string | null }>(
      `/api/anime/detail?url=${encodeURIComponent(url)}`,
    ),
  animeDownload: (
    entry: AnimeEntry,
    match: AnimeMetadataMatch,
    overrides: { season?: number | null; episode?: number | null } = {},
  ) =>
    request<Job>('/api/anime/download', {
      method: 'POST',
      body: JSON.stringify({
        release_title: entry.title,
        torrent_url: entry.download_url,
        detail_url: entry.detail_url,
        magnet_uri: entry.magnet_uri,
        info_hash: entry.info_hash,
        tmdb_id: match.tmdb_id,
        kind: match.kind,
        english_title: match.english_title,
        year: match.year,
        season: overrides.season ?? null,
        episode: overrides.episode ?? null,
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
  titles: () => request<{ rows: TitleRow[]; library: string }>('/api/titles'),
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

  serverContents: (rescan = false) => request<{ movies: ServerTitle[]; shows: ServerTitle[] }>(`/api/server/contents${rescan ? '?rescan=true' : ''}`),

  serverShow: (path: string) => request<{ path: string; seasons: ServerSeason[] }>(`/api/server/show?path=${encodeURIComponent(path)}`),
  renameServerItem: (kind: 'movie' | 'episode', path: string, title: string) =>
    request<{ renamed: boolean; kind: string; path: string; name: string; folder_renamed: boolean }>(
      '/api/server/rename',
      { method: 'POST', body: JSON.stringify({ kind, path, title }) },
    ),

  serverDirs: () => request<{ movie_dirs: string[]; show_dirs: string[] }>('/api/server/dirs'),
  addServerDir: (kind: 'movie' | 'show', path: string) =>
    request<{ kind: string; dirs: string[] }>('/api/server/dirs', {
      method: 'POST',
      body: JSON.stringify({ kind, path }),
    }),
  removeServerDir: (kind: 'movie' | 'show', path: string) =>
    request<{ kind: string; dirs: string[] }>('/api/server/dirs', {
      method: 'DELETE',
      body: JSON.stringify({ kind, path }),
    }),

  settings: () => request<{ settings: SettingRow[] }>('/api/settings'),
  setSetting: (key: string, value: any) => request('/api/settings', { method: 'POST', body: JSON.stringify({ key, value }) }),
};
