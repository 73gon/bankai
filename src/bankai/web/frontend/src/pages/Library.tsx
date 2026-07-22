import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Library as LibraryIcon,
  Trash2,
  Play,
  Pause,
  ArrowLeft,
  Loader2,
  RotateCcw,
  CheckCircle2,
  UploadCloud,
  X,
  RefreshCw,
  Film,
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  Filter,
  Check,
  ZoomIn,
  ZoomOut,
  Languages,
  AudioLines,
  Minus,
  Plus,
  ScrollText,
  Download,
  CirclePause,
  CirclePlay,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type MediaInfo, type TitleRow, type AudioTrack, type TorrentCandidate } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { formatBytes } from '@/lib/utils';
import { cn } from '@/lib/utils';

let queueRowsCache: TitleRow[] | null = null;

// --- ANSI colour rendering for job logs -----------------------------------
const ANSI_FG: Record<number, string> = {
  30: '#6b7280',
  31: '#f87171',
  32: '#4ade80',
  33: '#fbbf24',
  34: '#60a5fa',
  35: '#e879f9',
  36: '#22d3ee',
  37: '#e5e7eb',
  90: '#9ca3af',
  91: '#fca5a5',
  92: '#86efac',
  93: '#fde047',
  94: '#93c5fd',
  95: '#f0abfc',
  96: '#67e8f9',
  97: '#ffffff',
};

function ansi256(n: number): string {
  if (n < 16) return ANSI_FG[n < 8 ? 30 + n : 82 + n] ?? '#d1d5db';
  if (n >= 232) {
    const v = 8 + (n - 232) * 10;
    return `rgb(${v},${v},${v})`;
  }
  const i = n - 16;
  const conv = (x: number) => (x ? 55 + x * 40 : 0);
  return `rgb(${conv(Math.floor(i / 36))},${conv(Math.floor((i % 36) / 6))},${conv(i % 6)})`;
}

function AnsiLog({ text }: { text: string }) {
  const nodes: JSX.Element[] = [];
  let color: string | undefined;
  let bold = false;
  let key = 0;
  const re = /\x1b\[([0-9;]*)m/g;
  let last = 0;
  const push = (t: string) => {
    if (!t) return;
    nodes.push(
      <span key={key++} style={{ color, fontWeight: bold ? 600 : undefined }}>
        {t}
      </span>,
    );
  };
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    last = re.lastIndex;
    const codes = m[1].split(';').filter(Boolean).map(Number);
    if (codes.length === 0) {
      color = undefined;
      bold = false;
    }
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) {
        color = undefined;
        bold = false;
      } else if (c === 1) bold = true;
      else if (c === 22) bold = false;
      else if (c === 39) color = undefined;
      else if (ANSI_FG[c]) color = ANSI_FG[c];
      else if (c === 38 && codes[i + 1] === 5) {
        color = ansi256(codes[i + 2]);
        i += 2;
      } else if (c === 38 && codes[i + 1] === 2) {
        color = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`;
        i += 4;
      }
    }
  }
  push(text.slice(last));
  return <>{nodes}</>;
}

// Strip machine-only protocol markers and Rich's wide level/timestamp padding
// so the log reads like a concise human activity feed instead of a firehose of
// identical progress lines.
const LOG_NOISE_RE = /BANKAI_(?:STAGE|PROGRESS)\b/;
// Wrapped continuations of a progress line (rich word-wraps them, so the
// leading BANKAI_PROGRESS token is on the previous line only).
const LOG_FRAGMENT_RE = /^(?:eta|speed|pct|status|downloaded)=/i;
function cleanLog(raw: string): string {
  const out: string[] = [];
  let lastKey = '';
  for (let line of raw.split('\n')) {
    if (LOG_NOISE_RE.test(line)) continue;
    // Collapse Rich's padded "[HH:MM:SS] INFO     msg" / "           INFO   msg"
    // gutter into a compact "HH:MM:SS  msg".
    line = line.replace(/^(\x1b\[[0-9;]*m)*\[(\d\d:\d\d:\d\d)\]\s+\w+\s+/, '$2  ');
    line = line.replace(/^(\x1b\[[0-9;]*m)*\s{6,}\w+\s{2,}/, '          ');
    const bare = line.replace(/\x1b\[[0-9;]*m/g, '').trim();
    // Drop wrapped progress fragments (eta=/speed=...) that would otherwise
    // spam the log, and collapse consecutive identical lines.
    if (bare && LOG_FRAGMENT_RE.test(bare)) continue;
    if (bare && bare === lastKey) continue;
    if (bare) lastKey = bare;
    out.push(line);
  }
  return out
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/\s+$/, '');
}

// Truncated cell with a hover tooltip showing the full text. The inner element
// is width-capped so truncation actually happens in an auto-layout table, and
// a native `title` provides the full value on hover (never clipped by the
// table's own overflow container, unlike an absolutely-positioned popover).
function TruncCell({
  text,
  tooltip,
  mono,
  danger,
  width = '16rem',
}: {
  text: string | null | undefined;
  tooltip?: string | null;
  mono?: boolean;
  danger?: boolean;
  width?: string;
}) {
  if (!text) return <span className='text-foreground'>-</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className={cn('truncate cursor-default', mono && 'font-mono', danger && 'text-destructive')} style={{ maxWidth: width }}>
          {text}
        </div>
      </TooltipTrigger>
      <TooltipContent className={cn(mono && 'font-mono')}>{tooltip || text}</TooltipContent>
    </Tooltip>
  );
}

function TorrentCandidateTable({
  candidates,
  busy,
  onSelect,
}: {
  candidates: TorrentCandidate[];
  busy: boolean;
  onSelect: (candidate: TorrentCandidate) => void | Promise<void>;
}) {
  return (
    <div className='min-h-0 overflow-auto rounded-lg border border-border'>
      <table className='w-full min-w-[58rem] table-fixed text-left text-sm text-foreground'>
        <thead className='sticky top-0 z-10 bg-card text-xs uppercase tracking-wide text-foreground'>
          <tr>
            <th className='w-[46%] px-3 py-2'>Title</th>
            <th className='w-32 px-3 py-2'>Policy</th>
            <th className='w-24 px-3 py-2 text-right'>Seeders</th>
            <th className='w-24 px-3 py-2 text-right'>Size</th>
            <th className='w-24 px-3 py-2 text-right'>Length</th>
            <th className='w-36 px-3 py-2'>Source</th>
            <th className='w-24 px-3 py-2 text-right'>Action</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((candidate) => (
            <tr key={candidate.id} className='border-t border-border/70 hover:bg-white/[0.03]'>
              <td className='px-3 py-2.5 font-medium'><TruncCell text={candidate.title} width='100%' /></td>
              <td className='px-3 py-2.5'>
                <Badge variant={candidate.eligible ? 'success' : 'warning'}>{candidate.eligible ? 'Meets policy' : 'Manual override'}</Badge>
              </td>
              <td className='px-3 py-2.5 text-right font-mono'>{candidate.seeders}</td>
              <td className='px-3 py-2.5 text-right font-mono'>{formatBytes(candidate.size_bytes)}</td>
              <td className='px-3 py-2.5 text-right font-mono'>{candidate.runtime_seconds ? `${Math.round(candidate.runtime_seconds / 60)} min` : '—'}</td>
              <td className='px-3 py-2.5'><TruncCell text={candidate.indexer} width='8rem' /></td>
              <td className='px-3 py-2.5 text-right'>
                <Button size='sm' disabled={busy} onClick={() => onSelect(candidate)}>Select</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// the user's scroll position). Only auto-scrolls to the bottom when the user is
// already near it, so reading earlier lines isn't interrupted.
const LogPanel = memo(function LogPanel({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const pinnedRef = useRef(true); // default: stuck to the bottom (newest)
  const lastTopRef = useRef(0);
  const cleaned = useMemo(() => cleanLog(text), [text]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    lastTopRef.current = el.scrollTop;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Default to the bottom and follow new output; if the user scrolled up to
    // read, keep their exact position instead of jumping.
    if (pinnedRef.current) el.scrollTop = el.scrollHeight;
    else el.scrollTop = lastTopRef.current;
  }, [cleaned]);
  return (
    <pre
      ref={ref}
      onScroll={onScroll}
      className='ansi-log max-h-72 overflow-auto rounded-md bg-black/60 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground'
    >
      <AnsiLog text={cleaned || '(no output yet)'} />
    </pre>
  );
});

// Fixed set of statuses a row can be in.
type Status =
  | 'queued'
  | 'downloading'
  | 'waiting_action'
  | 'failed'
  | 'stopped'
  | 'cancelled'
  | 'review'
  | 'repacking'
  | 'approved'
  | 'transferring'
  | 'done'
  | 'deleted';

const STATUS_VARIANT: Record<Status, 'muted' | 'info' | 'destructive' | 'warning' | 'success' | 'review' | 'transfer' | 'repack'> = {
  queued: 'muted',
  downloading: 'info',
  waiting_action: 'warning',
  failed: 'destructive',
  stopped: 'warning',
  cancelled: 'warning',
  review: 'review',
  repacking: 'repack',
  approved: 'success',
  transferring: 'transfer',
  done: 'success',
  deleted: 'muted',
};

const STATUS_ROW_TINT: Record<Status, string> = {
  queued: 'bg-muted/25 hover:bg-muted/35',
  downloading: 'bg-info/[0.12] hover:bg-info/[0.18]',
  waiting_action: 'bg-warning/[0.16] hover:bg-warning/[0.22]',
  failed: 'bg-destructive/[0.14] hover:bg-destructive/[0.2]',
  stopped: 'bg-amber-500/[0.12] hover:bg-amber-500/[0.18]',
  cancelled: 'bg-warning/[0.12] hover:bg-warning/[0.18]',
  review: 'bg-foreground/[0.08] hover:bg-foreground/[0.13]',
  repacking: 'bg-repack/[0.13] hover:bg-repack/[0.2]',
  approved: 'bg-success/[0.1] hover:bg-success/[0.16]',
  transferring: 'bg-transfer/[0.13] hover:bg-transfer/[0.19]',
  done: 'bg-success/[0.13] hover:bg-success/[0.19]',
  deleted: 'bg-muted/20 hover:bg-muted/30',
};

const STATUS_LABEL: Record<Status, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  waiting_action: 'Waiting for action',
  failed: 'Failed',
  stopped: 'Stopped',
  cancelled: 'Cancelled',
  review: 'Review',
  repacking: 'Repacking',
  approved: 'Approved',
  transferring: 'Transferring',
  done: 'Done',
  deleted: 'Deleted',
};

function rowStatus(r: TitleRow): Status {
  if (r.row_kind === 'job') {
    if (r.action_required) return 'waiting_action';
    if (r.pending) return 'queued';
    if (r.job_status === 'running') return 'downloading';
    if (r.job_status === 'failed' || r.job_status === 'error') return 'failed';
    if (r.job_status === 'stopped') return 'stopped';
    if (r.job_status === 'cancelled') return 'cancelled';
    if (r.job_status === 'deleted' || r.stage === 'deleted') return 'deleted';
    return 'done';
  }
  if (r.stage === 'deleted') return 'deleted';
  if (r.stage === 'repacking' || r.repack_status === 'repacking') return 'repacking';
  if (r.transfer_status === 'transferring') return 'transferring';
  if (r.transfer_status === 'failed') return 'failed';
  if (r.stage === 'transferred' || r.transfer_status === 'done') return 'done';
  if (r.stage === 'approved') return 'approved';
  return 'review';
}

function StatusCell({ r }: { r: TitleRow }) {
  const s = rowStatus(r);
  if (s === 'downloading') {
    const pct = Math.round(r.overall_percent ?? 0);
    return (
      <div className='min-w-[10rem]'>
        <Badge variant={STATUS_VARIANT[s]} className='gap-1.5'>
          <Loader2 className='h-3 w-3 animate-spin' />
          {r.step_label || 'Downloading'} {pct > 0 ? `${pct}%` : ''}
        </Badge>
        {pct > 0 && (
          <div className='mt-1 h-1 overflow-hidden rounded-full bg-secondary'>
            <div
              className='h-full rounded-full bg-gradient-to-r from-info to-transfer'
              style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
            />
          </div>
        )}
      </div>
    );
  }
  if (s === 'transferring') {
    const pct = Math.round(r.transfer_percent || 0);
    return (
      <Badge variant={STATUS_VARIANT[s]} className='gap-1.5'>
        <Loader2 className='h-3 w-3 animate-spin' /> Transferring {pct > 0 ? `${pct}%` : ''}
      </Badge>
    );
  }
  if (s === 'repacking') {
    const pct = Math.round(r.repack_percent || 0);
    return (
      <Badge variant={STATUS_VARIANT[s]} className='gap-1.5'>
        <Loader2 className='h-3 w-3 animate-spin' /> {r.repack_label || (r.repack_kind === 'torrent' ? 'Replacing torrent' : 'Repacking')}{' '}
        {pct > 0 ? `${pct}%` : ''}
      </Badge>
    );
  }
  return <Badge variant={STATUS_VARIANT[s]}>{STATUS_LABEL[s]}</Badge>;
}

function statusRank(r: TitleRow): number {
  if (r.row_kind === 'job') {
    if (r.action_required) return 0;
    if (r.job_status === 'running') return 0;
    if (r.pending) return 1;
    if (r.job_status === 'failed' || r.job_status === 'error') return 2;
    return 3;
  }
  if (r.stage === 'repacking' || r.repack_status === 'repacking') return 4;
  if (r.stage === 'review') return 4;
  if (r.stage === 'approved') return 5;
  return 6; // transferred
}

type SortCol = 'title' | 'type' | 'status' | 'created_at' | 'updated_at';
type SortDir = 'asc' | 'desc';
type PageSize = 10 | 25 | 50 | 100;

const QUEUE_PREFERENCES_KEY = 'bankai:queue-table-preferences';
const SORT_COLUMNS: SortCol[] = ['title', 'type', 'status', 'created_at', 'updated_at'];
const PAGE_SIZES: PageSize[] = [10, 25, 50, 100];

interface QueuePreferences {
  filter: string;
  statuses: Status[];
  filtersOpen: boolean;
  sortCol: SortCol;
  sortDir: SortDir;
  pageSize: PageSize;
}

function readQueuePreferences(): QueuePreferences {
  const fallback: QueuePreferences = {
    filter: '',
    statuses: [],
    filtersOpen: false,
    sortCol: 'created_at',
    sortDir: 'desc',
    pageSize: 50,
  };
  try {
    const raw = localStorage.getItem(QUEUE_PREFERENCES_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<QueuePreferences>;
    const allStatuses = Object.keys(STATUS_LABEL) as Status[];
    return {
      filter: typeof saved.filter === 'string' ? saved.filter : fallback.filter,
      statuses: Array.isArray(saved.statuses) ? saved.statuses.filter((status): status is Status => allStatuses.includes(status as Status)) : [],
      filtersOpen: typeof saved.filtersOpen === 'boolean' ? saved.filtersOpen : fallback.filtersOpen,
      sortCol: SORT_COLUMNS.includes(saved.sortCol as SortCol) ? (saved.sortCol as SortCol) : fallback.sortCol,
      sortDir: saved.sortDir === 'asc' || saved.sortDir === 'desc' ? saved.sortDir : fallback.sortDir,
      pageSize: PAGE_SIZES.includes(saved.pageSize as PageSize) ? (saved.pageSize as PageSize) : fallback.pageSize,
    };
  } catch {
    return fallback;
  }
}

function dateTimeLabel(ts: number | null): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  // German format, 24h clock: DD.MM.YYYY HH:mm
  return d.toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function titleWithYear(r: TitleRow): string {
  return r.year ? `${r.title} (${r.year})` : r.title;
}

// Multi-select dropdown of every row status (a lightweight popover — no
// external dependency). Selecting none means "all".
function StatusMultiSelect({
  selected,
  onToggle,
  onClear,
  counts,
}: {
  selected: Set<Status>;
  onToggle: (s: Status) => void;
  onClear: () => void;
  counts: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);
  const all = Object.keys(STATUS_LABEL) as Status[];
  const label =
    selected.size === 0
      ? 'All statuses'
      : all
          .filter((s) => selected.has(s))
          .map((s) => STATUS_LABEL[s])
          .join(', ');
  return (
    <div ref={ref} className='relative'>
      <button
        onClick={() => setOpen((o) => !o)}
        className='flex h-9 min-w-[12rem] max-w-[20rem] items-center justify-between gap-2 rounded-md border border-white/10 bg-gradient-to-b from-white/[0.02] to-transparent px-3 text-sm transition-colors hover:border-white/20'
      >
        <span className='truncate text-left text-muted-foreground'>{label}</span>
        <ChevronsUpDown className='h-4 w-4 shrink-0 opacity-50' />
      </button>
      {open && (
        <div className='absolute z-50 mt-1 w-64 rounded-md border border-border bg-card p-1 shadow-lg'>
          {all.map((s) => (
            <button
              key={s}
              onClick={() => onToggle(s)}
              className='flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-sm hover:bg-secondary'
            >
              <span className='flex items-center gap-2'>
                <span
                  className={cn(
                    'flex h-4 w-4 items-center justify-center rounded border',
                    selected.has(s) ? 'border-primary bg-primary text-primary-foreground' : 'border-input',
                  )}
                >
                  {selected.has(s) && <Check className='h-3 w-3' strokeWidth={3} />}
                </span>
                {STATUS_LABEL[s]}
              </span>
              <span className='text-xs text-muted-foreground'>{counts[s] ?? 0}</span>
            </button>
          ))}
          {selected.size > 0 && (
            <button onClick={onClear} className='mt-1 w-full rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-secondary'>
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Library() {
  const [initialPreferences] = useState(readQueuePreferences);
  const [rows, setRows] = useState<TitleRow[]>(queueRowsCache ?? []);
  const [loading, setLoading] = useState(queueRowsCache == null);
  const [filter, setFilter] = useState(initialPreferences.filter);
  const [statusFilters, setStatusFilters] = useState<Set<Status>>(() => new Set(initialPreferences.statuses));
  const [filtersOpen, setFiltersOpen] = useState(initialPreferences.filtersOpen);
  const [sortCol, setSortCol] = useState<SortCol>(initialPreferences.sortCol);
  const [sortDir, setSortDir] = useState<SortDir>(initialPreferences.sortDir);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(initialPreferences.pageSize);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [redoing, setRedoing] = useState<Set<string>>(new Set());

  const [review, setReview] = useState<TitleRow | null>(null);
  const [del, setDel] = useState<TitleRow | null>(null);
  const [torrentAction, setTorrentAction] = useState<TitleRow | null>(null);
  const [torrentCandidates, setTorrentCandidates] = useState<TorrentCandidate[]>([]);
  const [torrentLoading, setTorrentLoading] = useState(false);
  const [torrentReplacement, setTorrentReplacement] = useState<TitleRow | null>(null);
  const [replacementMode, setReplacementMode] = useState<'auto' | 'manual'>('manual');
  const [replacementCandidates, setReplacementCandidates] = useState<TorrentCandidate[]>([]);
  const [replacementRuntime, setReplacementRuntime] = useState<number | null>(null);
  const [replacementLoading, setReplacementLoading] = useState(false);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const restoreScrollRef = useRef<number | null>(null);
  useEffect(() => {
    try {
      const preferences: QueuePreferences = {
        filter,
        statuses: Array.from(statusFilters),
        filtersOpen,
        sortCol,
        sortDir,
        pageSize,
      };
      localStorage.setItem(QUEUE_PREFERENCES_KEY, JSON.stringify(preferences));
    } catch {
      /* localStorage may be unavailable in a locked-down browser. */
    }
  }, [filter, statusFilters, filtersOpen, sortCol, sortDir, pageSize]);

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      const r = await api.titles();
      queueRowsCache = r.rows;
      setRows(r.rows);
    } catch (e: any) {
      if (!silent) toast.error(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }
  useEffect(() => {
    load(queueRowsCache != null);
    const t = setInterval(() => load(true), 3000);
    return () => clearInterval(t);
  }, []);

  // Live-refresh the log of the currently expanded job so running jobs show
  // progress. LogPanel preserves scroll unless the user is at the bottom.
  const rowsRef = useRef<TitleRow[]>(rows);
  rowsRef.current = rows;
  useEffect(() => {
    if (!expanded) return;
    const jobId = rowsRef.current.find((r) => r.id === expanded)?.job_id;
    if (!jobId) return;
    let cancelled = false;
    const fetchLog = async () => {
      try {
        const res = await api.jobLog(jobId);
        if (!cancelled) setLogs((l) => ({ ...l, [jobId]: res.log || '(no log output)' }));
      } catch {
        /* keep the last log we have */
      }
    };
    fetchLog();
    const t = setInterval(fetchLog, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [expanded]);

  function typeLabel(r: TitleRow): string {
    if (r.kind === 'episode') {
      const parts: string[] = [];
      if (r.series) parts.push(r.series);
      if (r.season != null) parts.push(`S${String(r.season).padStart(2, '0')}`);
      return parts.length ? parts.join(' · ') : 'Episode';
    }
    return 'Movie';
  }

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) {
      const k = rowStatus(r);
      c[k] = (c[k] ?? 0) + 1;
    }
    return c;
  }, [rows]);

  const filtered = useMemo(() => {
    const list = rows.filter(
      (e) => titleWithYear(e).toLowerCase().includes(filter.toLowerCase()) && (statusFilters.size === 0 || statusFilters.has(rowStatus(e))),
    );
    const dir = sortDir === 'asc' ? 1 : -1;
    list.sort((a, b) => {
      let d = 0;
      if (sortCol === 'title') d = a.title.localeCompare(b.title);
      else if (sortCol === 'type') d = typeLabel(a).localeCompare(typeLabel(b));
      else if (sortCol === 'status') d = statusRank(a) - statusRank(b);
      else if (sortCol === 'created_at') d = (a.created_at ?? 0) - (b.created_at ?? 0);
      else d = (a.updated_at ?? 0) - (b.updated_at ?? 0);
      if (d === 0) d = (a.created_at ?? 0) - (b.created_at ?? 0);
      return d * dir;
    });
    return list;
  }, [rows, filter, statusFilters, sortCol, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = useMemo(() => filtered.slice(safePage * pageSize, safePage * pageSize + pageSize), [filtered, safePage, pageSize]);
  useEffect(() => {
    setPage(0);
  }, [filter, statusFilters, sortCol, sortDir, pageSize]);

  function toggleFilter(k: Status) {
    setStatusFilters((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  }

  function sortBy(col: SortCol) {
    if (sortCol === col) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortCol(col);
      setSortDir(col === 'title' || col === 'type' ? 'asc' : 'desc');
    }
  }

  async function toggleExpand(r: TitleRow) {
    restoreScrollRef.current = tableScrollRef.current?.scrollTop ?? null;
    const next = expanded === r.id ? null : r.id;
    setExpanded(next);
    if (next && r.job_id && logs[r.job_id] === undefined) {
      setLogs((l) => ({ ...l, [r.job_id!]: 'Loading logs…' }));
      try {
        const res = await api.jobLog(r.job_id);
        setLogs((l) => ({ ...l, [r.job_id!]: res.log || '(no log output)' }));
      } catch (e: any) {
        setLogs((l) => ({ ...l, [r.job_id!]: `Failed to load log: ${e.message}` }));
      }
    }
  }

  useLayoutEffect(() => {
    if (restoreScrollRef.current == null || !tableScrollRef.current) return;
    tableScrollRef.current.scrollTop = restoreScrollRef.current;
    restoreScrollRef.current = null;
  }, [expanded]);

  async function transferOne(path: string) {
    try {
      await api.transfer(path);
      toast.success('Sending to server');
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function cancelJob(id: string) {
    try {
      await api.cancelJob(id);
      toast.success('Cancelled');
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function deleteJob(id: string) {
    try {
      await api.deleteJob(id);
      toast.success('Removed');
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function redo(r: TitleRow) {
    const key = r.path || r.title;
    setRedoing((s) => new Set(s).add(r.id));
    try {
      const res = await api.redoTitle(key);
      toast.success(`Re-running ${res.title}`);
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setRedoing((s) => {
        const n = new Set(s);
        n.delete(r.id);
        return n;
      });
    }
  }

  async function openTorrentAction(r: TitleRow) {
    if (!r.job_id) return;
    setTorrentAction(r);
    setTorrentCandidates([]);
    setTorrentLoading(true);
    try {
      const result = await api.torrentAction(r.job_id);
      setTorrentCandidates(result.candidates);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTorrentLoading(false);
    }
  }

  async function stopJob(id: string) {
    try {
      await api.stopJob(id);
      toast.success('Job stopped — it can be continued later');
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function continueJob(id: string) {
    try {
      await api.continueJob(id);
      toast.success('Job continued');
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function chooseTorrent(candidate: TorrentCandidate) {
    if (!torrentAction?.job_id) return;
    setTorrentLoading(true);
    try {
      await api.chooseTorrent(torrentAction.job_id, candidate.id);
      toast.success('Torrent selected — pipeline resumed');
      setTorrentAction(null);
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTorrentLoading(false);
    }
  }

  async function openTorrentReplacement(r: TitleRow) {
    if (!r.path) return;
    setTorrentReplacement(r);
    setReplacementMode('manual');
    setReplacementCandidates([]);
    setReplacementRuntime(null);
    setReplacementLoading(true);
    let runtime: number | null = null;
    try {
      const media = await api.mediaInfo(r.path);
      runtime = media.audio_tracks.find((track) => track.is_german)?.duration ?? media.duration ?? null;
      setReplacementRuntime(runtime);
    } catch {
      // Runtime ranking is an enhancement; torrent selection still works
      // without it when probing a partially written file fails.
    }
    try {
      const result = await api.torrentSearch(titleWithYear(r), runtime);
      setReplacementCandidates(result.candidates);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplacementLoading(false);
    }
  }

  async function startTorrentReplacement(candidate?: TorrentCandidate) {
    if (!torrentReplacement?.path) return;
    setReplacementBusy(true);
    try {
      await api.replaceTorrent({
        path: torrentReplacement.path,
        query: titleWithYear(torrentReplacement),
        target_runtime_seconds: replacementRuntime,
        candidate,
      });
      toast.success(candidate ? 'Selected torrent is downloading in the background' : 'Automatic torrent replacement started');
      setTorrentReplacement(null);
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplacementBusy(false);
    }
  }

  function SyncCell({ r }: { r: TitleRow }) {
    if (r.row_kind !== 'library') return <span className='text-foreground'>—</span>;
    const c = r.sync_confidence;
    if (c == null) {
      if (r.needs_sync_review)
        return (
          <Badge variant='review' title='Automatic alignment was inconclusive — open Review.'>
            Manual review
          </Badge>
        );
      return (
        <Badge variant='muted' title='No automatic audio sync was applied.'>
          Not analyzed
        </Badge>
      );
    }
    const pct = Math.round(c * 100);
    const acc =
      c >= 0.9
        ? { label: 'Spot on', variant: 'success' as const }
        : c >= 0.75
          ? { label: 'Nearly synced', variant: 'info' as const }
          : c >= 0.5
            ? { label: 'Needs tuning', variant: 'warning' as const }
            : { label: 'Out of sync', variant: 'destructive' as const };
    return (
      <Badge variant={acc.variant} title={`Automatic sync confidence ${pct}%`}>
        {acc.label}
      </Badge>
    );
  }

  function Poster({ r }: { r: TitleRow }) {
    if (r.poster) return <img src={r.poster} alt='' loading='lazy' className='h-14 w-10 shrink-0 rounded object-cover' />;
    return (
      <div className='flex h-14 w-10 shrink-0 items-center justify-center rounded bg-secondary/60'>
        {r.kind === 'episode' ? <LibraryIcon className='h-4 w-4 text-muted-foreground' /> : <Film className='h-4 w-4 text-muted-foreground' />}
      </div>
    );
  }

  function RowView({ r }: { r: TitleRow }) {
    const isLib = r.row_kind === 'library';
    const isJob = r.row_kind === 'job';
    const canCancel = isJob && r.pending;
    const canStop = isJob && r.job_status === 'running';
    const canContinue = isJob && r.job_status === 'stopped';
    const canDelete = isJob && ['failed', 'error', 'cancelled', 'stopped'].includes(r.job_status || '');
    const isOpen = expanded === r.id;
    const status = rowStatus(r);
    const isRepacking = r.stage === 'repacking' || r.repack_status === 'repacking';
    const stop = (e: React.MouseEvent) => e.stopPropagation();
    return (
      <>
        <tr className={cn('border-t border-border text-foreground', STATUS_ROW_TINT[status])}>
          <td className='px-2 py-2 align-middle'>
            <Poster r={r} />
          </td>
          <td className='max-w-[24rem] px-2 py-2 align-middle'>
            <div className='flex items-center gap-1.5'>
              <span className='truncate font-medium'>{titleWithYear(r)}</span>
            </div>
            {isLib && r.size ? <div className='text-xs text-foreground'>{formatBytes(r.size)}</div> : null}
          </td>
          <td className='px-2 py-2 align-middle text-xs text-foreground'>{typeLabel(r)}</td>
          <td className='px-2 py-2 align-middle'>
            <StatusCell r={r} />
          </td>
          <td className='px-2 py-2 align-middle text-xs text-foreground'>
            <TruncCell text={r.reason} tooltip={r.reason_detail || r.reason} danger width='18rem' />
          </td>
          <td className='px-2 py-2 align-middle'>
            <SyncCell r={r} />
          </td>
          <td className='whitespace-nowrap px-2 py-2 align-middle text-xs text-foreground'>{dateTimeLabel(r.created_at)}</td>
          <td className='whitespace-nowrap px-2 py-2 align-middle text-xs text-foreground'>{dateTimeLabel(r.updated_at)}</td>
          <td className='px-2 py-2 align-middle text-xs text-foreground'>
            <TruncCell text={r.path} mono width='20rem' />
          </td>
          <td className='px-2 py-2 align-middle' onClick={stop}>
            <div className='flex items-center justify-end gap-1'>
              {r.action_required && (
                <Button size='sm' variant='default' onClick={() => openTorrentAction(r)}>
                  <Download data-icon='inline-start' /> Select torrent
                </Button>
              )}
              {isLib && (
                <Button size='sm' variant='default' onClick={() => setReview(r)} disabled={isRepacking}>
                  <Play data-icon='inline-start' /> Review
                </Button>
              )}
              {isLib && r.kind === 'movie' && (
                <Button
                  size='icon'
                  variant='ghost'
                  onClick={() => openTorrentReplacement(r)}
                  disabled={isRepacking}
                  title='Select a new torrent'
                >
                  <Download className='h-4 w-4' />
                </Button>
              )}
              <Button
                size='icon'
                variant='ghost'
                onClick={() => redo(r)}
                disabled={redoing.has(r.id) || isRepacking}
                title='Redo — re-run the pipeline for this title'
              >
                {redoing.has(r.id) ? <Loader2 className='h-4 w-4 animate-spin' /> : <RefreshCw className='h-4 w-4' />}
              </Button>
              {r.job_id && (
                <Button size='icon' variant='ghost' onClick={() => toggleExpand(r)} title={isOpen ? 'Hide logs' : 'Show logs'}>
                  <ScrollText className='h-4 w-4' />
                </Button>
              )}
              {canCancel && (
                <Button size='icon' variant='ghost' onClick={() => cancelJob(r.job_id!)} title='Cancel'>
                  <X className='h-4 w-4' />
                </Button>
              )}
              {canStop && (
                <Button size='icon' variant='ghost' onClick={() => stopJob(r.job_id!)} title='Stop and keep resumable'>
                  <CirclePause className='h-4 w-4' />
                </Button>
              )}
              {canContinue && (
                <Button size='sm' variant='default' onClick={() => continueJob(r.job_id!)}>
                  <CirclePlay data-icon='inline-start' /> Continue
                </Button>
              )}
              {canDelete && (
                <Button size='icon' variant='ghost' onClick={() => deleteJob(r.job_id!)} title='Remove — delete this failed job'>
                  <Trash2 className='h-4 w-4 text-red-400' />
                </Button>
              )}
              {isLib && (r.stage === 'approved' || r.transfer_status === 'failed') && (
                <Button size='sm' variant='default' onClick={() => transferOne(r.path!)} title='Send to media server'>
                  <UploadCloud data-icon='inline-start' />
                  {r.transfer_status === 'failed' ? 'Retry transfer' : 'Transfer'}
                </Button>
              )}
              {isLib && (
                <Button size='icon' variant='ghost' onClick={() => setDel(r)} title='Delete' disabled={isRepacking}>
                  <Trash2 className='h-4 w-4 text-red-400' />
                </Button>
              )}
            </div>
          </td>
        </tr>
        {isOpen && (
          <tr className='border-t border-border bg-black/20'>
            <td colSpan={10} className='px-3 py-2'>
              {r.job_id ? (
                <LogPanel text={logs[r.job_id] ?? 'Loading logs…'} />
              ) : (
                <p className='text-xs text-muted-foreground'>No log available for this title.</p>
              )}
            </td>
          </tr>
        )}
      </>
    );
  }

  function SortHeader({ col, label, className }: { col: SortCol; label: string; className?: string }) {
    const active = sortCol === col;
    return (
      <th className={cn('px-2 py-2 font-medium', className)}>
        <button onClick={() => sortBy(col)} className='inline-flex items-center gap-1 hover:text-foreground'>
          {label}
          {active ? (
            sortDir === 'asc' ? (
              <ChevronUp className='h-3 w-3' />
            ) : (
              <ChevronDown className='h-3 w-3' />
            )
          ) : (
            <ChevronsUpDown className='h-3 w-3 opacity-40' />
          )}
        </button>
      </th>
    );
  }

  async function doDelete() {
    if (!del?.path) return;
    try {
      await api.deleteFile(del.path);
      toast.success('Deleted');
      setDel(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className='flex h-full min-h-0 flex-col gap-4'>
      <header className='flex flex-wrap items-center justify-between gap-3'>
        <div className='flex items-baseline gap-2'>
          <h1 className='text-2xl font-semibold'>Queue</h1>
          <span className='text-sm text-muted-foreground'>— Every title in one table: downloads, review, sync and transfer.</span>
        </div>
      </header>

      {/* Collapsible filter bar */}
      <div className='rounded-lg border border-border bg-card/40'>
        <button onClick={() => setFiltersOpen((o) => !o)} className='flex w-full items-center justify-between px-3 py-2 text-sm font-medium'>
          <span className='flex items-center gap-2'>
            <Filter className='h-4 w-4 text-muted-foreground' />
            Filters
            {(statusFilters.size > 0 || filter) && <Badge variant='accent'>{statusFilters.size + (filter ? 1 : 0)}</Badge>}
          </span>
          {filtersOpen ? <ChevronUp className='h-4 w-4' /> : <ChevronDown className='h-4 w-4' />}
        </button>
        {filtersOpen && (
          <div className='flex flex-wrap items-center gap-3 border-t border-border px-3 py-3'>
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Search title…' className='w-56' />
            <StatusMultiSelect selected={statusFilters} onToggle={toggleFilter} onClear={() => setStatusFilters(new Set())} counts={counts} />
            {(statusFilters.size > 0 || filter) && (
              <Button
                variant='ghost'
                size='sm'
                onClick={() => {
                  setStatusFilters(new Set());
                  setFilter('');
                }}
              >
                Reset
              </Button>
            )}
          </div>
        )}
      </div>

      {loading ? (
        <div className='space-y-2'>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className='h-12' />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState icon={LibraryIcon} title='Nothing here yet' description='Queue a title from Discover or Search — it will appear here.' />
      ) : (
        <div ref={tableScrollRef} className='min-h-0 flex-1 overflow-auto rounded-lg border'>
          <table className='w-full text-sm'>
            <thead className='sticky top-0 z-10 bg-card text-left text-xs uppercase tracking-wide text-foreground'>
              <tr>
                <th className='w-14 px-2 py-2' />
                <SortHeader col='title' label='Title' className='text-left' />
                <SortHeader col='type' label='Type' className='text-left' />
                <SortHeader col='status' label='Status' className='text-left' />
                <th className='px-2 py-2 text-left font-medium'>Reason</th>
                <th className='px-2 py-2 text-left font-medium'>Sync</th>
                <SortHeader col='created_at' label='Created at' className='text-left' />
                <SortHeader col='updated_at' label='Updated at' className='text-left' />
                <th className='px-2 py-2 text-left font-medium'>Path</th>
                <th className='px-2 py-2 text-right font-medium'>Actions</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => (
                <RowView key={r.id} r={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && (
        <div className='flex items-center justify-between gap-3 text-sm'>
          <div className='flex items-center gap-2 text-xs text-muted-foreground'>
            <span>
              {filtered.length === 0 ? '0' : `${safePage * pageSize + 1}–${Math.min(filtered.length, (safePage + 1) * pageSize)}`} of{' '}
              {filtered.length}
            </span>
            <span>·</span>
            <span>Per page</span>
            <Select
              value={String(pageSize)}
              onValueChange={(value) => {
                const next = Number(value) as PageSize;
                if (PAGE_SIZES.includes(next)) setPageSize(next);
              }}
            >
              <SelectTrigger className='h-7 w-[86px]'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value='10'>10</SelectItem>
                  <SelectItem value='25'>25</SelectItem>
                  <SelectItem value='50'>50</SelectItem>
                  <SelectItem value='100'>100</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
          <div className='flex items-center gap-2'>
            <Button size='sm' variant='secondary' disabled={safePage === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
              Prev
            </Button>
            <span className='text-xs text-muted-foreground'>
              Page {safePage + 1} / {pageCount}
            </span>
            <Button size='sm' variant='secondary' disabled={safePage >= pageCount - 1} onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      <Dialog open={!!del} onOpenChange={(o) => !o && setDel(null)}>
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Delete file?</DialogTitle>
            <DialogDescription>
              This permanently removes <span className='font-medium'>{del?.name}</span> from the local library. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='outline' onClick={() => setDel(null)}>
              Cancel
            </Button>
            <Button variant='destructive' onClick={doDelete}>
              <Trash2 className='h-4 w-4' /> Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!torrentAction} onOpenChange={(open) => !open && setTorrentAction(null)}>
        <DialogContent className='flex max-h-[88vh] w-[96vw] max-w-7xl flex-col overflow-hidden'>
          <DialogHeader>
            <DialogTitle>Select a torrent</DialogTitle>
            <DialogDescription>
              No release met your configured quality, size, and seeder rules. Pick a release explicitly to resume {torrentAction?.title}.
            </DialogDescription>
          </DialogHeader>
          {torrentLoading && torrentCandidates.length === 0 ? (
            <div className='flex items-center justify-center py-10'>
              <Loader2 className='h-6 w-6 animate-spin' />
            </div>
          ) : torrentCandidates.length === 0 ? (
            <EmptyState icon={Download} title='No torrent results' description='Try the job again later when indexers have more releases.' />
          ) : (
            <TorrentCandidateTable candidates={torrentCandidates} busy={torrentLoading} onSelect={chooseTorrent} />
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!torrentReplacement}
        onOpenChange={(open) => {
          if (!open && !replacementBusy) setTorrentReplacement(null);
        }}
      >
        <DialogContent className='flex max-h-[88vh] w-[96vw] max-w-7xl flex-col overflow-hidden'>
          <DialogHeader>
            <DialogTitle>Repack with a new torrent</DialogTitle>
            <DialogDescription>
              Replace the HQ video for {torrentReplacement?.title}. The existing German audio is retained and remuxed with the new release.
            </DialogDescription>
          </DialogHeader>
          <Tabs
            className='flex min-h-0 flex-1 flex-col'
            value={replacementMode}
            onValueChange={(value) => setReplacementMode(value as 'auto' | 'manual')}
          >
            <TabsList>
              <TabsTrigger value='auto'>Automatic</TabsTrigger>
              <TabsTrigger value='manual'>Manual</TabsTrigger>
            </TabsList>
            <TabsContent value='auto'>
              <div className='flex flex-col gap-4 py-3'>
                <p className='text-sm text-foreground'>
                  Bankai will prefer an eligible release close to the German runtime, then fall back to the release with the most seeders.
                </p>
                <Button onClick={() => startTorrentReplacement()} disabled={replacementBusy}>
                  {replacementBusy ? <Loader2 className='h-4 w-4 animate-spin' /> : <Download data-icon='inline-start' />}
                  Pick automatically
                </Button>
              </div>
            </TabsContent>
            <TabsContent value='manual' className='min-h-0 flex-1 overflow-hidden'>
              {replacementLoading ? (
                <div className='flex items-center justify-center py-10'>
                  <Loader2 className='h-6 w-6 animate-spin' />
                </div>
              ) : replacementCandidates.length === 0 ? (
                <EmptyState icon={Download} title='No matching torrents' description='Try the automatic option later when indexers have more releases.' />
              ) : (
                <TorrentCandidateTable candidates={replacementCandidates} busy={replacementBusy} onSelect={startTorrentReplacement} />
              )}
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>

      {/* Review studio */}
      {review && review.path && (
        <WaveformReview
          entry={review}
          onClose={() => {
            setReview(null);
            load();
          }}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Waveform review — lightweight audio A/B aligner (no video transcode)
// --------------------------------------------------------------------------

function decodePeaks(b64: string): Uint8Array {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

function fmtClock(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function fmtClockMs(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const totalMs = Math.round(s * 1000);
  const m = Math.floor(totalMs / 60_000);
  const sec = Math.floor((totalMs % 60_000) / 1000);
  const ms = totalMs % 1000;
  return `${m}:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
}

function WaveformReview({ entry, onClose }: { entry: TitleRow; onClose: () => void }) {
  const path = entry.path as string;
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [engStream, setEngStream] = useState<number | null>(null);
  const [gerStream, setGerStream] = useState<number | null>(null);
  // Both lanes are fetched as WIDE track-time buffers (3× the view) so panning
  // and zooming redraw instantly from memory (real-time), and each lane is
  // robustly scaled to its visible loudness so quiet dialogue stays readable.
  const [engBuf, setEngBuf] = useState<{ peaks: Uint8Array; start: number; dur: number } | null>(null);
  const [gerBuf, setGerBuf] = useState<{ peaks: Uint8Array; start: number; dur: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [delayMs, setDelayMs] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  // Drift correction: time-stretch factor for the German track (atempo). 1 =
  // no stretch. >1 speeds it up (shorter), <1 slows it down (longer). Applied
  // on top of the constant delay so a dub that slowly slides out of sync
  // (e.g. PAL 25fps vs 23.976) can be corrected.
  const [stretch, setStretch] = useState(1);
  const [windowSec, setWindowSec] = useState(30);
  const [center, setCenter] = useState(60);
  const [playing, setPlaying] = useState<'none' | 'both' | 'eng' | 'ger'>('none');
  const [busy, setBusy] = useState<string | null>(null);
  const [canvasW, setCanvasW] = useState(800);
  const [canvasH, setCanvasH] = useState(() => {
    try {
      const v = parseInt(localStorage.getItem('bankai:review-bars-height') || '', 10);
      return Number.isFinite(v) && v >= 100 && v <= 640 ? v : 160;
    } catch {
      return 160;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('bankai:review-bars-height', String(canvasH));
    } catch {
      /* ignore */
    }
  }, [canvasH]);
  const [dragging, setDragging] = useState(false);
  // Playback quality for the video preview (px height). 360p..1080p. Persisted
  // so the choice carries over to the next review.
  const [quality, setQuality] = useState(() => {
    try {
      const v = parseInt(localStorage.getItem('bankai:review-quality') || '', 10);
      return [360, 480, 720, 1080].includes(v) ? v : 480;
    } catch {
      return 480;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('bankai:review-quality', String(quality));
    } catch {
      /* ignore */
    }
  }, [quality]);
  // Seek position within the current window, 0..1. Click a lane / drag the
  // playhead to move it; playback starts from here.
  const [seekFrac, setSeekFrac] = useState(0);
  const [videoLoading, setVideoLoading] = useState(true);
  const [gerLoading, setGerLoading] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [replaceMode, setReplaceMode] = useState<'auto' | 'manual'>('manual');
  const [replaceCandidates, setReplaceCandidates] = useState<TorrentCandidate[]>([]);
  const [replaceLoading, setReplaceLoading] = useState(false);

  const wrapRef = useRef<HTMLDivElement>(null);
  const engCanvas = useRef<HTMLCanvasElement>(null);
  const gerCanvas = useRef<HTMLCanvasElement>(null);
  const gerAudio = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const dragRef = useRef<{ x: number; delay: number } | null>(null);
  const engHeadRef = useRef<HTMLDivElement>(null);
  const gerHeadRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);
  const livePosRef = useRef(0);
  const wasPlayingRef = useRef(false);
  const resumeModeRef = useRef<'both' | 'eng' | 'ger'>('both');
  const seekFracRef = useRef(0);
  const playTokenRef = useRef(0);
  const playbackStartOffsetRef = useRef(0);

  const duration = info?.duration ?? 0;
  const viewStart = Math.max(0, center - windowSec / 2);
  const pxPerSec = canvasW / windowSec;
  // Clip covering the visible window (capped) so we can seek within it.
  const clipLen = Math.min(windowSec, 90);

  // Move both lane playheads to a fraction (0..1) of the window.
  function parkHeads(frac: number) {
    for (const r of [engHeadRef, gerHeadRef]) {
      if (r.current) {
        r.current.style.opacity = '1';
        r.current.style.transform = `translateX(${frac * canvasW}px)`;
      }
    }
  }

  function haltAudioVideo() {
    playTokenRef.current += 1;
    if (gerAudio.current) {
      gerAudio.current.pause();
      gerAudio.current.src = '';
      gerAudio.current = null;
    }
    if (videoRef.current) videoRef.current.pause();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }

  // Hard stop — reset the playheads to the parked seek position.
  function stopAll() {
    haltAudioVideo();
    parkHeads(seekFracRef.current);
    setPlaying('none');
  }

  // Pause, but remember where we are so Play resumes from here (no rewind).
  function pauseAt() {
    haltAudioVideo();
    const f = livePosRef.current;
    seekFracRef.current = f;
    setSeekFrac(f);
    parkHeads(f);
    setPlaying('none');
  }

  function togglePlay() {
    if (playing !== 'none') pauseAt();
    else playSection('both');
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const m = await api.mediaInfo(path);
        if (cancelled) return;
        setInfo(m);
        setDelayMs(m.delay_ms);
        setSavedDelay(m.delay_ms);
        setStretch(1);
        const g = m.audio_tracks.find((t) => t.is_german);
        const e = m.audio_tracks.find((t) => t.language === 'eng' && !t.is_german) ?? m.audio_tracks.find((t) => !t.is_german) ?? m.audio_tracks[0];
        const gi = g ? g.index : null;
        const ei = e ? e.index : null;
        setGerStream(gi);
        setEngStream(ei);
        // Open ~1/3 into the runtime — usually dialogue-heavy, easy to align.
        setCenter(Math.max(60, (m.duration ?? 600) * 0.35));
      } catch (e: any) {
        if (!cancelled) toast.error(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      stopAll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  // Keep the canvases the width of their container.
  useEffect(() => {
    const compute = () => {
      const el = wrapRef.current;
      if (el) setCanvasW(Math.max(320, el.clientWidth));
    };
    compute();
    const el = wrapRef.current;
    const ro = el ? new ResizeObserver(compute) : null;
    if (ro && el) ro.observe(el);
    window.addEventListener('resize', compute);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', compute);
    };
  }, [loading]);

  // Windowed waveform fetch (debounced). Both lanes are fetched as WIDE
  // track-time buffers (3× the visible window: one window of margin on each
  // side) so panning/zooming redraw instantly from memory and only re-fetch
  // once the drag settles. German is fetched delay-shifted into track time.
  useEffect(() => {
    if (dragging) return;
    // Backend caps bins at 4000 — never request more or it 422s (which would
    // silently leave a lane blank). ~3 bins per visible pixel across the buffer.
    const MAX_BINS = 4000;
    const bins = Math.min(MAX_BINS, Math.max(600, Math.round(canvasW * 3)));
    const bufDur = windowSec * 3;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    const fetchAll = async (attempt: number): Promise<void> => {
      try {
        if (engStream != null) {
          const start = Math.max(0, viewStart - windowSec);
          const w = await api.waveform(path, engStream, start, bufDur, bins);
          if (!cancelled) setEngBuf({ peaks: decodePeaks(w.peaks), start, dur: bufDur });
        }
        if (gerStream != null) {
          if (!cancelled) setGerLoading(true);
          const start = Math.max(0, viewStart - windowSec - delayMs / 1000);
          const w = await api.waveform(path, gerStream, start, bufDur, bins);
          if (!cancelled) setGerBuf({ peaks: decodePeaks(w.peaks), start, dur: bufDur });
        }
        if (!cancelled) setGerLoading(false);
      } catch {
        // Likely 503 (transcoder busy) — retry a few times before giving up.
        if (!cancelled && attempt < 4) {
          retryTimer = setTimeout(() => fetchAll(attempt + 1), 600);
        } else if (!cancelled) {
          setGerLoading(false);
        }
      }
    };
    const t = setTimeout(() => fetchAll(0), 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
      if (retryTimer) clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, engStream, gerStream, viewStart, windowSec, canvasW, dragging]);

  // Prefetch the video clip when the view/quality settles (debounced so
  // dragging the timeline doesn't spawn a transcode per pixel).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    setVideoLoading(true);
    const t = setTimeout(() => {
      // Keep the HQ reference picture and English track in one clip. A single
      // browser media clock guarantees that they cannot appear offset simply
      // because two independently fetched elements started at different times.
      v.src = api.videoClipUrl(path, viewStart, clipLen, quality, engStream);
      v.load();
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, viewStart, windowSec, quality, engStream]);

  function drawGrid(ctx: CanvasRenderingContext2D, W: number, H: number) {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    for (let s = Math.ceil(viewStart); s < viewStart + windowSec; s++) {
      ctx.fillRect((s - viewStart) * pxPerSec, 0, 1, H);
    }
  }

  function finishLane(ctx: CanvasRenderingContext2D, W: number, H: number) {
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(0, H / 2, W, 1);
  }

  // Draw one lane from a wide track-time buffer. `delaySec` shifts the buffer
  // (0 for the reference/English lane, delayMs for German). `stretch` previews
  // drift correction: the window's left edge stays anchored (aligned by the
  // delay) and time advances by `stretch` across it, so the user can line up
  // the left edge with the delay then stretch to match the right edge. The
  // backend's fixed EBU R128 LUFS scale is retained so quiet and loud scenes remain
  // visually comparable.
  function drawLane(
    canvas: HTMLCanvasElement | null,
    buf: { peaks: Uint8Array; start: number; dur: number } | null,
    delaySec: number,
    color: string,
    stretch = 1,
  ) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    drawGrid(ctx, W, H);
    if (buf && buf.peaks.length) {
      const bn = buf.peaks.length;
      const vals = new Float32Array(W);
      const anchor = viewStart - delaySec;
      for (let x = 0; x < W; x++) {
        const viewTime = viewStart + (x / W) * windowSec;
        const trackTime = anchor + (viewTime - viewStart) * stretch;
        const bi = Math.floor(((trackTime - buf.start) / buf.dur) * bn);
        const v = bi >= 0 && bi < bn ? buf.peaks[bi] : 0;
        vals[x] = v;
      }
      // Backend values already use a fixed perceived-loudness scale. Do not
      // normalise each viewport: doing so made a quiet scene look as loud as
      // an explosion and was the main source of misleading visual spikes.
      ctx.fillStyle = color;
      for (let x = 0; x < W; x++) {
        const h = Math.min(1, vals[x] / 127) * (H / 2 - 2);
        ctx.fillRect(x, H / 2 - h, 1, h * 2);
      }
    }
    finishLane(ctx, W, H);
  }

  function drawEng() {
    drawLane(engCanvas.current, engBuf, 0, '#38bdf8');
  }

  function drawGer() {
    drawLane(gerCanvas.current, gerBuf, delayMs / 1000, '#f472b6', stretch);
  }

  useEffect(() => {
    drawEng();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engBuf, canvasW, canvasH, windowSec, center]);

  // German redraws on every delay change too — that's the smooth drag.
  useEffect(() => {
    drawGer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gerBuf, delayMs, stretch, canvasW, canvasH, windowSec, center]);

  function onGerDown(e: React.MouseEvent) {
    const startX = e.clientX;
    const origDelay = delayMs;
    dragRef.current = { x: e.clientX, delay: delayMs };
    let moved = false;
    setDragging(true);
    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      if (Math.abs(ev.clientX - startX) > 3) moved = true;
      const pps = canvasW / windowSec;
      const dxSec = (ev.clientX - d.x) / pps;
      setDelayMs(Math.round(d.delay + dxSec * 1000));
    };
    const onUp = (ev: MouseEvent) => {
      dragRef.current = null;
      setDragging(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (!moved) {
        // A click (not a drag) means "seek here", not "re-align".
        setDelayMs(origDelay);
        seekFromClientX(ev.clientX);
      }
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  function zoom(factor: number) {
    setWindowSec((w) => Math.min(600, Math.max(1, Math.round(w * factor))));
  }

  function waitUntilReady(media: HTMLMediaElement, token: number): Promise<void> {
    if (media.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA && !media.seeking) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        media.removeEventListener('canplay', ready);
        media.removeEventListener('seeked', ready);
        media.removeEventListener('loadeddata', ready);
        media.removeEventListener('error', failed);
      };
      const ready = () => {
        if (media.readyState < HTMLMediaElement.HAVE_FUTURE_DATA || media.seeking) return;
        cleanup();
        if (token === playTokenRef.current) resolve();
        else reject(new Error('playback superseded'));
      };
      const failed = () => {
        cleanup();
        reject(new Error('media failed to load'));
      };
      media.addEventListener('canplay', ready, { once: true });
      media.addEventListener('seeked', ready);
      media.addEventListener('loadeddata', ready);
      media.addEventListener('error', failed, { once: true });
    });
  }

  async function playSection(which: 'both' | 'eng' | 'ger') {
    resumeModeRef.current = which;
    stopAll();
    const token = ++playTokenRef.current;
    const startFrac = seekFracRef.current;
    const startOffset = startFrac * windowSec; // seconds into the window
    playbackStartOffsetRef.current = startOffset;
    const startT = viewStart + startOffset; // absolute reference time
    const dur = Math.max(1, Math.min(clipLen - startOffset, windowSec - startOffset));
    let german: HTMLAudioElement | null = null;
    if (which !== 'eng' && gerStream != null) {
      const gs = Math.max(0, startT - delayMs / 1000);
      const a = new Audio(api.audioClipUrl(path, gerStream, gs, dur));
      a.preload = 'auto';
      a.onended = () => stopAll();
      gerAudio.current = a;
      german = a;
    }
    // The picture follows the reference (English) timeline; seek into the clip.
    const v = videoRef.current;
    if (v) {
      // English is embedded in the preview MP4 and always shares the picture's
      // timeline. German-only mode deliberately mutes that reference track.
      v.muted = which === 'ger' || engStream == null;
      if (!v.currentSrc && !v.src) {
        setVideoLoading(true);
        v.src = api.videoClipUrl(path, viewStart, clipLen, quality, engStream);
        v.load();
      }
      if (v.readyState < HTMLMediaElement.HAVE_METADATA) {
        try {
          await new Promise<void>((resolve, reject) => {
            v.addEventListener('loadedmetadata', () => resolve(), { once: true });
            v.addEventListener('error', () => reject(new Error('video failed to load')), { once: true });
          });
        } catch {
          if (token === playTokenRef.current) setPlaying('none');
          return;
        }
      }
      try {
        v.currentTime = startOffset;
      } catch {
        /* not seekable yet */
      }
      try {
        // Both elements may buffer in parallel, but German audio is not
        // allowed to start until the picture can render from the seek point.
        await Promise.all([waitUntilReady(v, token), german ? waitUntilReady(german, token) : Promise.resolve()]);
        if (token !== playTokenRef.current) return;
        await v.play();
        if (german) await german.play();
      } catch {
        if (token === playTokenRef.current) stopAll();
        return;
      }
    }
    if (token !== playTokenRef.current) return;
    setPlaying(which);
    // Animate both lane playheads from the seek position while it plays.
    const tick = () => {
      let posSec = startOffset;
      const vv = videoRef.current;
      if (vv && !vv.paused && vv.currentTime > 0) posSec = vv.currentTime;
      else if (gerAudio.current) posSec = startOffset + gerAudio.current.currentTime;
      const frac = Math.min(1, posSec / windowSec);
      livePosRef.current = frac;
      parkHeads(frac);
      if (frac >= 1) {
        stopAll();
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  function pauseGermanForVideo() {
    gerAudio.current?.pause();
  }

  function resumeGermanWithVideo() {
    const video = videoRef.current;
    const german = gerAudio.current;
    if (!video || !german || playing === 'none' || playing === 'eng') return;
    german.currentTime = Math.max(0, video.currentTime - playbackStartOffsetRef.current);
    void german.play().catch(() => {});
  }

  // Set the seek position from a pointer X (relative to the lane), and support
  // click-drag to scrub precisely.
  function seekFromClientX(clientX: number) {
    const canvas = engCanvas.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    seekFracRef.current = frac;
    setSeekFrac(frac);
  }

  // The English/reference lane scrolls through the movie. Zooming is reserved
  // for the German lane so the two wheel gestures cannot be confused.
  function onWheelPan(e: React.WheelEvent) {
    e.preventDefault();
    if (playing !== 'none') {
      resumeModeRef.current = playing;
      haltAudioVideo();
      setPlaying('none');
    }
    const delta = Math.sign(e.deltaY || e.deltaX) * Math.max(1, windowSec * 0.15);
    const maxCenter = Math.max(windowSec / 2, duration - windowSec / 2);
    setCenter((value) => Math.max(windowSec / 2, Math.min(maxCenter, value + delta)));
  }

  // Mouse wheel over a lane zooms the time window in/out.
  function onWheelZoom(e: React.WheelEvent) {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 0.8 : 1.25);
  }

  // Keep the playheads parked at the seek position whenever we're not playing.
  useEffect(() => {
    if (playing !== 'none') return;
    parkHeads(seekFrac);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekFrac, canvasW, playing, windowSec, loading]);

  // Keyboard shortcuts: Space = play/pause, +/- = zoom, arrows = pan.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (loading) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlay();
      } else if (e.key === '+' || e.key === '=') {
        e.preventDefault();
        zoom(0.8);
      } else if (e.key === '-' || e.key === '_') {
        e.preventDefault();
        zoom(1.25);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setCenter((c) => Math.max(windowSec / 2, c - windowSec * 0.2));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        setCenter((c) => Math.min(duration || c + windowSec, c + windowSec * 0.2));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, playing, windowSec, center, delayMs, duration, engStream, gerStream]);

  async function approve() {
    setBusy('approve');
    try {
      const result = await api.approve(path, {
        delay_ms: delayMs,
        atempo: stretch !== 1 ? stretch : undefined,
        track_index: gerStream,
      });
      toast.success(result.background ? 'Repacking in the background' : 'Approved — ready to send to server');
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  const drift = delayMs - savedDelay;

  const engTrack = info?.audio_tracks.find((t) => t.index === engStream) ?? null;
  const gerTrack = info?.audio_tracks.find((t) => t.index === gerStream) ?? null;
  // Length drift between the reference (HQ) and German audio hints at a
  // frame-rate/speed mismatch (e.g. 25fps PAL vs 23.976) even before aligning.
  const lenDrift = engTrack?.duration != null && gerTrack?.duration != null ? engTrack.duration - gerTrack.duration : null;
  // Prefer the pipeline's MEASURED frame-drift ratio (from visual sync) — the
  // definitive signal; otherwise fall back to the crude track-length ratio.
  const measuredDrift = info?.drift_ratio ?? null;
  const sourceFps = info?.source_fps ?? null;
  const referenceFps = info?.reference_fps ?? info?.video_fps ?? null;
  // Pipeline drift semantics are source-time / reference-time, so PAL versus
  // 23.976 is 25 / 23.976 = 1.0427 (speed the German track up by that factor).
  const fpsStretch = sourceFps && referenceFps ? sourceFps / referenceFps : null;
  const durationStretch = engTrack?.duration && gerTrack?.duration && engTrack.duration > 0 ? gerTrack.duration / engTrack.duration : null;

  async function openReplacement() {
    setReplaceOpen(true);
    setReplaceMode('manual');
    setReplaceCandidates([]);
    setReplaceLoading(true);
    try {
      const result = await api.torrentSearch(titleWithYear(entry), gerTrack?.duration ?? duration ?? null);
      setReplaceCandidates(result.candidates);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplaceLoading(false);
    }
  }

  async function startReplacement(candidate?: TorrentCandidate) {
    setBusy('replace');
    try {
      await api.replaceTorrent({
        path,
        query: titleWithYear(entry),
        target_runtime_seconds: gerTrack?.duration ?? duration ?? null,
        candidate,
      });
      toast.success(candidate ? 'Selected torrent is downloading in the background' : 'Automatic torrent replacement started');
      setReplaceOpen(false);
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }
  const suggestedStretch =
    measuredDrift != null && Math.abs(measuredDrift - 1) > 0.0005
      ? measuredDrift
      : fpsStretch != null && Math.abs(fpsStretch - 1) > 0.0005
        ? fpsStretch
        : durationStretch;
  // Flag a real drift: a measured frame-drift, or lengths differing by >2s.
  const driftSuspected =
    (measuredDrift != null && Math.abs(measuredDrift - 1) > 0.0015) ||
    (fpsStretch != null && Math.abs(fpsStretch - 1) > 0.0015) ||
    (durationStretch != null && lenDrift != null && Math.abs(lenDrift) > 2 && Math.abs(durationStretch - 1) > 0.001);
  const stretchPct = ((stretch - 1) * 100).toFixed(2);
  const trackMeta = (t: AudioTrack | null, videoFps: number | null | undefined) => {
    const bits: string[] = [];
    if (t?.codec) bits.push(t.codec.toUpperCase());
    if (t?.channels) bits.push(`${t.channels}ch`);
    if (t?.sample_rate) bits.push(`${(t.sample_rate / 1000).toFixed(1)} kHz`);
    if (t?.duration != null) bits.push(`len ${fmtClockMs(t.duration)}`);
    if (videoFps) bits.push(`${videoFps.toFixed(3)} fps`);
    return bits;
  };

  return (
    <div className='fixed inset-0 z-50 flex flex-col bg-background'>
      <div className='flex items-center justify-between gap-3 border-b border-border/50 px-4 py-3 md:px-6'>
        <div className='flex min-w-0 items-center gap-2'>
          <Button size='icon' variant='ghost' onClick={onClose} title='Close'>
            <ArrowLeft className='h-5 w-5' />
          </Button>
          <h2 className='truncate text-lg font-semibold'>
            {entry.title}
            {entry.year ? <span className='ml-1 text-muted-foreground'>({entry.year})</span> : null}
          </h2>
          <div className='shrink-0 rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 font-mono text-sm text-foreground'>
            {formatBytes(entry.size ?? info?.size ?? 0)}
          </div>
          <div className='shrink-0 rounded-md border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-foreground'>
            Delay <span className='font-mono font-semibold'>{delayMs > 0 ? '+' : ''}{delayMs} ms</span>
            {drift !== 0 && <span className='ml-1 text-xs text-amber-400'>(unsaved {drift > 0 ? '+' : ''}{drift})</span>}
          </div>
        </div>
        <div className='flex items-center gap-3'>
          {entry.kind === 'movie' && (
            <Button variant='secondary' onClick={openReplacement} disabled={busy != null}>
              <Download data-icon='inline-start' /> New torrent
            </Button>
          )}
          <Button onClick={approve} disabled={busy === 'approve'}>
            {busy === 'approve' ? <Loader2 className='h-4 w-4 animate-spin' /> : <CheckCircle2 className='h-4 w-4' />}
            Approve
          </Button>
        </div>
      </div>

      <Dialog open={replaceOpen} onOpenChange={setReplaceOpen}>
        <DialogContent className='flex max-h-[88vh] w-[96vw] max-w-7xl flex-col overflow-hidden'>
          <DialogHeader>
            <DialogTitle>Repack with a new torrent</DialogTitle>
            <DialogDescription>
              The existing HQ video is replaced. The reviewed German audio is retained and the downloaded torrent files are removed after remuxing.
            </DialogDescription>
          </DialogHeader>
          <Tabs className='flex min-h-0 flex-1 flex-col' value={replaceMode} onValueChange={(value) => setReplaceMode(value as 'auto' | 'manual')}>
            <TabsList>
              <TabsTrigger value='auto'>Automatic</TabsTrigger>
              <TabsTrigger value='manual'>Manual</TabsTrigger>
            </TabsList>
            <TabsContent value='auto'>
              <div className='flex flex-col gap-4 py-3'>
                <p className='text-sm text-foreground'>Bankai will prefer an eligible release close to the German runtime, then fall back to the release with the most seeders.</p>
                <Button onClick={() => startReplacement()} disabled={busy === 'replace'}>
                  {busy === 'replace' ? <Loader2 className='h-4 w-4 animate-spin' /> : <Download data-icon='inline-start' />}
                  Pick automatically
                </Button>
              </div>
            </TabsContent>
            <TabsContent value='manual' className='min-h-0 flex-1 overflow-hidden'>
              {replaceLoading ? (
                <div className='flex items-center justify-center py-10'>
                  <Loader2 className='h-6 w-6 animate-spin' />
                </div>
              ) : replaceCandidates.length === 0 ? (
                <EmptyState icon={Download} title='No matching torrents' description='Try the automatic option later when indexers have more releases.' />
              ) : (
                <TorrentCandidateTable candidates={replaceCandidates} busy={busy === 'replace'} onSelect={startReplacement} />
              )}
            </TabsContent>
          </Tabs>
        </DialogContent>
      </Dialog>

      <div className='flex flex-1 flex-col overflow-hidden'>
        {loading ? (
          <div className='space-y-3 p-3'>
            <Skeleton className='h-40 w-full' />
            <Skeleton className='h-24 w-full' />
          </div>
        ) : (
          <div className='flex min-h-0 flex-1 flex-col gap-2 overflow-hidden px-3 py-2'>
            {/* Video preview grows to fill the space above the waveforms */}
            <div className='relative flex min-h-0 flex-1 items-center justify-center'>
              <div
                className='relative h-full max-h-full max-w-full overflow-hidden rounded-lg border border-border/60 bg-black p-1 shadow-inner shadow-black/40'
                style={{ aspectRatio: info?.width && info?.height ? `${info.width} / ${info.height}` : '16 / 9' }}
              >
                <video
                  ref={videoRef}
                  muted={playing === 'ger' || engStream == null}
                  playsInline
                  preload='auto'
                  onLoadedData={() => setVideoLoading(false)}
                  onCanPlay={() => setVideoLoading(false)}
                  onWaiting={pauseGermanForVideo}
                  onSeeking={pauseGermanForVideo}
                  onPlaying={resumeGermanWithVideo}
                  onSeeked={resumeGermanWithVideo}
                  onError={() => setVideoLoading(false)}
                  className='h-full w-full rounded-md object-contain'
                />
                {videoLoading && (
                  <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                    <Loader2 className='h-8 w-8 animate-spin text-white' />
                  </div>
                )}
              </div>
            </div>

            <div ref={wrapRef} className='relative flex shrink-0 flex-col gap-1'>
              <div className='flex items-center justify-between px-1 text-xs'>
                <span className='flex items-center gap-2 text-sky-400'>
                  <Languages className='h-3.5 w-3.5' /> English (reference · click to seek · scroll to move)
                </span>
                <span className='font-mono text-white'>
                  {fmtClock(viewStart)} – {fmtClock(viewStart + windowSec)}
                </span>
              </div>
              <div className='flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border/60 bg-secondary/30 px-3 py-1.5 font-mono text-xs text-white md:text-sm'>
                {trackMeta(engTrack, info?.video_fps).map((b, i) => (
                  <span key={i}>{b}</span>
                ))}
              </div>
              <div className='relative'>
                <canvas
                  ref={engCanvas}
                  width={canvasW}
                  height={canvasH}
                  onClick={(event) => seekFromClientX(event.clientX)}
                  onWheel={onWheelPan}
                  className='w-full cursor-pointer rounded-md bg-black/40'
                />
                <div
                  ref={engHeadRef}
                  className='pointer-events-none absolute inset-y-0 left-0 z-10 -ml-1.5 w-3'
                  style={{ transform: `translateX(${seekFrac * canvasW}px)` }}
                >
                  <div className='absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-sky-300 shadow-[0_0_6px_rgba(56,189,248,0.9)]' />
                  <div className='absolute -top-1 left-1/2 h-2 w-2 -translate-x-1/2 rounded-sm bg-sky-300' />
                </div>
              </div>
              <div className='flex items-center justify-between px-1 text-xs'>
                <span className='flex items-center gap-2 text-pink-400'>
                  <AudioLines className='h-3.5 w-3.5' /> German (filmpalast · drag to align, click to seek)
                  {gerStream != null && !gerBuf && <Loader2 className='h-3 w-3 animate-spin' />}
                </span>
                {lenDrift != null && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className={cn('font-mono', Math.abs(lenDrift) > 1 ? 'text-amber-400' : 'text-white')}>
                        Δlen {lenDrift > 0 ? '+' : ''}{lenDrift.toFixed(2)}s
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>Length difference between HQ and German audio. A large value suggests frame-rate or speed drift.</TooltipContent>
                  </Tooltip>
                )}
              </div>
              <div className='flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border/60 bg-secondary/30 px-3 py-1.5 font-mono text-xs text-white md:text-sm'>
                {trackMeta(gerTrack, sourceFps).map((b, i) => (
                  <span key={i}>{b}</span>
                ))}
              </div>
              {/* Drift correction (time-stretch) — auto-suggested, manual override */}
              <div className='flex flex-wrap items-center gap-2 rounded-md border border-border/50 bg-secondary/20 px-2.5 py-1.5 text-xs'>
                <span className='text-white'>Drift</span>
                <div className='flex items-center gap-1'>
                  <Button
                    size='icon'
                    variant='outline'
                    className='h-6 w-6'
                    title='Slow German down (−0.05%)'
                    onClick={() => setStretch((s) => Math.max(0.5, +(s - 0.0005).toFixed(6)))}
                  >
                    <Minus className='h-3 w-3' />
                  </Button>
                  <span className='w-[8.5rem] text-center font-mono text-white'>
                    ×{stretch.toFixed(4)} ({stretch >= 1 ? '+' : ''}
                    {stretchPct}%)
                  </span>
                  <Button
                    size='icon'
                    variant='outline'
                    className='h-6 w-6'
                    title='Speed German up (+0.05%)'
                    onClick={() => setStretch((s) => Math.min(2, +(s + 0.0005).toFixed(6)))}
                  >
                    <Plus className='h-3 w-3' />
                  </Button>
                </div>
                {sourceFps && referenceFps && fpsStretch != null && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className='rounded-md bg-background/50 px-2 py-1 font-mono text-foreground'>
                        FPS ×{fpsStretch.toFixed(4)} = {sourceFps.toFixed(3)} / {referenceFps.toFixed(3)}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>German source FPS divided by HQ reference FPS. This remains a preview until you select Use.</TooltipContent>
                  </Tooltip>
                )}
                {suggestedStretch != null && Math.abs(suggestedStretch - 1) > 0.0005 && (
                  <Button
                    size='sm'
                    variant='secondary'
                    className='h-6'
                    onClick={() => setStretch(+suggestedStretch.toFixed(6))}
                    title='Load the suggested factor into the preview; approving applies it to the file'
                  >
                    Use ×{suggestedStretch.toFixed(4)}
                  </Button>
                )}
                {stretch !== 1 && (
                  <Button size='sm' variant='ghost' className='h-6' onClick={() => setStretch(1)}>
                    Reset
                  </Button>
                )}
                {driftSuspected && (
                  <span className='text-warning'>
                    {measuredDrift != null && sourceFps && referenceFps
                      ? `visual drift ×${measuredDrift.toFixed(4)} — German ${sourceFps.toFixed(3)} vs ${referenceFps.toFixed(3)} fps`
                      : fpsStretch != null && sourceFps && referenceFps
                        ? `FPS mismatch — German ${sourceFps.toFixed(3)} vs ${referenceFps.toFixed(3)}`
                        : 'drift likely — dub length differs'}
                  </span>
                )}
              </div>
              <div className='relative'>
                <canvas
                  ref={gerCanvas}
                  width={canvasW}
                  height={canvasH}
                  onMouseDown={onGerDown}
                  onWheel={onWheelZoom}
                  className='w-full cursor-ew-resize rounded-md bg-black/40'
                />
                <div
                  ref={gerHeadRef}
                  className='pointer-events-none absolute inset-y-0 left-0 z-10 w-0.5 bg-pink-300 shadow-[0_0_6px_rgba(244,114,182,0.9)]'
                  style={{ transform: `translateX(${seekFrac * canvasW}px)` }}
                />
                {gerLoading && (
                  <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                    <Loader2 className='h-5 w-5 animate-spin text-pink-400' />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {!loading && (
          <div className='shrink-0 space-y-3 border-t border-border bg-card px-4 py-3 shadow-[0_-8px_24px_-12px_rgba(0,0,0,0.6)]'>
            {/* Timeline / pan */}
            <div className='flex items-center gap-3'>
              <span className='w-12 shrink-0 text-right font-mono text-xs text-white'>{fmtClock(viewStart)}</span>
              <Slider
                value={[Math.min(center, duration || center)]}
                min={0}
                max={duration || 1}
                step={0.5}
                onValueChange={(v) => setCenter(v[0])}
                onPointerDown={() => {
                  wasPlayingRef.current = playing !== 'none';
                  if (playing !== 'none') {
                    resumeModeRef.current = playing;
                    haltAudioVideo();
                    setPlaying('none');
                  }
                }}
                onValueCommit={() => {
                  if (wasPlayingRef.current) {
                    wasPlayingRef.current = false;
                    playSection(resumeModeRef.current);
                  }
                }}
                className='flex-1'
              />
              <span className='w-12 shrink-0 font-mono text-xs text-white'>{fmtClock(duration)}</span>
            </div>

            <div className='flex flex-wrap items-center gap-3'>
              {/* Zoom */}
              <div className='flex items-center gap-1'>
                <Button size='icon' variant='secondary' onClick={() => zoom(2)} title='Zoom out'>
                  <ZoomOut className='h-4 w-4' />
                </Button>
                <span className='w-20 text-center text-xs text-white'>
                  {windowSec >= 60 ? `${(windowSec / 60).toFixed(1)} min` : `${windowSec}s`} view
                </span>
                <Button size='icon' variant='secondary' onClick={() => zoom(0.5)} title='Zoom in'>
                  <ZoomIn className='h-4 w-4' />
                </Button>
              </div>

              {/* Video quality */}
              <div className='flex items-center gap-1'>
                <span className='text-xs text-white'>Quality</span>
                <select
                  value={quality}
                  onChange={(e) => setQuality(parseInt(e.target.value, 10))}
                  className='cursor-pointer rounded-md border border-white/10 bg-black/20 px-2 py-1 text-xs transition-colors hover:border-white/20'
                  aria-label='Video preview quality'
                >
                  <option value={360}>360p</option>
                  <option value={480}>480p</option>
                  <option value={720}>720p</option>
                  <option value={1080}>1080p</option>
                </select>
              </div>

              {/* Waveform lane height */}
              <div className='flex items-center gap-2'>
                <span className='text-xs text-white'>Bars</span>
                <Slider value={[canvasH]} min={100} max={640} step={20} onValueChange={(v) => setCanvasH(v[0])} className='w-32' />
                <span className='w-9 text-right font-mono text-xs text-white'>{canvasH}px</span>
              </div>

              {/* Fine nudge */}
              <div className='flex items-center gap-1'>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 100)}>
                  -100
                </Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 10)}>
                  -10
                </Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 1)}>
                  -1
                </Button>
                <span className='px-1 text-xs text-white'>ms</span>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 1)}>
                  +1
                </Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 10)}>
                  +10
                </Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 100)}>
                  +100
                </Button>
              </div>

              {savedDelay !== delayMs && (
                <Button size='sm' variant='ghost' onClick={() => setDelayMs(savedDelay)} title='Reset to saved'>
                  <RotateCcw className='h-4 w-4' /> Reset
                </Button>
              )}

              {/* Playback */}
              <div className='ml-auto flex items-center gap-2'>
                {playing !== 'none' ? (
                  <Button size='sm' variant='secondary' onClick={pauseAt} title='Pause (Space)'>
                    <Pause className='h-4 w-4' /> Pause {playing === 'eng' ? 'English' : playing === 'ger' ? 'German' : 'both'}
                  </Button>
                ) : (
                  <>
                    <Button size='sm' variant='secondary' onClick={() => playSection('eng')}>
                      <Play className='h-4 w-4' /> English
                    </Button>
                    <Button size='sm' variant='secondary' onClick={() => playSection('ger')}>
                      <Play className='h-4 w-4' /> German
                    </Button>
                    <Button size='sm' onClick={() => playSection('both')} title='Play (Space)'>
                      <Play className='h-4 w-4' /> Play both
                    </Button>
                  </>
                )}
              </div>
            </div>

            {(engStream == null || gerStream == null) && (
              <p className='text-xs text-amber-400'>
                {gerStream == null ? 'No German audio track found. ' : ''}
                {engStream == null ? 'No English/reference track found.' : ''}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
