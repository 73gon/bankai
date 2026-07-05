import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Library as LibraryIcon,
  Trash2,
  Play,
  Pause,
  ArrowLeft,
  Loader2,
  Minus,
  Plus,
  RotateCcw,
  CheckCircle2,
  Send,
  UploadCloud,
  Clock,
  AlertCircle,
  X,
  RefreshCw,
  Film,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  Languages,
  AudioLines,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type MediaInfo, type TitleRow } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatBytes } from '@/lib/utils';
import { cn } from '@/lib/utils';

function stageBadge(stage: string | null) {
  switch (stage) {
    case 'approved':
      return <Badge variant='success'>Approved</Badge>;
    case 'transferred':
      return <Badge variant='accent'>Transferred</Badge>;
    case 'review':
      return <Badge variant='warning'>Review</Badge>;
    default:
      return null;
  }
}

function StatusCell({ r }: { r: TitleRow }) {
  if (r.row_kind === 'job') {
    if (r.pending)
      return (
        <Badge variant='muted' className='gap-1.5'>
          <Clock className='h-3 w-3' /> Queued
        </Badge>
      );
    const s = r.job_status;
    if (s === 'running') {
      const pct = Math.round(r.overall_percent ?? 0);
      return (
        <div className='min-w-[10rem]'>
          <Badge variant='accent' className='gap-1.5'>
            <Loader2 className='h-3 w-3 animate-spin' />
            {r.step_label || 'Working'} {pct > 0 ? `${pct}%` : ''}
          </Badge>
          <div className='mt-1 h-1 overflow-hidden rounded-full bg-secondary'>
            <div
              className='h-full rounded-full bg-gradient-to-r from-fuchsia-500 to-violet-500'
              style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
            />
          </div>
        </div>
      );
    }
    if (s === 'failed' || s === 'error')
      return (
        <Badge variant='destructive' className='gap-1.5'>
          <AlertCircle className='h-3 w-3' /> Failed
        </Badge>
      );
    if (s === 'cancelled') return <Badge variant='warning'>Cancelled</Badge>;
    if (s === 'done' || s === 'success') return <Badge variant='success'>Done</Badge>;
    return <Badge variant='muted'>{s}</Badge>;
  }
  return stageBadge(r.stage) ?? <Badge variant='muted'>Review</Badge>;
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

type FilterKey = 'active' | 'review' | 'approved' | 'done' | 'failed';

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'active', label: 'Downloading' },
  { key: 'review', label: 'Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'done', label: 'Done' },
  { key: 'failed', label: 'Failed' },
];

function rowCategory(r: TitleRow): FilterKey {
  if (r.row_kind === 'job') {
    if (r.pending || r.job_status === 'running') return 'active';
    if (r.job_status === 'failed' || r.job_status === 'error' || r.job_status === 'cancelled')
      return 'failed';
    return 'done'; // finished job with no local file (already on server)
  }
  if (r.transfer_status === 'transferring') return 'approved';
  if (r.stage === 'transferred' || r.transfer_status === 'done') return 'done';
  if (r.stage === 'approved') return 'approved';
  return 'review';
}

type SortCol = 'title' | 'type' | 'status' | 'when';

function whenLabel(ts: number | null): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return (
    d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ', ' +
    d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  );
}

function titleWithYear(r: TitleRow): string {
  return r.year ? `${r.title} (${r.year})` : r.title;
}

export default function Library() {
  const [rows, setRows] = useState<TitleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [statusFilters, setStatusFilters] = useState<Set<FilterKey>>(new Set());
  const [sortCol, setSortCol] = useState<SortCol>('when');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
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

  // Size the page to however many rows fit the viewport (no window scroll).
  useEffect(() => {
    const el = tableWrapRef.current;
    if (!el) return;
    const compute = () => {
      const rowH = 60;
      const headH = 42;
      const h = el.clientHeight - headH;
      setPageSize(Math.max(4, Math.floor(h / rowH)));
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
      const k = rowCategory(r);
      c[k] = (c[k] ?? 0) + 1;
    }
    return c;
  }, [rows]);

  const filtered = useMemo(() => {
    const list = rows.filter(
      (e) =>
        titleWithYear(e).toLowerCase().includes(filter.toLowerCase()) &&
        (statusFilters.size === 0 || statusFilters.has(rowCategory(e))),
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

  function toggleFilter(k: FilterKey) {
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

  async function retryJob(id: string) {
    try {
      await api.retryJob(id);
      toast.success('Retrying');
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
    if (r.needs_sync_review)
      return (
        <Badge
          variant='warning'
          title={
            'Automatic audio sync was low-confidence' +
            (r.sync_confidence != null ? ` (${Math.round(r.sync_confidence * 100)}%)` : '') +
            '. Open Review to check and nudge the German delay.'
          }
        >
          Check sync
        </Badge>
      );
    if (r.sync_confidence != null)
      return (
        <span className='text-xs text-muted-foreground' title='Automatic sync confidence'>
          {Math.round(r.sync_confidence * 100)}%
        </span>
      );
    return <span className='text-xs text-muted-foreground'>—</span>;
  }

  function TransferCell({ r }: { r: TitleRow }) {
    if (r.row_kind !== 'library') return <span className='text-muted-foreground'>—</span>;
    const st = r.transfer_status;
    if (st === 'transferring') {
      const pct = Math.round(r.transfer_percent || 0);
      return (
        <Badge variant='accent' className='gap-1.5' title='Transfer in progress'>
          <Loader2 className='h-3 w-3 animate-spin' />
          {pct > 0 ? `Sending ${pct}%` : 'Sending…'}
        </Badge>
      );
    }
    if (st === 'done' || r.stage === 'transferred') {
      return (
        <Badge variant='success' className='gap-1.5' title='Transferred to the media server'>
          <CheckCircle2 className='h-3 w-3' /> Done
        </Badge>
      );
    }
    if (st === 'failed') {
      return (
        <Button size='sm' variant='destructive' onClick={() => transferOne(r.path!)} title='Retry transfer'>
          <RotateCcw className='h-4 w-4' /> Retry
        </Button>
      );
    }
    if (r.stage === 'approved') {
      return (
        <Button size='sm' variant='secondary' onClick={() => transferOne(r.path!)} title='Send to media server'>
          <UploadCloud className='h-4 w-4' /> Send
        </Button>
      );
    }
    return <span className='text-xs text-muted-foreground'>—</span>;
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
    const canRetry = isJob && (r.job_status === 'failed' || r.job_status === 'error' || r.job_status === 'cancelled');
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
          <td className='px-2 py-2 align-middle'>
            <SyncCell r={r} />
          </td>
          <td className='px-2 py-2 align-middle'>
            <TransferCell r={r} />
          </td>
          <td className='whitespace-nowrap px-2 py-2 align-middle text-xs text-muted-foreground'>
            {whenLabel(r.done_at)}
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
              {canRetry && (
                <Button size='icon' variant='ghost' onClick={() => retryJob(r.job_id!)} title='Retry'>
                  <RotateCcw className='h-4 w-4' />
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
            <td colSpan={8} className='px-3 py-2'>
              {r.job_id ? (
                <pre className='max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-3 text-[11px] leading-relaxed text-muted-foreground'>
                  {logs[r.job_id] ?? 'Loading logs…'}
                </pre>
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
        <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Search…' className='w-56' />
      </header>

      <div className='flex flex-wrap items-center gap-1.5'>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => toggleFilter(f.key)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
              statusFilters.has(f.key)
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border text-muted-foreground hover:bg-secondary hover:text-foreground',
            )}
          >
            {f.label}
            <span className='ml-1.5 opacity-70'>{counts[f.key] ?? 0}</span>
          </button>
        ))}
        {statusFilters.size > 0 && (
          <button
            onClick={() => setStatusFilters(new Set())}
            className='px-2 py-1 text-xs text-muted-foreground hover:text-foreground'
          >
            Clear
          </button>
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
                <th className='px-2 py-2 text-left font-medium'>Sync</th>
                <th className='px-2 py-2 text-left font-medium'>Transfer</th>
                <SortHeader col='when' label='When' className='text-left' />
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

      {!loading && filtered.length > pageSize && (
        <div className='flex items-center justify-between gap-3 text-sm'>
          <span className='text-xs text-muted-foreground'>
            {safePage * pageSize + 1}–{Math.min(filtered.length, (safePage + 1) * pageSize)} of{' '}
            {filtered.length}
          </span>
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
        <ReviewStudio
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

function formatTime(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  return `${h > 0 ? h + ':' : ''}${mm}:${String(sec).padStart(2, '0')}`;
}

/**
 * Video player that supports seeking for transcoded HEVC/4K previews.
 *
 * Direct streams use native controls (HTTP range requests give real seeking).
 * Transcoded streams have no known duration and aren't natively seekable, so we
 * drive a custom scrubber off the known media duration and re-request the
 * transcode from the chosen offset (the backend accepts a `t` start time).
 */
function Player({
  videoRef,
  path,
  audioIdx,
  duration,
  useTranscode,
}: {
  videoRef: { current: HTMLVideoElement | null };
  path: string;
  audioIdx: number;
  duration: number | null;
  useTranscode: boolean;
}) {
  const [base, setBase] = useState(0); // transcode start offset (seconds)
  const [cur, setCur] = useState(0); // displayed playhead (seconds)
  const [playing, setPlaying] = useState(false);
  const [dragging, setDragging] = useState(false);
  const total = duration ?? 0;

  const src = useTranscode ? api.transcodeUrl(path, audioIdx, base) : api.streamUrl(path);

  // Reset when the file or playback mode changes.
  useEffect(() => {
    setBase(0);
    setCur(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, useTranscode]);

  // When the audio track changes in transcode mode, reload from the current spot.
  useEffect(() => {
    if (!useTranscode) return;
    const v = videoRef.current;
    setBase((b) => (v ? b + v.currentTime : b));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioIdx]);

  // Reload the element and resume when the source (base offset / audio) changes.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.load();
    if (playing) v.play().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  function displayTime(): number {
    const v = videoRef.current;
    const local = v ? v.currentTime : 0;
    return useTranscode ? base + local : local;
  }

  function seekTo(t: number) {
    const clamped = Math.max(0, total ? Math.min(total, t) : t);
    if (useTranscode) {
      setBase(clamped);
      setCur(clamped);
    } else {
      const v = videoRef.current;
      if (v) v.currentTime = clamped;
      setCur(clamped);
    }
  }

  // Keyboard: space = play/pause, ← / → = seek 10s.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      const v = videoRef.current;
      if (!v) return;
      if (ev.key === ' ') {
        ev.preventDefault();
        v.paused ? v.play() : v.pause();
      } else if (ev.key === 'ArrowLeft') {
        ev.preventDefault();
        seekTo(displayTime() - 10);
      } else if (ev.key === 'ArrowRight') {
        ev.preventDefault();
        seekTo(displayTime() + 10);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useTranscode, base, total]);

  function togglePlay() {
    const v = videoRef.current;
    if (!v) return;
    v.paused ? v.play() : v.pause();
  }

  return (
    <div className='space-y-2'>
      <video
        ref={videoRef}
        src={src}
        controls={!useTranscode}
        autoPlay={playing}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={() => {
          if (!dragging) setCur(displayTime());
        }}
        className='max-h-[70vh] w-full rounded-md bg-black'
      />
      {useTranscode && (
        <>
          <div className='flex items-center gap-3'>
            <Button size='icon' variant='secondary' onClick={togglePlay}>
              {playing ? <Pause className='h-4 w-4' /> : <Play className='h-4 w-4' />}
            </Button>
            <span className='w-14 shrink-0 text-right font-mono text-xs text-muted-foreground'>{formatTime(cur)}</span>
            <Slider
              value={[Math.min(cur, total || cur)]}
              min={0}
              max={total || 1}
              step={1}
              onValueChange={(v) => {
                setDragging(true);
                setCur(v[0]);
              }}
              onValueCommit={(v) => {
                setDragging(false);
                seekTo(v[0]);
              }}
              className='flex-1'
            />
            <span className='w-14 shrink-0 font-mono text-xs text-muted-foreground'>{formatTime(total)}</span>
          </div>
          <p className='text-[11px] text-muted-foreground'>
            Transcoded preview (HEVC/4K). Seeking re-encodes from the chosen point.
          </p>
        </>
      )}
    </div>
  );
}

function ReviewStudio({ entry, onClose }: { entry: TitleRow; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [delay, setDelay] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  const [audioIdx, setAudioIdx] = useState(0);
  const [useTranscode, setUseTranscode] = useState(false);
  const [repacking, setRepacking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const path = entry.path as string;

  async function loadInfo() {
    setLoading(true);
    try {
      const m = await api.mediaInfo(path);
      setInfo(m);
      setDelay(m.delay_ms);
      setSavedDelay(m.delay_ms);
      setUseTranscode(!m.browser_playable);
      const ger = m.audio_tracks.find((t) => t.is_german);
      setAudioIdx(ger ? ger.order : 0);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    loadInfo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry.path]);

  const previewDelta = delay - savedDelay;

  // Keyboard shortcuts for delay nudging ([ / ]). Playback keys live in the Player.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (ev.key === '[') setDelay((d) => d - 10);
      else if (ev.key === ']') setDelay((d) => d + 10);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  async function repack() {
    setRepacking(true);
    try {
      const r = await api.repack(path, delay);
      toast.success(r.message || 'Repacked');
      await loadInfo();
      if (videoRef.current) videoRef.current.load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setRepacking(false);
    }
  }

  async function persistDelay() {
    try {
      await api.setDelay(path, delay);
      setSavedDelay(delay);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function approve() {
    setBusy('approve');
    try {
      await api.approve(path);
      toast.success('Approved — ready to transfer');
      await loadInfo();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function transfer() {
    setBusy('transfer');
    try {
      await api.transfer(path);
      toast.success('Transferred to server');
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className='fixed inset-0 z-50 flex flex-col bg-background'>
      <div className='flex items-center justify-between gap-3 border-b border-border/50 px-4 py-3 md:px-6'>
        <div className='flex min-w-0 items-center gap-2'>
          <Button size='icon' variant='ghost' onClick={onClose} title='Close'>
            <ArrowLeft className='h-5 w-5' />
          </Button>
          <h2 className='truncate text-lg font-semibold'>{entry.name}</h2>
          {info && stageBadge(info.stage)}
        </div>
        <div className='flex items-center gap-2'>
          {info?.stage === 'approved' ? (
            <Button onClick={transfer} disabled={busy === 'transfer'}>
              {busy === 'transfer' ? <Loader2 className='h-4 w-4 animate-spin' /> : <Send className='h-4 w-4' />}
              Transfer to server
            </Button>
          ) : info?.stage === 'transferred' ? (
            <Badge variant='accent'>Already transferred</Badge>
          ) : (
            <Button onClick={approve} disabled={busy === 'approve'} variant='default'>
              {busy === 'approve' ? <Loader2 className='h-4 w-4 animate-spin' /> : <CheckCircle2 className='h-4 w-4' />}
              Approve
            </Button>
          )}
        </div>
      </div>

      <div className='flex-1 overflow-auto p-4 md:p-6'>
        <div className='mx-auto max-w-5xl space-y-4'>
          <p className='text-sm text-muted-foreground'>QC the German dub timing, then approve and transfer.</p>

          {loading ? (
            <Skeleton className='aspect-video w-full' />
          ) : (
            <>
              <Player
                videoRef={videoRef}
                path={path}
                audioIdx={audioIdx}
                duration={info?.duration ?? null}
                useTranscode={useTranscode}
              />

            <div className='grid gap-4 md:grid-cols-2'>
              {/* Audio track + transcode */}
              <div className='space-y-3'>
                <div className='flex items-center gap-2 text-sm font-medium'>
                  <AudioLines className='h-4 w-4' /> Audio track
                </div>
                <Select value={String(audioIdx)} onValueChange={(v) => setAudioIdx(Number(v))}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {info?.audio_tracks.map((t) => (
                      <SelectItem key={t.order} value={String(t.order)}>
                        {t.is_german ? '🇩🇪 ' : ''}
                        {t.language || 'und'} · {t.codec}
                        {t.title ? ` · ${t.title}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className='flex items-center gap-2 text-xs text-muted-foreground'>
                  <input type='checkbox' checked={useTranscode} onChange={(e) => setUseTranscode(e.target.checked)} />
                  Transcode (needed for 4K/HEVC)
                </label>
                <div className='flex flex-wrap gap-1.5'>
                  {info?.has_german ? (
                    <Badge variant='success' className='gap-1'>
                      <Languages className='h-3 w-3' /> German present
                    </Badge>
                  ) : (
                    <Badge variant='destructive'>No German track</Badge>
                  )}
                  {info?.video_codec && <Badge variant='muted'>{info.video_codec}</Badge>}
                  {info?.width && (
                    <Badge variant='muted'>
                      {info.width}×{info.height}
                    </Badge>
                  )}
                </div>
              </div>

              {/* Delay adjust */}
              <div className='space-y-3'>
                <div className='flex items-center justify-between text-sm font-medium'>
                  <span>German audio delay</span>
                  <span className='font-mono text-primary-foreground'>
                    {delay > 0 ? '+' : ''}
                    {delay} ms
                  </span>
                </div>
                <div className='flex items-center gap-2'>
                  <Button size='icon' variant='outline' onClick={() => setDelay((d) => d - 100)}>
                    <Minus className='h-4 w-4' />
                  </Button>
                  <Slider
                    value={[delay]}
                    min={-2000}
                    max={2000}
                    step={10}
                    onValueChange={(v) => setDelay(v[0])}
                    onValueCommit={persistDelay}
                    className='flex-1'
                  />
                  <Button size='icon' variant='outline' onClick={() => setDelay((d) => d + 100)}>
                    <Plus className='h-4 w-4' />
                  </Button>
                </div>
                <div className='flex items-center gap-2'>
                  <Input
                    type='number'
                    step={10}
                    value={delay}
                    onChange={(e) => setDelay(Number(e.target.value))}
                    onBlur={persistDelay}
                    className='w-28'
                  />
                  <Button size='sm' variant='ghost' onClick={() => setDelay(0)}>
                    <RotateCcw className='h-4 w-4' /> Reset
                  </Button>
                </div>
                {previewDelta !== 0 && (
                  <p className='text-xs text-amber-300'>
                    Preview offset {previewDelta > 0 ? '+' : ''}
                    {previewDelta} ms — repack to bake it into the file.
                  </p>
                )}
                <Button onClick={repack} disabled={repacking} className='w-full'>
                  {repacking ? <Loader2 className='h-4 w-4 animate-spin' /> : <AudioLines className='h-4 w-4' />}
                  Repack with {delay > 0 ? '+' : ''}
                  {delay} ms
                </Button>
                <p className='text-[11px] text-muted-foreground'>Shortcuts: space = play/pause, ←/→ = seek 10s, [ / ] = ∓10 ms.</p>
              </div>
            </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
