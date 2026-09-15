import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Database, Play, RefreshCw, Save, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeAutomationStatus, type SettingRow } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { AnimeMappingDialog } from '@/components/AnimeMappingDialog';

const LABELS: Record<string, { label: string; description: string; suffix?: string }> = {
  'transfer.anime_shows_dir': { label: 'Anime library folder', description: 'Dedicated Jellyfin Anime destination, using TVDB ordering.' },
  'anime.rss_url': { label: 'Nyaa Erai-raws RSS URL', description: 'The actual uploader feed. Only HTTPS Nyaa RSS for Erai-raws is accepted; this is not Erai-raws website RSS.' },
  'anime.enabled': { label: 'Automatic downloads', description: 'Follow the Nyaa Erai-raws uploader RSS and process its historical catalogue.' },
  'anime.backfill_enabled': { label: 'Historical backfill', description: 'Index all 2160p and 1080p releases first, then use 720p only for missing old episodes.' },
  'anime.min_free_space_gib': { label: 'Free-space reserve', description: 'Pause new automatic downloads at or below this amount.', suffix: 'GiB' },
  'anime.poll_interval_seconds': { label: 'Nyaa RSS polling interval', description: 'How often the configured Nyaa uploader RSS URL is checked.', suffix: 'seconds' },
  'anime.settle_minutes': { label: 'Quality settling window', description: 'Wait for alternate encodes before choosing the best release.', suffix: 'minutes' },
  'anime.max_enqueues_per_cycle': { label: 'Verified episodes per cycle', description: 'Build a large ordered backlog; qBittorrent controls how many torrents download at once.' },
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
  const [mappingTitle, setMappingTitle] = useState<string | null>(null);

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

  async function retryHeld() {
    setBusy(true);
    try {
      const result = await api.retryHeldAnime();
      setStatus(result);
      toast.success(result.requested + ' held releases scheduled for fresh checks');
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
          <Button variant='secondary' onClick={() => void retryHeld()} disabled={busy || !status?.counts.held}>
            <RefreshCw data-icon='inline-start' /> {status?.retry_pending ? 'Retry pending (' + status.retry_pending + ')' : 'Retry held releases'}
          </Button>
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
            <div><p className='text-xs text-muted-foreground'>Free space</p><p className='font-mono text-lg'>{status.free_space_gib == null ? 'Unavailable' : status.free_space_gib + ' GiB'}</p></div>
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

      {status && status.series_indexes.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Episode-order indexing</CardTitle><CardDescription>Before downloading a newly discovered show, its high-quality releases and related AniDB parts are indexed so earlier episodes can be selected first.</CardDescription></CardHeader>
          <CardContent className='flex flex-col gap-3'>
            {status.series_indexes.map((index) => (
              <div key={index.title} className='flex flex-wrap items-center justify-between gap-2'>
                <span className='text-sm'>{index.title}</span>
                <Badge variant={index.complete ? 'success' : index.error ? 'warning' : 'info'}>{index.complete ? 'Ready' : index.error ? 'Paused' : 'Indexing ' + index.phase + 'p'}</Badge>
                {index.error && <p className='w-full text-xs text-warning'>{index.error}</p>}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {status && status.held.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Held for review</CardTitle><CardDescription>These releases were not downloaded because a safety condition could not be proven.</CardDescription></CardHeader>
          <CardContent className='flex flex-col gap-2'>
            {Array.from(new Map(status.held.map((item) => [item.title.replace(/\s+-\s+\d+(?:v\d+)?\s*(?:\[[^\]]*\]\s*)*$/, ""), item])).values()).map((item) => (
              <div key={item.info_hash} className='flex flex-col gap-2 rounded-md border border-border/70 p-3'><a href={item.detail_url} target='_blank' rel='noreferrer' className='flex flex-col gap-1'>
                <span className='text-sm font-medium'>{item.title}</span>
                <span className='text-xs text-warning'>{item.reason}</span>
              </a>{item.reason.includes('TVDB') && <Button size='sm' variant='secondary' className='self-start' onClick={() => setMappingTitle(item.title)}>Choose TVDB show</Button>}</div>
            ))}
          </CardContent>
        </Card>
      )}
      <AnimeMappingDialog title={mappingTitle} onClose={() => setMappingTitle(null)} onSaved={() => void load()} />
    </div>
  );
}