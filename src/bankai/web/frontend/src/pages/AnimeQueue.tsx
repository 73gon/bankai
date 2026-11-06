import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, CircleStop, Play, RefreshCw, RotateCcw, Search, Trash2, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { animeQueuePath, api, recall, type AnimeQueuePage, type Job } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/zon/ui/alert-dialog';
import { Button } from '@/zon/ui/button';
import { Checkbox } from '@/zon/ui/checkbox';
import { Empty, EmptyActions, EmptyDescription, EmptyTitle } from '@/zon/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/zon/ui/input-group';
import { Spinner } from '@/zon/ui/spinner';
import { Switch } from '@/zon/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';
import { AnimePoster } from '@/components/AnimePoster';
import { formatBytes } from '@/lib/utils';
import { cn } from '@/zon/lib/cn';

function formatTime(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}

function phaseVariant(phase: string) {
  if (phase === 'done') return 'success' as const;
  if (phase === 'failed' || phase === 'cancelled') return 'destructive' as const;
  if (phase === 'stopped' || phase === 'queued') return 'warning' as const;
  if (phase === 'downloading') return 'torrent' as const;
  if (phase === 'complete') return 'starting' as const;
  if (phase === 'transferring') return 'transfer' as const;
  if (phase === 'deleting') return 'repack' as const;
  return 'info' as const;
}

/** A checkbox inside a table: a real button, so the click lands. */
function Tick({ checked, indeterminate, label, onChange }: {
  checked: boolean;
  indeterminate?: boolean;
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
      onCheckedChange={(on) => onChange(on)}
    />
  );
}

function titleCase(value: string) {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

/** One bar, showing whichever number is meaningful right now.
 *
 * Once the copy into the library starts, overall progress stops telling you
 * anything useful -- it is pinned near the end of the last stage -- so the bar
 * hands over to the transfer's own byte progress. The label says which number
 * it is, since the colour alone should not have to carry that.
 */
/** "4 min", "2 h 10 min": a download's time left. */
function eta(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}

function JobProgress({ job }: { job: Job }) {
  const transferring =
    job.phase === 'transferring' && job.transfer_percent !== null && job.transfer_percent !== undefined;
  const value = (transferring ? job.transfer_percent : job.overall_percent) ?? 0;
  const percent = Math.max(0, Math.min(100, value));
  return (
    <div className='flex min-w-36 flex-col gap-2'>
      <span className='flex items-center gap-1.5 whitespace-nowrap font-mono text-xs tabular-nums'>
        {transferring && <span className='text-transfer'>Transfer</span>}
        <span>{Math.round(percent)}%</span>
        {/* Speed and time left while downloading, as qBittorrent's tab shows them. */}
        {!transferring && job.download_speed ? (
          <span className='text-muted-foreground'>
            {formatBytes(job.download_speed)}/s{job.download_eta && job.download_eta < 8_640_000 ? ` · ${eta(job.download_eta)}` : ''}
          </span>
        ) : null}
      </span>
      <div className='h-1.5 overflow-hidden rounded-full bg-secondary'>
        <div
          className={cn(
            'h-full rounded-full transition-[width,background-color]',
            transferring ? 'bg-transfer' : 'bg-primary',
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

// The release lifecycle, in the order work actually flows through it, so the
// chips never reshuffle under the pointer as counts change. These are the same
// states the reconciler tracks; the queue should not invent its own vocabulary.
type QueueSortKey = 'title' | 'status' | 'progress' | 'updated';

// Text reads A-Z; a number or a time is most interesting at its largest.
const QUEUE_FIRST_DIRECTION: Record<QueueSortKey, SortDir> = {
  title: 'asc', status: 'asc', progress: 'desc', updated: 'desc',
};

const STATUS_ORDER = [
  'queued',
  'downloading',
  'complete',
  'transferring',
  'deleting',
  'done',
  // Off the happy path.
  'running',
  'stopped',
  'failed',
  'cancelled',
];

// The first view -- page one, nothing filtered -- as the page opens with it.
const firstView = () => recall<AnimeQueuePage>(animeQueuePath(0, 100, false, { q: '', status: 'all' }));

export default function AnimeQueue() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [jobs, setJobs] = useState<Job[]>(() => firstView()?.jobs ?? []);
  const [total, setTotal] = useState(() => firstView()?.total ?? 0);
  const [counts, setCounts] = useState<Record<string, number>>(() => firstView()?.counts ?? {});
  const [page, setPage] = useState(0);
  const [showCompleted, setShowCompleted] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<SortState<QueueSortKey> | null>(null);
  const [loading, setLoading] = useState(() => firstView() === undefined);
  const [busy, setBusy] = useState<string | null>(null);
  // Ticked jobs, by id: only those on the page as it is now count.
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const pageSize = 100;

  // Typing must not fire a request per keystroke; the queue snapshot is a
  // filesystem walk on the server.
  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  // Any filter change invalidates the current offset.
  useEffect(() => {
    setPage(0);
  }, [search, status, showCompleted]);

  // Another view, other rows: ticks never carry over to jobs out of sight.
  useEffect(() => {
    setPicked(new Set());
  }, [search, status, showCompleted, page, sort]);

  // A job that left the page (finished, removed elsewhere) is no longer ticked.
  useEffect(() => {
    setPicked((current) => {
      const shown = new Set(jobs.map((job) => job.id));
      const kept = [...current].filter((id) => shown.has(id));
      return kept.length === current.size ? current : new Set(kept);
    });
  }, [jobs]);

  // Hiding completed jobs while filtered to them would leave an empty table
  // with no visible reason why.
  useEffect(() => {
    if (!showCompleted && status === 'done') setStatus('all');
  }, [showCompleted, status]);

  function toggleSort(key: QueueSortKey) {
    setSort((current) => nextSort(current, key, QUEUE_FIRST_DIRECTION));
    // A different order means a different first page.
    setPage(0);
  }

  const load = useCallback(async (silent = false) => {
    const filters = { q: search, status, sort: sort?.key, dir: sort?.dir };
    if (!silent) {
      // A view seen before shows at once; only an unseen one waits behind a spinner.
      const held = recall<AnimeQueuePage>(animeQueuePath(page, pageSize, showCompleted, filters));
      if (held) {
        setJobs(held.jobs);
        setTotal(held.total);
        setCounts(held.counts ?? {});
      } else {
        setLoading(true);
      }
    }
    try {
      const result = await api.animeQueue(page, pageSize, showCompleted, filters);
      setJobs(result.jobs);
      setTotal(result.total);
      setCounts(result.counts ?? {});
    } catch (error: any) {
      if (!silent) toast.error(error.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [page, showCompleted, search, status, sort]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async (first = false) => {
      await load(!first);
      if (!cancelled) timer = window.setTimeout(() => void poll(), 5000);
    };
    void poll(true);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [load]);

  const chips = useMemo(() => {
    const matched = Object.values(counts).reduce((sum, value) => sum + value, 0);
    const visible = STATUS_ORDER.filter((name) => counts[name]).map((name) => ({
      value: name,
      label: titleCase(name),
      count: counts[name],
    }));
    // Keep the active chip mounted even once its count drops to zero, so the
    // control the user just pressed cannot vanish from under the pointer.
    if (status !== 'all' && !visible.some((chip) => chip.value === status)) {
      visible.push({ value: status, label: titleCase(status), count: 0 });
    }
    return [{ value: 'all', label: 'All', count: matched }, ...visible];
  }, [counts, status]);

  const filtered = search.trim() !== '' || status !== 'all';

  function clearFilters() {
    setQuery('');
    setSearch('');
    setStatus('all');
  }

  async function act(job: Job, action: 'stop' | 'continue' | 'retry' | 'delete') {
    setBusy(job.id);
    try {
      if (action === 'stop') await api.stopJob(job.id);
      if (action === 'continue') await api.continueJob(job.id);
      if (action === 'retry') await api.retryJob(job.id);
      if (action === 'delete') await api.deleteJob(job.id);
      await load(true);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  const pickedJobs = jobs.filter((job) => picked.has(job.id));
  const pickedRunning = pickedJobs.filter((job) => job.status === 'running').length;
  const allPicked = jobs.length > 0 && pickedJobs.length === jobs.length;

  function pick(id: string, on: boolean) {
    setPicked((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  async function deletePicked() {
    const targets = pickedJobs;
    setDeleting(true);
    const failed: string[] = [];
    // A few at a time: each delete may stop a process or remove a torrent.
    for (let start = 0; start < targets.length; start += 4) {
      const results = await Promise.allSettled(targets.slice(start, start + 4).map((job) => api.deleteJob(job.id)));
      results.forEach((result, index) => {
        if (result.status === 'rejected') failed.push(targets[start + index].title);
      });
    }
    const removed = targets.length - failed.length;
    if (removed) toast.success(`${removed} job${removed === 1 ? '' : 's'} deleted`);
    if (failed.length) toast.error(`${failed.length} could not be deleted: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}`);
    setPicked(new Set());
    setConfirmDelete(false);
    setDeleting(false);
    await load(true);
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div className='flex min-w-0 items-baseline gap-3'>
          <h1 className='font-heading text-3xl font-light'>Queue</h1>
          <p className='truncate text-sm text-muted-foreground'>Erai-raws and hand-picked anime downloads</p>
        </div>
        <div className='flex items-center gap-3'>
          <label className='flex items-center gap-2 whitespace-nowrap text-sm text-foreground'>
            <Switch
              checked={showCompleted}
              onCheckedChange={setShowCompleted}
              aria-label='Show completed Anime downloads'
            />
            Show completed
          </label>
          <Button variant='secondary' onClick={() => void load()} disabled={loading}>
            <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} />
            Refresh
          </Button>
        </div>
      </div>

      <div className='flex flex-wrap items-center gap-3'>
        <InputGroup className='min-w-64 flex-1 sm:max-w-sm'>
          <InputGroupAddon>
            <Search aria-hidden='true' />
          </InputGroupAddon>
          <InputGroupInput
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && query) {
                event.preventDefault();
                setQuery('');
              }
            }}
            placeholder='Filter by title…'
            aria-label='Filter Anime downloads by title'
          />
          {query && (
            <InputGroupAddon align='inline-end'>
              <InputGroupButton
                variant='ghost'
                size='icon-sm'
                aria-label='Clear title filter'
                onClick={() => {
                  setQuery('');
                  searchRef.current?.focus();
                }}
              >
                <X className='size-3.5' />
              </InputGroupButton>
            </InputGroupAddon>
          )}
        </InputGroup>
        <ToggleGroup
          aria-label='Filter by status'
          className='flex-wrap'
          value={[status]}
          onValueChange={(values) => values[0] && setStatus(values[0])}
        >
          {chips.map((chip) => (
            <ToggleGroupItem key={chip.value} value={chip.value} aria-label={chip.label}>
              <span>{chip.label}</span>
              <span className='font-mono text-[0.65rem] tabular-nums opacity-70'>{chip.count}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      <div className='min-h-0 flex-1'>
        <div className='min-w-full'>
          {loading && jobs.length === 0 ? (
            <div className='flex min-h-40 items-center justify-center'><Spinner label='Loading the Anime queue' /></div>
          ) : jobs.length === 0 ? (
            <Empty>
              <EmptyTitle>{filtered ? 'No matching Anime downloads' : showCompleted ? 'Anime queue is empty' : 'No unfinished Anime downloads'}</EmptyTitle>
              <EmptyDescription>
                {filtered
                  ? 'No job matches this title and status combination.'
                  : showCompleted
                    ? 'Automatic and manual Erai downloads will appear here.'
                    : 'Completed downloads are hidden by default.'}
              </EmptyDescription>
              {filtered && (
                <EmptyActions>
                  <Button variant='secondary' onClick={clearFilters}>Clear filters</Button>
                </EmptyActions>
              )}
            </Empty>
          ) : (
            <Table variant='plain' className='min-w-[900px] table-fixed'>
              <colgroup>
                <col className='w-10' />
                <col />
                <col className='w-28' />
                <col className='w-80' />
                <col className='w-32' />
                <col className='w-24' />
              </colgroup>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <Tick
                      label='Select every job on this page'
                      checked={allPicked}
                      indeterminate={pickedJobs.length > 0 && !allPicked}
                      onChange={(on) => setPicked(on ? new Set(jobs.map((job) => job.id)) : new Set())}
                    />
                  </TableHead>
                  <SortHeader label='Title' column='title' sort={sort} onSort={toggleSort} />
                  <SortHeader label='Status' column='status' sort={sort} onSort={toggleSort} />
                  <SortHeader label='Progress' column='progress' sort={sort} onSort={toggleSort} />
                  <SortHeader label='Updated' column='updated' sort={sort} onSort={toggleSort} />
                  {/* Actions are buttons, not a value, so there is nothing to order by. */}
                  <TableHead numeric>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((job) => (
                  <TableRow key={job.id} className='data-row' data-selected={picked.has(job.id) ? '' : undefined}>
                    <TableCell>
                      <Tick label={'Select ' + job.title} checked={picked.has(job.id)} onChange={(on) => pick(job.id, on)} />
                    </TableCell>
                    <TableCell>
                      <div className='flex items-center gap-2.5'>
                        <AnimePoster url={job.poster_url} title={job.series_title || job.title} className='w-8 shrink-0 rounded' />
                        <div className='min-w-0'>
                          <p className='truncate font-medium text-foreground' title={job.title}>{job.title}</p>
                          <p className='truncate text-xs text-muted-foreground' title={job.reason || job.step_label || job.kind}>{job.reason || job.step_label || job.kind}</p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusTag variant={phaseVariant(job.phase || job.status)}>{titleCase(job.phase || job.status)}</StatusTag>
                    </TableCell>
                    <TableCell><JobProgress job={job} /></TableCell>
                    <TableCell className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatTime(job.updated_at)}</TableCell>
                    <TableCell>
                      <div className='flex justify-end gap-1'>
                        {job.status === 'running' && (
                          <Button size='icon-sm' variant='ghost' aria-label={'Stop ' + job.title} onClick={() => void act(job, 'stop')} disabled={busy === job.id}><CircleStop /></Button>
                        )}
                        {job.status === 'stopped' && (
                          <Button size='icon-sm' variant='ghost' aria-label={'Continue ' + job.title} onClick={() => void act(job, 'continue')} disabled={busy === job.id}><Play /></Button>
                        )}
                        {job.status === 'failed' && (
                          <Button size='icon-sm' variant='ghost' aria-label={'Retry ' + job.title} onClick={() => void act(job, 'retry')} disabled={busy === job.id}><RotateCcw /></Button>
                        )}
                        <Button size='icon-sm' variant='ghost' aria-label={'Remove ' + job.title} onClick={() => void act(job, 'delete')} disabled={busy === job.id}><Trash2 /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </div>

      {/* Sticks to the bottom of the view while jobs are ticked; it sits below the table, so ticking never moves a row. */}
      {pickedJobs.length > 0 && (
        <div className='sticky bottom-6 z-10 mx-auto flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0'>
          <span className='text-xs text-muted-foreground'>
            <span className='font-mono tabular-nums text-foreground'>{pickedJobs.length}</span> job{pickedJobs.length === 1 ? '' : 's'} selected
          </span>
          <Button size='sm' variant='destructive' disabled={deleting} onClick={() => setConfirmDelete(true)}>
            <Trash2 data-icon='inline-start' /> Delete
          </Button>
          <Button size='sm' variant='ghost' onClick={() => setPicked(new Set())}>
            <X data-icon='inline-start' /> Clear
          </Button>
        </div>
      )}

      <AlertDialog open={confirmDelete} onOpenChange={(open) => !open && !deleting && setConfirmDelete(false)}>
        <AlertDialogContent size='default'>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {pickedJobs.length} job{pickedJobs.length === 1 ? '' : 's'}?</AlertDialogTitle>
            <AlertDialogDescription>
              They leave the queue for good, and their torrents leave qBittorrent with them (unless another
              job still needs one): a deleted job is a download given up. Files already in the library are
              never touched.
              {pickedRunning > 0 && ` ${pickedRunning} ${pickedRunning === 1 ? 'is' : 'are'} running and will be stopped.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className='max-h-48 overflow-y-auto rounded-item border border-field-edge bg-field-surface px-3 py-2 text-sm text-foreground'>
            {pickedJobs.map((job) => (
              <li key={job.id} className='truncate' title={job.title}>{job.title}</li>
            ))}
          </ul>
          <AlertDialogFooter>
            <Button variant='secondary' disabled={deleting} onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button variant='destructive' disabled={deleting || pickedJobs.length === 0} onClick={() => void deletePicked()}>
              {deleting ? <Spinner data-icon='inline-start' /> : <Trash2 data-icon='inline-start' />}
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Paging only: the status chips above already count the jobs. */}
      {total > pageSize && (
        <footer className='flex shrink-0 items-center justify-end gap-2 border-t border-border pt-2.5 text-xs text-muted-foreground'>
          <span className='font-mono text-[0.68rem] tabular-nums'>Page {page + 1} of {Math.ceil(total / pageSize)}</span>
          <Button size='sm' variant='secondary' disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft data-icon='inline-start' /> Previous</Button>
          <Button size='sm' variant='secondary' disabled={(page + 1) * pageSize >= total || loading} onClick={() => setPage((value) => value + 1)}>Next <ChevronRight data-icon='inline-end' /></Button>
        </footer>
      )}
    </div>
  );
}
