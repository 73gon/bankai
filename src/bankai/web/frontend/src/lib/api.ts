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
  poster_url: string | null;
  overview: string | null;
  is_new?: boolean;
  available?: boolean;
  checked?: boolean;
  filmpalast_url?: string;
}

export interface SearchResult {
  site: string;
  title: string;
  year: number | null;
  kind: string;
  url: string;
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
  finished_at: number | null;
  exit_code: number | null;
  final_path: string | null;
  step: number | null;
  total_steps: number | null;
  step_label: string;
  overall_percent: number | null;
  pending: boolean;
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
  auto_delay_ms: number;
  transfer_status: string;
  transfer_percent: number;
}

/** Unified one-row-per-title view (library file OR in-flight/failed job). */
export interface TitleRow {
  row_kind: 'library' | 'job';
  id: string;
  title: string;
  kind: string;
  year: number | null;
  poster: string | null;
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
  reason_detail?: string | null;
  delay_ms: number;
  needs_sync_review: boolean;
  sync_confidence: number | null;
  auto_delay_ms: number;
  transfer_status: string;
  transfer_percent: number;
  job_id: string | null;
  job_status: string | null;
  step_label: string | null;
  overall_percent: number | null;
  total_steps: number | null;
  pending: boolean;
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
  auto_delay_ms: number;
  audio_tracks: AudioTrack[];
}

export interface ServerTitle {
  name: string;
  present: boolean;
  location: string | null;
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

  discoverTrending: (kind: string) => request<{ configured: boolean; items: DiscoverItem[] }>(`/api/discover/trending?kind=${kind}`),
  discoverSearch: (q: string, kind: string) =>
    request<{ configured: boolean; items: DiscoverItem[] }>(`/api/discover/search?q=${encodeURIComponent(q)}&kind=${kind}`),
  discoverGerman: (id: number, kind: string) =>
    request<{ tvdb_id: number; kind: string; german: string | null }>(`/api/discover/german?id=${id}&kind=${kind}`),
  posterUrl: (url: string) => `/api/discover/poster?url=${encodeURIComponent(url)}`,

  search: (q: string, kind: string, site?: string) =>
    request<{ results: SearchResult[] }>(`/api/search?q=${encodeURIComponent(q)}&kind=${kind}${site ? `&site=${site}` : ''}`),
  episodes: (show: string, season: number, site?: string) =>
    request<{ found: boolean; site: string | null; episodes: EpisodeItem[] }>(
      `/api/series/episodes?show=${encodeURIComponent(show)}&season=${season}${site ? `&site=${site}` : ''}`,
    ),

  queue: () => request<{ jobs: Job[] }>('/api/queue'),
  queueMovie: (body: { title: string; german?: string; url?: string; site?: string; year?: number }) =>
    request('/api/queue/movie', { method: 'POST', body: JSON.stringify(body) }),
  queueShow: (body: { show: string; season: number; episodes?: number[]; site?: string }) =>
    request('/api/queue/show', { method: 'POST', body: JSON.stringify(body) }),
  cancelJob: (id: string) => request(`/api/queue/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string) => request(`/api/queue/${id}/retry`, { method: 'POST' }),
  deleteJob: (id: string) => request(`/api/queue/${id}`, { method: 'DELETE' }),
  jobLog: (id: string) => request<{ id: string; status: string; log: string }>(`/api/jobs/${id}/log`),

  library: () => request<{ entries: LibraryEntry[]; library: string }>('/api/library'),
  titles: () => request<{ rows: TitleRow[]; library: string }>('/api/titles'),
  redoTitle: (path: string) =>
    request<{ redo: any; title: string }>('/api/titles/redo', {
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
  audioClipUrl: (path: string, stream: number, start: number, dur: number) =>
    `/api/media/audioclip?path=${encodeURIComponent(path)}&stream=${stream}&start=${start}&dur=${dur}`,
  videoClipUrl: (path: string, start: number, dur: number, height = 480) =>
    `/api/media/videoclip?path=${encodeURIComponent(path)}&start=${start}&dur=${dur}&height=${height}`,

  setDelay: (path: string, delay_ms: number) => request('/api/review/delay', { method: 'POST', body: JSON.stringify({ path, delay_ms }) }),
  repack: (path: string, delay_ms: number) =>
    request<{ ok: boolean; message: string; delay_ms: number }>('/api/review/repack', {
      method: 'POST',
      body: JSON.stringify({ path, delay_ms }),
    }),
  approve: (path: string) => request('/api/review/approve', { method: 'POST', body: JSON.stringify({ path }) }),
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
