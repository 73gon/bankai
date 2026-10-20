import { type KeyboardEvent, type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronsDown,
  ChevronsUp,
  CircleHelp,
  CirclePause,
  Clock3,
  Download,
  ListFilter,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Square,
  TriangleAlert,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type QBittorrentItem } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { SortHeader, nextSort, type SortState } from '@/components/sort-header';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/zon/ui/alert-dialog';
import { Button } from '@/zon/ui/button';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Popover, PopoverContent, PopoverTrigger } from '@/zon/ui/popover';
import { Separator } from '@/zon/ui/separator';
import { Spinner } from '@/zon/ui/spinner';
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/zon/ui/table';
import { cn } from '@/zon/lib/cn';

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function formatSpeed(value: number) {
  return `${formatBytes(value)}/s`;
}

function formatEta(value: number, progress: number) {
  if (progress >= 1) return 'Complete';
  if (!Number.isFinite(value) || value <= 0 || value >= 8_640_000) return '∞';
  const days = Math.floor(value / 86_400);
  const hours = Math.floor((value % 86_400) / 3_600);
  const minutes = Math.floor((value % 3_600) / 60);
  const seconds = Math.floor(value % 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatAdded(value: number) {
  if (!value) return 'Unknown';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value * 1000));
}

type StatusStyle = {
  label: string;
  icon: LucideIcon;
  badge: 'transfer' | 'info' | 'success' | 'warning' | 'destructive' | 'muted';
  row: string;
  iconColor: string;
  progress: string;
};

const COMPLETED: StatusStyle = { label: 'Completed', icon: CheckCircle2, badge: 'transfer', row: 'bg-transfer/[0.08] hover:bg-transfer/[0.13]', iconColor: 'text-transfer', progress: 'bg-transfer' };

function torrentStatus(item: QBittorrentItem): StatusStyle {
  const state = item.state.toLowerCase();
  // qBittorrent suffixes its states with DL or UP; the UP variants mean the download already finished.
  const finished = state.endsWith('up') || item.progress >= 1;
  if (state.includes('error') || state.includes('missing')) {
    return { label: 'Error', icon: TriangleAlert, badge: 'destructive', row: 'bg-destructive/[0.08] hover:bg-destructive/[0.13]', iconColor: 'text-destructive', progress: 'bg-destructive' };
  }
  if (state.includes('queued')) {
    if (finished) return COMPLETED;
    return { label: 'Queued', icon: Clock3, badge: 'warning', row: 'bg-warning/[0.08] hover:bg-warning/[0.13]', iconColor: 'text-warning', progress: 'bg-warning' };
  }
  if (state.includes('stalledup')) {
    return { label: 'Seeding', icon: ChevronsUp, badge: 'info', row: 'bg-info/[0.08] hover:bg-info/[0.13]', iconColor: 'text-info', progress: 'bg-info' };
  }
  if (state.includes('stalled')) {
    if (finished) return COMPLETED;
    return { label: 'Stalled', icon: CirclePause, badge: 'success', row: 'bg-success/[0.08] hover:bg-success/[0.13]', iconColor: 'text-success', progress: 'bg-success' };
  }
  if (state.includes('downloading') || state.includes('forceddl') || state.includes('metadl')) {
    if (finished) return COMPLETED;
    return { label: 'Downloading', icon: ChevronsDown, badge: 'success', row: 'bg-success/[0.08] hover:bg-success/[0.13]', iconColor: 'text-success', progress: 'bg-success' };
  }
  if (state.includes('uploading') || state.includes('forcedup')) {
    return { label: 'Seeding', icon: ChevronsUp, badge: 'info', row: 'bg-info/[0.08] hover:bg-info/[0.13]', iconColor: 'text-info', progress: 'bg-info' };
  }
  if (state.includes('checking') || state.includes('moving') || state.includes('allocating')) {
    return { label: 'Checking', icon: RefreshCw, badge: 'warning', row: 'bg-warning/[0.08] hover:bg-warning/[0.13]', iconColor: 'text-warning', progress: 'bg-warning' };
  }
  if (finished) return COMPLETED;
  if (state.includes('paused') || state.includes('stopped')) {
    return { label: 'Paused', icon: CirclePause, badge: 'muted', row: 'bg-muted/20 hover:bg-muted/30', iconColor: 'text-muted-foreground', progress: 'bg-muted-foreground' };
  }
  return { label: 'Unknown', icon: CircleHelp, badge: 'muted', row: 'bg-muted/20 hover:bg-muted/30', iconColor: 'text-muted-foreground', progress: 'bg-muted-foreground' };
}

// Every status torrentStatus can return, in the order they matter while
// something is actually running.
const STATUSES = [
  'Downloading',
  'Seeding',
  'Completed',
  'Queued',
  'Stalled',
  'Checking',
  'Paused',
  'Error',
  'Unknown',
] as const;

type SortKey =
  | 'name' | 'status' | 'size' | 'progress' | 'seeds'
  | 'peers' | 'dlspeed' | 'upspeed' | 'eta' | 'added_on';

type Sort = SortState<SortKey>;

const SORTERS: Record<SortKey, (item: QBittorrentItem) => number | string> = {
  name: (item) => item.name.toLocaleLowerCase(),
  status: (item) => torrentStatus(item).label,
  size: (item) => item.size_bytes,
  progress: (item) => item.progress,
  seeds: (item) => item.seeds,
  peers: (item) => item.peers,
  dlspeed: (item) => item.dlspeed,
  upspeed: (item) => item.upspeed,
  // A finished torrent has no ETA at all, so it sorts past every real one
  // rather than landing among the fastest at zero seconds.
  eta: (item) => (item.progress >= 1 ? Number.POSITIVE_INFINITY : item.eta),
  added_on: (item) => item.added_on,
};

// Text reads naturally A-Z; a number is nearly always most interesting at its
// largest, so the first click on each gives you what you probably wanted.
const FIRST_DIRECTION: Record<SortKey, 'asc' | 'desc'> = {
  name: 'asc', status: 'asc', size: 'desc', progress: 'desc', seeds: 'desc',
  peers: 'desc', dlspeed: 'desc', upspeed: 'desc', eta: 'asc', added_on: 'desc',
};

export default function QBittorrent() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [items, setItems] = useState<QBittorrentItem[]>(() => recall<{ items: QBittorrentItem[] }>(pagePaths.qbittorrent)?.items ?? []);
  const [query, setQuery] = useState('');
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<Sort | null>(null);
  const [loading, setLoading] = useState(() => recall(pagePaths.qbittorrent) === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextTorrent, setContextTorrent] = useState<QBittorrentItem | null>(null);
  const [contextPoint, setContextPoint] = useState({ x: 0, y: 0 });
  const [actioning, setActioning] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ torrent: QBittorrentItem; deleteFiles: boolean } | null>(null);
  // The point the context menu opens at: a 1px span moved to the pointer.
  const contextAnchor = useRef<HTMLSpanElement>(null);

  const refresh = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const result = await api.qbittorrentTorrents();
      setItems(result.items);
      setError(null);
    } catch (caught: any) {
      setError(caught.message);
      if (manual) toast.error(caught.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  // Counted before the status filter is applied, so the dropdown keeps
  // showing what selecting each one would get you.
  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      const label = torrentStatus(item).label;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return counts;
  }, [items]);

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    const filtered = items.filter(
      (item) =>
        (!term || item.name.toLocaleLowerCase().includes(term)) &&
        (statuses.size === 0 || statuses.has(torrentStatus(item).label)),
    );
    if (!sort) return filtered;
    const pick = SORTERS[sort.key];
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((left, right) => {
      const a = pick(left);
      const b = pick(right);
      if (typeof a === 'string' || typeof b === 'string') {
        return String(a).localeCompare(String(b)) * factor;
      }
      return (a === b ? 0 : a < b ? -1 : 1) * factor;
    });
  }, [items, query, statuses, sort]);

  function toggleSort(key: SortKey) {
    setSort((current) => nextSort(current, key, FIRST_DIRECTION));
  }

  function toggleStatus(label: string) {
    setStatuses((current) => {
      const next = new Set(current);
      if (!next.delete(label)) next.add(label);
      return next;
    });
  }
  const downloading = items.filter((item) => item.progress < 1 && item.dlspeed > 0).length;
  const totalDown = items.reduce((sum, item) => sum + item.dlspeed, 0);
  const totalUp = items.reduce((sum, item) => sum + item.upspeed, 0);
  const totalPeers = items.reduce((sum, item) => sum + item.peers, 0);
  const totalSize = items.reduce((sum, item) => sum + item.size_bytes, 0);
  const contextState = contextTorrent?.state.toLowerCase() ?? '';
  const contextStopped = contextState.includes('stopped') || contextState.includes('paused');

  function showContextMenu(event: MouseEvent<HTMLTableRowElement>, torrent: QBittorrentItem) {
    event.preventDefault();
    setContextPoint({ x: event.clientX, y: event.clientY });
    setContextTorrent(torrent);
  }

  function showKeyboardMenu(event: KeyboardEvent<HTMLTableRowElement>, torrent: QBittorrentItem) {
    if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    setContextPoint({ x: rect.left + 32, y: rect.top + Math.min(rect.height, 32) });
    setContextTorrent(torrent);
  }

  async function runTorrentAction(torrent: QBittorrentItem, action: 'start' | 'stop') {
    setActioning(torrent.hash);
    setContextTorrent(null);
    try {
      if (action === 'start') await api.qbittorrentStart(torrent.hash);
      else await api.qbittorrentStop(torrent.hash);
      toast.success(`${action === 'start' ? 'Started' : 'Stopped'} ${torrent.name}`);
      await refresh();
    } catch (caught: any) {
      toast.error(caught.message);
    } finally {
      setActioning(null);
    }
  }

  async function confirmRemove() {
    if (!removeTarget) return;
    const { torrent, deleteFiles } = removeTarget;
    setActioning(torrent.hash);
    try {
      await api.qbittorrentRemove(torrent.hash, deleteFiles);
      toast.success(deleteFiles ? `Removed ${torrent.name} and its files` : `Removed ${torrent.name}`);
      setRemoveTarget(null);
      await refresh();
    } catch (caught: any) {
      toast.error(caught.message);
    } finally {
      setActioning(null);
    }
  }

  return (
    // A column that fills the page: the table scrolls inside it and the
    // summary sits under the table as a real footer rather than floating
    // over the last few rows.
    <div className='flex h-full min-h-0 flex-col gap-4'>
      <header className='flex flex-wrap items-start justify-between gap-3'>
        <div>
          <h1 className='font-heading text-2xl font-light'>qBittorrent</h1>
          <p className='mt-1 text-sm text-muted-foreground'>All downloads from the configured qBittorrent server. Updated every 3 seconds.</p>
        </div>
        <Button variant='secondary' disabled={refreshing} onClick={() => void refresh(true)}>
          <RefreshCw data-icon='inline-start' className={cn(refreshing && 'animate-spin')} />
          Refresh
        </Button>
      </header>

      <div className='flex flex-wrap items-center gap-2'>
        <InputGroup className='w-full max-w-xs'>
          <InputGroupAddon>
            <Search aria-hidden='true' />
          </InputGroupAddon>
          <InputGroupInput value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Filter downloads…' aria-label='Filter downloads' />
        </InputGroup>

        <Popover>
          <PopoverTrigger render={<Button variant='secondary' aria-label='Filter by status' />}>
            <ListFilter data-icon='inline-start' />
            {statuses.size === 0 ? 'All statuses' : `${statuses.size} selected`}
            <ChevronDown className='opacity-60' aria-hidden='true' />
          </PopoverTrigger>
          <PopoverContent align='start' className='w-60 gap-0 p-1'>
            {STATUSES.map((label) => {
              const on = statuses.has(label);
              const count = statusCounts.get(label) ?? 0;
              return (
                <button
                  key={label}
                  type='button'
                  role='menuitemcheckbox'
                  aria-checked={on}
                  onClick={() => toggleStatus(label)}
                  disabled={count === 0 && !on}
                  className='flex w-full cursor-pointer items-center gap-2 rounded-item material-row px-2 py-1.5 text-left text-[13px] focus-ring disabled:cursor-default disabled:opacity-40'
                >
                  <span className='flex size-3.5 shrink-0 items-center justify-center'>
                    {on && <Check className='size-3.5' aria-hidden='true' />}
                  </span>
                  <span className='flex-1'>{label}</span>
                  <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{count}</span>
                </button>
              );
            })}
            {statuses.size > 0 && (
              <>
                <Separator className='my-1' />
                <button
                  type='button'
                  onClick={() => setStatuses(new Set())}
                  className='w-full cursor-pointer rounded-item material-row px-2 py-1.5 text-left text-[13px] text-muted-foreground focus-ring'
                >
                  Clear
                </button>
              </>
            )}
          </PopoverContent>
        </Popover>

        {sort && (
          <Button variant='ghost' onClick={() => setSort(null)}>Reset sort</Button>
        )}
      </div>

      {loading ? (
        <div className='flex min-h-72 items-center justify-center gap-2 text-muted-foreground'>
          <Spinner size='lg' label='Loading qBittorrent' /> Loading qBittorrent…
        </div>
      ) : error && items.length === 0 ? (
        <Empty>
          <EmptyMedia><Download /></EmptyMedia>
          <EmptyTitle>qBittorrent is unavailable</EmptyTitle>
          <EmptyDescription>{error}</EmptyDescription>
        </Empty>
      ) : visible.length === 0 ? (
        <Empty>
          <EmptyMedia><Download /></EmptyMedia>
          <EmptyTitle>{query ? 'No matching downloads' : 'No torrents'}</EmptyTitle>
          <EmptyDescription>{query ? 'Try another filter.' : 'qBittorrent has no downloads yet.'}</EmptyDescription>
        </Empty>
      ) : (
          <Table variant='plain' dense className='min-w-[1180px]' containerProps={{ className: 'min-h-0 flex-1 overflow-auto' }}>
            <TableHeader className='sticky top-0 z-10 bg-background'>
              <TableRow>
                <SortHeader label='Name' column='name' sort={sort} onSort={toggleSort} />
                <SortHeader label='Status' column='status' sort={sort} onSort={toggleSort} />
                <SortHeader label='Size' column='size' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Progress' column='progress' sort={sort} onSort={toggleSort} className='w-44' />
                <SortHeader label='Seeds' column='seeds' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Peers' column='peers' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Down' column='dlspeed' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Up' column='upspeed' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='ETA' column='eta' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='Added on' column='added_on' sort={sort} onSort={toggleSort} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((item) => {
                const percent = Math.max(0, Math.min(100, item.progress * 100));
                const status = torrentStatus(item);
                const StatusIcon = status.icon;
                return (
                  <TableRow
                    key={item.hash}
                    tabIndex={0}
                    onContextMenu={(event) => showContextMenu(event, item)}
                    onKeyDown={(event) => showKeyboardMenu(event, item)}
                    className={cn(
                      'data-row cursor-context-menu focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-control-focus',
                      status.row,
                    )}
                  >
                    <TableCell className='max-w-[26rem] py-2 font-medium text-foreground'>
                      <div className='flex items-center gap-2'>
                        <StatusIcon className={cn('size-3.5 shrink-0', status.iconColor)} aria-hidden='true' />
                        <div className='truncate' title={item.name}>{item.name}</div>
                      </div>
                    </TableCell>
                    <TableCell className='py-2'><StatusTag variant={status.badge}>{status.label}</StatusTag></TableCell>
                    <TableCell numeric className='py-2 text-xs'>{formatBytes(item.size_bytes)}</TableCell>
                    <TableCell className='py-2'>
                      <div className='flex items-center gap-2'>
                        <div className='h-1.5 flex-1 overflow-hidden rounded-full bg-secondary'>
                          <div className={cn('h-full rounded-full transition-[width] duration-300', status.progress)} style={{ width: `${percent}%` }} />
                        </div>
                        <span className='w-10 text-right font-mono text-[0.68rem] tabular-nums'>{percent.toFixed(0)}%</span>
                      </div>
                    </TableCell>
                    <TableCell numeric className='py-2 text-xs'>{item.seeds} ({item.seeds_total})</TableCell>
                    <TableCell numeric className='py-2 text-xs'>{item.peers} ({item.peers_total})</TableCell>
                    <TableCell numeric className='py-2 text-xs'>{formatSpeed(item.dlspeed)}</TableCell>
                    <TableCell numeric className='py-2 text-xs'>{formatSpeed(item.upspeed)}</TableCell>
                    <TableCell numeric className='py-2 text-[0.68rem]'>{formatEta(item.eta, item.progress)}</TableCell>
                    <TableCell className='whitespace-nowrap py-2 font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatAdded(item.added_on)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
      )}

      {!loading && visible.length > 0 && (
        <footer className='flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-t border-border pt-2.5 text-xs text-muted-foreground'>
          <span><span className='font-mono tabular-nums text-foreground'>{visible.length.toLocaleString()}</span> torrents{visible.length !== items.length ? ` of ${items.length.toLocaleString()}` : ''}</span>
          <span><span className='font-mono tabular-nums text-foreground'>{downloading.toLocaleString()}</span> downloading</span>
          <span className='flex items-center gap-1.5'><ChevronsDown className='size-3.5 text-success' /><span className='font-mono tabular-nums text-foreground'>{formatSpeed(totalDown)}</span></span>
          <span className='flex items-center gap-1.5'><ChevronsUp className='size-3.5 text-info' /><span className='font-mono tabular-nums text-foreground'>{formatSpeed(totalUp)}</span></span>
          <span><span className='font-mono tabular-nums text-foreground'>{totalPeers.toLocaleString()}</span> peers</span>
          <span><span className='font-mono tabular-nums text-foreground'>{formatBytes(totalSize)}</span> total</span>
        </footer>
      )}

      <Popover open={contextTorrent !== null} onOpenChange={(open) => !open && setContextTorrent(null)}>
        {contextTorrent && (
          <PopoverContent className='w-64 gap-0 p-1' align='start' sideOffset={4} positioner={{ anchor: contextAnchor }}>
            <div className='truncate px-3 py-2 text-xs font-medium text-muted-foreground' title={contextTorrent.name}>
              {contextTorrent.name}
            </div>
            <Separator className='mb-1' />
            <Button
              variant='ghost'
              size='sm'
              className='w-full justify-start'
              disabled={!contextStopped || actioning === contextTorrent.hash}
              onClick={() => void runTorrentAction(contextTorrent, 'start')}
            >
              <Play data-icon='inline-start' />
              Start
            </Button>
            <Button
              variant='ghost'
              size='sm'
              className='w-full justify-start'
              disabled={contextStopped || actioning === contextTorrent.hash}
              onClick={() => void runTorrentAction(contextTorrent, 'stop')}
            >
              <Square data-icon='inline-start' />
              Stop
            </Button>
            <Separator className='my-1' />
            <Button
              variant='ghost'
              size='sm'
              className='w-full justify-start'
              disabled={actioning === contextTorrent.hash}
              onClick={() => {
                setRemoveTarget({ torrent: contextTorrent, deleteFiles: false });
                setContextTorrent(null);
              }}
            >
              <Trash2 data-icon='inline-start' />
              Remove torrent
            </Button>
            <Button
              variant='ghost'
              size='sm'
              className='w-full justify-start text-destructive hover:text-destructive'
              disabled={actioning === contextTorrent.hash}
              onClick={() => {
                setRemoveTarget({ torrent: contextTorrent, deleteFiles: true });
                setContextTorrent(null);
              }}
            >
              <Trash2 data-icon='inline-start' />
              Remove and delete files
            </Button>
          </PopoverContent>
        )}
      </Popover>
      {/* Where the context menu opens: moved to the pointer, or to the row for the keyboard. */}
      <span
        ref={contextAnchor}
        aria-hidden='true'
        className='pointer-events-none fixed size-px'
        style={{ left: contextPoint.x, top: contextPoint.y }}
      />

      <AlertDialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <AlertDialogContent size='default'>
          <AlertDialogHeader>
            <AlertDialogTitle>{removeTarget?.deleteFiles ? 'Remove torrent and delete files?' : 'Remove torrent?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {removeTarget?.deleteFiles
                ? 'This removes the torrent from qBittorrent and permanently deletes all of its downloaded files.'
                : 'This removes the torrent from qBittorrent but keeps its downloaded files on disk.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {removeTarget && (
            <div className='break-all rounded-item border border-field-edge bg-field-surface px-3 py-2 text-sm font-medium text-foreground'>
              {removeTarget.torrent.name}
            </div>
          )}
          <AlertDialogFooter>
            <Button variant='secondary' disabled={actioning !== null} onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button variant='destructive' disabled={!removeTarget || actioning !== null} onClick={() => void confirmRemove()}>
              {actioning ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Trash2 data-icon='inline-start' />}
              {removeTarget?.deleteFiles ? 'Remove and delete files' : 'Remove torrent'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
