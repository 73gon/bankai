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
  ChevronRight,
  ChevronsUpDown,
  Filter,
  Check,
  ZoomIn,
  ZoomOut,
  Languages,
  AudioLines,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type MediaInfo, type TitleRow, type AudioTrack } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatBytes } from '@/lib/utils';
import { cn } from '@/lib/utils';

// --- ANSI colour rendering for job logs -----------------------------------
const ANSI_FG: Record<number, string> = {
  30: '#6b7280', 31: '#f87171', 32: '#4ade80', 33: '#fbbf24',
  34: '#60a5fa', 35: '#e879f9', 36: '#22d3ee', 37: '#e5e7eb',
  90: '#9ca3af', 91: '#fca5a5', 92: '#86efac', 93: '#fde047',
  94: '#93c5fd', 95: '#f0abfc', 96: '#67e8f9', 97: '#ffffff',
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
  return out.join('\n').replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
}

// Truncated cell with a hover tooltip showing the full text. The inner element
// is width-capped so truncation actually happens in an auto-layout table, and
// a native `title` provides the full value on hover (never clipped by the
// table's own overflow container, unlike an absolutely-positioned popover).
function TruncCell({
  text,
  mono,
  danger,
  width = '16rem',
}: {
  text: string | null | undefined;
  mono?: boolean;
  danger?: boolean;
  width?: string;
}) {
  if (!text) return <span className='text-muted-foreground/40'>-</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className={cn('truncate cursor-default', mono && 'font-mono', danger && 'text-destructive')}
          style={{ maxWidth: width }}
        >
          {text}
        </div>
      </TooltipTrigger>
      <TooltipContent className={cn(mono && 'font-mono')}>{text}</TooltipContent>
    </Tooltip>
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
  | 'failed'
  | 'cancelled'
  | 'review'
  | 'approved'
  | 'transferring'
  | 'done';

const STATUS_VARIANT: Record<Status, 'muted' | 'accent' | 'destructive' | 'warning' | 'success'> = {
  queued: 'muted',
  downloading: 'accent',
  failed: 'destructive',
  cancelled: 'warning',
  review: 'warning',
  approved: 'success',
  transferring: 'accent',
  done: 'success',
};

const STATUS_LABEL: Record<Status, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  failed: 'Failed',
  cancelled: 'Cancelled',
  review: 'Review',
  approved: 'Approved',
  transferring: 'Transferring',
  done: 'Done',
};

function rowStatus(r: TitleRow): Status {
  if (r.row_kind === 'job') {
    if (r.pending) return 'queued';
    if (r.job_status === 'running') return 'downloading';
    if (r.job_status === 'failed' || r.job_status === 'error') return 'failed';
    if (r.job_status === 'cancelled') return 'cancelled';
    return 'done';
  }
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
        <Badge variant='accent' className='gap-1.5'>
          <Loader2 className='h-3 w-3 animate-spin' />
          {r.step_label || 'Downloading'} {pct > 0 ? `${pct}%` : ''}
        </Badge>
        <div className='mt-1 h-1 overflow-hidden rounded-full bg-secondary'>
          {pct > 0 ? (
            <div
              className='h-full rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500'
              style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
            />
          ) : (
            // No determinate percent yet (e.g. extracting) — show motion so it
            // doesn't look stuck at 0.
            <div className='h-full w-1/3 animate-[indeterminate_1.3s_ease-in-out_infinite] rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500' />
          )}
        </div>
      </div>
    );
  }
  if (s === 'transferring') {
    const pct = Math.round(r.transfer_percent || 0);
    return (
      <Badge variant='accent' className='gap-1.5'>
        <Loader2 className='h-3 w-3 animate-spin' /> Transferring {pct > 0 ? `${pct}%` : ''}
      </Badge>
    );
  }
  return <Badge variant={STATUS_VARIANT[s]}>{STATUS_LABEL[s]}</Badge>;
}

function statusRank(r: TitleRow): number {
  if (r.row_kind === 'job') {
    if (r.job_status === 'running') return 0;
    if (r.pending) return 1;
    if (r.job_status === 'failed' || r.job_status === 'error') return 2;
    return 3;
  }
  if (r.stage === 'review') return 4;
  if (r.stage === 'approved') return 5;
  return 6; // transferred
}

type SortCol = 'title' | 'type' | 'status' | 'when';

function whenLabel(ts: number | null): string {
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
      : all.filter((s) => selected.has(s)).map((s) => STATUS_LABEL[s]).join(', ');
  return (
    <div ref={ref} className='relative'>
      <button
        onClick={() => setOpen((o) => !o)}
        className='flex h-9 min-w-[12rem] max-w-[20rem] items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-sm transition-colors hover:bg-secondary/40'
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
                    selected.has(s)
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-input',
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
            <button
              onClick={onClear}
              className='mt-1 w-full rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-secondary'
            >
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Library() {
  const [rows, setRows] = useState<TitleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [statusFilters, setStatusFilters] = useState<Set<Status>>(new Set());
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [sortCol, setSortCol] = useState<SortCol>('when');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(0);
  const [autoSize, setAutoSize] = useState(10);
  const [pageChoice, setPageChoice] = useState<string>('auto');
  const pageSize = pageChoice === 'auto' ? autoSize : parseInt(pageChoice, 10);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [redoing, setRedoing] = useState<Set<string>>(new Set());

  const [review, setReview] = useState<TitleRow | null>(null);
  const [del, setDel] = useState<TitleRow | null>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      const r = await api.titles();
      setRows(r.rows);
    } catch (e: any) {
      if (!silent) toast.error(e.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }
  useEffect(() => {
    load();
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

  // Size the page to however many rows fit the viewport (no window scroll).
  useEffect(() => {
    const el = tableWrapRef.current;
    if (!el) return;
    const compute = () => {
      const rowH = 60;
      const headH = 42;
      const h = el.clientHeight - headH;
      setAutoSize(Math.max(4, Math.floor(h / rowH)));
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [loading]);

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
      (e) =>
        titleWithYear(e).toLowerCase().includes(filter.toLowerCase()) &&
        (statusFilters.size === 0 || statusFilters.has(rowStatus(e))),
    );
    const dir = sortDir === 'asc' ? 1 : -1;
    list.sort((a, b) => {
      let d = 0;
      if (sortCol === 'title') d = a.title.localeCompare(b.title);
      else if (sortCol === 'type') d = typeLabel(a).localeCompare(typeLabel(b));
      else if (sortCol === 'status') d = statusRank(a) - statusRank(b);
      else d = (a.done_at ?? 0) - (b.done_at ?? 0);
      if (d === 0) d = (a.done_at ?? 0) - (b.done_at ?? 0);
      return d * dir;
    });
    return list;
  }, [rows, filter, statusFilters, sortCol, sortDir]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = useMemo(
    () => filtered.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [filtered, safePage, pageSize],
  );
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

  function SyncCell({ r }: { r: TitleRow }) {
    if (r.row_kind !== 'library') return <span className='text-muted-foreground'>—</span>;
    const c = r.sync_confidence;
    if (c == null) {
      if (r.needs_sync_review)
        return (
          <Badge variant='warning' title='Audio sync needs a manual check — open Review.'>
            Check sync
          </Badge>
        );
      return (
        <Badge variant='muted' title='No automatic audio sync was applied.'>
          Not synced
        </Badge>
      );
    }
    const pct = Math.round(c * 100);
    const acc =
      c >= 0.9
        ? { label: 'Spot on', variant: 'success' as const }
        : c >= 0.75
          ? { label: 'Probably on spot', variant: 'success' as const }
          : c >= 0.5
            ? { label: 'Slightly off', variant: 'warning' as const }
            : { label: 'Completely off', variant: 'destructive' as const };
    return (
      <Badge variant={acc.variant} title={`Automatic sync confidence ${pct}%`}>
        {acc.label}
      </Badge>
    );
  }

  function Poster({ r }: { r: TitleRow }) {
    if (r.poster)
      return (
        <img
          src={r.poster}
          alt=''
          loading='lazy'
          className='h-14 w-10 shrink-0 rounded object-cover'
        />
      );
    return (
      <div className='flex h-14 w-10 shrink-0 items-center justify-center rounded bg-secondary/60'>
        {r.kind === 'episode' ? (
          <LibraryIcon className='h-4 w-4 text-muted-foreground' />
        ) : (
          <Film className='h-4 w-4 text-muted-foreground' />
        )}
      </div>
    );
  }

  function RowView({ r }: { r: TitleRow }) {
    const isLib = r.row_kind === 'library';
    const isJob = r.row_kind === 'job';
    const canCancel = isJob && (r.pending || r.job_status === 'running');
    const canDelete = isJob && (r.job_status === 'failed' || r.job_status === 'error' || r.job_status === 'cancelled');
    const isOpen = expanded === r.id;
    const stop = (e: React.MouseEvent) => e.stopPropagation();
    return (
      <>
        <tr
          className='cursor-pointer border-t border-border hover:bg-secondary/30'
          onClick={() => toggleExpand(r)}
        >
          <td className='px-2 py-2 align-middle'>
            <Poster r={r} />
          </td>
          <td className='max-w-[24rem] px-2 py-2 align-middle'>
            <div className='flex items-center gap-1.5'>
              {isOpen ? (
                <ChevronDown className='h-3.5 w-3.5 shrink-0 text-muted-foreground' />
              ) : (
                <ChevronRight className='h-3.5 w-3.5 shrink-0 text-muted-foreground' />
              )}
              <span className='truncate font-medium'>{titleWithYear(r)}</span>
            </div>
            {isLib && r.size ? (
              <div className='pl-5 text-xs text-muted-foreground'>{formatBytes(r.size)}</div>
            ) : null}
          </td>
          <td className='px-2 py-2 align-middle text-xs text-muted-foreground'>{typeLabel(r)}</td>
          <td className='px-2 py-2 align-middle'>
            <StatusCell r={r} />
          </td>
          <td className='px-2 py-2 align-middle text-xs text-muted-foreground'>
            <TruncCell text={r.reason} danger width='18rem' />
          </td>
          <td className='px-2 py-2 align-middle'>
            <SyncCell r={r} />
          </td>
          <td className='whitespace-nowrap px-2 py-2 align-middle text-xs text-muted-foreground'>
            {whenLabel(r.done_at)}
          </td>
          <td className='px-2 py-2 align-middle text-xs text-muted-foreground'>
            <TruncCell text={r.path} mono width='20rem' />
          </td>
          <td className='px-2 py-2 align-middle' onClick={stop}>
            <div className='flex items-center justify-end gap-1'>
              {isLib && (
                <Button size='sm' variant='secondary' onClick={() => setReview(r)}>
                  <Play className='h-4 w-4' /> Review
                </Button>
              )}
              <Button
                size='icon'
                variant='ghost'
                onClick={() => redo(r)}
                disabled={redoing.has(r.id)}
                title='Redo — re-run the pipeline for this title'
              >
                {redoing.has(r.id) ? (
                  <Loader2 className='h-4 w-4 animate-spin' />
                ) : (
                  <RefreshCw className='h-4 w-4' />
                )}
              </Button>
              {canCancel && (
                <Button size='icon' variant='ghost' onClick={() => cancelJob(r.job_id!)} title='Cancel'>
                  <X className='h-4 w-4' />
                </Button>
              )}
              {canDelete && (
                <Button size='icon' variant='ghost' onClick={() => deleteJob(r.job_id!)} title='Remove — delete this failed job'>
                  <Trash2 className='h-4 w-4 text-red-400' />
                </Button>
              )}
              {isLib && (r.stage === 'approved' || r.transfer_status === 'failed') && (
                <Button size='icon' variant='ghost' onClick={() => transferOne(r.path!)} title='Send to media server'>
                  <UploadCloud className='h-4 w-4' />
                </Button>
              )}
              {isLib && (
                <Button size='icon' variant='ghost' onClick={() => setDel(r)} title='Delete'>
                  <Trash2 className='h-4 w-4 text-red-400' />
                </Button>
              )}
            </div>
          </td>
        </tr>
        {isOpen && (
          <tr className='border-t border-border bg-black/20'>
            <td colSpan={9} className='px-3 py-2'>
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
        <button
          onClick={() => sortBy(col)}
          className='inline-flex items-center gap-1 hover:text-foreground'
        >
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
    <div className='flex h-[calc(100vh-3.5rem)] flex-col gap-4'>
      <header className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>Queue</h1>
          <p className='text-sm text-muted-foreground'>
            Every title in one table — downloads, review, sync and transfer.
          </p>
        </div>
      </header>

      {/* Collapsible filter bar */}
      <div className='rounded-lg border border-border bg-card/40'>
        <button
          onClick={() => setFiltersOpen((o) => !o)}
          className='flex w-full items-center justify-between px-3 py-2 text-sm font-medium'
        >
          <span className='flex items-center gap-2'>
            <Filter className='h-4 w-4 text-muted-foreground' />
            Filters
            {(statusFilters.size > 0 || filter) && (
              <Badge variant='accent'>{statusFilters.size + (filter ? 1 : 0)}</Badge>
            )}
          </span>
          {filtersOpen ? <ChevronUp className='h-4 w-4' /> : <ChevronDown className='h-4 w-4' />}
        </button>
        {filtersOpen && (
          <div className='flex flex-wrap items-center gap-3 border-t border-border px-3 py-3'>
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder='Search title…'
              className='w-56'
            />
            <StatusMultiSelect
              selected={statusFilters}
              onToggle={toggleFilter}
              onClear={() => setStatusFilters(new Set())}
              counts={counts}
            />
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
        <div ref={tableWrapRef} className='min-h-0 flex-1 overflow-auto rounded-lg border'>
          <table className='w-full text-sm'>
            <thead className='sticky top-0 z-10 bg-card text-left text-xs uppercase tracking-wide text-muted-foreground'>
              <tr>
                <th className='w-14 px-2 py-2' />
                <SortHeader col='title' label='Title' className='text-left' />
                <SortHeader col='type' label='Type' className='text-left' />
                <SortHeader col='status' label='Status' className='text-left' />
                <th className='px-2 py-2 text-left font-medium'>Reason</th>
                <th className='px-2 py-2 text-left font-medium'>Sync</th>
                <SortHeader col='when' label='Time' className='text-left' />
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
              {filtered.length === 0
                ? '0'
                : `${safePage * pageSize + 1}–${Math.min(filtered.length, (safePage + 1) * pageSize)}`}{' '}
              of {filtered.length}
            </span>
            <span>·</span>
            <span>Per page</span>
            <Select value={pageChoice} onValueChange={(v) => setPageChoice(v)}>
              <SelectTrigger className='h-7 w-[86px]'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='auto'>Auto</SelectItem>
                <SelectItem value='10'>10</SelectItem>
                <SelectItem value='25'>25</SelectItem>
                <SelectItem value='50'>50</SelectItem>
                <SelectItem value='100'>100</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className='flex items-center gap-2'>
            <Button
              size='sm'
              variant='secondary'
              disabled={safePage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Prev
            </Button>
            <span className='text-xs text-muted-foreground'>
              Page {safePage + 1} / {pageCount}
            </span>
            <Button
              size='sm'
              variant='secondary'
              disabled={safePage >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            >
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

function WaveformReview({ entry, onClose }: { entry: TitleRow; onClose: () => void }) {
  const path = entry.path as string;
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [engStream, setEngStream] = useState<number | null>(null);
  const [gerStream, setGerStream] = useState<number | null>(null);
  const [engPeaks, setEngPeaks] = useState<Uint8Array | null>(null);
  // German is fetched as a WIDE buffer (track-time) so dragging the offset
  // redraws instantly from memory (smooth grab-and-pan, no re-fetch mid-drag).
  const [gerBuf, setGerBuf] = useState<{ peaks: Uint8Array; trackStart: number; dur: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [delayMs, setDelayMs] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  const [windowSec, setWindowSec] = useState(30);
  const [center, setCenter] = useState(60);
  const [playing, setPlaying] = useState<'none' | 'both' | 'eng' | 'ger'>('none');
  const [busy, setBusy] = useState<string | null>(null);
  const [canvasW, setCanvasW] = useState(800);
  const [canvasH, setCanvasH] = useState(160);
  const [dragging, setDragging] = useState(false);
  // Playback quality for the video preview (px height). 360p..1080p.
  const [quality, setQuality] = useState(480);
  // Seek position within the current window, 0..1. Click a lane / drag the
  // playhead to move it; playback starts from here.
  const [seekFrac, setSeekFrac] = useState(0);
  const [, setSeeking] = useState(false);
  const [videoLoading, setVideoLoading] = useState(true);
  const [gerLoading, setGerLoading] = useState(false);

  const wrapRef = useRef<HTMLDivElement>(null);
  const engCanvas = useRef<HTMLCanvasElement>(null);
  const gerCanvas = useRef<HTMLCanvasElement>(null);
  const engAudio = useRef<HTMLAudioElement | null>(null);
  const gerAudio = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const dragRef = useRef<{ x: number; delay: number } | null>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);

  const duration = info?.duration ?? 0;
  const viewStart = Math.max(0, center - windowSec / 2);
  const pxPerSec = canvasW / windowSec;
  // Clip covering the visible window (capped) so we can seek within it.
  const clipLen = Math.min(windowSec, 90);

  function stopAll() {
    for (const r of [engAudio, gerAudio]) {
      if (r.current) {
        r.current.pause();
        r.current.src = '';
        r.current = null;
      }
    }
    if (videoRef.current) videoRef.current.pause();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (playheadRef.current) {
      playheadRef.current.style.opacity = '1';
      playheadRef.current.style.transform = `translateX(${seekFrac * canvasW}px)`;
    }
    setPlaying('none');
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
        const g = m.audio_tracks.find((t) => t.is_german);
        const e =
          m.audio_tracks.find((t) => t.language === 'eng' && !t.is_german) ??
          m.audio_tracks.find((t) => !t.is_german) ??
          m.audio_tracks[0];
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

  // Windowed waveform fetch (debounced) — decode only the visible slice so it
  // stays fast on weak hardware. German is fetched already delay-shifted.
  // English: fetch the visible window. German: fetch a WIDE track-time buffer
  // (3× the view) so dragging the offset redraws from memory (smooth), and
  // prefetch the video clip for this window. Never re-fetch mid-drag.
  useEffect(() => {
    if (dragging) return;
    // Backend caps bins at 4000 — never request more or it 422s (which would
    // silently leave a lane blank). English uses 1x, German a wider buffer.
    const MAX_BINS = 4000;
    const bins = Math.min(MAX_BINS, Math.max(200, Math.round(canvasW)));
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    const fetchAll = async (attempt: number): Promise<void> => {
      try {
        if (engStream != null) {
          const w = await api.waveform(path, engStream, viewStart, windowSec, bins);
          if (!cancelled) setEngPeaks(decodePeaks(w.peaks));
        }
        if (gerStream != null) {
          if (!cancelled) setGerLoading(true);
          const trackStart = Math.max(0, viewStart - delayMs / 1000 - windowSec / 2);
          const bufDur = windowSec * 2;
          const w = await api.waveform(path, gerStream, trackStart, bufDur, Math.min(MAX_BINS, bins * 2));
          if (!cancelled) setGerBuf({ peaks: decodePeaks(w.peaks), trackStart, dur: bufDur });
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
      v.src = api.videoClipUrl(path, viewStart, clipLen, quality);
      v.load();
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, viewStart, windowSec, quality]);

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

  function drawEng() {
    const canvas = engCanvas.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    drawGrid(ctx, W, H);
    if (engPeaks && engPeaks.length) {
      ctx.fillStyle = '#38bdf8';
      const n = engPeaks.length;
      for (let x = 0; x < W; x++) {
        const v = engPeaks[Math.floor((x / W) * n)] || 0;
        const h = (v / 127) * (H / 2 - 2);
        ctx.fillRect(x, H / 2 - h, 1, h * 2);
      }
    }
    finishLane(ctx, W, H);
  }

  function drawGer() {
    const canvas = gerCanvas.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    drawGrid(ctx, W, H);
    if (gerBuf && gerBuf.peaks.length) {
      ctx.fillStyle = '#f472b6';
      const delaySec = delayMs / 1000;
      const bn = gerBuf.peaks.length;
      for (let x = 0; x < W; x++) {
        const viewTime = viewStart + (x / W) * windowSec;
        const trackTime = viewTime - delaySec;
        const bi = Math.floor(((trackTime - gerBuf.trackStart) / gerBuf.dur) * bn);
        const v = bi >= 0 && bi < bn ? gerBuf.peaks[bi] : 0;
        const h = (v / 127) * (H / 2 - 2);
        ctx.fillRect(x, H / 2 - h, 1, h * 2);
      }
    }
    finishLane(ctx, W, H);
  }

  useEffect(() => {
    drawEng();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engPeaks, canvasW, canvasH, windowSec, center]);

  // German redraws on every delay change too — that's the smooth drag.
  useEffect(() => {
    drawGer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gerBuf, delayMs, canvasW, canvasH, windowSec, center]);

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

  function playSection(which: 'both' | 'eng' | 'ger') {
    stopAll();
    const startOffset = seekFrac * windowSec; // seconds into the window
    const startT = viewStart + startOffset; // absolute reference time
    const dur = Math.max(1, Math.min(clipLen - startOffset, windowSec - startOffset));
    if (which !== 'ger' && engStream != null) {
      const a = new Audio(api.audioClipUrl(path, engStream, startT, dur));
      a.onended = () => stopAll();
      engAudio.current = a;
      a.play().catch(() => {});
    }
    if (which !== 'eng' && gerStream != null) {
      const gs = Math.max(0, startT - delayMs / 1000);
      const a = new Audio(api.audioClipUrl(path, gerStream, gs, dur));
      a.onended = () => stopAll();
      gerAudio.current = a;
      a.play().catch(() => {});
    }
    // The picture follows the reference (English) timeline; seek into the clip.
    const v = videoRef.current;
    if (v) {
      try {
        v.currentTime = startOffset;
      } catch {
        /* not seekable yet */
      }
      v.play().catch(() => {});
    }
    setPlaying(which);
    // Animate the playhead from the seek position while it plays.
    const tick = () => {
      const line = playheadRef.current;
      let posSec = startOffset;
      const vv = videoRef.current;
      if (vv && !vv.paused && vv.currentTime > 0) posSec = vv.currentTime;
      else if (engAudio.current) posSec = startOffset + engAudio.current.currentTime;
      else if (gerAudio.current) posSec = startOffset + gerAudio.current.currentTime;
      const frac = Math.min(1, posSec / windowSec);
      if (line) {
        line.style.opacity = '1';
        line.style.transform = `translateX(${frac * canvasW}px)`;
      }
      if (frac >= 1) {
        stopAll();
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  // Set the seek position from a pointer X (relative to the lane), and support
  // click-drag to scrub precisely.
  function seekFromClientX(clientX: number) {
    const canvas = engCanvas.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    setSeekFrac(frac);
  }

  function onSeekDown(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (playing !== 'none') stopAll();
    seekFromClientX(e.clientX);
    setSeeking(true);
    const onMove = (ev: MouseEvent) => seekFromClientX(ev.clientX);
    const onUp = () => {
      setSeeking(false);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  // Keep the playhead parked at the seek position whenever we're not playing.
  useEffect(() => {
    if (playing !== 'none') return;
    const line = playheadRef.current;
    if (line) {
      line.style.opacity = '1';
      line.style.transform = `translateX(${seekFrac * canvasW}px)`;
    }
  }, [seekFrac, canvasW, playing, windowSec, loading]);

  async function approve() {
    setBusy('approve');
    try {
      if (delayMs !== savedDelay) {
        const r = await api.repack(path, delayMs);
        if (!r.ok) throw new Error(r.message);
        setSavedDelay(delayMs);
      }
      await api.approve(path);
      toast.success('Approved — ready to send to server');
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
  const lenDrift =
    engTrack?.duration != null && gerTrack?.duration != null
      ? engTrack.duration - gerTrack.duration
      : null;
  const trackMeta = (t: AudioTrack | null, videoFps: number | null | undefined) => {
    const bits: string[] = [];
    if (t?.codec) bits.push(t.codec.toUpperCase());
    if (t?.channels) bits.push(`${t.channels}ch`);
    if (t?.sample_rate) bits.push(`${(t.sample_rate / 1000).toFixed(1)} kHz`);
    if (t?.duration != null) bits.push(`len ${fmtClock(t.duration)}`);
    if (videoFps) bits.push(`${videoFps} fps`);
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
        </div>
        <div className='flex items-center gap-3'>
          <div className='rounded-md border border-border bg-card px-3 py-1.5 text-sm'>
            Delay <span className='font-mono font-semibold'>{delayMs > 0 ? '+' : ''}{delayMs} ms</span>
            {drift !== 0 && (
              <span className='ml-1 text-xs text-amber-400'>(unsaved {drift > 0 ? '+' : ''}{drift})</span>
            )}
          </div>
          <Button onClick={approve} disabled={busy === 'approve'}>
            {busy === 'approve' ? <Loader2 className='h-4 w-4 animate-spin' /> : <CheckCircle2 className='h-4 w-4' />}
            Approve
          </Button>
        </div>
      </div>

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
              <video
                ref={videoRef}
                muted
                playsInline
                preload='auto'
                onLoadedData={() => setVideoLoading(false)}
                onError={() => setVideoLoading(false)}
                className='max-h-full w-auto rounded-md bg-black object-contain'
              />
              {videoLoading && (
                <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                  <Loader2 className='h-8 w-8 animate-spin text-muted-foreground' />
                </div>
              )}
            </div>

            <div ref={wrapRef} className='relative flex shrink-0 flex-col gap-1'>
              <div className='flex items-center justify-between px-1 text-xs'>
                <span className='flex items-center gap-2 text-sky-400'>
                  <Languages className='h-3.5 w-3.5' /> English (reference · HQ video)
                </span>
                <span className='font-mono text-muted-foreground'>
                  {fmtClock(viewStart)} – {fmtClock(viewStart + windowSec)}
                </span>
              </div>
              <div className='flex flex-wrap items-center gap-x-3 px-1 text-[10px] font-mono text-muted-foreground'>
                {trackMeta(engTrack, info?.video_fps).map((b, i) => (
                  <span key={i}>{b}</span>
                ))}
              </div>
              <canvas
                ref={engCanvas}
                width={canvasW}
                height={canvasH}
                onMouseDown={onSeekDown}
                className='w-full cursor-crosshair rounded-md bg-black/40'
              />
              <div className='flex items-center justify-between px-1 text-xs'>
                <span className='flex items-center gap-2 text-pink-400'>
                  <AudioLines className='h-3.5 w-3.5' /> German (filmpalast · drag to align, click to seek)
                  {gerStream != null && !gerBuf && <Loader2 className='h-3 w-3 animate-spin' />}
                </span>
                {lenDrift != null && (
                  <span
                    className={cn(
                      'font-mono',
                      Math.abs(lenDrift) > 1 ? 'text-amber-400' : 'text-muted-foreground',
                    )}
                    title='Length difference between the HQ and German audio — a large value suggests a frame-rate/speed drift.'
                  >
                    Δlen {lenDrift > 0 ? '+' : ''}
                    {lenDrift.toFixed(2)}s
                  </span>
                )}
              </div>
              <div className='flex flex-wrap items-center gap-x-3 px-1 text-[10px] font-mono text-muted-foreground'>
                {trackMeta(gerTrack, null).map((b, i) => (
                  <span key={i}>{b}</span>
                ))}
              </div>
              <div className='relative'>
                <canvas
                  ref={gerCanvas}
                  width={canvasW}
                  height={canvasH}
                  onMouseDown={onGerDown}
                  className='w-full cursor-ew-resize rounded-md bg-black/40'
                />
                {gerLoading && (
                  <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                    <Loader2 className='h-5 w-5 animate-spin text-pink-400' />
                  </div>
                )}
              </div>
              <div
                ref={playheadRef}
                onMouseDown={onSeekDown}
                className='absolute inset-y-0 left-0 w-2 -ml-1 cursor-ew-resize'
                style={{ opacity: 1, transform: 'translateX(0px)' }}
              >
                <div className='absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-white/90' />
                <div className='absolute -top-0.5 left-1/2 h-2.5 w-2.5 -translate-x-1/2 rounded-sm bg-white shadow' />
              </div>
            </div>
          </div>
        )}

        {!loading && (
          <div className='shrink-0 space-y-3 border-t border-border bg-card px-4 py-3 shadow-[0_-8px_24px_-12px_rgba(0,0,0,0.6)]'>
            {/* Timeline / pan */}
            <div className='flex items-center gap-3'>
              <span className='w-12 shrink-0 text-right font-mono text-xs text-muted-foreground'>
                {fmtClock(viewStart)}
              </span>
              <Slider
                value={[Math.min(center, duration || center)]}
                min={0}
                max={duration || 1}
                step={0.5}
                onValueChange={(v) => setCenter(v[0])}
                className='flex-1'
              />
              <span className='w-12 shrink-0 font-mono text-xs text-muted-foreground'>{fmtClock(duration)}</span>
            </div>

            <div className='flex flex-wrap items-center gap-3'>
              {/* Zoom */}
              <div className='flex items-center gap-1'>
                <Button size='icon' variant='secondary' onClick={() => zoom(2)} title='Zoom out'>
                  <ZoomOut className='h-4 w-4' />
                </Button>
                <span className='w-20 text-center text-xs text-muted-foreground'>
                  {windowSec >= 60 ? `${(windowSec / 60).toFixed(1)} min` : `${windowSec}s`} view
                </span>
                <Button size='icon' variant='secondary' onClick={() => zoom(0.5)} title='Zoom in'>
                  <ZoomIn className='h-4 w-4' />
                </Button>
              </div>

              {/* Video quality */}
              <div className='flex items-center gap-1'>
                <span className='text-xs text-muted-foreground'>Quality</span>
                <select
                  value={quality}
                  onChange={(e) => setQuality(parseInt(e.target.value, 10))}
                  className='rounded-md border border-border bg-card px-2 py-1 text-xs'
                  title='Video preview quality'
                >
                  <option value={360}>360p</option>
                  <option value={480}>480p</option>
                  <option value={720}>720p</option>
                  <option value={1080}>1080p</option>
                </select>
              </div>

              {/* Waveform lane height */}
              <div className='flex items-center gap-2'>
                <span className='text-xs text-muted-foreground'>Bars</span>
                <Slider
                  value={[canvasH]}
                  min={100}
                  max={640}
                  step={20}
                  onValueChange={(v) => setCanvasH(v[0])}
                  className='w-32'
                />
                <span className='w-9 text-right font-mono text-xs text-muted-foreground'>{canvasH}px</span>
              </div>

              {/* Fine nudge */}
              <div className='flex items-center gap-1'>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 100)}>-100</Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 10)}>-10</Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d - 1)}>-1</Button>
                <span className='px-1 text-xs text-muted-foreground'>ms</span>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 1)}>+1</Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 10)}>+10</Button>
                <Button size='sm' variant='secondary' onClick={() => setDelayMs((d) => d + 100)}>+100</Button>
              </div>

              {savedDelay !== delayMs && (
                <Button size='sm' variant='ghost' onClick={() => setDelayMs(savedDelay)} title='Reset to saved'>
                  <RotateCcw className='h-4 w-4' /> Reset
                </Button>
              )}

              {/* Playback */}
              <div className='ml-auto flex items-center gap-2'>
                {playing !== 'none' ? (
                  <Button size='sm' variant='secondary' onClick={stopAll}>
                    <Pause className='h-4 w-4' /> Stop
                  </Button>
                ) : (
                  <>
                    <Button size='sm' variant='secondary' onClick={() => playSection('eng')}>
                      <Play className='h-4 w-4' /> English
                    </Button>
                    <Button size='sm' variant='secondary' onClick={() => playSection('ger')}>
                      <Play className='h-4 w-4' /> German
                    </Button>
                    <Button size='sm' onClick={() => playSection('both')}>
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
