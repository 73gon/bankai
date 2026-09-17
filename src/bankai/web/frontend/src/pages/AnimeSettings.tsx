import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Database, Play, Save, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeAutomationStatus, type SettingRow } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

const LABELS: Record<string, { label: string; description: string; suffix?: string }> = {
  'transfer.anime_shows_dir': { label: 'Anime library folder', description: 'Dedicated Jellyfin Anime destination, using TVDB ordering.' },
  'anime.rss_url': { label: 'Nyaa Erai-raws RSS URL', description: 'The actual uploader feed. Only HTTPS Nyaa RSS for Erai-raws is accepted; this is not Erai-raws website RSS.' },
  'anime.enabled': { label: 'Automatic downloads', description: 'Follow the Nyaa Erai-raws uploader RSS and process its historical catalogue.' },
  'anime.backfill_enabled': { label: 'Historical backfill', description: 'Index all 2160p and 1080p releases first, then use 720p only for missing old episodes.' },
  'anime.min_free_space_gib': { label: 'Free-space reserve', description: 'Pause new automatic downloads at or below this amount.', suffix: 'GiB' },
  'anime.poll_interval_seconds': { label: 'Nyaa RSS polling interval', description: 'How often the configured Nyaa uploader RSS URL is checked.', suffix: 'seconds' },
  'anime.settle_minutes': { label: 'Quality settling window', description: 'Wait for alternate encodes before choosing the best release.', suffix: 'minutes' },
  'anime.max_enqueues_per_cycle': { label: 'Verified episodes per cycle', description: 'Build a large ordered backlog; qBittorrent controls how many torrents download at once.' },
  'anime.max_concurrent_transfers': { label: 'Concurrent transfers', description: 'How many finished downloads are published into the library at once. Downloading is unlimited — qBittorrent governs that.' },
  'anime.max_hevc_upgrades_per_cycle': { label: 'HEVC upgrades per cycle', description: 'Queued AVC episodes swapped for their HEVC encode each cycle. HEVC is roughly half the size for the same episode. Set to 0 to stop upgrading.' },
  'anime.backfill_request_delay_seconds': { label: 'Backfill request delay', description: 'Delay between historical Nyaa catalogue pages.', suffix: 'seconds' },
};

function formatDate(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : 'Never';
}

export default function AnimeSettings() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [status, setStatus] = useState<AnimeAutomationStatus | null>(null);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [settings, automation] = await Promise.all([api.settings(), api.animeAutomation()]);
      setRows(settings.settings.filter((row) => row.key.startsWith('anime.') || row.key === 'transfer.anime_shows_dir'));
      setStatus(automation);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      void api.animeAutomation().then(setStatus).catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);
  const dirty = useMemo(() => Object.keys(edits), [edits]);

  async function save() {
    setBusy(true);
    try {
      for (const key of dirty) await api.setSetting(key, edits[key]);
      toast.success('Anime settings saved');
      setEdits({});
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    setBusy(true);
    try {
      setStatus(await api.runAnimeAutomation());
      toast.success('Erai-raws check started');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  function value(row: SettingRow) {
    return row.key in edits ? edits[row.key] : row.value;
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Settings</h1>
          <p className='text-sm text-muted-foreground'>Autonomous Erai-raws policy, storage guard, and backfill progress.</p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button variant='secondary' onClick={() => void runNow()} disabled={busy || !status?.enabled}>
            <Play data-icon='inline-start' /> Run now
          </Button>
          <Button onClick={() => void save()} disabled={busy || dirty.length === 0}>
            <Save data-icon='inline-start' /> Save {dirty.length || ''}
          </Button>
        </div>
      </div>

      {loading ? <Skeleton className='h-44 w-full' /> : status && (
        <Card className={status.paused ? 'border-warning/40' : 'border-success/40'}>
          <CardHeader>
            <div className='flex flex-wrap items-center justify-between gap-3'>
              <div className='flex flex-col gap-1'>
                <CardTitle className='flex items-center gap-2'>
                  {status.paused ? <AlertTriangle className='text-warning' /> : <ShieldCheck className='text-success' />}
                  {status.paused ? 'Automation paused' : 'Automation ready'}
                </CardTitle>
                <CardDescription>{status.pause_reason || 'All safety checks are available.'}</CardDescription>
              </div>
              <Badge variant={status.paused ? 'warning' : 'success'}>{status.running ? 'Checking now' : status.enabled ? 'Enabled' : 'Disabled'}</Badge>
            </div>
          </CardHeader>
          <CardContent className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <div><p className='text-xs text-muted-foreground'>Library free</p><p className='font-mono text-lg'>{status.free_space_gib == null ? 'Unavailable' : status.free_space_gib + ' GiB'}</p></div>
            <div><p className='text-xs text-muted-foreground'>Download free</p><p className='font-mono text-lg'>{status.download_free_space_gib == null ? 'Checking' : status.download_free_space_gib + ' GiB'}</p></div>
            <div><p className='text-xs text-muted-foreground'>Reserve</p><p className='font-mono text-lg'>{status.min_free_space_gib} GiB</p></div>
            <div><p className='text-xs text-muted-foreground'>Last successful check</p><p className='text-sm'>{formatDate(status.last_success)}</p></div>
            <div><p className='text-xs text-muted-foreground'>Last cycle</p><p className='font-mono text-lg'>{status.last_enqueued} queued</p></div>
            <div className='sm:col-span-2 lg:col-span-4'><p className='text-xs text-muted-foreground'>Active feed: {status.feed_source}</p><a href={status.rss_url} target='_blank' rel='noreferrer' className='break-all font-mono text-xs underline underline-offset-4'>{status.rss_url}</a></div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Download policy</CardTitle><CardDescription>Every automatic item requires a trusted Erai uploader, explicit German subtitle evidence, an unambiguous TVDB match, and post-download subtitle verification.</CardDescription></CardHeader>
        <CardContent className='grid gap-5 md:grid-cols-2'>
          {rows.map((row) => {
            const meta = LABELS[row.key] || { label: row.key, description: '' };
            const current = value(row);
            const isBoolean = typeof row.value === 'boolean';
            const isNumber = typeof row.value === 'number';
            return (
              <label key={row.key} className='flex items-start justify-between gap-4 rounded-md border border-border/70 p-4'>
                <span className='flex flex-col gap-1'>
                  <span className='font-medium'>{meta.label}</span>
                  <span className='text-xs leading-relaxed text-muted-foreground'>{meta.description}</span>
                </span>
                {isBoolean ? (
                  <Switch checked={Boolean(current)} onCheckedChange={(next) => setEdits((old) => ({ ...old, [row.key]: next }))} aria-label={meta.label} />
                ) : (
                  <span className='flex min-w-36 items-center gap-2'>
                    <Input type={isNumber ? 'number' : 'text'} min={isNumber ? 0 : undefined} value={String(current ?? '')} onChange={(event) => setEdits((old) => ({ ...old, [row.key]: isNumber ? Number(event.target.value) : event.target.value }))} />
                    {meta.suffix && <span className='text-xs text-muted-foreground'>{meta.suffix}</span>}
                  </span>
                )}
              </label>
            );
          })}
        </CardContent>
      </Card>

      {status && (
        <Card>
          <CardHeader>
            <CardTitle className='flex items-center gap-2'><Database /> Historical quality index</CardTitle>
            <CardDescription>720p is not considered until the full 2160p and 1080p catalogue passes finish. Capped Nyaa queries are split into complementary search branches.</CardDescription>
          </CardHeader>
          {status.backfill.error && <p className='px-5 pb-3 text-sm text-warning'>{status.backfill.error} New RSS episodes continue independently.</p>}
          <CardContent className='grid gap-4 sm:grid-cols-4'>
            <div><p className='text-xs text-muted-foreground'>Phase</p><Badge variant={status.backfill.complete ? 'success' : 'info'}>{status.backfill.phase}</Badge></div>
            <div><p className='text-xs text-muted-foreground'>Search branches completed</p><p className='font-mono text-xl'>{status.backfill.queries_completed}</p></div>
            <div><p className='text-xs text-muted-foreground'>1080p+ episodes</p><p className='font-mono text-xl'>{status.backfill.found_1080}</p></div>
            <div><p className='text-xs text-muted-foreground'>720p fallbacks</p><p className='font-mono text-xl'>{status.backfill.fallback_720}</p></div>
          </CardContent>
        </Card>
      )}

    </div>
  );
}
