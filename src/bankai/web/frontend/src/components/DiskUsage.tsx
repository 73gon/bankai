import { Fragment, useEffect, useState } from 'react';
import { ChevronRight, HardDrive, RefreshCw } from 'lucide-react';
import { api, type StorageArea, type StorageUsage, type StorageVolume } from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/utils';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import { Skeleton } from '@/zon/ui/skeleton';
import { Spinner } from '@/zon/ui/spinner';

/**
 * Each area's colour, fixed by what it is (never by its size or place), from
 * the categorical order validated against the dark card surface. Areas on
 * another drive reuse the first slots under their own legend.
 */
const SWATCH: Record<string, string> = {
  anime: '#3987e5',
  movies: '#d95926',
  shows: '#199e70',
  downloads: '#c98500',
  staging: '#d55181',
  recycle: '#9085e9',
  state: '#3987e5',
  install: '#d95926',
  work: '#199e70',
};
const OTHER = 'color-mix(in oklab, var(--muted-foreground) 55%, transparent)';

const swatchOf = (area: StorageArea) => SWATCH[area.key.startsWith('recycle:') ? 'recycle' : area.key] ?? OTHER;

/** "/mnt/g" reads as "G: drive"; anything else by its path. */
function volumeName(volume: StorageVolume) {
  const drive = /^\/mnt\/([a-z])$/i.exec(volume.path);
  if (drive) return drive[1].toUpperCase() + ': drive';
  return volume.path === '/' ? 'WSL system disk (seireitei)' : volume.path;
}

const share = (bytes: number, of: number) => (of > 0 ? (bytes / of) * 100 : 0);
const percent = (bytes: number, of: number) => {
  const value = share(bytes, of);
  return value > 0 && value < 0.1 ? '<0.1%' : value.toFixed(1) + '%';
};

type Segment = { key: string; label: string; bytes: number; color: string };

/** One drive: a bar of what fills it, then every area with what it is made of. */
function VolumeCard({ volume, areas }: { volume: StorageVolume; areas: StorageArea[] }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const segments: Segment[] = [
    ...areas.filter((area) => area.bytes > 0).map((area) => ({ key: area.key, label: area.label, bytes: area.bytes, color: swatchOf(area) })),
    ...(volume.other > 0 ? [{ key: 'other', label: 'Other (not bankai)', bytes: volume.other, color: OTHER }] : []),
  ];
  const shown = segments.find((segment) => segment.key === hovered);
  const toggle = (key: string) => setOpen((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  const full = volume.free / volume.total < 0.02;

  return (
    <Card>
      <CardHeader>
        <CardTitle className='flex items-center gap-2'><HardDrive className='size-4' /> {volumeName(volume)}</CardTitle>
        <CardDescription>
          <span className='font-mono tabular-nums text-foreground'>{formatBytes(volume.used)}</span> of {formatBytes(volume.total)} used ·{' '}
          <span className={cn('font-mono tabular-nums', full ? 'text-destructive' : 'text-foreground')}>{formatBytes(volume.free)}</span> free
          {full && <span className='text-destructive'> — full</span>}
        </CardDescription>
      </CardHeader>
      <CardContent className='flex flex-col gap-3'>
        <div className='flex flex-col gap-1.5'>
          {/* The drive end to end: each area, what bankai does not know, and the free rest as empty track. */}
          <div
            className='flex h-4 w-full gap-0.5 overflow-hidden rounded-[4px] bg-field-surface'
            role='img'
            aria-label={segments.map((segment) => `${segment.label} ${formatBytes(segment.bytes)}`).join(', ') + `, free ${formatBytes(volume.free)}`}
            onMouseLeave={() => setHovered(null)}
          >
            {segments.map((segment) => (
              <div
                key={segment.key}
                className={cn('h-full shrink-0 transition-opacity', hovered && hovered !== segment.key && 'opacity-40')}
                style={{ width: share(segment.bytes, volume.total) + '%', minWidth: 2, background: segment.color }}
                onMouseEnter={() => setHovered(segment.key)}
              />
            ))}
          </div>
          <p className='h-4 text-xs text-muted-foreground'>
            {shown ? (
              <>
                <span className='text-foreground'>{shown.label}</span> · <span className='font-mono tabular-nums'>{formatBytes(shown.bytes)}</span> · {percent(shown.bytes, volume.total)} of the drive
              </>
            ) : (
              'Point at a part of the bar for its size.'
            )}
          </p>
        </div>
        <div className='flex flex-col'>
          {[...areas].sort((a, b) => b.bytes - a.bytes).map((area) => {
            const expandable = area.parts.length > 0 || area.shared_bytes > 0 || Boolean(area.torrents?.groups.length);
            const isOpen = open.has(area.key);
            return (
              <Fragment key={area.key}>
                <button
                  type='button'
                  disabled={!expandable}
                  onClick={() => toggle(area.key)}
                  onMouseEnter={() => setHovered(area.key)}
                  onMouseLeave={() => setHovered(null)}
                  aria-expanded={expandable ? isOpen : undefined}
                  className='grid grid-cols-[1rem_0.75rem_1fr_auto_3.5rem] items-center gap-2 rounded-item px-1 py-1.5 text-left text-sm focus-ring enabled:cursor-pointer enabled:hover:bg-control-surface/60'
                >
                  <ChevronRight className={cn('size-3.5 text-muted-foreground transition-transform', isOpen && 'rotate-90', !expandable && 'invisible')} />
                  <span className='size-2.5 rounded-[3px]' style={{ background: swatchOf(area) }} aria-hidden='true' />
                  <span className='min-w-0'>
                    <span className='block truncate text-foreground'>{area.label}</span>
                    <span className='block truncate font-mono text-[0.68rem] text-muted-foreground'>{area.path}</span>
                  </span>
                  <span className='font-mono text-xs tabular-nums text-foreground'>{formatBytes(area.bytes)}</span>
                  <span className='text-right font-mono text-xs tabular-nums text-muted-foreground'>{percent(area.bytes, volume.total)}</span>
                </button>
                {isOpen && (
                  <div className='mb-2 ml-[2.25rem] flex flex-col gap-1 border-l border-border/60 py-1 pl-3 text-xs'>
                    {area.parts.map((part) => (
                      <div key={part.label} className='grid grid-cols-[1fr_auto_auto] gap-3'>
                        <span className='text-muted-foreground'>{part.label} <span className='font-mono text-[0.68rem]'>· {part.files.toLocaleString()} file{part.files === 1 ? '' : 's'}</span></span>
                        <span className='font-mono tabular-nums text-foreground'>{formatBytes(part.bytes)}</span>
                        <span className='w-12 text-right font-mono tabular-nums text-muted-foreground'>{percent(part.bytes, area.bytes)}</span>
                      </div>
                    ))}
                    {area.shared_bytes > 0 && (
                      <p className='text-muted-foreground'>
                        Plus <span className='font-mono text-foreground'>{formatBytes(area.shared_bytes)}</span> that are the same files as in the library (hard links), taking no extra space.
                      </p>
                    )}
                    {area.torrents && area.torrents.groups.length > 0 && (
                      <div className='mt-1 flex flex-col gap-1'>
                        <span className='eyebrow text-[0.62rem]'>qBittorrent — {area.torrents.count} torrents</span>
                        {area.torrents.groups.map((group) => (
                          <div key={group.label} className='grid grid-cols-[1fr_auto] gap-3'>
                            <span className='text-muted-foreground'>{group.label} <span className='font-mono text-[0.68rem]'>· {group.torrents}</span></span>
                            <span className='font-mono tabular-nums text-foreground'>{formatBytes(group.bytes)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {area.key.startsWith('recycle:') && (
                      <p className='text-muted-foreground'>Deleted files Windows still keeps. Emptying the recycle bin on the drive frees this.</p>
                    )}
                  </div>
                )}
              </Fragment>
            );
          })}
          {volume.other > 0 && (
            <div
              className='grid grid-cols-[1rem_0.75rem_1fr_auto_3.5rem] items-center gap-2 px-1 py-1.5 text-sm'
              onMouseEnter={() => setHovered('other')}
              onMouseLeave={() => setHovered(null)}
            >
              <span />
              <span className='size-2.5 rounded-[3px]' style={{ background: OTHER }} aria-hidden='true' />
              <span className='text-muted-foreground'>Other (not bankai)</span>
              <span className='font-mono text-xs tabular-nums text-foreground'>{formatBytes(volume.other)}</span>
              <span className='text-right font-mono text-xs tabular-nums text-muted-foreground'>{percent(volume.other, volume.total)}</span>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** The dashboard's disk section: every drive bankai uses, and what fills it. */
export function DiskUsage() {
  const [usage, setUsage] = useState<StorageUsage | null>(null);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    // Asked again while a scan runs, so its result shows when it lands.
    async function poll() {
      try {
        const result = await api.storage();
        if (!alive) return;
        setUsage(result);
        if (result.scanning) timer = window.setTimeout(() => void poll(), 3_000);
      } catch {
        if (alive) timer = window.setTimeout(() => void poll(), 15_000);
      }
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  async function rescan() {
    await api.refreshStorage();
    setUsage((current) => (current ? { ...current, scanning: true } : current));
    // Follow the scan until it is done.
    const follow = async () => {
      const result = await api.storage();
      setUsage(result);
      if (result.scanning) window.setTimeout(() => void follow(), 3_000);
    };
    window.setTimeout(() => void follow(), 1_500);
  }

  return (
    <section className='flex flex-col gap-3'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div>
          <h2 className='font-heading text-xl font-light'>Disk usage</h2>
          <p className='text-xs text-muted-foreground'>
            {usage?.scanned_at
              ? `Scanned ${timeAgo(usage.scanned_at)}` + (usage.duration_seconds ? ` in ${Math.max(1, Math.round(usage.duration_seconds))} s` : '') + '. A file in two places (hard links) counts once.'
              : 'Every folder bankai uses, walked and added up.'}
          </p>
        </div>
        <Button size='sm' variant='secondary' disabled={!usage || usage.scanning} onClick={() => void rescan()}>
          {usage?.scanning ? <Spinner label='Scanning' /> : <RefreshCw data-icon='inline-start' />} {usage?.scanning ? 'Scanning…' : 'Scan again'}
        </Button>
      </div>
      {!usage || (!usage.scanned_at && usage.scanning) ? (
        <div className='grid gap-3 xl:grid-cols-2'>
          {Array.from({ length: 2 }, (_, index) => <Skeleton key={index} shape='card' className='h-64' />)}
        </div>
      ) : usage.volumes.length === 0 ? (
        <p className='text-sm text-muted-foreground'>No scan yet.</p>
      ) : (
        <div className='grid items-start gap-3 xl:grid-cols-2'>
          {usage.volumes.map((volume) => (
            <VolumeCard key={volume.path} volume={volume} areas={usage.areas.filter((area) => area.volume === volume.path)} />
          ))}
        </div>
      )}
    </section>
  );
}
