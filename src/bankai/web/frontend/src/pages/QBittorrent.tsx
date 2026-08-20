import { type KeyboardEvent, type MouseEvent, useCallback, useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  ChevronsDown,
  ChevronsUp,
  CircleHelp,
  CirclePause,
  Clock3,
  Download,
  Gauge,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Square,
  TriangleAlert,
  Trash2,
  Upload,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type QBittorrentItem } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';

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

function torrentStatus(item: QBittorrentItem): StatusStyle {
  const state = item.state.toLowerCase();
  if (state.includes('error') || state.includes('missing')) {
    return { label: 'Error', icon: TriangleAlert, badge: 'destructive', row: 'bg-destructive/[0.08] hover:bg-destructive/[0.13]', iconColor: 'text-destructive', progress: 'bg-destructive' };
  }
  if (state.includes('queued')) {
    return { label: 'Queued', icon: Clock3, badge: 'warning', row: 'bg-warning/[0.08] hover:bg-warning/[0.13]', iconColor: 'text-warning', progress: 'bg-warning' };
  }
  if (state.includes('stalledup')) {
    return { label: 'Seeding', icon: ChevronsUp, badge: 'info', row: 'bg-info/[0.08] hover:bg-info/[0.13]', iconColor: 'text-info', progress: 'bg-info' };
  }
  if (state.includes('stalled')) {
    return { label: 'Stalled', icon: CirclePause, badge: 'success', row: 'bg-success/[0.08] hover:bg-success/[0.13]', iconColor: 'text-success', progress: 'bg-success' };
  }
  if (state.includes('downloading') || state.includes('forceddl') || state.includes('metadl')) {
    return { label: 'Downloading', icon: ChevronsDown, badge: 'success', row: 'bg-success/[0.08] hover:bg-success/[0.13]', iconColor: 'text-success', progress: 'bg-success' };
  }
  if (state.includes('uploading') || state.includes('forcedup')) {
    return { label: 'Seeding', icon: ChevronsUp, badge: 'info', row: 'bg-info/[0.08] hover:bg-info/[0.13]', iconColor: 'text-info', progress: 'bg-info' };
  }
  if (state.includes('checking') || state.includes('moving') || state.includes('allocating')) {
    return { label: 'Checking', icon: RefreshCw, badge: 'warning', row: 'bg-warning/[0.08] hover:bg-warning/[0.13]', iconColor: 'text-warning', progress: 'bg-warning' };
  }
  if (item.progress >= 1) {
    return { label: 'Completed', icon: CheckCircle2, badge: 'transfer', row: 'bg-transfer/[0.08] hover:bg-transfer/[0.13]', iconColor: 'text-transfer', progress: 'bg-transfer' };
  }
  if (state.includes('paused') || state.includes('stopped')) {
    return { label: 'Paused', icon: CirclePause, badge: 'muted', row: 'bg-muted/20 hover:bg-muted/30', iconColor: 'text-muted-foreground', progress: 'bg-muted-foreground' };
  }
  return { label: 'Unknown', icon: CircleHelp, badge: 'muted', row: 'bg-muted/20 hover:bg-muted/30', iconColor: 'text-muted-foreground', progress: 'bg-muted-foreground' };
}

export default function QBittorrent() {
  const [items, setItems] = useState<QBittorrentItem[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [contextTorrent, setContextTorrent] = useState<QBittorrentItem | null>(null);
  const [contextPoint, setContextPoint] = useState({ x: 0, y: 0 });
  const [actioning, setActioning] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ torrent: QBittorrentItem; deleteFiles: boolean } | null>(null);

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

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return term ? items.filter((item) => item.name.toLocaleLowerCase().includes(term)) : items;
  }, [items, query]);
  const downloading = items.filter((item) => item.progress < 1 && item.dlspeed > 0).length;
  const totalDown = items.reduce((sum, item) => sum + item.dlspeed, 0);
  const totalUp = items.reduce((sum, item) => sum + item.upspeed, 0);
  const totalPeers = items.reduce((sum, item) => sum + item.peers, 0);
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
    <div className='flex flex-col gap-6'>
      <header className='flex flex-wrap items-start justify-between gap-3'>
        <div>
          <h1 className='text-2xl font-semibold'>qBittorrent</h1>
          <p className='mt-1 text-sm text-muted-foreground'>All downloads from the configured qBittorrent server. Updated every 3 seconds.</p>
        </div>
        <Button variant='secondary' disabled={refreshing} onClick={() => void refresh(true)}>
          <RefreshCw data-icon='inline-start' className={cn(refreshing && 'animate-spin')} />
          Refresh
        </Button>
      </header>

      <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
        {[
          { label: 'Torrents', value: items.length.toLocaleString(), icon: Gauge },
          { label: 'Downloading', value: downloading.toLocaleString(), icon: Download },
          { label: 'Down / up', value: `${formatSpeed(totalDown)} / ${formatSpeed(totalUp)}`, icon: Upload },
          { label: 'Connected peers', value: totalPeers.toLocaleString(), icon: Users },
        ].map(({ label, value, icon: Icon }) => (
          <Card key={label}>
            <CardContent className='flex items-center gap-3 p-4'>
              <div className='flex size-10 items-center justify-center rounded-md bg-secondary text-muted-foreground'><Icon className='size-5' /></div>
              <div className='min-w-0'>
                <div className='text-xs uppercase tracking-wide text-muted-foreground'>{label}</div>
                <div className='truncate font-mono text-sm font-medium text-foreground'>{value}</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className='relative max-w-lg'>
        <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
        <Input className='pl-9' value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Filter downloads…' />
      </div>

      {loading ? (
        <div className='flex min-h-72 items-center justify-center text-muted-foreground'>
          <Loader2 className='mr-2 size-5 animate-spin' /> Loading qBittorrent…
        </div>
      ) : error && items.length === 0 ? (
        <EmptyState icon={Download} title='qBittorrent is unavailable' description={error} />
      ) : visible.length === 0 ? (
        <EmptyState icon={Download} title={query ? 'No matching downloads' : 'No torrents'} description={query ? 'Try another filter.' : 'qBittorrent has no downloads yet.'} />
      ) : (
        <div className='overflow-x-auto rounded-lg border border-border/70'>
          <table className='w-full min-w-[1180px] border-collapse text-sm'>
            <thead className='bg-secondary/45 text-left text-xs uppercase tracking-wide text-muted-foreground'>
              <tr>
                <th className='px-4 py-3 font-medium'>Name</th>
                <th className='px-3 py-3 font-medium'>Status</th>
                <th className='px-3 py-3 font-medium'>Size</th>
                <th className='w-52 px-3 py-3 font-medium'>Progress</th>
                <th className='px-3 py-3 text-right font-medium'>Seeds</th>
                <th className='px-3 py-3 text-right font-medium'>Peers</th>
                <th className='px-3 py-3 text-right font-medium'>Down</th>
                <th className='px-3 py-3 text-right font-medium'>Up</th>
                <th className='px-3 py-3 font-medium'>ETA</th>
                <th className='px-4 py-3 font-medium'>Added on</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => {
                const percent = Math.max(0, Math.min(100, item.progress * 100));
                const status = torrentStatus(item);
                const StatusIcon = status.icon;
                return (
                  <tr
                    key={item.hash}
                    tabIndex={0}
                    onContextMenu={(event) => showContextMenu(event, item)}
                    onKeyDown={(event) => showKeyboardMenu(event, item)}
                    className={cn(
                      'cursor-context-menu border-t border-border/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60',
                      status.row,
                    )}
                  >
                    <td className='max-w-[28rem] px-4 py-3 font-medium text-foreground'>
                      <div className='flex items-center gap-2'>
                        <StatusIcon className={cn('size-4 shrink-0', status.iconColor)} aria-hidden='true' />
                        <div className='truncate' title={item.name}>{item.name}</div>
                      </div>
                    </td>
                    <td className='px-3 py-3'><Badge variant={status.badge}>{status.label}</Badge></td>
                    <td className='whitespace-nowrap px-3 py-3 font-mono text-xs'>{formatBytes(item.size_bytes)}</td>
                    <td className='px-3 py-3'>
                      <div className='flex items-center gap-2'>
                        <div className='h-2 flex-1 overflow-hidden rounded-full bg-secondary'>
                          <div className={cn('h-full rounded-full transition-[width] duration-300', status.progress)} style={{ width: `${percent}%` }} />
                        </div>
                        <span className='w-12 text-right font-mono text-xs'>{percent.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono'>{item.seeds} ({item.seeds_total})</td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono'>{item.peers} ({item.peers_total})</td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono text-xs'>{formatSpeed(item.dlspeed)}</td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono text-xs'>{formatSpeed(item.upspeed)}</td>
                    <td className='whitespace-nowrap px-3 py-3 font-mono text-xs'>{formatEta(item.eta, item.progress)}</td>
                    <td className='whitespace-nowrap px-4 py-3 text-xs text-muted-foreground'>{formatAdded(item.added_on)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Popover open={contextTorrent !== null} onOpenChange={(open) => !open && setContextTorrent(null)}>
        <PopoverAnchor asChild>
          <span
            aria-hidden='true'
            className='pointer-events-none fixed size-px'
            style={{ left: contextPoint.x, top: contextPoint.y }}
          />
        </PopoverAnchor>
        {contextTorrent && (
          <PopoverContent className='w-64 p-1' align='start' sideOffset={4}>
            <div className='truncate px-3 py-2 text-xs font-medium text-muted-foreground' title={contextTorrent.name}>
              {contextTorrent.name}
            </div>
            <Separator className='mb-1' />
            <Button
              variant='ghost'
              className='w-full justify-start'
              disabled={!contextStopped || actioning === contextTorrent.hash}
              onClick={() => void runTorrentAction(contextTorrent, 'start')}
            >
              <Play data-icon='inline-start' />
              Start
            </Button>
            <Button
              variant='ghost'
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

      <Dialog open={removeTarget !== null} onOpenChange={(open) => !open && setRemoveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{removeTarget?.deleteFiles ? 'Remove torrent and delete files?' : 'Remove torrent?'}</DialogTitle>
            <DialogDescription>
              {removeTarget?.deleteFiles
                ? 'This removes the torrent from qBittorrent and permanently deletes all of its downloaded files.'
                : 'This removes the torrent from qBittorrent but keeps its downloaded files on disk.'}
            </DialogDescription>
          </DialogHeader>
          {removeTarget && (
            <div className='rounded-md border border-border/70 bg-secondary/35 px-3 py-2 text-sm font-medium text-foreground'>
              {removeTarget.torrent.name}
            </div>
          )}
          <DialogFooter>
            <Button variant='secondary' disabled={actioning !== null} onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button variant='destructive' disabled={!removeTarget || actioning !== null} onClick={() => void confirmRemove()}>
              {actioning ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Trash2 data-icon='inline-start' />}
              {removeTarget?.deleteFiles ? 'Remove and delete files' : 'Remove torrent'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
