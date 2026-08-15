import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Gauge, Loader2, RefreshCw, Search, Upload, Users } from 'lucide-react';
import { toast } from 'sonner';
import { api, type QBittorrentItem } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function formatSpeed(value: number) {
  return value > 0 ? `${formatBytes(value)}/s` : '—';
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

function stateLabel(state: string) {
  return state
    .replace(/DL$/, '')
    .replace(/UP$/, '')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (letter) => letter.toUpperCase());
}

function stateVariant(state: string): 'success' | 'info' | 'warning' | 'destructive' | 'secondary' {
  const normalized = state.toLowerCase();
  if (normalized.includes('error') || normalized.includes('missing')) return 'destructive';
  if (normalized.includes('downloading') || normalized.includes('forceddl')) return 'info';
  if (normalized.includes('uploading') || normalized.includes('forcedup')) return 'success';
  if (normalized.includes('stalled') || normalized.includes('queued') || normalized.includes('checking')) return 'warning';
  return 'secondary';
}

export default function QBittorrent() {
  const [items, setItems] = useState<QBittorrentItem[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
                return (
                  <tr key={item.hash} className='border-t border-border/60 bg-card/45 transition-colors hover:bg-secondary/20'>
                    <td className='max-w-[28rem] px-4 py-3 font-medium text-foreground'><div className='truncate' title={item.name}>{item.name}</div></td>
                    <td className='px-3 py-3'><Badge variant={stateVariant(item.state)}>{stateLabel(item.state)}</Badge></td>
                    <td className='whitespace-nowrap px-3 py-3 font-mono text-xs'>{formatBytes(item.size_bytes)}</td>
                    <td className='px-3 py-3'>
                      <div className='flex items-center gap-2'>
                        <div className='h-2 flex-1 overflow-hidden rounded-full bg-secondary'>
                          <div className='h-full rounded-full bg-primary transition-[width] duration-300' style={{ width: `${percent}%` }} />
                        </div>
                        <span className='w-12 text-right font-mono text-xs'>{percent.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className='px-3 py-3 text-right font-mono'>{item.seeds}</td>
                    <td className='px-3 py-3 text-right font-mono'>{item.peers}</td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono text-xs text-info'>{formatSpeed(item.dlspeed)}</td>
                    <td className='whitespace-nowrap px-3 py-3 text-right font-mono text-xs text-success'>{formatSpeed(item.upspeed)}</td>
                    <td className='whitespace-nowrap px-3 py-3 font-mono text-xs'>{formatEta(item.eta, item.progress)}</td>
                    <td className='whitespace-nowrap px-4 py-3 text-xs text-muted-foreground'>{formatAdded(item.added_on)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
