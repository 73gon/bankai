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
  Languages,
  AudioLines,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type LibraryEntry, type MediaInfo } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatBytes, timeAgo } from '@/lib/utils';

type SortKey = 'name' | 'date' | 'size';

function stageBadge(stage: string) {
  switch (stage) {
    case 'approved':
      return <Badge variant='success'>Approved</Badge>;
    case 'transferred':
      return <Badge variant='accent'>Transferred</Badge>;
    case 'review':
    default:
      return <Badge variant='warning'>Review</Badge>;
  }
}

export default function Library() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>('date');
  const [filter, setFilter] = useState('');
  const [openSeries, setOpenSeries] = useState<Record<string, boolean>>({});

  const [review, setReview] = useState<LibraryEntry | null>(null);
  const [del, setDel] = useState<LibraryEntry | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await api.library();
      setEntries(r.entries);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    let list = entries.filter((e) => e.name.toLowerCase().includes(filter.toLowerCase()));
    list.sort((a, b) => {
      if (sort === 'name') return a.name.localeCompare(b.name);
      if (sort === 'size') return b.size - a.size;
      return b.mtime - a.mtime;
    });
    return list;
  }, [entries, filter, sort]);

  const movies = filtered.filter((e) => e.kind === 'movie');
  const shows = filtered.filter((e) => e.kind === 'episode');
  const seriesGroups = useMemo(() => {
    const map = new Map<string, LibraryEntry[]>();
    for (const e of shows) {
      const k = e.series || 'Unknown';
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(e);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [shows]);

  function toggleSelect(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const selectableEntries = useMemo(
    () => filtered.filter((e) => e.stage === 'review' || e.stage === 'approved'),
    [filtered],
  );
  const approvedCount = useMemo(() => entries.filter((e) => e.stage === 'approved').length, [entries]);
  const selectedList = useMemo(() => filtered.filter((e) => selected.has(e.path)), [filtered, selected]);
  const allSelected = selectableEntries.length > 0 && selectableEntries.every((e) => selected.has(e.path));

  function toggleSelectAll() {
    setSelected((prev) => {
      if (selectableEntries.every((e) => prev.has(e.path))) return new Set();
      return new Set(selectableEntries.map((e) => e.path));
    });
  }

  async function approveSelected() {
    const paths = selectedList.filter((e) => e.stage !== 'transferred').map((e) => e.path);
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
      // Send the explicitly selected approved items, or all approved when none selected.
      const paths = selectedList.filter((e) => e.stage === 'approved').map((e) => e.path);
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

  function Row({ e }: { e: LibraryEntry }) {
    const selectable = e.stage === 'review' || e.stage === 'approved';
    return (
      <div className='flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-secondary/40'>
        <div className='flex min-w-0 items-center gap-3'>
          <input
            type='checkbox'
            className='h-4 w-4 shrink-0 cursor-pointer accent-primary disabled:opacity-30'
            checked={selected.has(e.path)}
            disabled={!selectable}
            onChange={() => toggleSelect(e.path)}
            title={selectable ? 'Select' : 'Already transferred'}
          />
          <div className='min-w-0'>
            <div className='truncate text-sm font-medium'>{e.name}</div>
            <div className='flex items-center gap-2 text-xs text-muted-foreground'>
              <span>{formatBytes(e.size)}</span>
              <span>· {timeAgo(e.mtime)}</span>
            </div>
          </div>
        </div>
        <div className='flex items-center gap-2'>
          {stageBadge(e.stage)}
          <Button size='sm' variant='secondary' onClick={() => setReview(e)}>
            <Play className='h-4 w-4' /> Review
          </Button>
          <Button size='icon' variant='ghost' onClick={() => setDel(e)} title='Delete'>
            <Trash2 className='h-4 w-4 text-red-400' />
          </Button>
        </div>
      </div>
    );
  }

  async function doDelete() {
    if (!del) return;
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
          <p className='text-sm text-muted-foreground'>Review, QC and approve merged titles.</p>
        </div>
        <div className='flex items-center gap-2'>
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Filter…' className='w-44' />
          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger className='w-32'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='date'>Newest</SelectItem>
              <SelectItem value='name'>Name</SelectItem>
              <SelectItem value='size'>Size</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </header>

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
            <Skeleton key={i} className='h-14' />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState icon={LibraryIcon} title='Library is empty' description='Merged titles will appear here for review.' />
      ) : (
        <div className='space-y-6'>
          {movies.length > 0 && (
            <Card>
              <CardContent className='p-3'>
                <div className='mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground'>Movies</div>
                {movies.map((e) => (
                  <Row key={e.path} e={e} />
                ))}
              </CardContent>
            </Card>
          )}

          {seriesGroups.map(([series, eps]) => {
            const open = openSeries[series] ?? true;
            return (
              <Card key={series}>
                <CardContent className='p-3'>
                  <button
                    className='flex w-full items-center gap-2 px-2 py-1 text-left'
                    onClick={() => setOpenSeries((s) => ({ ...s, [series]: !open }))}
                  >
                    {open ? <ChevronDown className='h-4 w-4' /> : <ChevronRight className='h-4 w-4' />}
                    <span className='font-semibold'>{series}</span>
                    <Badge variant='muted' className='ml-1'>
                      {eps.length}
                    </Badge>
                  </button>
                  {open && eps.sort((a, b) => a.name.localeCompare(b.name)).map((e) => <Row key={e.path} e={e} />)}
                </CardContent>
              </Card>
            );
          })}
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
      {review && (
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

function ReviewStudio({ entry, onClose }: { entry: LibraryEntry; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [delay, setDelay] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  const [audioIdx, setAudioIdx] = useState(0);
  const [useTranscode, setUseTranscode] = useState(false);
  const [repacking, setRepacking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function loadInfo() {
    setLoading(true);
    try {
      const m = await api.mediaInfo(entry.path);
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
      const r = await api.repack(entry.path, delay);
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
      await api.setDelay(entry.path, delay);
      setSavedDelay(delay);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function approve() {
    setBusy('approve');
    try {
      await api.approve(entry.path);
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
      await api.transfer(entry.path);
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
                path={entry.path}
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
