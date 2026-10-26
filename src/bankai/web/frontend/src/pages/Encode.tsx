import { useEffect, useMemo, useState } from 'react';
import { ChevronRight, Clock, Loader2, Shrink, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type EncodeCandidate, type EncodeOverview, type EncodeScope, type TrackScopeName } from '@/lib/api';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Progress } from '@/zon/ui/progress';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';

const SCOPES: Array<{ key: TrackScopeName; label: string }> = [
  { key: 'anime', label: 'Anime' },
  { key: 'shows', label: 'Shows' },
  { key: 'movies', label: 'Movies' },
];

const STEPS: Record<string, string> = {
  encode: 'Encoding the picture',
  dolby_vision: 'Adding Dolby Vision',
  mux: 'Putting the tracks together',
  check: 'Checking against the original',
};

function formatBytes(value: number | null | undefined) {
  if (!value || !Number.isFinite(value) || value <= 0) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() ?? path;
}

function duration(seconds: number) {
  const minutes = Math.max(0, Math.round(seconds / 60));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const CODECS: Record<string, string> = { hevc: 'HEVC', h264: 'H.264', vc1: 'VC-1', mpeg2video: 'MPEG-2', mpeg4: 'MPEG-4' };

function codecName(codec: string | null | undefined) {
  return CODECS[codec ?? ''] ?? (codec ? codec.toUpperCase() : 'HEVC');
}

type SortKey = 'name' | 'size' | 'mbps' | 'source' | 'codec' | 'estimate' | 'status';
// Names read A-Z; sizes, bitrates and savings are most telling at their largest.
const FIRST: Record<SortKey, SortDir> = { name: 'asc', size: 'desc', mbps: 'desc', source: 'asc', codec: 'asc', estimate: 'desc', status: 'asc' };

function sortValue(item: EncodeCandidate, key: SortKey, current: string | null): string | number {
  switch (key) {
    case 'name':
      return fileName(item.path).toLowerCase();
    case 'size':
      return item.size;
    case 'mbps':
      return item.mbps;
    case 'source':
      return codecName(item.source);
    case 'codec':
      return codecName(item.codec);
    case 'estimate':
      return item.estimate;
    case 'status':
      // Encoding, then the queue in its order, then failed, then the rest.
      return item.path === current ? 0 : item.queued ? item.queued : item.failed ? 1e6 : 2e6;
  }
}

function compare(a: string | number, b: string | number, dir: SortDir) {
  const order = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
  return dir === 'asc' ? order : -order;
}

/** A checkbox inside a table: a real button, so the click lands. */
function Tick({ checked, indeterminate, disabled, label, onChange }: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  label: string;
  onChange: (on: boolean) => void;
}) {
  return (
    <Checkbox
      nativeButton
      render={<button type='button' />}
      className='align-middle'
      aria-label={label}
      checked={checked}
      indeterminate={indeterminate}
      disabled={disabled}
      onCheckedChange={(on) => onChange(on)}
    />
  );
}

/** What is being encoded, what waits in the queue, what was done. */
function QueueCard({ data, onChanged }: { data: EncodeOverview; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const { current } = data;
  const waiting = data.queue.filter((row) => row.path !== current?.path);

  async function remove(paths: string[] | null) {
    setBusy(true);
    try {
      const result = await api.dequeueEncode(paths);
      toast.success(result.removed + ' file' + (result.removed === 1 ? '' : 's') + ' taken out of the queue');
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader className='flex flex-row flex-wrap items-start justify-between gap-3'>
        <div className='flex min-w-0 flex-col gap-1'>
          <CardTitle className='text-lg'>Queue</CardTitle>
          <CardDescription>Encoded one at a time, in this order, on the graphics card.</CardDescription>
        </div>
        {waiting.length > 0 && (
          <Button size='sm' variant='secondary' onClick={() => void remove(null)} disabled={busy}>Clear the queue</Button>
        )}
      </CardHeader>
      <CardContent className='flex flex-col gap-3 text-sm'>
        {current && (
          <div className='flex flex-col gap-1.5 rounded-control border border-field-edge p-3'>
            <p className='flex flex-wrap items-baseline gap-x-2'>
              <span className='truncate font-mono text-xs text-foreground'>{fileName(current.path)}</span>
              <span className='text-xs text-muted-foreground'>{formatBytes(current.size)} · as {codecName(current.codec)}</span>
            </p>
            <Progress
              value={current.step === 'encode' && current.fraction != null ? Math.round(current.fraction * 100) : null}
              label={STEPS[current.step] ?? 'Encoding'}
              showValue={current.step === 'encode' && current.fraction != null}
            />
            <p className='text-xs text-muted-foreground'>
              Started {duration(Date.now() / 1000 - current.started)} ago{current.eta != null ? `, about ${duration(current.eta)} left for the picture` : ''}.
            </p>
          </div>
        )}
        {waiting.length ? (
          <ol className='flex flex-col divide-y divide-border/60 rounded-control border border-field-edge'>
            {waiting.map((row, index) => (
              <li key={row.path} className='flex items-center gap-3 px-3 py-1.5'>
                <span className='w-6 shrink-0 font-mono text-xs tabular-nums text-muted-foreground'>{index + 1}</span>
                <div className='min-w-0 flex-1'>
                  <p className='truncate font-mono text-xs text-foreground' title={row.path}>{fileName(row.path)}</p>
                  {row.waiting && <p className='flex items-center gap-1 text-[0.68rem] text-muted-foreground'><Clock className='size-3' aria-hidden='true' /> Waits: {row.waiting}</p>}
                </div>
                <span className='shrink-0 font-mono text-xs tabular-nums text-muted-foreground'>{formatBytes(row.size)}</span>
                <span className='hidden shrink-0 font-mono text-xs tabular-nums sm:inline' title='Frees about'>−{formatBytes(row.estimate)}</span>
                <Button size='icon-sm' variant='ghost' aria-label={'Take ' + fileName(row.path) + ' out of the queue'} onClick={() => void remove([row.path])} disabled={busy}>
                  <X />
                </Button>
              </li>
            ))}
          </ol>
        ) : !current && <p className='text-muted-foreground'>Nothing queued. Tick files below and press Encode.</p>}
        {data.done.files > 0 && (
          <p>
            Done: <b>{data.done.files.toLocaleString('en-US')}</b> file{data.done.files === 1 ? '' : 's'}, <b>{formatBytes(data.done.bytes)}</b> freed.
            {data.last && (
              <span className='text-muted-foreground'>
                {' '}Last: {fileName(data.last.path)}, {formatBytes(data.last.before)} → {formatBytes(data.last.after)}, SSIM {data.last.ssim.toFixed(3)}.
              </span>
            )}
          </p>
        )}
        {data.failed.length > 0 && (
          <details className='text-xs'>
            <summary className='cursor-pointer text-warning'>{data.failed.length} file{data.failed.length === 1 ? '' : 's'} failed; they can be queued again below</summary>
            <ul className='mt-1.5 flex flex-col gap-1'>
              {data.failed.map((row) => (
                <li key={row.path} className='break-all font-mono text-[0.68rem] text-muted-foreground'>{fileName(row.path)}: {row.error}</li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function Status({ row, current }: { row: EncodeCandidate; current: string | null }) {
  if (row.path === current) return <StatusTag variant='info'>Encoding</StatusTag>;
  if (row.queued) return <StatusTag variant='secondary'>Queued #{row.queued}</StatusTag>;
  if (row.failed) return <span title={row.failed}><StatusTag variant='warning'>Failed</StatusTag></span>;
  return null;
}

/** One library's files worth encoding, ticked to be queued; shows by show, films as a list. */
function ScopePanel({ library, scope, current, onQueued }: {
  library: TrackScopeName;
  scope: EncodeScope;
  current: string | null;
  onQueued: () => void;
}) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [sort, setSort] = useState<SortState<SortKey>>({ key: 'name', dir: 'asc' });
  const free = useMemo(() => scope.candidates.filter((row) => !row.queued && row.path !== current), [scope.candidates, current]);
  const sorted = useMemo(
    () => [...scope.candidates].sort((a, b) => compare(sortValue(a, sort.key, current), sortValue(b, sort.key, current), sort.dir)),
    [scope.candidates, sort, current],
  );
  const groups = useMemo(() => {
    const out = new Map<string, EncodeCandidate[]>();
    for (const row of sorted) out.set(row.group, [...(out.get(row.group) ?? []), row]);
    // A show sorts by its files together: their size and saving summed, else its first file's place.
    const total = (items: EncodeCandidate[], key: 'size' | 'estimate') => items.reduce((sum, item) => sum + item[key], 0);
    return [...out.entries()].sort(([nameA, a], [nameB, b]) => {
      if (sort.key === 'size' || sort.key === 'estimate') return compare(total(a, sort.key), total(b, sort.key), sort.dir);
      if (sort.key === 'name') return compare(nameA.toLowerCase(), nameB.toLowerCase(), sort.dir);
      return compare(sortValue(a[0], sort.key, current), sortValue(b[0], sort.key, current), sort.dir);
    });
  }, [sorted, sort, current]);
  const onSort = (key: SortKey) => setSort((before) => nextSort(before, key, FIRST));
  const flat = library === 'movies' || groups.every(([group]) => !group);
  const chosen = free.filter((row) => picked.has(row.path));

  // Ticks on files queued meanwhile, or no longer listed, fall away.
  useEffect(() => {
    const ids = new Set(free.map((row) => row.path));
    setPicked((before) => new Set([...before].filter((path) => ids.has(path))));
  }, [free]);

  function tick(paths: string[], on: boolean) {
    setPicked((before) => {
      const next = new Set(before);
      for (const path of paths) {
        if (on) next.add(path);
        else next.delete(path);
      }
      return next;
    });
  }

  async function encode() {
    if (!chosen.length) return;
    setBusy(true);
    try {
      const result = await api.enqueueEncode(chosen.map((row) => row.path));
      toast.success(result.added + ' file' + (result.added === 1 ? '' : 's') + ' queued' + (result.refused.length ? `; ${result.refused.length} no longer worth it` : ''));
      setPicked(new Set());
      onQueued();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  const row = (item: EncodeCandidate, nested = false) => {
    const takeable = !item.queued && item.path !== current;
    return (
      <TableRow key={item.path} data-selected={picked.has(item.path) ? '' : undefined}>
        <TableCell className='w-7 pr-2'>
          <Tick checked={picked.has(item.path)} disabled={!takeable} label={'Encode ' + fileName(item.path)} onChange={(on) => tick([item.path], on)} />
        </TableCell>
        <TableCell className='max-w-0'>
          <p className={cn('truncate font-mono text-xs', nested && 'pl-5')} title={item.path}>{fileName(item.path)}</p>
        </TableCell>
        <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(item.size)}</TableCell>
        <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground'>{item.mbps} Mbps</TableCell>
        <TableCell numeric className='whitespace-nowrap text-xs text-muted-foreground'>{codecName(item.source)}</TableCell>
        <TableCell numeric className={cn('whitespace-nowrap text-xs', item.source && item.source !== item.codec && 'text-foreground font-medium')}>{codecName(item.codec)}</TableCell>
        <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(item.estimate)}</TableCell>
        <TableCell numeric><Status row={item} current={current} /></TableCell>
      </TableRow>
    );
  };

  const left = Object.entries(scope.left);
  const allFree = free.map((item) => item.path);
  const allPicked = allFree.length > 0 && allFree.every((path) => picked.has(path));

  return (
    <div className='flex flex-col gap-3'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <p className='text-sm'>
          {scope.read < scope.files ? (
            <span className='text-muted-foreground'>
              <Loader2 className='mr-1.5 inline size-3.5 animate-spin' aria-hidden='true' />
              Reading the files: {scope.read.toLocaleString('en-US')} of {scope.files.toLocaleString('en-US')}; the list fills as it goes.
            </span>
          ) : scope.totals.files ? (
            <><b>{scope.totals.files.toLocaleString('en-US')}</b> file{scope.totals.files === 1 ? '' : 's'} worth encoding: <b>{formatBytes(scope.totals.bytes)}</b> now, about <b>{formatBytes(scope.totals.saving)}</b> to free.</>
          ) : <span className='text-muted-foreground'>No file here is far bigger than it needs to be.</span>}
        </p>
        <Button onClick={() => void encode()} disabled={busy || !chosen.length}>
          <Shrink data-icon='inline-start' />
          {chosen.length
            ? `Encode ${chosen.length} file${chosen.length === 1 ? '' : 's'} · frees about ${formatBytes(chosen.reduce((sum, item) => sum + item.estimate, 0))}`
            : 'Encode'}
        </Button>
      </div>
      {scope.candidates.length > 0 && (
        <Card className='gap-0 overflow-hidden [--card-padding:0]'>
          <Table variant='plain' dense containerProps={{ className: 'max-h-[65dvh] overflow-auto' }}>
            <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
              <TableRow>
                <TableHead className='w-7 pr-2'>
                  <Tick
                    checked={allPicked}
                    indeterminate={!allPicked && allFree.some((path) => picked.has(path))}
                    disabled={!allFree.length}
                    label='Tick every file'
                    onChange={(on) => tick(allFree, on)}
                  />
                </TableHead>
                <SortHeader label={flat ? 'File' : 'Show / file'} column='name' sort={sort} onSort={onSort} className='w-full' />
                <SortHeader label='Size' column='size' sort={sort} onSort={onSort} align='right' />
                <SortHeader label='Picture' column='mbps' sort={sort} onSort={onSort} align='right' />
                <SortHeader label='Now' column='source' sort={sort} onSort={onSort} align='right' />
                <SortHeader label='As' column='codec' sort={sort} onSort={onSort} align='right' />
                <SortHeader label='Frees about' column='estimate' sort={sort} onSort={onSort} align='right' />
                <SortHeader label='Status' column='status' sort={sort} onSort={onSort} align='right' />
              </TableRow>
            </TableHeader>
            <TableBody>
              {flat ? sorted.map((item) => row(item)) : groups.map(([group, items]) => {
                const paths = items.filter((item) => !item.queued && item.path !== current).map((item) => item.path);
                const ticked = paths.filter((path) => picked.has(path)).length;
                const expanded = open.has(group);
                const queued = items.filter((item) => item.queued).length;
                return [
                  <TableRow key={'group:' + group} className='bg-control-surface/40'>
                    <TableCell className='w-7 pr-2'>
                      <Tick
                        checked={paths.length > 0 && ticked === paths.length}
                        indeterminate={ticked > 0 && ticked < paths.length}
                        disabled={!paths.length}
                        label={'Encode every file of ' + group}
                        onChange={(on) => tick(paths, on)}
                      />
                    </TableCell>
                    <TableCell className='max-w-0'>
                      <button
                        type='button'
                        className='flex w-full min-w-0 cursor-pointer items-center gap-1.5 text-left focus-ring'
                        aria-expanded={expanded}
                        onClick={() => setOpen((before) => {
                          const next = new Set(before);
                          if (next.has(group)) next.delete(group);
                          else next.add(group);
                          return next;
                        })}
                      >
                        <ChevronRight className={cn('size-3.5 shrink-0 text-muted-foreground transition-[rotate]', expanded && 'rotate-90')} aria-hidden='true' />
                        <span className='truncate font-medium text-foreground'>{group || 'Other'}</span>
                        <span className='shrink-0 text-xs text-muted-foreground'>{items.length} file{items.length === 1 ? '' : 's'}</span>
                      </button>
                    </TableCell>
                    <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(items.reduce((sum, item) => sum + item.size, 0))}</TableCell>
                    <TableCell numeric />
                    <TableCell numeric />
                    <TableCell numeric />
                    <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(items.reduce((sum, item) => sum + item.estimate, 0))}</TableCell>
                    <TableCell numeric>{queued > 0 && <StatusTag variant='secondary'>{queued} queued</StatusTag>}</TableCell>
                  </TableRow>,
                  ...(expanded ? items.map((item) => row(item, true)) : []),
                ];
              })}
            </TableBody>
          </Table>
        </Card>
      )}
      {left.length > 0 && <p className='text-xs text-muted-foreground'>Left out: {left.map(([reason, value]) => `${value} ${reason}`).join(' · ')}</p>}
    </div>
  );
}

/** Videos far bigger than anyone can see: ticked, queued, and encoded again one at a time. */
export default function Encode() {
  const [data, setData] = useState<EncodeOverview | null>(null);
  const [tab, setTab] = useState<TrackScopeName>('shows');

  async function load() {
    try {
      setData(await api.encode());
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>Encode</h1>
        <p className='text-sm text-muted-foreground'>
          Videos far bigger than anyone can see, encoded again on the graphics card: about half the size, the same picture, and they play
          wherever the originals did. HEVC stays HEVC, H.264 stays H.264; every audio track, subtitle, HDR10 and Dolby Vision is kept, and
          each file replaces its original only once it is checked.
        </p>
      </div>
      {data === null ? <Skeleton className='h-96 w-full' /> : (
        <>
          <QueueCard data={data} onChanged={() => void load()} />
          <Tabs value={tab} onValueChange={(value) => setTab(value as TrackScopeName)} className='gap-4'>
            <TabsList>
              {SCOPES.map((item) => {
                const scope = data.scopes[item.key];
                return (
                  <TabsTab key={item.key} value={item.key}>
                    {item.label}
                    {scope.totals.files > 0 && <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{scope.totals.files}</span>}
                  </TabsTab>
                );
              })}
            </TabsList>
            {SCOPES.map((item) => (
              <TabsPanel key={item.key} value={item.key}>
                <ScopePanel library={item.key} scope={data.scopes[item.key]} current={data.current?.path ?? null} onQueued={() => void load()} />
              </TabsPanel>
            ))}
          </Tabs>
          <p className='text-xs text-muted-foreground'>
            Listed: pictures of 30 Mbps and more in 4K, 22 Mbps for 1080p H.264, 15 Mbps for other 1080p, 10 Mbps below; in anime, every
            episode that is not HEVC, to become HEVC. Left out: Dolby Vision profile 5, interlaced pictures, and AV1, which is smaller than HEVC
            already. A queued file still seeding or changed in the last hour waits its turn; a result less than a fifth
            smaller is thrown away and the original kept. The Dashboard's Encoding lane shows the file being encoded.
          </p>
        </>
      )}
    </div>
  );
}
