import { useEffect, useMemo, useRef, useState } from 'react';
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
  Clock,
  AlertCircle,
  X,
  RefreshCw,
  Film,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  ZoomIn,
  ZoomOut,
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
  // Library row: transfer state is folded into the status here.
  if (r.transfer_status === 'transferring') {
    const pct = Math.round(r.transfer_percent || 0);
    return (
      <Badge variant='accent' className='gap-1.5' title='Transferring to the media server'>
        <Loader2 className='h-3 w-3 animate-spin' /> Transferring {pct > 0 ? `${pct}%` : ''}
      </Badge>
    );
  }
  if (r.stage === 'transferred' || r.transfer_status === 'done')
    return (
      <Badge variant='success' className='gap-1.5'>
        <CheckCircle2 className='h-3 w-3' /> Done
      </Badge>
    );
  if (r.transfer_status === 'failed')
    return <Badge variant='destructive'>Transfer failed</Badge>;
  if (r.stage === 'approved') return <Badge variant='success'>Approved</Badge>;
  return <Badge variant='warning'>Review</Badge>;
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
            <td colSpan={7} className='px-3 py-2'>
              {r.job_id ? (
                <pre className='ansi-log max-h-72 overflow-auto rounded-md bg-black/50 p-3 text-[11px] leading-relaxed'>
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
                <SortHeader col='when' label='Time' className='text-left' />
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

interface Wave {
  sr: number;
  duration: number;
  peaks: Uint8Array;
}

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
  const [eng, setEng] = useState<Wave | null>(null);
  const [ger, setGer] = useState<Wave | null>(null);
  const [loading, setLoading] = useState(true);
  const [delayMs, setDelayMs] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  const [windowSec, setWindowSec] = useState(20);
  const [center, setCenter] = useState(60);
  const [playing, setPlaying] = useState<'none' | 'both' | 'eng' | 'ger'>('none');
  const [busy, setBusy] = useState<string | null>(null);
  const [canvasW, setCanvasW] = useState(800);

  const wrapRef = useRef<HTMLDivElement>(null);
  const engCanvas = useRef<HTMLCanvasElement>(null);
  const gerCanvas = useRef<HTMLCanvasElement>(null);
  const engAudio = useRef<HTMLAudioElement | null>(null);
  const gerAudio = useRef<HTMLAudioElement | null>(null);
  const dragRef = useRef<{ x: number; delay: number } | null>(null);

  const duration = info?.duration ?? eng?.duration ?? ger?.duration ?? 0;
  const viewStart = Math.max(0, center - windowSec / 2);

  function stopAll() {
    for (const r of [engAudio, gerAudio]) {
      if (r.current) {
        r.current.pause();
        r.current.src = '';
        r.current = null;
      }
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
        setCenter(Math.min(120, (m.duration ?? 240) / 2));
        const tasks: Promise<void>[] = [];
        if (ei != null)
          tasks.push(
            api.waveform(path, ei).then((w) => {
              if (!cancelled) setEng({ sr: w.sr, duration: w.duration, peaks: decodePeaks(w.peaks) });
            }),
          );
        if (gi != null)
          tasks.push(
            api.waveform(path, gi).then((w) => {
              if (!cancelled) setGer({ sr: w.sr, duration: w.duration, peaks: decodePeaks(w.peaks) });
            }),
          );
        await Promise.all(tasks);
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
    const el = wrapRef.current;
    if (!el) return;
    const compute = () => setCanvasW(Math.max(320, el.clientWidth));
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [loading]);

  function drawWave(
    canvas: HTMLCanvasElement | null,
    wave: Wave | null,
    shiftSec: number,
    color: string,
  ) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    // faint grid every second
    ctx.fillStyle = 'rgba(255,255,255,0.06)';
    const pxPerSec = W / windowSec;
    for (let s = Math.ceil(viewStart); s < viewStart + windowSec; s++) {
      const x = (s - viewStart) * pxPerSec;
      ctx.fillRect(x, 0, 1, H);
    }
    if (wave) {
      ctx.fillStyle = color;
      const sr = wave.sr;
      for (let x = 0; x < W; x++) {
        const t = viewStart + (x / W) * windowSec - shiftSec;
        const idx = Math.floor(t * sr);
        let v = 0;
        if (idx >= 0 && idx < wave.peaks.length) v = wave.peaks[idx];
        const h = (v / 127) * (H / 2 - 2);
        ctx.fillRect(x, H / 2 - h, 1, h * 2);
      }
    }
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(0, H / 2, W, 1);
    // playhead at center
    ctx.fillStyle = 'rgba(255,255,255,0.35)';
    ctx.fillRect(W / 2, 0, 1, H);
  }

  useEffect(() => {
    drawWave(engCanvas.current, eng, 0, '#38bdf8');
    drawWave(gerCanvas.current, ger, delayMs / 1000, '#f472b6');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eng, ger, delayMs, windowSec, center, canvasW]);

  function onGerDown(e: React.MouseEvent) {
    dragRef.current = { x: e.clientX, delay: delayMs };
    const onMove = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const pxPerSec = canvasW / windowSec;
      const dxSec = (ev.clientX - d.x) / pxPerSec;
      setDelayMs(Math.round(d.delay + dxSec * 1000));
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  function zoom(factor: number) {
    setWindowSec((w) => Math.min(600, Math.max(1, Math.round(w * factor))));
  }

  function playSection(which: 'both' | 'eng' | 'ger') {
    stopAll();
    const dur = Math.min(windowSec, 30);
    if (which !== 'ger' && engStream != null) {
      const a = new Audio(api.audioClipUrl(path, engStream, viewStart, dur));
      a.onended = () => setPlaying('none');
      engAudio.current = a;
      a.play().catch(() => {});
    }
    if (which !== 'eng' && gerStream != null) {
      const gs = Math.max(0, viewStart - delayMs / 1000);
      const a = new Audio(api.audioClipUrl(path, gerStream, gs, dur));
      a.onended = () => setPlaying('none');
      gerAudio.current = a;
      a.play().catch(() => {});
    }
    setPlaying(which);
  }

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

  return (
    <div className='fixed inset-0 z-50 flex flex-col bg-background'>
      <div className='flex items-center justify-between gap-3 border-b border-border/50 px-4 py-3 md:px-6'>
        <div className='flex min-w-0 items-center gap-2'>
          <Button size='icon' variant='ghost' onClick={onClose} title='Close'>
            <ArrowLeft className='h-5 w-5' />
          </Button>
          <h2 className='truncate text-lg font-semibold'>{entry.title}</h2>
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

      <div className='flex-1 overflow-auto p-4 md:p-6'>
        {loading ? (
          <div className='space-y-3'>
            <Skeleton className='h-24 w-full' />
            <Skeleton className='h-24 w-full' />
          </div>
        ) : (
          <div className='mx-auto max-w-6xl space-y-4'>
            <p className='text-sm text-muted-foreground'>
              The English track (blue) is locked to the HQ picture. Drag the German track (pink) left/right
              until the waveforms line up, or nudge in milliseconds. Play both to hear the match, then approve.
            </p>

            <div ref={wrapRef} className='space-y-2'>
              <div className='flex items-center gap-2 text-xs text-sky-400'>
                <Languages className='h-3.5 w-3.5' /> English (reference)
              </div>
              <canvas ref={engCanvas} width={canvasW} height={90} className='w-full rounded-md bg-black/40' />
              <div className='flex items-center gap-2 text-xs text-pink-400'>
                <AudioLines className='h-3.5 w-3.5' /> German (drag to align)
              </div>
              <canvas
                ref={gerCanvas}
                width={canvasW}
                height={90}
                onMouseDown={onGerDown}
                className='w-full cursor-ew-resize rounded-md bg-black/40'
              />
            </div>

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
