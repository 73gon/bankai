import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, CircleStop, Play, RefreshCw, RotateCcw, Search, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, type Job } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@/lib/utils';

function formatTime(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}

function statusVariant(status: string) {
  if (status === 'done') return 'success' as const;
  if (status === 'failed' || status === 'cancelled') return 'destructive' as const;
  if (status === 'stopped' || status === 'queued') return 'warning' as const;
  return 'info' as const;
}

function titleCase(value: string) {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

// Fixed order so chips never reshuffle under the pointer as counts change.
const STATUS_ORDER = ['running', 'queued', 'stopped', 'failed', 'cancelled', 'done'];

export default function AnimeQueue() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [page, setPage] = useState(0);
  const [showCompleted, setShowCompleted] = useState(false);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
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

  // Hiding completed jobs while filtered to them would leave an empty table
  // with no visible reason why.
  useEffect(() => {
    if (!showCompleted && status === 'done') setStatus('all');
  }, [showCompleted, status]);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await api.animeQueue(page, pageSize, showCompleted, { q: search, status });
      setJobs(result.jobs);
      setTotal(result.total);
      setCounts(result.counts ?? {});
    } catch (error: any) {
      if (!silent) toast.error(error.message);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [page, showCompleted, search, status]);

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

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Queue</h1>
          <p className='text-sm text-muted-foreground'>Only Erai-raws and manually selected Anime downloads appear here.</p>
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
        <div className='relative min-w-64 flex-1 sm:max-w-sm'>
          <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
          <Input
            ref={searchRef}
            className='pl-9 pr-9'
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
            <button
              type='button'
              aria-label='Clear title filter'
              onClick={() => {
                setQuery('');
                searchRef.current?.focus();
              }}
              className='filter-chip animate-fade-in absolute right-2 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60'
            >
              <X className='size-3.5' />
            </button>
          )}
        </div>
        <div className='flex flex-wrap items-center gap-1.5' role='group' aria-label='Filter by status'>
          {chips.map((chip) => {
            const active = status === chip.value;
            return (
              <button
                key={chip.value}
                type='button'
                aria-pressed={active}
                onClick={() => setStatus(chip.value)}
                className={cn(
                  'filter-chip inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                  active
                    ? 'border-transparent bg-primary text-primary-foreground'
                    : 'border-border/70 bg-secondary/40 text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {chip.label}
                <span className='font-mono text-[0.65rem] tabular-nums opacity-70'>{chip.count}</span>
              </button>
            );
          })}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Anime downloads</CardTitle>
          <CardDescription>
            {total} {showCompleted ? 'historical' : 'unfinished'} job{total === 1 ? '' : 's'}
            {filtered ? ' matching the current filter' : ''}
          </CardDescription>
        </CardHeader>
        <CardContent className='overflow-x-auto'>
          {loading && jobs.length === 0 ? (
            <div className='flex min-h-40 items-center justify-center'><Spinner /></div>
          ) : jobs.length === 0 ? (
            <EmptyState
              title={filtered ? 'No matching Anime downloads' : showCompleted ? 'Anime queue is empty' : 'No unfinished Anime downloads'}
              description={
                filtered
                  ? 'No job matches this title and status combination.'
                  : showCompleted
                    ? 'Automatic and manual Erai downloads will appear here.'
                    : 'Completed downloads are hidden by default.'
              }
              action={filtered ? <Button variant='secondary' onClick={clearFilters}>Clear filters</Button> : undefined}
            />
          ) : (
            <table className='w-full min-w-[820px] border-collapse text-sm'>
              <thead>
                <tr className='border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground'>
                  <th className='px-3 py-3 font-medium'>Title</th>
                  <th className='px-3 py-3 font-medium'>Status</th>
                  <th className='px-3 py-3 font-medium'>Progress</th>
                  <th className='px-3 py-3 font-medium'>Updated</th>
                  <th className='px-3 py-3 text-right font-medium'>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} className='border-b border-border/60 align-top last:border-0'>
                    <td className='px-3 py-4'>
                      <div className='flex items-start gap-3'>
                        <AnimePoster url={job.poster_url} title={job.series_title || job.title} className='w-12 shrink-0' />
                        <div className='flex flex-col gap-1'>
                          <p className='font-medium text-foreground'>{job.title}</p>
                          <p className='max-w-xl text-xs text-muted-foreground'>{job.reason || job.step_label || job.kind}</p>
                        </div>
                      </div>
                    </td>
                    <td className='px-3 py-4'><Badge variant={statusVariant(job.status)}>{job.status}</Badge></td>
                    <td className='px-3 py-4'>
                      <div className='flex min-w-36 flex-col gap-2'>
                        <span className='font-mono text-xs tabular-nums'>{Math.round(job.overall_percent ?? 0)}%</span>
                        <div className='h-1.5 overflow-hidden rounded-full bg-secondary'>
                          <div className='h-full rounded-full bg-primary transition-[width]' style={{ width: String(Math.max(0, Math.min(100, job.overall_percent ?? 0))) + '%' }} />
                        </div>
                      </div>
                    </td>
                    <td className='px-3 py-4 text-xs text-muted-foreground'>{formatTime(job.updated_at)}</td>
                    <td className='px-3 py-4'>
                      <div className='flex justify-end gap-1'>
                        {job.status === 'running' && (
                          <Button size='icon' variant='ghost' aria-label={'Stop ' + job.title} onClick={() => void act(job, 'stop')} disabled={busy === job.id}><CircleStop /></Button>
                        )}
                        {job.status === 'stopped' && (
                          <Button size='icon' variant='ghost' aria-label={'Continue ' + job.title} onClick={() => void act(job, 'continue')} disabled={busy === job.id}><Play /></Button>
                        )}
                        {job.status === 'failed' && (
                          <Button size='icon' variant='ghost' aria-label={'Retry ' + job.title} onClick={() => void act(job, 'retry')} disabled={busy === job.id}><RotateCcw /></Button>
                        )}
                        <Button size='icon' variant='ghost' aria-label={'Remove ' + job.title} onClick={() => void act(job, 'delete')} disabled={busy === job.id}><Trash2 /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
        {total > pageSize && (
          <div className='flex items-center justify-between border-t border-border/60 px-6 py-4'>
            <span className='text-xs text-muted-foreground'>Page {page + 1} of {Math.ceil(total / pageSize)}</span>
            <div className='flex gap-2'>
              <Button size='sm' variant='secondary' disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft data-icon='inline-start' /> Previous</Button>
              <Button size='sm' variant='secondary' disabled={(page + 1) * pageSize >= total || loading} onClick={() => setPage((value) => value + 1)}>Next <ChevronRight data-icon='inline-end' /></Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
