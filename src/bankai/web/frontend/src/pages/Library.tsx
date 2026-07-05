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
import { formatBytes, timeAgo } from '@/lib/utils';
import { cn } from '@/lib/utils';

type SortKey = 'name' | 'date' | 'size' | 'status';

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

type FilterKey = 'all' | 'active' | 'review' | 'approved' | 'transferred' | 'failed';

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Downloading' },
  { key: 'review', label: 'Review' },
  { key: 'approved', label: 'Approved' },
  { key: 'transferred', label: 'On server' },
  { key: 'failed', label: 'Failed' },
];

const PAGE_SIZE = 12;

function rowCategory(r: TitleRow): FilterKey {
  if (r.row_kind === 'job') {
    if (r.pending || r.job_status === 'running') return 'active';
    if (r.job_status === 'failed' || r.job_status === 'error' || r.job_status === 'cancelled')
      return 'failed';
    return 'transferred'; // finished job with no local file (already on server)
  }
  if (r.transfer_status === 'transferring') return 'approved';
  if (r.stage === 'transferred' || r.transfer_status === 'done') return 'transferred';
  if (r.stage === 'approved') return 'approved';
  return 'review';
}

export default function Library() {
  const [rows, setRows] = useState<TitleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>('status');
  const [filter, setFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<FilterKey>('all');
  const [page, setPage] = useState(0);

  const [review, setReview] = useState<TitleRow | null>(null);
  const [del, setDel] = useState<TitleRow | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);

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
    // Poll quietly so download progress + transfer column update live.
    const t = setInterval(() => load(true), 3000);
    return () => clearInterval(t);
  }, []);

  const filtered = useMemo(() => {
    const list = rows.filter(
      (e) =>
        e.title.toLowerCase().includes(filter.toLowerCase()) &&
        (statusFilter === 'all' || rowCategory(e) === statusFilter),
    );
    list.sort((a, b) => {
      if (sort === 'name') return a.title.localeCompare(b.title);
      if (sort === 'size') return (b.size ?? 0) - (a.size ?? 0);
      if (sort === 'status') {
        const d = statusRank(a) - statusRank(b);
        return d !== 0 ? d : (b.mtime ?? 0) - (a.mtime ?? 0);
      }
      return (b.mtime ?? 0) - (a.mtime ?? 0);
    });
    return list;
  }, [rows, filter, sort, statusFilter]);

  // Per-filter counts for the chip labels.
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows.length };
    for (const r of rows) {
      const k = rowCategory(r);
      c[k] = (c[k] ?? 0) + 1;
    }
    return c;
  }, [rows]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = useMemo(
    () => filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [filtered, safePage],
  );

  // Reset to the first page whenever the view changes.
  useEffect(() => {
    setPage(0);
  }, [filter, statusFilter, sort]);

  function toggleSelect(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const selectableEntries = useMemo(
    () =>
      filtered.filter(
        (e) => e.row_kind === 'library' && (e.stage === 'review' || e.stage === 'approved'),
      ),
    [filtered],
  );
  const approvedCount = useMemo(
    () => rows.filter((e) => e.row_kind === 'library' && e.stage === 'approved').length,
    [rows],
  );
  const selectedList = useMemo(
    () => filtered.filter((e) => e.path && selected.has(e.path)),
    [filtered, selected],
  );
  const allSelected =
    selectableEntries.length > 0 && selectableEntries.every((e) => selected.has(e.path!));

  function toggleSelectAll() {
    setSelected((prev) => {
      if (selectableEntries.every((e) => prev.has(e.path!))) return new Set();
      return new Set(selectableEntries.map((e) => e.path!));
    });
  }

  async function approveSelected() {
    const paths = selectedList.filter((e) => e.stage !== 'transferred').map((e) => e.path!);
    if (paths.length === 0) {
      toast.error('Nothing selected to approve');
      return;
    }
    setBatchBusy(true);
    try {
      const r = await api.approveBatch(paths);
      toast.success(`Approved ${r.count} title${r.count === 1 ? '' : 's'}`);
      setSelected(new Set());
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBatchBusy(false);
    }
  }

  async function sendApprovedToServer() {
    setBatchBusy(true);
    try {
      const paths = selectedList.filter((e) => e.stage === 'approved').map((e) => e.path!);
      const r = await api.transferBatch(paths);
      if (r.count === 0) {
        toast.error('No approved titles to send');
      } else {
        toast.success(`Sending ${r.count} title${r.count === 1 ? '' : 's'} to the server`);
      }
      setSelected(new Set());
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBatchBusy(false);
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
        <Badge variant='success' className='gap-1.5' title='On the media server'>
          <CheckCircle2 className='h-3 w-3' /> On server
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
    return <span className='text-xs text-muted-foreground'>Approve first</span>;
  }

  function typeLabel(r: TitleRow): string {
    if (r.kind === 'episode') {
      const parts: string[] = [];
      if (r.series) parts.push(r.series);
      if (r.season != null) parts.push(`S${String(r.season).padStart(2, '0')}`);
      return parts.length ? parts.join(' · ') : 'Episode';
    }
    return 'Movie';
  }

  function RowView({ r }: { r: TitleRow }) {
    const isLib = r.row_kind === 'library';
    const selectable = isLib && (r.stage === 'review' || r.stage === 'approved');
    const isJob = r.row_kind === 'job';
    const canCancel = isJob && (r.pending || r.job_status === 'running');
    const canRetry = isJob && (r.job_status === 'failed' || r.job_status === 'error' || r.job_status === 'cancelled');
    return (
      <tr className='border-t border-border hover:bg-secondary/30'>
        <td className='px-2 py-2 align-middle'>
          <input
            type='checkbox'
            className='h-4 w-4 cursor-pointer accent-primary disabled:opacity-30'
            checked={!!r.path && selected.has(r.path)}
            disabled={!selectable}
            onChange={() => r.path && toggleSelect(r.path)}
            title={selectable ? 'Select' : 'Only approvable titles'}
          />
        </td>
        <td className='max-w-[22rem] px-2 py-2 align-middle'>
          <div className='truncate font-medium'>{r.title}</div>
          <div className='text-xs text-muted-foreground'>
            {isLib ? (
              <>
                {formatBytes(r.size ?? 0)}
                {r.mtime ? <span> · {timeAgo(r.mtime)}</span> : null}
              </>
            ) : (
              <span>{r.mtime ? timeAgo(r.mtime) : 'just now'}</span>
            )}
          </div>
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
        <td className='px-2 py-2 align-middle'>
          <div className='flex items-center justify-end gap-1.5'>
            {isLib && (
              <Button size='sm' variant='secondary' onClick={() => setReview(r)}>
                <Play className='h-4 w-4' /> Review
              </Button>
            )}
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
    <div className='space-y-6'>
      <header className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>Library</h1>
          <p className='text-sm text-muted-foreground'>
            Every title in one table — downloads, review, sync and transfer.
          </p>
        </div>
        <div className='flex items-center gap-2'>
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Filter…' className='w-44' />
          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger className='w-32'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='status'>Status</SelectItem>
              <SelectItem value='date'>Newest</SelectItem>
              <SelectItem value='name'>Name</SelectItem>
              <SelectItem value='size'>Size</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </header>

      <div className='flex flex-wrap items-center gap-1.5'>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setStatusFilter(f.key)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
              statusFilter === f.key
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border text-muted-foreground hover:bg-secondary hover:text-foreground',
            )}
          >
            {f.label}
            <span className='ml-1.5 opacity-70'>{counts[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {!loading && filtered.length > 0 && (
        <div className='flex flex-wrap items-center gap-3 rounded-md border bg-card/40 px-3 py-2'>
          <label className='flex cursor-pointer items-center gap-2 text-sm'>
            <input
              type='checkbox'
              className='h-4 w-4 cursor-pointer accent-primary'
              checked={allSelected}
              onChange={toggleSelectAll}
              disabled={selectableEntries.length === 0}
            />
            Select all
          </label>
          <span className='text-xs text-muted-foreground'>
            {selected.size} selected · {approvedCount} approved
          </span>
          <div className='ml-auto flex items-center gap-2'>
            <Button size='sm' variant='secondary' onClick={approveSelected} disabled={batchBusy || selected.size === 0}>
              {batchBusy ? <Loader2 className='h-4 w-4 animate-spin' /> : <CheckCircle2 className='h-4 w-4' />}
              Approve selected
            </Button>
            <Button size='sm' onClick={sendApprovedToServer} disabled={batchBusy || approvedCount === 0}>
              {batchBusy ? <Loader2 className='h-4 w-4 animate-spin' /> : <Send className='h-4 w-4' />}
              Send approved to server
            </Button>
          </div>
        </div>
      )}

      {loading ? (
        <div className='space-y-2'>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className='h-12' />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState icon={LibraryIcon} title='Nothing here yet' description='Queue a title from Discover or Search — it will appear here.' />
      ) : (
        <div className='overflow-x-auto rounded-lg border'>
          <table className='w-full text-sm'>
            <thead className='bg-card/60 text-xs uppercase tracking-wide text-muted-foreground'>
              <tr>
                <th className='w-8 px-2 py-2' />
                <th className='px-2 py-2 text-left font-medium'>Title</th>
                <th className='px-2 py-2 text-left font-medium'>Type</th>
                <th className='px-2 py-2 text-left font-medium'>Status</th>
                <th className='px-2 py-2 text-left font-medium'>Sync</th>
                <th className='px-2 py-2 text-left font-medium'>Transfer</th>
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

      {!loading && filtered.length > PAGE_SIZE && (
        <div className='flex items-center justify-between gap-3 text-sm'>
          <span className='text-xs text-muted-foreground'>
            {safePage * PAGE_SIZE + 1}–{Math.min(filtered.length, (safePage + 1) * PAGE_SIZE)} of{' '}
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
