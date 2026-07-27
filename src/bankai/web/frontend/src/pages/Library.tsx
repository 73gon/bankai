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
  ScrollText,
  Download,
  CirclePause,
  CirclePlay,
  ExternalLink,
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

async function writeClipboard(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // LAN-hosted HTTP pages may not receive the secure Clipboard API.
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand('copy');
  textarea.remove();
  if (!copied) throw new Error('Clipboard access was denied');
}

function SourceCell({ r }: { r: TitleRow }) {
  const [copiedSource, setCopiedSource] = useState<string | null>(null);
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
    },
    [],
  );
  const sources = [
    {
      label: 'DE',
      name: 'German stream',
      url: r.german_source_url,
      title: null,
    },
    {
      label: 'HQ',
      name: 'HQ torrent',
      url: r.torrent_source_url,
      title: r.torrent_source_title,
    },
  ].filter((source) => source.url);

  if (sources.length === 0) return <span className='text-foreground'>—</span>;
  return (
    <div className='flex items-center gap-1'>
      {sources.map((source) => (
        <Tooltip key={source.label}>
          <TooltipTrigger asChild>
            <Button
              size='sm'
              variant={copiedSource === source.label ? 'default' : 'secondary'}
              className='h-7 w-10 px-2 font-mono text-xs'
              aria-label={
                copiedSource === source.label
                  ? `${source.name} source copied`
                  : `Copy ${source.name} source`
              }
              aria-live='polite'
              onClick={async (event) => {
                event.stopPropagation();
                try {
                  await writeClipboard(source.url!);
                  setCopiedSource(source.label);
                  if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
                  copiedTimerRef.current = setTimeout(() => {
                    setCopiedSource((current) =>
                      current === source.label ? null : current,
                    );
                  }, 1600);
                  toast.success(`${source.name} source copied`);
                } catch (error: any) {
                  toast.error(error.message || 'Could not copy source');
                }
              }}
            >
              <span className='relative grid place-items-center'>
                <span
                  className={cn(
                    'transition-all duration-200',
                    copiedSource === source.label
                      ? 'scale-50 opacity-0'
                      : 'scale-100 opacity-100',
                  )}
                >
                  {source.label}
                </span>
                <Check
                  aria-hidden='true'
                  className={cn(
                    'absolute transition-all duration-200',
                    copiedSource === source.label
                      ? 'scale-100 opacity-100'
                      : 'scale-50 opacity-0',
                  )}
                />
              </span>
            </Button>
          </TooltipTrigger>
          <TooltipContent className='max-w-md'>
            <div className='flex flex-col gap-1'>
              <span className='font-medium'>
                {copiedSource === source.label
                  ? `${source.name} source copied`
                  : `Copy ${source.name} source`}
              </span>
              {source.title && <span>{source.title}</span>}
              <span className='break-all font-mono text-xs'>{source.url}</span>
            </div>
          </TooltipContent>
        </Tooltip>
      ))}
    </div>
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
            <th className='w-32 px-3 py-2 text-right'>Actions</th>
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
                <div className='flex items-center justify-end gap-1'>
                  {candidate.info_url && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button size='icon' variant='ghost' asChild>
                          <a href={candidate.info_url} target='_blank' rel='noreferrer' aria-label={`Open ${candidate.title} details`}>
                            <ExternalLink data-icon='inline-start' />
                          </a>
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Open the indexer description</TooltipContent>
                    </Tooltip>
                  )}
                  <Button size='sm' disabled={busy} onClick={() => onSelect(candidate)}>Select</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface TorrentFilters {
  minSeeders: string;
  maxSeeders: string;
  minSizeGib: string;
  maxSizeGib: string;
  magnet: string;
}

const EMPTY_TORRENT_FILTERS: TorrentFilters = {
  minSeeders: '',
  maxSeeders: '',
  minSizeGib: '',
  maxSizeGib: '',
  magnet: '',
};

function nullableNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function TorrentPickerTools({
  filters,
  onChange,
  onSearch,
  onMagnet,
  busy,
}: {
  filters: TorrentFilters;
  onChange: (next: TorrentFilters) => void;
  onSearch: () => void | Promise<void>;
  onMagnet: (magnet: string) => void | Promise<void>;
  busy: boolean;
}) {
  const field = (key: keyof Omit<TorrentFilters, 'magnet'>, label: string, placeholder: string) => (
    <label className='flex min-w-0 flex-col gap-1 text-xs text-foreground'>
      {label}
      <Input
        type='number'
        min='0'
        step={key.includes('Size') ? '0.1' : '1'}
        value={filters[key]}
        onChange={(event) => onChange({ ...filters, [key]: event.target.value })}
        placeholder={placeholder}
        className='h-8'
      />
    </label>
  );
  return (
    <div className='flex flex-col gap-3 rounded-lg border border-border bg-secondary/20 p-3'>
      <div className='grid gap-2 sm:grid-cols-2 lg:grid-cols-[repeat(4,minmax(7rem,1fr))_auto] lg:items-end'>
        {field('minSeeders', 'Min seeders', 'Default')}
        {field('maxSeeders', 'Max seeders', 'Unlimited')}
        {field('minSizeGib', 'Min size (GB)', 'Default')}
        {field('maxSizeGib', 'Max size (GB)', 'Default')}
        <Button variant='secondary' onClick={onSearch} disabled={busy}>
          {busy ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <RefreshCw data-icon='inline-start' />}
          Apply
        </Button>
      </div>
      <div className='flex flex-col gap-2 sm:flex-row'>
        <Input
          value={filters.magnet}
          onChange={(event) => onChange({ ...filters, magnet: event.target.value })}
          placeholder='Paste a magnet link'
          className='min-w-0 flex-1 font-mono text-xs'
        />
        <Button
          onClick={() => onMagnet(filters.magnet)}
          disabled={busy || !filters.magnet.trim()}
        >
          <Download data-icon='inline-start' /> Use magnet
        </Button>
      </div>
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
  if (r.action_required) return 'waiting_action';
  if (r.pending) return 'queued';
  if (r.job_status === 'running') return 'downloading';
  if (r.job_status === 'failed' || r.job_status === 'error') return 'failed';
  if (r.job_status === 'stopped') return 'stopped';
  if (r.job_status === 'cancelled') return 'cancelled';
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
  if (s === 'queued') {
    return (
      <Badge variant={STATUS_VARIANT[s]}>
        Queued{r.queue_position != null ? ` #${r.queue_position}` : ''}
      </Badge>
    );
  }
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
  if (r.action_required || r.job_status === 'running') return 0;
  if (r.pending) return 1;
  if (r.job_status === 'failed' || r.job_status === 'error') return 2;
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

function torrentContext(r: TitleRow) {
  const match = /S(\d{1,2})E(\d{1,3})/i.exec(r.name || r.title);
  const season = r.season ?? (match ? Number(match[1]) : null);
  const episode = match ? Number(match[2]) : null;
  const seriesTitle = r.series || r.title.replace(/\s*[-–—]?\s*S\d{1,2}E\d{1,3}.*$/i, '').trim();
  if (r.kind === 'episode') {
    return {
      query: `${seriesTitle} S${String(season ?? 1).padStart(2, '0')}E${String(episode ?? 1).padStart(2, '0')}`,
      kind: 'episode' as const,
      seriesTitle,
      season,
      episode,
    };
  }
  return {
    query: titleWithYear(r),
    kind: 'movie' as const,
    seriesTitle: null,
    season: null,
    episode: null,
  };
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
  const [queueBusy, setQueueBusy] = useState<Set<string>>(new Set());

  const [review, setReview] = useState<TitleRow | null>(null);
  const [del, setDel] = useState<TitleRow | null>(null);
  const [torrentAction, setTorrentAction] = useState<TitleRow | null>(null);
  const [torrentCandidates, setTorrentCandidates] = useState<TorrentCandidate[]>([]);
  const [torrentLoading, setTorrentLoading] = useState(false);
  const [torrentFilters, setTorrentFilters] = useState<TorrentFilters>(EMPTY_TORRENT_FILTERS);
  const [torrentActionRuntime, setTorrentActionRuntime] = useState<number | null>(null);
  const [torrentReplacement, setTorrentReplacement] = useState<TitleRow | null>(null);
  const [replacementMode, setReplacementMode] = useState<'auto' | 'manual'>('manual');
  const [replacementCandidates, setReplacementCandidates] = useState<TorrentCandidate[]>([]);
  const [replacementRuntime, setReplacementRuntime] = useState<number | null>(null);
  const [replacementLoading, setReplacementLoading] = useState(false);
  const [replacementBusy, setReplacementBusy] = useState(false);
  const [replacementFilters, setReplacementFilters] = useState<TorrentFilters>(EMPTY_TORRENT_FILTERS);
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

  async function load(silent = false, preserveScroll = false) {
    if (!silent) setLoading(true);
    try {
      const r = await api.titles();
      queueRowsCache = r.rows;
      if (preserveScroll) restoreScrollRef.current = tableScrollRef.current?.scrollTop ?? null;
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
    if (r.kind === 'episode') return 'Show';
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
  }, [expanded, rows]);

  async function transferOne(path: string) {
    try {
      await api.transfer(path);
      toast.success('Sending to server');
      await load(true, true);
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
    const savedScroll = tableScrollRef.current?.scrollTop ?? null;
    try {
      const result = await api.deleteJob(id);
      if (!result.deleted) throw new Error('The job could not be removed');
      setRows((current) => {
        const next = current.filter((row) => row.job_id !== id && row.id !== id);
        queueRowsCache = next;
        return next;
      });
      restoreScrollRef.current = savedScroll;
      toast.success('Removed');
      await load(true, true);
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
    setTorrentFilters(EMPTY_TORRENT_FILTERS);
    setTorrentLoading(true);
    try {
      const result = await api.torrentAction(r.job_id);
      setTorrentCandidates(result.candidates);
      setTorrentActionRuntime(result.target_runtime_seconds);
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

  async function forceJob(id: string) {
    setQueueBusy((current) => new Set(current).add(id));
    try {
      await api.forceJob(id);
      toast.success('Queued job started immediately');
      await load(true, true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setQueueBusy((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  async function moveQueuedJob(r: TitleRow, delta: number) {
    if (!r.job_id || r.queue_position == null) return;
    setQueueBusy((current) => new Set(current).add(r.id));
    try {
      await api.setJobPriority(r.job_id, r.queue_position + delta);
      await load(true, true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setQueueBusy((current) => {
        const next = new Set(current);
        next.delete(r.id);
        return next;
      });
    }
  }

  async function chooseTorrent(candidate: TorrentCandidate) {
    if (!torrentAction?.job_id) return;
    setTorrentLoading(true);
    try {
      await api.chooseTorrent(torrentAction.job_id, candidate);
      toast.success('Torrent selected — pipeline resumed');
      setTorrentAction(null);
      load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTorrentLoading(false);
    }
  }

  async function searchTorrentAction() {
    if (!torrentAction) return;
    setTorrentLoading(true);
    try {
      const context = torrentContext(torrentAction);
      const result = await api.torrentSearch(context.query, torrentActionRuntime, {
        kind: context.kind,
        seriesTitle: context.seriesTitle,
        season: context.season,
        episode: context.episode,
        minSeeders: nullableNumber(torrentFilters.minSeeders),
        maxSeeders: nullableNumber(torrentFilters.maxSeeders),
        minSizeGib: nullableNumber(torrentFilters.minSizeGib),
        maxSizeGib: nullableNumber(torrentFilters.maxSizeGib),
      });
      setTorrentCandidates(result.candidates);
      setTorrentFilters((current) => ({
        ...current,
        minSeeders: current.minSeeders || String(result.policy.min_seeders),
        minSizeGib: current.minSizeGib || String(result.policy.min_size_gib),
        maxSizeGib: current.maxSizeGib || String(result.policy.max_size_gib),
      }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setTorrentLoading(false);
    }
  }

  async function chooseTorrentMagnet(magnet: string) {
    if (!torrentAction?.job_id) return;
    setTorrentLoading(true);
    try {
      await api.chooseTorrentMagnet(torrentAction.job_id, magnet, torrentAction.title);
      toast.success('Magnet added — pipeline resumed');
      setTorrentAction(null);
      await load(true, true);
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
    setReplacementFilters(EMPTY_TORRENT_FILTERS);
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
      const context = torrentContext(r);
      const result = await api.torrentSearch(context.query, runtime, {
        kind: context.kind,
        seriesTitle: context.seriesTitle,
        season: context.season,
        episode: context.episode,
      });
      setReplacementCandidates(result.candidates);
      setReplacementFilters((current) => ({
        ...current,
        minSeeders: String(result.policy.min_seeders),
        minSizeGib: String(result.policy.min_size_gib),
        maxSizeGib: String(result.policy.max_size_gib),
      }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplacementLoading(false);
    }
  }

  async function searchReplacementTorrents() {
    if (!torrentReplacement) return;
    setReplacementLoading(true);
    try {
      const context = torrentContext(torrentReplacement);
      const result = await api.torrentSearch(context.query, replacementRuntime, {
        kind: context.kind,
        seriesTitle: context.seriesTitle,
        season: context.season,
        episode: context.episode,
        minSeeders: nullableNumber(replacementFilters.minSeeders),
        maxSeeders: nullableNumber(replacementFilters.maxSeeders),
        minSizeGib: nullableNumber(replacementFilters.minSizeGib),
        maxSizeGib: nullableNumber(replacementFilters.maxSizeGib),
      });
      setReplacementCandidates(result.candidates);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplacementLoading(false);
    }
  }

  async function startTorrentReplacement(candidate?: TorrentCandidate, magnet?: string) {
    if (!torrentReplacement?.path) return;
    setReplacementBusy(true);
    try {
      const context = torrentContext(torrentReplacement);
      await api.replaceTorrent({
        path: torrentReplacement.path,
        query: context.query,
        target_runtime_seconds: replacementRuntime,
        candidate,
        magnet_uri: magnet || undefined,
        kind: context.kind,
        series_title: context.seriesTitle,
        season: context.season,
        episode: context.episode,
      });
      toast.success(candidate || magnet ? 'Selected torrent is downloading in the background' : 'Automatic torrent replacement started');
      setTorrentReplacement(null);
      await load(true, true);
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
    const canCancel = !!r.job_id && r.pending;
    const canForce = !!r.job_id && r.pending;
    const canStop = !!r.job_id && r.job_status === 'running';
    const canContinue = !!r.job_id && r.job_status === 'stopped';
    const canDelete = isJob && ['failed', 'error', 'cancelled', 'stopped'].includes(r.job_status || '');
    const rerunActive =
      r.pending || ['running', 'stopped'].includes(r.job_status || '');
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
          <td className='px-2 py-2 align-middle'>
            <SourceCell r={r} />
          </td>
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
              {canForce && (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        size='icon'
                        variant='ghost'
                        disabled={queueBusy.has(r.id) || (r.queue_position ?? 1) <= 1}
                        onClick={() => moveQueuedJob(r, -1)}
                        aria-label='Move queued job earlier'
                      >
                        <ChevronUp data-icon='inline-start' />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Move earlier in the queue</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        size='icon'
                        variant='ghost'
                        disabled={queueBusy.has(r.id) || (r.queue_position ?? 0) >= (r.queue_total ?? 0)}
                        onClick={() => moveQueuedJob(r, 1)}
                        aria-label='Move queued job later'
                      >
                        <ChevronDown data-icon='inline-start' />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Move later in the queue</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        size='icon'
                        variant='ghost'
                        disabled={queueBusy.has(r.id)}
                        onClick={() => forceJob(r.job_id!)}
                        aria-label='Force queued job to start'
                      >
                        {queueBusy.has(r.id) ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <CirclePlay data-icon='inline-start' />}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Start now, bypassing the concurrent-job limit</TooltipContent>
                  </Tooltip>
                </>
              )}
              {isLib && (
                <Button size='sm' variant='default' onClick={() => setReview(r)} disabled={isRepacking}>
                  <Play data-icon='inline-start' /> Review
                </Button>
              )}
              {isLib && (
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
                disabled={redoing.has(r.id) || isRepacking || rerunActive}
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
            <td colSpan={11} className='px-3 py-2'>
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
      await load(true, true);
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
                <th className='px-2 py-2 text-left font-medium'>Sources</th>
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
          <TorrentPickerTools
            filters={torrentFilters}
            onChange={setTorrentFilters}
            onSearch={searchTorrentAction}
            onMagnet={chooseTorrentMagnet}
            busy={torrentLoading}
          />
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
              <div className='flex min-h-0 flex-1 flex-col gap-3 py-3'>
                <TorrentPickerTools
                  filters={replacementFilters}
                  onChange={setReplacementFilters}
                  onSearch={searchReplacementTorrents}
                  onMagnet={(magnet) => startTorrentReplacement(undefined, magnet)}
                  busy={replacementLoading || replacementBusy}
                />
                {replacementLoading ? (
                  <div className='flex items-center justify-center py-10'>
                    <Loader2 className='h-6 w-6 animate-spin' />
                  </div>
                ) : replacementCandidates.length === 0 ? (
                  <EmptyState icon={Download} title='No matching torrents' description='Change the filters or paste a magnet link.' />
                ) : (
                  <TorrentCandidateTable candidates={replacementCandidates} busy={replacementBusy} onSelect={startTorrentReplacement} />
                )}
              </div>
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

type WaveBuffer = {
  peaks: Uint8Array;
  start: number;
  dur: number;
};

type TimelineRange = {
  start: number;
  end: number;
};

const OVERVIEW_WAVEFORM_HEIGHT = 54;

function mergeTimelineRanges(ranges: TimelineRange[]): TimelineRange[] {
  const sorted = ranges
    .filter(
      (range) =>
        Number.isFinite(range.start)
        && Number.isFinite(range.end)
        && range.end > range.start,
    )
    .sort((a, b) => a.start - b.start);
  const merged: TimelineRange[] = [];
  for (const range of sorted) {
    const last = merged[merged.length - 1];
    if (last && range.start <= last.end + 0.15) {
      last.end = Math.max(last.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
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
  const [engBuf, setEngBuf] = useState<WaveBuffer | null>(null);
  const [gerBuf, setGerBuf] = useState<WaveBuffer | null>(null);
  const [engOverviewBuf, setEngOverviewBuf] = useState<WaveBuffer | null>(null);
  const [gerOverviewBuf, setGerOverviewBuf] = useState<WaveBuffer | null>(null);
  const [engOverviewLoading, setEngOverviewLoading] = useState(false);
  const [gerOverviewLoading, setGerOverviewLoading] = useState(false);
  const [cachedVideoRanges, setCachedVideoRanges] = useState<TimelineRange[]>([]);
  const [bufferedVideoRanges, setBufferedVideoRanges] = useState<TimelineRange[]>([]);
  const [loading, setLoading] = useState(true);
  const [delayMs, setDelayMs] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  // Drift correction: time-stretch factor for the German track (atempo). 1 =
  // no stretch. >1 speeds it up (shorter), <1 slows it down (longer). Applied
  // on top of the constant delay so a dub that slowly slides out of sync
  // (e.g. PAL 25fps vs 23.976) can be corrected.
  const [stretch, setStretch] = useState(1);
  const [stretchInput, setStretchInput] = useState('1.000000');
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
  const [replaceFilters, setReplaceFilters] = useState<TorrentFilters>(EMPTY_TORRENT_FILTERS);

  const wrapRef = useRef<HTMLDivElement>(null);
  const engOverviewCanvas = useRef<HTMLCanvasElement>(null);
  const engCanvas = useRef<HTMLCanvasElement>(null);
  const gerCanvas = useRef<HTMLCanvasElement>(null);
  const gerOverviewCanvas = useRef<HTMLCanvasElement>(null);
  const gerAudio = useRef<HTMLAudioElement | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const dragRef = useRef<{ x: number; delay: number } | null>(null);
  const overviewDragRef = useRef<{
    lane: 'eng' | 'ger';
    pointerId: number;
    grabOffset: number;
  } | null>(null);
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
  const engTrack = info?.audio_tracks.find((t) => t.index === engStream) ?? null;
  const gerTrack = info?.audio_tracks.find((t) => t.index === gerStream) ?? null;
  const engOverviewDuration = engTrack?.duration ?? duration;
  const gerOverviewDuration = gerTrack?.duration ?? duration;
  const viewStart = Math.max(0, center - windowSec / 2);
  const pxPerSec = canvasW / windowSec;
  // Reusable preview segments make nearby seeks hit the same cached file.
  // Each segment also contains the next segment, so normal forward panning is
  // already available while the user is inspecting the current section.
  const videoSegmentSec = Math.min(60, Math.max(10, windowSec));
  const videoClipStart = Math.floor(viewStart / videoSegmentSec) * videoSegmentSec;
  const videoClipLen = Math.max(
    1,
    Math.min(120, duration > 0 ? duration - videoClipStart : videoSegmentSec * 2, videoSegmentSec * 2),
  );

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
        setStretchInput('1.000000');
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
  // track-time buffers so panning/zooming redraw instantly from memory and
  // only re-fetch once the drag settles. German uses the same global affine
  // mapping as playback/repack: source_time = (reference_time - delay) * rate.
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
        const tasks: Promise<void>[] = [];
        if (engStream != null) {
          const start = Math.max(0, viewStart - windowSec);
          tasks.push(
            api.waveform(path, engStream, start, bufDur, bins).then((w) => {
              if (!cancelled) setEngBuf({ peaks: decodePeaks(w.peaks), start, dur: bufDur });
            }),
          );
        }
        if (gerStream != null) {
          if (!cancelled) setGerLoading(true);
          // Keep the mapped source window within the backend's 1800s cap. At
          // ordinary drift factors this retains a full visible-window margin
          // on both sides; unusually large factors still retain as much cache
          // margin as the endpoint permits.
          const visibleSourceDur = windowSec * stretch;
          const margin = Math.min(
            windowSec,
            Math.max(0, (1800 - visibleSourceDur) / (2 * stretch)),
          );
          const mappedStart = (viewStart - margin - delayMs / 1000) * stretch;
          const mappedEnd = (viewStart + windowSec + margin - delayMs / 1000) * stretch;
          const start = Math.max(0, mappedStart);
          const dur = Math.max(0.05, Math.min(1800, mappedEnd - start));
          tasks.push(
            api.waveform(path, gerStream, start, dur, bins).then((w) => {
              if (!cancelled) setGerBuf({ peaks: decodePeaks(w.peaks), start, dur });
            }),
          );
        }
        await Promise.all(tasks);
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
  }, [path, engStream, gerStream, viewStart, windowSec, canvasW, dragging, delayMs, stretch]);

  // Fetch compact full-track overviews in backend-safe 30-minute chunks.
  // Chunks are requested sequentially so this background work consumes only
  // one transcoder slot and cannot crowd out the detailed lanes or preview.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function fetchChunk(
      stream: number,
      start: number,
      dur: number,
      bins: number,
    ) {
      let lastError: unknown;
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          return await api.waveform(path, stream, start, dur, bins);
        } catch (error) {
          lastError = error;
          if (cancelled) throw error;
          await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
        }
      }
      throw lastError;
    }

    async function fetchOverview(
      stream: number,
      totalDuration: number,
    ): Promise<WaveBuffer> {
      const targetBins = 2400;
      const pieces: Uint8Array[] = [];
      let totalBins = 0;
      for (let start = 0; start < totalDuration && !cancelled; start += 1800) {
        const chunkDur = Math.min(1800, totalDuration - start);
        const chunkBins = Math.max(
          50,
          Math.round(targetBins * (chunkDur / totalDuration)),
        );
        const response = await fetchChunk(stream, start, chunkDur, chunkBins);
        const peaks = decodePeaks(response.peaks);
        pieces.push(peaks);
        totalBins += peaks.length;
      }
      const peaks = new Uint8Array(totalBins);
      let offset = 0;
      for (const piece of pieces) {
        peaks.set(piece, offset);
        offset += piece.length;
      }
      return { peaks, start: 0, dur: totalDuration };
    }

    setEngOverviewBuf(null);
    setGerOverviewBuf(null);
    setEngOverviewLoading(engStream != null && engOverviewDuration > 0);
    setGerOverviewLoading(gerStream != null && gerOverviewDuration > 0);
    timer = setTimeout(async () => {
      if (engStream != null && engOverviewDuration > 0) {
        try {
          const overview = await fetchOverview(engStream, engOverviewDuration);
          if (!cancelled) setEngOverviewBuf(overview);
        } catch {
          // The detailed lanes remain usable if a full overview cannot decode.
        } finally {
          if (!cancelled) setEngOverviewLoading(false);
        }
      }
      if (gerStream != null && gerOverviewDuration > 0 && !cancelled) {
        try {
          const overview = await fetchOverview(gerStream, gerOverviewDuration);
          if (!cancelled) setGerOverviewBuf(overview);
        } catch {
          // The detailed lanes remain usable if a full overview cannot decode.
        } finally {
          if (!cancelled) setGerOverviewLoading(false);
        }
      }
    }, 500);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [path, engStream, gerStream, engOverviewDuration, gerOverviewDuration]);

  function markVideoClipCached(start: number, len: number) {
    if (!Number.isFinite(start) || !Number.isFinite(len) || len <= 0) return;
    setCachedVideoRanges((current) =>
      mergeTimelineRanges([...current, { start, end: start + len }]),
    );
  }

  function captureVideoBuffered(media: HTMLVideoElement) {
    const clipStart = Number(media.dataset.clipStart);
    if (!Number.isFinite(clipStart)) return;
    const ranges: TimelineRange[] = [];
    for (let index = 0; index < media.buffered.length; index++) {
      ranges.push({
        start: clipStart + media.buffered.start(index),
        end: clipStart + media.buffered.end(index),
      });
    }
    if (ranges.length > 0) {
      setBufferedVideoRanges((current) =>
        mergeTimelineRanges([...current, ...ranges]),
      );
    }
  }

  // Cached previews depend on the segment grid, quality, and embedded English
  // track. Restore matching disk-cache ranges when review opens or the cache
  // key changes; new prefetches are merged into this map in real time.
  useEffect(() => {
    let cancelled = false;
    setCachedVideoRanges([]);
    setBufferedVideoRanges([]);
    if (duration > 0) {
      void api
        .videoClipCache(path, videoSegmentSec, quality, engStream)
        .then((result) => {
          if (!cancelled) {
            setCachedVideoRanges((current) =>
              mergeTimelineRanges([...current, ...result.ranges]),
            );
          }
        })
        .catch(() => {
          // Cache visualization is optional; preview loading still works.
        });
    }
    return () => {
      cancelled = true;
    };
  }, [path, quality, engStream, videoSegmentSec, duration]);

  // Load a cache-aligned video segment with only a tiny debounce.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    setVideoLoading(true);
    const t = setTimeout(() => {
      // Keep the HQ reference picture and English track in one clip. A single
      // browser media clock guarantees that they cannot appear offset simply
      // because two independently fetched elements started at different times.
      v.src = api.videoClipUrl(path, videoClipStart, videoClipLen, quality, engStream);
      v.dataset.clipStart = String(videoClipStart);
      v.dataset.clipLen = String(videoClipLen);
      v.load();
    }, 75);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, videoClipStart, videoClipLen, quality, engStream]);

  // Warm the previous/next segment in the server cache after the current one
  // settles. Fetches are sequential to avoid starving waveform/audio work.
  useEffect(() => {
    const controller = new AbortController();
    const starts = [
      Math.max(0, videoClipStart - videoSegmentSec),
      videoClipStart + videoSegmentSec,
    ].filter((start) => start !== videoClipStart && (duration <= 0 || start < duration));
    const timer = setTimeout(async () => {
      for (const start of starts) {
        if (controller.signal.aborted) break;
        try {
          const span = Math.max(
            1,
            Math.min(120, duration > 0 ? duration - start : videoSegmentSec * 2, videoSegmentSec * 2),
          );
          const response = await fetch(api.videoClipUrl(path, start, span, quality, engStream), {
            signal: controller.signal,
            cache: 'force-cache',
          });
          if (response.ok && !controller.signal.aborted) {
            markVideoClipCached(start, span);
          }
          await response.body?.cancel();
        } catch {
          if (controller.signal.aborted) break;
        }
      }
    }, 750);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, videoClipStart, videoSegmentSec, duration, quality, engStream]);

  function fillReferenceRanges(
    ctx: CanvasRenderingContext2D,
    W: number,
    H: number,
    ranges: TimelineRange[],
    color: string,
  ) {
    ctx.fillStyle = color;
    for (const range of ranges) {
      const start = Math.max(viewStart, range.start);
      const end = Math.min(viewStart + windowSec, range.end);
      if (end <= start) continue;
      ctx.fillRect(
        ((start - viewStart) / windowSec) * W,
        0,
        Math.max(1, ((end - start) / windowSec) * W),
        H,
      );
    }
  }

  function drawGrid(ctx: CanvasRenderingContext2D, W: number, H: number) {
    ctx.clearRect(0, 0, W, H);
    fillReferenceRanges(
      ctx,
      W,
      H,
      cachedVideoRanges,
      'rgba(148,163,184,0.16)',
    );
    fillReferenceRanges(
      ctx,
      W,
      H,
      bufferedVideoRanges,
      'rgba(226,232,240,0.20)',
    );
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
  // drift from the global timeline origin, exactly like ffmpeg atempo followed
  // by mkvmerge delay during the final repack. The backend's fixed loudness
  // scale is retained so quiet and loud scenes remain visually comparable. At
  // close zoom levels the buffer contains detailed PCM RMS samples;
  // interpolation avoids throwing away sub-pixel transitions.
  function drawLane(
    canvas: HTMLCanvasElement | null,
    buf: WaveBuffer | null,
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
      for (let x = 0; x < W; x++) {
        const viewTime = viewStart + (x / W) * windowSec;
        const trackTime = (viewTime - delaySec) * stretch;
        const position = ((trackTime - buf.start) / buf.dur) * (bn - 1);
        const left = Math.floor(position);
        const mix = position - left;
        const a = left >= 0 && left < bn ? buf.peaks[left] : 0;
        const b = left + 1 >= 0 && left + 1 < bn ? buf.peaks[left + 1] : a;
        vals[x] = a + (b - a) * mix;
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

  function drawOverviewLane(
    canvas: HTMLCanvasElement | null,
    buf: WaveBuffer | null,
    totalDuration: number,
    color: string,
    lane: 'eng' | 'ger',
  ) {
    if (!canvas || totalDuration <= 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    const mapReferenceTime = (referenceTime: number) =>
      lane === 'ger'
        ? (referenceTime - delayMs / 1000) * stretch
        : referenceTime;
    const xForTrackTime = (trackTime: number) =>
      (Math.min(totalDuration, Math.max(0, trackTime)) / totalDuration) * W;
    const fillMappedRanges = (ranges: TimelineRange[], fill: string) => {
      ctx.fillStyle = fill;
      for (const range of ranges) {
        const mappedStart = mapReferenceTime(range.start);
        const mappedEnd = mapReferenceTime(range.end);
        if (mappedEnd <= 0 || mappedStart >= totalDuration) continue;
        const left = xForTrackTime(mappedStart);
        const right = xForTrackTime(mappedEnd);
        ctx.fillRect(left, 0, Math.max(1, right - left), H);
      }
    };

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(0,0,0,0.4)';
    ctx.fillRect(0, 0, W, H);
    fillMappedRanges(cachedVideoRanges, 'rgba(148,163,184,0.22)');
    fillMappedRanges(bufferedVideoRanges, 'rgba(226,232,240,0.30)');
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    for (let tick = 1; tick < 10; tick++) {
      ctx.fillRect(Math.round((tick / 10) * W), 0, 1, H);
    }

    if (buf && buf.peaks.length > 0) {
      const count = buf.peaks.length;
      ctx.fillStyle = color;
      for (let x = 0; x < W; x++) {
        const from = Math.min(count - 1, Math.floor((x / W) * count));
        const to = Math.min(count, Math.max(from + 1, Math.ceil(((x + 1) / W) * count)));
        let peak = 0;
        for (let index = from; index < to; index++) {
          peak = Math.max(peak, buf.peaks[index]);
        }
        const height = Math.min(1, peak / 127) * (H / 2 - 2);
        ctx.fillRect(x, H / 2 - height, 1, height * 2);
      }
    }

    ctx.fillStyle = 'rgba(255,255,255,0.2)';
    ctx.fillRect(0, H / 2, W, 1);

    const viewportStart = mapReferenceTime(viewStart);
    const viewportEnd = mapReferenceTime(viewStart + windowSec);
    const left = xForTrackTime(viewportStart);
    const right = xForTrackTime(viewportEnd);
    ctx.fillStyle = lane === 'ger'
      ? 'rgba(244,114,182,0.10)'
      : 'rgba(56,189,248,0.10)';
    ctx.fillRect(left, 0, Math.max(2, right - left), H);
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(left + 1, 1, Math.max(1, right - left - 2), H - 2);
    ctx.fillStyle = color;
    ctx.fillRect(left, 0, 3, H);
    ctx.fillRect(Math.max(left, right - 3), 0, 3, H);

    const parkedReferenceTime = viewStart + seekFrac * windowSec;
    const parkedX = xForTrackTime(mapReferenceTime(parkedReferenceTime));
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillRect(parkedX, 0, 1, H);
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
  }, [engBuf, canvasW, canvasH, windowSec, center, cachedVideoRanges, bufferedVideoRanges]);

  // German redraws on every delay change too — that's the smooth drag.
  useEffect(() => {
    drawGer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gerBuf, delayMs, stretch, canvasW, canvasH, windowSec, center, cachedVideoRanges, bufferedVideoRanges]);

  useEffect(() => {
    drawOverviewLane(
      engOverviewCanvas.current,
      engOverviewBuf,
      engOverviewDuration,
      '#38bdf8',
      'eng',
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    engOverviewBuf,
    engOverviewDuration,
    canvasW,
    center,
    windowSec,
    seekFrac,
    cachedVideoRanges,
    bufferedVideoRanges,
  ]);

  useEffect(() => {
    drawOverviewLane(
      gerOverviewCanvas.current,
      gerOverviewBuf,
      gerOverviewDuration,
      '#f472b6',
      'ger',
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    gerOverviewBuf,
    gerOverviewDuration,
    canvasW,
    center,
    windowSec,
    seekFrac,
    delayMs,
    stretch,
    cachedVideoRanges,
    bufferedVideoRanges,
  ]);

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

  function overviewReferenceTime(
    clientX: number,
    lane: 'eng' | 'ger',
    canvas: HTMLCanvasElement,
  ) {
    const rect = canvas.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    if (lane === 'ger') {
      const sourceTime = fraction * gerOverviewDuration;
      return sourceTime / stretch + delayMs / 1000;
    }
    return fraction * engOverviewDuration;
  }

  function moveOverviewViewport(
    clientX: number,
    lane: 'eng' | 'ger',
    canvas: HTMLCanvasElement,
    grabOffset: number,
  ) {
    if (duration <= 0) return;
    const requestedCenter =
      overviewReferenceTime(clientX, lane, canvas) - grabOffset;
    const halfWindow = Math.min(windowSec / 2, duration / 2);
    const maxCenter = Math.max(halfWindow, duration - halfWindow);
    setCenter(Math.max(halfWindow, Math.min(maxCenter, requestedCenter)));
  }

  function onOverviewPointerDown(
    event: React.PointerEvent<HTMLCanvasElement>,
    lane: 'eng' | 'ger',
  ) {
    event.preventDefault();
    const pointerTime = overviewReferenceTime(
      event.clientX,
      lane,
      event.currentTarget,
    );
    const viewportCenter = viewStart + windowSec / 2;
    const withinViewport =
      pointerTime >= viewStart && pointerTime <= viewStart + windowSec;
    const grabOffset = withinViewport ? pointerTime - viewportCenter : 0;
    overviewDragRef.current = {
      lane,
      pointerId: event.pointerId,
      grabOffset,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    resetSeekToStart();
    moveOverviewViewport(
      event.clientX,
      lane,
      event.currentTarget,
      grabOffset,
    );
  }

  function onOverviewPointerMove(
    event: React.PointerEvent<HTMLCanvasElement>,
  ) {
    const drag = overviewDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    moveOverviewViewport(
      event.clientX,
      drag.lane,
      event.currentTarget,
      drag.grabOffset,
    );
  }

  function onOverviewPointerEnd(
    event: React.PointerEvent<HTMLCanvasElement>,
  ) {
    const drag = overviewDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    overviewDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function zoom(factor: number) {
    resetSeekToStart();
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
    const desiredRefTime = viewStart + startOffset;
    let activeClipStart = videoClipStart;
    if (
      desiredRefTime < videoClipStart
      || desiredRefTime >= videoClipStart + videoClipLen - 1
    ) {
      activeClipStart = Math.floor(desiredRefTime / videoSegmentSec) * videoSegmentSec;
    }
    const activeClipLen = Math.max(
      1,
      Math.min(
        120,
        duration > 0 ? duration - activeClipStart : videoSegmentSec * 2,
        videoSegmentSec * 2,
      ),
    );
    const videoStartAt = desiredRefTime - activeClipStart;
    playbackStartOffsetRef.current = videoStartAt;
    const dur = Math.max(
      1,
      Math.min(activeClipLen - videoStartAt, windowSec - startOffset),
    );
    let german: HTMLAudioElement | null = null;
    if (which !== 'eng' && gerStream != null) {
      const sourceStart = (desiredRefTime - delayMs / 1000) * stretch;
      const lead = sourceStart < 0 ? Math.min(dur, -sourceStart / stretch) : 0;
      const a = new Audio(
        api.audioClipUrl(path, gerStream, Math.max(0, sourceStart), dur, lead, stretch),
      );
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
      const loadedClipStart = Number(v.dataset.clipStart);
      if (
        !v.currentSrc
        || !v.src
        || !Number.isFinite(loadedClipStart)
        || Math.abs(loadedClipStart - activeClipStart) > 0.01
      ) {
        setVideoLoading(true);
        v.src = api.videoClipUrl(path, activeClipStart, activeClipLen, quality, engStream);
        v.dataset.clipStart = String(activeClipStart);
        v.dataset.clipLen = String(activeClipLen);
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
        v.currentTime = videoStartAt;
      } catch {
        /* not seekable yet */
      }
      try {
        // Both elements may buffer in parallel, but German audio is not
        // allowed to start until the picture can render from the seek point.
        await Promise.all([waitUntilReady(v, token), german ? waitUntilReady(german, token) : Promise.resolve()]);
        if (token !== playTokenRef.current) return;
        await Promise.all([v.play(), german ? german.play() : Promise.resolve()]);
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
      if (vv && !vv.paused) {
        posSec = startOffset + Math.max(0, vv.currentTime - playbackStartOffsetRef.current);
      }
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
    const activeMode = playing;
    seekFracRef.current = frac;
    livePosRef.current = frac;
    setSeekFrac(frac);
    parkHeads(frac);
    const video = videoRef.current;
    if (video && video.readyState >= HTMLMediaElement.HAVE_METADATA) {
      const loadedStart = Number(video.dataset.clipStart);
      const desired = viewStart + frac * windowSec;
      if (
        Number.isFinite(loadedStart)
        && desired >= loadedStart
        && desired < loadedStart + video.duration
      ) {
        video.currentTime = desired - loadedStart;
      }
    }
    if (activeMode !== 'none') {
      resumeModeRef.current = activeMode;
      void playSection(activeMode);
    }
  }

  function resetSeekToStart() {
    if (playing !== 'none') {
      resumeModeRef.current = playing;
      haltAudioVideo();
      setPlaying('none');
    }
    seekFracRef.current = 0;
    livePosRef.current = 0;
    playbackStartOffsetRef.current = 0;
    setSeekFrac(0);
    parkHeads(0);
  }

  // The English/reference lane scrolls through the movie. Zooming is reserved
  // for the German lane so the two wheel gestures cannot be confused.
  function onWheelPan(e: React.WheelEvent) {
    e.preventDefault();
    resetSeekToStart();
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
        resetSeekToStart();
        setCenter((c) => Math.max(windowSec / 2, c - windowSec * 0.2));
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        resetSeekToStart();
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

  // Length drift between the reference (HQ) and German audio hints at a
  // frame-rate/speed mismatch (e.g. 25fps PAL vs 23.976) even before aligning.
  const lenDrift = engTrack?.duration != null && gerTrack?.duration != null ? engTrack.duration - gerTrack.duration : null;
  // Prefer the pipeline's measured frame-drift ratio only when the visual
  // matcher was confident. Low-confidence estimates (such as a few ambiguous
  // frames in animation) remain visible as diagnostics but must not become an
  // actionable suggestion.
  const measuredDrift = info?.drift_ratio ?? null;
  const measuredDriftReliable =
    measuredDrift != null && (info?.sync_confidence ?? 0) >= 0.6 && !info?.needs_sync_review;
  const sourceFps = info?.source_fps ?? null;
  const referenceFps = info?.reference_fps ?? info?.video_fps ?? null;
  // A source authored at 25fps is shorter than the corresponding 24fps
  // reference and must be slowed down: atempo = reference_fps / source_fps.
  const fpsStretch = sourceFps && referenceFps ? referenceFps / sourceFps : null;
  const durationStretch = engTrack?.duration && gerTrack?.duration && engTrack.duration > 0 ? gerTrack.duration / engTrack.duration : null;
  const lengthsDiffer = lenDrift != null && Math.abs(lenDrift) > 2;

  async function openReplacement() {
    setReplaceOpen(true);
    setReplaceMode('manual');
    setReplaceCandidates([]);
    setReplaceFilters(EMPTY_TORRENT_FILTERS);
    setReplaceLoading(true);
    try {
      const context = torrentContext(entry);
      const result = await api.torrentSearch(context.query, gerTrack?.duration ?? duration ?? null, {
        kind: context.kind,
        seriesTitle: context.seriesTitle,
        season: context.season,
        episode: context.episode,
      });
      setReplaceCandidates(result.candidates);
      setReplaceFilters((current) => ({
        ...current,
        minSeeders: String(result.policy.min_seeders),
        minSizeGib: String(result.policy.min_size_gib),
        maxSizeGib: String(result.policy.max_size_gib),
      }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplaceLoading(false);
    }
  }

  async function searchReviewTorrents() {
    setReplaceLoading(true);
    try {
      const context = torrentContext(entry);
      const result = await api.torrentSearch(context.query, gerTrack?.duration ?? duration ?? null, {
        kind: context.kind,
        seriesTitle: context.seriesTitle,
        season: context.season,
        episode: context.episode,
        minSeeders: nullableNumber(replaceFilters.minSeeders),
        maxSeeders: nullableNumber(replaceFilters.maxSeeders),
        minSizeGib: nullableNumber(replaceFilters.minSizeGib),
        maxSizeGib: nullableNumber(replaceFilters.maxSizeGib),
      });
      setReplaceCandidates(result.candidates);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setReplaceLoading(false);
    }
  }

  async function startReplacement(candidate?: TorrentCandidate, magnet?: string) {
    setBusy('replace');
    try {
      const context = torrentContext(entry);
      await api.replaceTorrent({
        path,
        query: context.query,
        target_runtime_seconds: gerTrack?.duration ?? duration ?? null,
        candidate,
        magnet_uri: magnet || undefined,
        kind: context.kind,
        series_title: context.seriesTitle,
        season: context.season,
        episode: context.episode,
      });
      toast.success(candidate || magnet ? 'Selected torrent is downloading in the background' : 'Automatic torrent replacement started');
      setReplaceOpen(false);
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }
  const suggestedStretch =
    measuredDriftReliable && measuredDrift != null && Math.abs(measuredDrift - 1) > 0.0005
      ? measuredDrift
      : lengthsDiffer && fpsStretch != null && Math.abs(fpsStretch - 1) > 0.0005
        ? fpsStretch
        : lengthsDiffer
          ? durationStretch
          : null;
  // Flag a real drift: a measured frame-drift, or lengths differing by >2s.
  const driftSuspected =
    (measuredDriftReliable && measuredDrift != null && Math.abs(measuredDrift - 1) > 0.0015) ||
    (lengthsDiffer && fpsStretch != null && Math.abs(fpsStretch - 1) > 0.0015) ||
    (lengthsDiffer && durationStretch != null && Math.abs(durationStretch - 1) > 0.001);
  const stretchPct = ((stretch - 1) * 100).toFixed(2);
  function applyStretchInput(value = stretchInput) {
    const parsed = Number(value.replace(',', '.'));
    if (!Number.isFinite(parsed) || parsed < 0.5 || parsed > 2) {
      toast.error('Drift factor must be between 0.5 and 2.0');
      setStretchInput(stretch.toFixed(6));
      return;
    }
    const next = +parsed.toFixed(6);
    setStretch(next);
    setStretchInput(next.toFixed(6));
  }
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
          <Button variant='secondary' onClick={openReplacement} disabled={busy != null}>
            <Download data-icon='inline-start' /> New torrent
          </Button>
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
              <div className='flex min-h-0 flex-1 flex-col gap-3 py-3'>
                <TorrentPickerTools
                  filters={replaceFilters}
                  onChange={setReplaceFilters}
                  onSearch={searchReviewTorrents}
                  onMagnet={(magnet) => startReplacement(undefined, magnet)}
                  busy={replaceLoading || busy === 'replace'}
                />
                {replaceLoading ? (
                  <div className='flex items-center justify-center py-10'>
                    <Loader2 className='h-6 w-6 animate-spin' />
                  </div>
                ) : replaceCandidates.length === 0 ? (
                  <EmptyState icon={Download} title='No matching torrents' description='Change the filters or paste a magnet link.' />
                ) : (
                  <TorrentCandidateTable candidates={replaceCandidates} busy={busy === 'replace'} onSelect={startReplacement} />
                )}
              </div>
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
                  onLoadedMetadata={(event) => {
                    const media = event.currentTarget;
                    const loadedStart = Number(media.dataset.clipStart);
                    if (Number.isFinite(loadedStart)) {
                      const loadedLen = Number(media.dataset.clipLen);
                      markVideoClipCached(
                        loadedStart,
                        Number.isFinite(loadedLen) ? loadedLen : media.duration,
                      );
                      media.currentTime = Math.max(0, viewStart - loadedStart);
                    }
                    captureVideoBuffered(media);
                  }}
                  onLoadedData={(event) => {
                    setVideoLoading(false);
                    captureVideoBuffered(event.currentTarget);
                  }}
                  onCanPlay={(event) => {
                    setVideoLoading(false);
                    captureVideoBuffered(event.currentTarget);
                  }}
                  onProgress={(event) => captureVideoBuffered(event.currentTarget)}
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
                  <Languages className='h-3.5 w-3.5' />
                  Full English · drag the marked window to navigate
                  {engOverviewLoading && <Loader2 className='h-3 w-3 animate-spin' />}
                </span>
                <span className='flex items-center gap-3 text-white'>
                  <span className='flex items-center gap-1'>
                    <span className='size-2 rounded-sm bg-muted-foreground/40' />
                    Cached
                  </span>
                  <span className='flex items-center gap-1'>
                    <span className='size-2 rounded-sm bg-foreground/45' />
                    Buffered
                  </span>
                  <span className='font-mono'>{fmtClock(engOverviewDuration)}</span>
                </span>
              </div>
              <div className='relative'>
                <canvas
                  ref={engOverviewCanvas}
                  width={canvasW}
                  height={OVERVIEW_WAVEFORM_HEIGHT}
                  onPointerDown={(event) => onOverviewPointerDown(event, 'eng')}
                  onPointerMove={onOverviewPointerMove}
                  onPointerUp={onOverviewPointerEnd}
                  onPointerCancel={onOverviewPointerEnd}
                  className='w-full touch-none cursor-grab rounded-md active:cursor-grabbing'
                  aria-label='Full English audio overview with draggable visible window'
                />
                {engOverviewLoading && (
                  <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                    <Loader2 className='h-4 w-4 animate-spin text-sky-400' />
                  </div>
                )}
              </div>
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
                <label className='flex items-center gap-2 text-white'>
                  Drift factor
                  <Input
                    value={stretchInput}
                    onChange={(event) => setStretchInput(event.target.value)}
                    onBlur={() => applyStretchInput()}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') applyStretchInput();
                    }}
                    inputMode='decimal'
                    className='h-7 w-28 font-mono'
                    aria-label='German audio drift factor'
                  />
                </label>
                <Button size='sm' variant='secondary' className='h-7' onClick={() => applyStretchInput()}>
                  Apply
                </Button>
                <span className='font-mono text-white'>{stretch >= 1 ? '+' : ''}{stretchPct}%</span>
                {suggestedStretch != null && Math.abs(suggestedStretch - 1) > 0.0005 && (
                  <Button
                    size='sm'
                    variant='secondary'
                    className='h-7'
                    onClick={() => {
                      const next = +suggestedStretch.toFixed(6);
                      setStretch(next);
                      setStretchInput(next.toFixed(6));
                    }}
                  >
                    Suggested ×{suggestedStretch.toFixed(4)}
                  </Button>
                )}
                {stretch !== 1 && (
                  <Button
                    size='sm'
                    variant='ghost'
                    className='h-7'
                    onClick={() => {
                      setStretch(1);
                      setStretchInput('1.000000');
                    }}
                  >
                    Reset
                  </Button>
                )}
                {driftSuspected && (
                  <span className='text-warning'>
                    {measuredDriftReliable && measuredDrift != null && sourceFps && referenceFps
                      ? `Measured ×${measuredDrift.toFixed(4)}`
                      : fpsStretch != null && sourceFps && referenceFps
                        ? `FPS ×${fpsStretch.toFixed(4)}`
                        : 'Track lengths differ'}
                  </span>
                )}
                {!measuredDriftReliable && measuredDrift != null && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className='text-muted-foreground'>
                        Visual ×{measuredDrift.toFixed(4)} ignored
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      Visual matching confidence was too low to recommend this drift factor.
                    </TooltipContent>
                  </Tooltip>
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
              <div className='flex items-center justify-between px-1 text-xs'>
                <span className='flex items-center gap-2 text-pink-400'>
                  <AudioLines className='h-3.5 w-3.5' />
                  Full German · drag the marked window to navigate
                  {gerOverviewLoading && <Loader2 className='h-3 w-3 animate-spin' />}
                </span>
                <span className='font-mono text-white'>{fmtClock(gerOverviewDuration)}</span>
              </div>
              <div className='relative'>
                <canvas
                  ref={gerOverviewCanvas}
                  width={canvasW}
                  height={OVERVIEW_WAVEFORM_HEIGHT}
                  onPointerDown={(event) => onOverviewPointerDown(event, 'ger')}
                  onPointerMove={onOverviewPointerMove}
                  onPointerUp={onOverviewPointerEnd}
                  onPointerCancel={onOverviewPointerEnd}
                  className='w-full touch-none cursor-grab rounded-md active:cursor-grabbing'
                  aria-label='Full German audio overview with draggable visible window'
                />
                {gerOverviewLoading && (
                  <div className='pointer-events-none absolute inset-0 flex items-center justify-center'>
                    <Loader2 className='h-4 w-4 animate-spin text-pink-400' />
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
                onValueChange={(v) => {
                  resetSeekToStart();
                  setCenter(v[0]);
                }}
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
