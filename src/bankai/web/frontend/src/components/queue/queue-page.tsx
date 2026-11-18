/**
 * What the anime queue and the Movies & Shows queue share, taken from the anime queue's design: a header
 * (title, a line under it, Show completed, Refresh), a title search beside status chips with counts, a
 * table whose rows tick, a progress cell, a bar floating at the bottom while rows are ticked, the delete
 * confirmation and the pager. Each page brings its rows, its columns and its actions.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { inBackground } from '@/lib/toast';
import { ChevronLeft, ChevronRight, RefreshCw, RotateCcw, Search, Trash2, X } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  Button,
  Checkbox,
  Empty,
  EmptyActions,
  EmptyDescription,
  EmptyTitle,
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  Spinner,
  Switch,
  ToggleGroup,
  ToggleGroupItem,
  cn,
} from '@zonsh/ui';
import { formatBytes } from '@/lib/utils';

export function formatQueueTime(value: number | null | undefined) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}

export function titleCase(value: string) {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

/** "4 min", "2 h 10 min": a download's time left. */
export function eta(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}

/** A value that follows another a moment later: typing must not fire a request or a filter per key. */
export function useSettled<T>(value: T, ms = 250): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** A checkbox inside a table: a real button, so the click lands. */
export function QueueTick({ checked, indeterminate, label, disabled, onChange }: {
  checked: boolean;
  indeterminate?: boolean;
  label: string;
  disabled?: boolean;
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

/** One bar and its number. ``label`` names a number that is not the job's whole progress (Transfer). */
export function QueueProgress({ percent, label, speed, secondsLeft, tone = 'primary' }: {
  percent: number | null | undefined;
  label?: string;
  speed?: number | null;
  secondsLeft?: number | null;
  tone?: 'primary' | 'transfer' | 'repack';
}) {
  const value = Math.max(0, Math.min(100, percent ?? 0));
  return (
    <div className='flex min-w-36 flex-col gap-2'>
      <span className='flex items-center gap-1.5 whitespace-nowrap font-mono text-xs tabular-nums'>
        {label && <span className={tone === 'transfer' ? 'text-transfer' : tone === 'repack' ? 'text-repack' : 'text-muted-foreground'}>{label}</span>}
        <span>{Math.round(value)}%</span>
        {/* Speed and time left while downloading, as qBittorrent's tab shows them. */}
        {speed ? (
          <span className='text-muted-foreground'>
            {formatBytes(speed)}/s{secondsLeft && secondsLeft < 8_640_000 ? ` · ${eta(secondsLeft)}` : ''}
          </span>
        ) : null}
      </span>
      <div className='h-1.5 overflow-hidden rounded-full bg-secondary'>
        <div
          className={cn(
            'h-full rounded-full transition-[width,background-color]',
            tone === 'transfer' ? 'bg-transfer' : tone === 'repack' ? 'bg-repack' : 'bg-primary',
          )}
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}

export function QueueHeader({ subtitle, showCompleted, onShowCompleted, completedLabel, loading, onRefresh, children }: {
  subtitle: string;
  showCompleted: boolean;
  onShowCompleted: (on: boolean) => void;
  /** What the switch hides, for screen readers: "Anime downloads". */
  completedLabel: string;
  loading: boolean;
  onRefresh: () => void;
  children?: ReactNode;
}) {
  return (
    <div className='flex flex-wrap items-center justify-between gap-3'>
      <div className='flex min-w-0 items-baseline gap-3'>
        <h1 className='font-heading text-3xl font-light'>Queue</h1>
        <p className='truncate text-sm text-muted-foreground'>{subtitle}</p>
      </div>
      <div className='flex items-center gap-3'>
        {children}
        <label className='flex items-center gap-2 whitespace-nowrap text-sm text-foreground'>
          <Switch checked={showCompleted} onCheckedChange={onShowCompleted} aria-label={`Show completed ${completedLabel}`} />
          Show completed
        </label>
        <Button variant='secondary' onClick={onRefresh} disabled={loading}>
          <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} />
          Refresh
        </Button>
      </div>
    </div>
  );
}

export interface QueueChip {
  value: string;
  label: string;
  count: number;
}

/**
 * The status chips: All, then every status with rows in the order work flows through them, so the
 * chips never reshuffle under the pointer as counts change. The active chip stays even at zero.
 */
export function useQueueChips(order: readonly string[], counts: Record<string, number>, status: string, label: (value: string) => string = titleCase) {
  return useMemo<QueueChip[]>(() => {
    const matched = Object.values(counts).reduce((sum, value) => sum + value, 0);
    const visible = order.filter((name) => counts[name]).map((name) => ({ value: name, label: label(name), count: counts[name] }));
    if (status !== 'all' && !visible.some((chip) => chip.value === status)) {
      visible.push({ value: status, label: label(status), count: 0 });
    }
    return [{ value: 'all', label: 'All', count: matched }, ...visible];
  }, [order, counts, status, label]);
}

export function QueueFilters({ query, onQuery, chips, status, onStatus, what }: {
  query: string;
  onQuery: (value: string) => void;
  chips: QueueChip[];
  status: string;
  onStatus: (value: string) => void;
  /** "Anime downloads", for the search field's label. */
  what: string;
}) {
  const searchRef = useRef<HTMLInputElement>(null);
  return (
    <div className='flex flex-wrap items-center gap-3'>
      <InputGroup className='min-w-64 flex-1 sm:max-w-sm'>
        <InputGroupAddon>
          <Search aria-hidden='true' />
        </InputGroupAddon>
        <InputGroupInput
          ref={searchRef}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape' && query) {
              event.preventDefault();
              onQuery('');
            }
          }}
          placeholder='Filter by title…'
          aria-label={`Filter ${what} by title`}
        />
        {query && (
          <InputGroupAddon align='inline-end'>
            <InputGroupButton
              variant='ghost'
              size='icon-sm'
              aria-label='Clear title filter'
              onClick={() => {
                onQuery('');
                searchRef.current?.focus();
              }}
            >
              <X className='size-3.5' />
            </InputGroupButton>
          </InputGroupAddon>
        )}
      </InputGroup>
      <ToggleGroup aria-label='Filter by status' className='flex-wrap' value={[status]} onValueChange={(values) => values[0] && onStatus(values[0])}>
        {chips.map((chip) => (
          <ToggleGroupItem key={chip.value} value={chip.value} aria-label={chip.label}>
            <span>{chip.label}</span>
            <span className='font-mono text-[0.65rem] tabular-nums opacity-70'>{chip.count}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

/** The spinner while nothing is there yet, the empty state, or the table. */
export function QueueBody({ loading, empty, what, filtered, showCompleted, emptyHint, onClearFilters, children }: {
  loading: boolean;
  empty: boolean;
  what: string;
  filtered: boolean;
  showCompleted: boolean;
  /** Under "<what> queue is empty": where its jobs come from. */
  emptyHint: string;
  onClearFilters: () => void;
  children: ReactNode;
}) {
  if (loading && empty) {
    return <div className='flex min-h-40 items-center justify-center'><Spinner label={`Loading the ${what} queue`} /></div>;
  }
  if (empty) {
    return (
      <Empty>
        <EmptyTitle>{filtered ? `No matching ${what} downloads` : showCompleted ? `${what} queue is empty` : `No unfinished ${what} downloads`}</EmptyTitle>
        <EmptyDescription>
          {filtered ? 'No job matches this title and status combination.' : showCompleted ? emptyHint : 'Completed downloads are hidden by default.'}
        </EmptyDescription>
        {filtered && (
          <EmptyActions>
            <Button variant='secondary' onClick={onClearFilters}>Clear filters</Button>
          </EmptyActions>
        )}
      </Empty>
    );
  }
  return (
    <div className='min-h-0 flex-1'>
      <div className='min-w-full'>{children}</div>
    </div>
  );
}

/** Sticks to the bottom of the view while rows are ticked; it sits below the table, so ticking never moves a row. */
export function QueueSelectionBar({ count, noun = 'job', onDelete, onClear, retryable = 0, onRetry, children }: {
  count: number;
  noun?: string;
  onDelete: () => void;
  onClear: () => void;
  /** Of the ticked, those that failed: Retry runs them again, all at once. */
  retryable?: number;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div className='sticky bottom-6 z-10 mx-auto flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0'>
      <span className='text-xs text-muted-foreground'>
        <span className='font-mono tabular-nums text-foreground'>{count}</span> {noun}{count === 1 ? '' : 's'} selected
      </span>
      {children}
      {onRetry && retryable > 0 && (
        <Button size='sm' variant='secondary' onClick={onRetry} title='Run every ticked failed job again'>
          <RotateCcw data-icon='inline-start' /> Retry {retryable === count ? '' : `${retryable} failed`}
        </Button>
      )}
      <Button size='sm' variant='destructive' onClick={onDelete}>
        <Trash2 data-icon='inline-start' /> Delete
      </Button>
      <Button size='sm' variant='ghost' onClick={onClear}>
        <X data-icon='inline-start' /> Clear
      </Button>
    </div>
  );
}

export function QueueDeleteDialog({ open, titles, running, description, onCancel, onConfirm }: {
  open: boolean;
  titles: string[];
  running: number;
  description: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <AlertDialogContent size='default'>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {titles.length} job{titles.length === 1 ? '' : 's'}?</AlertDialogTitle>
          <AlertDialogDescription>
            {description}
            {running > 0 && ` ${running} ${running === 1 ? 'is' : 'are'} running and will be stopped.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ul className='max-h-48 overflow-y-auto rounded-item border border-field-edge bg-field-surface px-3 py-2 text-sm text-foreground'>
          {titles.map((title, index) => (
            <li key={index} className='truncate' title={title}>{title}</li>
          ))}
        </ul>
        <AlertDialogFooter>
          <Button variant='secondary' onClick={onCancel}>Cancel</Button>
          <Button variant='destructive' disabled={titles.length === 0} onClick={onConfirm}>
            <Trash2 data-icon='inline-start' />
            Delete
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** Paging only: the status chips above already count the jobs. */
export function QueuePager({ page, pageSize, total, loading, onPage }: {
  page: number;
  pageSize: number;
  total: number;
  loading?: boolean;
  onPage: (page: number) => void;
}) {
  if (total <= pageSize) return null;
  return (
    <footer className='flex shrink-0 items-center justify-end gap-2 border-t border-border pt-2.5 text-xs text-muted-foreground'>
      <span className='font-mono text-[0.68rem] tabular-nums'>Page {page + 1} of {Math.ceil(total / pageSize)}</span>
      <Button size='sm' variant='secondary' disabled={page === 0 || loading} onClick={() => onPage(Math.max(0, page - 1))}><ChevronLeft data-icon='inline-start' /> Previous</Button>
      <Button size='sm' variant='secondary' disabled={(page + 1) * pageSize >= total || loading} onClick={() => onPage(page + 1)}>Next <ChevronRight data-icon='inline-end' /></Button>
    </footer>
  );
}

/** Ticked rows by id; only those still shown count, and a new view starts with none. */
export function useQueuePicks(shownIds: string[], resetOn: unknown[]) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setPicked(new Set()), resetOn);
  const key = shownIds.join('\n');
  useEffect(() => {
    setPicked((current) => {
      const shown = new Set(shownIds);
      const kept = [...current].filter((id) => shown.has(id));
      return kept.length === current.size ? current : new Set(kept);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  function pick(id: string, on: boolean) {
    setPicked((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  return { picked, setPicked, pick };
}


/** Run jobs again, a few at a time, with one toast for all of them. */
export function retryJobs(jobs: { id: string; title: string }[], retry: (id: string) => Promise<unknown>, done: () => void) {
  const work = (async () => {
    const failed: string[] = [];
    for (let start = 0; start < jobs.length; start += 4) {
      const results = await Promise.allSettled(jobs.slice(start, start + 4).map((job) => retry(job.id)));
      results.forEach((result, index) => {
        if (result.status === 'rejected') failed.push(jobs[start + index].title);
      });
    }
    if (failed.length === jobs.length) throw new Error(`none could be run again: ${failed.slice(0, 3).join(', ')}`);
    return failed;
  })();
  void inBackground(work, {
    loading: `Running ${jobs.length} job${jobs.length === 1 ? '' : 's'} again…`,
    success: (failed) => {
      const ran = jobs.length - failed.length;
      return `${ran} job${ran === 1 ? '' : 's'} running again`
        + (failed.length ? ` · ${failed.length} could not be: ${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}` : '');
    },
  }).finally(done).catch(() => undefined);
}

