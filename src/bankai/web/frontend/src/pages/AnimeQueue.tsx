import { useCallback, useEffect, useState } from 'react';
import { CircleStop, Play, RotateCcw, Trash2 } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import { animeQueuePath, api, recall, type AnimeQueuePage, type Job } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { Button, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { AnimePoster } from '@/components/AnimePoster';
import {
  QueueBody,
  QueueDeleteDialog,
  QueueFilters,
  QueueHeader,
  QueuePager,
  QueueProgress,
  QueueSelectionBar,
  QueueTick,
  retryJobs,
  formatQueueTime,
  titleCase,
  useQueueChips,
  useQueuePicks,
  useSettled,
} from '@/components/queue/queue-page';

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

/** One bar: once the copy into the library starts, the transfer's own bytes, as overall progress
 * stops near the end of the last stage. */
function JobProgress({ job }: { job: Job }) {
  const transferring = job.phase === 'transferring' && job.transfer_percent != null;
  return transferring
    ? <QueueProgress percent={job.transfer_percent} label='Transfer' tone='transfer' />
    : <QueueProgress percent={job.overall_percent} speed={job.download_speed} secondsLeft={job.download_eta} />;
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
  // Typing must not fire a request per keystroke; the queue snapshot is a filesystem walk on the server.
  const search = useSettled(query);
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState<SortState<QueueSortKey> | null>(null);
  const [loading, setLoading] = useState(() => firstView() === undefined);
  const [busy, setBusy] = useState<string | null>(null);
  // Ticked jobs, by id: only those on the page as it is now count; another view, other rows.
  const { picked, setPicked, pick } = useQueuePicks(jobs.map((job) => job.id), [search, status, showCompleted, page, sort]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pageSize = 100;

  // Any filter change invalidates the current offset.
  useEffect(() => {
    setPage(0);
  }, [search, status, showCompleted]);

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

  const chips = useQueueChips(STATUS_ORDER, counts, status);

  const filtered = search.trim() !== '' || status !== 'all';

  function clearFilters() {
    setQuery('');
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

  function deletePicked() {
    const targets = pickedJobs;
    // The confirmation closes at once; a toast follows the deletes.
    setPicked(new Set());
    setConfirmDelete(false);
    const work = (async () => {
      const failed: string[] = [];
      // A few at a time: each delete may stop a process or remove a torrent.
      for (let start = 0; start < targets.length; start += 4) {
        const results = await Promise.allSettled(targets.slice(start, start + 4).map((job) => api.deleteJob(job.id)));
        results.forEach((result, index) => {
          if (result.status === 'rejected') failed.push(targets[start + index].title);
        });
      }
      if (failed.length === targets.length) throw new Error(`none could be deleted: ${failed.slice(0, 3).join(', ')}`);
      return failed;
    })();
    void inBackground(work, {
      loading: `Deleting ${targets.length} job${targets.length === 1 ? '' : 's'}…`,
      success: (failed) => {
        const removed = targets.length - failed.length;
        return `${removed} job${removed === 1 ? '' : 's'} deleted`
          + (failed.length ? ` · ${failed.length} could not be: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}` : '');
      },
    }).finally(() => void load(true)).catch(() => undefined);
  }

  return (
    <div className='flex flex-col gap-6'>
      <QueueHeader
        subtitle='Erai-raws and hand-picked anime downloads'
        showCompleted={showCompleted}
        onShowCompleted={setShowCompleted}
        completedLabel='Anime downloads'
        loading={loading}
        onRefresh={() => void load()}
      />
      <QueueFilters query={query} onQuery={setQuery} chips={chips} status={status} onStatus={setStatus} what='Anime downloads' />

      <QueueBody
        loading={loading}
        empty={jobs.length === 0}
        what='Anime'
        filtered={filtered}
        showCompleted={showCompleted}
        emptyHint='Automatic and manual Erai downloads will appear here.'
        onClearFilters={clearFilters}
      >
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
                <QueueTick
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
                  <QueueTick label={'Select ' + job.title} checked={picked.has(job.id)} onChange={(on) => pick(job.id, on)} />
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
                <TableCell className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatQueueTime(job.updated_at)}</TableCell>
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
      </QueueBody>

      <QueueSelectionBar
        count={pickedJobs.length}
        onDelete={() => setConfirmDelete(true)}
        onClear={() => setPicked(new Set())}
        retryable={pickedJobs.filter((job) => job.status === 'failed').length}
        onRetry={() => {
          const failed = pickedJobs.filter((job) => job.status === 'failed').map((job) => ({ id: job.id, title: job.title }));
          setPicked(new Set());
          retryJobs(failed, (id) => api.retryJob(id), () => void load(true));
        }}
      />

      <QueueDeleteDialog
        open={confirmDelete}
        titles={pickedJobs.map((job) => job.title)}
        running={pickedRunning}
        description='They leave the queue for good, and their torrents leave qBittorrent with them (unless another job still needs one): a deleted job is a download given up. Files already in the library are never touched.'
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => deletePicked()}
      />

      <QueuePager page={page} pageSize={pageSize} total={total} loading={loading} onPage={setPage} />
    </div>
  );
}
