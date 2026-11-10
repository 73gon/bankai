import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { AlertTriangle, Bot, HardDrive, Image, Images, Info, Play, RefreshCw, Rss, Save, Search, Settings2, ShieldCheck, Shuffle, Timer, Undo2, type LucideIcon } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type AnimeAutomationStatus, type SettingRow } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Card } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from '@zonsh/ui';
import { Skeleton } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Switch } from '@zonsh/ui';
import { Textarea } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';
import { Tooltip, TooltipContent, TooltipTrigger } from '@zonsh/ui';

/** A setting's name, one line saying what it does, and -- behind the info mark -- the rest. */
type Meta = { label: string; hint: string; more?: string; suffix?: string };

const META: Record<string, Meta> = {
  'anime.enabled': {
    label: 'Automatic downloads',
    hint: 'Follow Erai-raws on Nyaa and download what qualifies.',
    more: 'Every automatic item needs a trusted Erai-raws upload, German subtitles it lists, one clear AniList match, and a subtitle check after the download.',
  },
  'anime.backfill_enabled': {
    label: 'Historical backfill',
    hint: "Work through Erai-raws' older releases too.",
    more: 'Every 2160p and 1080p release is indexed first; 720p only fills old episodes that have nothing better.',
  },
  'anime.german_avc_fallback': {
    label: 'AVC when HEVC lacks German',
    hint: 'Take the AVC encode when only it lists German subtitles.',
    more: "Erai-raws does not give every encode the same subtitles. German is required; HEVC is only preferred.",
  },
  'anime.auto_download_new': {
    label: 'New episodes by themselves',
    hint: 'Download an episode aired in the last two weeks once a release with German is found.',
    more: 'Only for shows whose episode before is in the library. Older missing episodes stay as "N available" until you download them.',
  },
  'anime.max_hevc_upgrades_per_cycle': {
    label: 'HEVC upgrades per cycle',
    hint: 'AVC episodes swapped for their HEVC encode each cycle; 0 stops it.',
    more: 'HEVC is about half the size for the same episode.',
  },
  'anime.rss_url': {
    label: 'Nyaa feed',
    hint: "Erai-raws' uploader RSS on nyaa.si.",
    more: "Only an HTTPS Nyaa feed of Erai-raws is accepted, not the RSS of Erai-raws' own website.",
  },
  'anime.erai_feed_token': {
    label: 'Erai-raws feed token',
    hint: 'Lets bankai read which subtitles each release has.',
    more: 'On any show page on erai-raws.info, right-click FHD under RSS Links, copy the link and paste it here: bankai keeps only its token, and trusts that list over the Nyaa description.',
  },
  'anime.erai_username': {
    label: 'Erai-raws login',
    hint: 'Your erai-raws.info member name or e-mail.',
    more: 'Erai-raws often uploads German subtitles days after a release, into a members-only folder. With the login, such a release is taken with that file beside it, and library episodes without German get it added.',
  },
  'anime.erai_password': {
    label: 'Erai-raws password',
    hint: 'Kept in config.toml.',
    more: 'After a refused login bankai waits an hour before trying again, or until it is changed here.',
  },
  'transfer.anime_shows_dir': {
    label: 'Library folder',
    hint: 'Where shows are laid out for Jellyfin.',
    more: 'Each show is laid out by its AniList entries, with the NFO files Jellyfin reads.',
  },
  'anime.min_free_space_gib': { label: 'Free-space reserve', hint: 'New downloads pause at or below this.', suffix: 'GiB' },
  'anime.max_concurrent_transfers': {
    label: 'Published at once',
    hint: 'Finished downloads moved into the library at the same time.',
    more: 'Downloading is not limited here: qBittorrent decides how many run at once.',
  },
  'anime.poll_interval_seconds': { label: 'Feed check every', hint: 'How often the Nyaa feed is read.', suffix: 'sec' },
  'anime.settle_minutes': { label: 'Settling window', hint: 'Waits for other encodes before choosing the best.', suffix: 'min' },
  'anime.max_enqueues_per_cycle': {
    label: 'Episodes per cycle',
    hint: 'Checked episodes queued per cycle.',
    more: 'A large ordered backlog is fine: qBittorrent controls how many download at once.',
  },
  'anime.backfill_request_delay_seconds': { label: 'Backfill page delay', hint: 'Pause between older catalogue pages.', suffix: 'sec' },
};

type Section = { id: string; title: string; description: string; icon: LucideIcon; keys?: string[] };

const SECTIONS: Section[] = [
  { id: 'automation', title: 'Automation', description: 'What bankai downloads by itself.', icon: Bot, keys: ['anime.enabled', 'anime.auto_download_new', 'anime.backfill_enabled', 'anime.german_avc_fallback', 'anime.max_hevc_upgrades_per_cycle'] },
  { id: 'sources', title: 'Sources', description: 'Where releases and their subtitle lists come from.', icon: Rss, keys: ['anime.rss_url', 'anime.erai_feed_token', 'anime.erai_username', 'anime.erai_password'] },
  { id: 'storage', title: 'Library & storage', description: 'Where episodes go, and the space kept free.', icon: HardDrive, keys: ['transfer.anime_shows_dir', 'anime.min_free_space_gib', 'anime.max_concurrent_transfers'] },
  { id: 'pacing', title: 'Pacing', description: 'How often each cycle runs, and how much it does.', icon: Timer, keys: ['anime.poll_interval_seconds', 'anime.settle_minutes', 'anime.max_enqueues_per_cycle', 'anime.backfill_request_delay_seconds'] },
];

// Shown by their own sections (Search, Episode pictures, Artwork), not as plain rows.
const OWN_KEYS = new Set([
  'anime.publishers', 'anime.nekobt_languages',
  'anime.thumbs_enabled', 'anime.thumb_mode', 'anime.thumb_percent', 'anime.thumb_time_seconds', 'anime.thumb_width',
  'anime.artwork_tmdb', 'metadata.tmdb_api_key',
]);
const THUMB_KEYS = ['anime.thumb_mode', 'anime.thumb_percent', 'anime.thumb_time_seconds', 'anime.thumb_width'];
const PLACED = new Set(SECTIONS.flatMap((section) => section.keys ?? []));

/** Seconds as m:ss. */
function clock(seconds: number) {
  const whole = Math.max(0, Math.round(seconds));
  return Math.floor(whole / 60) + ':' + String(whole % 60).padStart(2, '0');
}

function formatDate(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Never';
}

/** The rest of a setting's explanation, on hover or focus. */
function More({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <Tooltip>
      <TooltipTrigger
        render={<button type='button' className='inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-ring' aria-label='More about this setting' />}
      >
        <Info className='size-3.5' />
      </TooltipTrigger>
      <TooltipContent variant='text' className='max-w-80'>{text}</TooltipContent>
    </Tooltip>
  );
}

/** One setting: its name and what it does on the left, the control on the right. */
function Row({ title, hint, more, edited, children }: { title: string; hint?: ReactNode; more?: string; edited?: boolean; children: ReactNode }) {
  return (
    <Item size='sm' className='flex-wrap px-4 py-2.5 sm:flex-nowrap'>
      <ItemContent>
        <ItemTitle>
          {title}
          <More text={more} />
          {edited && <span className='size-1.5 rounded-full bg-info' title='Changed, not saved yet' aria-label='changed' />}
        </ItemTitle>
        {hint && <ItemDescription>{hint}</ItemDescription>}
      </ItemContent>
      <ItemActions className='ml-auto'>{children}</ItemActions>
    </Item>
  );
}

/** A group of settings: a tile of the page's one card, touching its neighbours, parted by hairlines
 *  (`border-t`, and `xl:border-r` on the left of a pair). */
function Tile({ title, icon: Icon, actions, className, children }: { title?: string; icon?: LucideIcon; actions?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className={cn('flex min-w-0 flex-col border-border/60 pb-1.5', className)}>
      {title && (
        <div className='flex min-h-10 items-center justify-between gap-3 px-4 pt-3'>
          <h2 className='flex items-center gap-2 eyebrow'>
            {Icon && <Icon className='size-3.5' aria-hidden='true' />} {title}
          </h2>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

/** A figure of the status block. */
function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className='flex min-w-0 flex-col gap-0.5'>
      <dt className='text-xs text-muted-foreground'>{label}</dt>
      <dd className='truncate font-mono text-sm tabular-nums text-foreground'>{children}</dd>
    </div>
  );
}

export default function AnimeSettings() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [status, setStatus] = useState<AnimeAutomationStatus | null>(null);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // A frame as the episode-picture setting being edited would take it.
  const [preview, setPreview] = useState<{ url: string; episode: string; position: number } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [sample, setSample] = useState(0);

  async function load() {
    // The skeleton only while there is nothing to show yet: coming back keeps the page.
    if (!rows.length) setLoading(true);
    try {
      const [settings, automation] = await Promise.all([api.settings(), api.animeAutomation()]);
      setRows(settings.settings.filter((row) => row.key.startsWith('anime.') || row.key === 'transfer.anime_shows_dir' || row.key === 'metadata.tmdb_api_key'));
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

  // Rows no section names (a setting added later) still show, under Other.
  const others = rows.filter((row) => !PLACED.has(row.key) && !OWN_KEYS.has(row.key));

  async function save() {
    setBusy(true);
    try {
      for (const key of dirty) await api.setSetting(key, edits[key]);
      toast.success(dirty.some((key) => THUMB_KEYS.includes(key))
        ? 'Anime settings saved; every episode picture is taken again at the new setting, about 20 a minute'
        : 'Anime settings saved');
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

  function setting(key: string): any {
    const row = rows.find((item) => item.key === key);
    return row ? value(row) : undefined;
  }

  function edit(key: string, next: any) {
    setEdits((old) => ({ ...old, [key]: next }));
  }

  async function showPreview(next = sample) {
    setPreviewing(true);
    try {
      const frame = await api.thumbPreview({
        mode: setting('anime.thumb_mode') ?? 'percent',
        percent: Number(setting('anime.thumb_percent') ?? 50),
        time_seconds: Number(setting('anime.thumb_time_seconds') ?? 600),
        width: Number(setting('anime.thumb_width') ?? 960),
        sample: next,
      });
      setPreview((old) => {
        if (old) URL.revokeObjectURL(old.url);
        return frame;
      });
      setSample(next);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setPreviewing(false);
    }
  }

  async function regenerate() {
    try {
      await api.regenerateThumbs();
      toast.success('Every episode picture is taken again, about 20 a minute; the Dashboard shows how far it is');
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  /** A plain setting's row: a switch, a number with its unit, or a text (secret) field. */
  function plainRow(key: string) {
    const row = rows.find((item) => item.key === key);
    if (!row) return null;
    const meta = META[key] ?? { label: key, hint: '' };
    const current = value(row);
    const isNumber = typeof row.value === 'number';
    let control: ReactNode;
    if (typeof row.value === 'boolean') {
      control = <Switch checked={Boolean(current)} onCheckedChange={(next) => edit(key, next)} aria-label={meta.label} />;
    } else if (isNumber) {
      control = (
        <span className='flex items-center gap-2'>
          <Input className='w-24 text-right' type='number' min={0} aria-label={meta.label} value={String(current ?? '')} onChange={(event) => edit(key, Number(event.target.value))} />
          <span className='w-8 text-xs text-muted-foreground'>{meta.suffix ?? ''}</span>
        </span>
      );
    } else {
      control = (
        <Input
          className='w-full sm:w-60'
          type={row.secret ? 'password' : 'text'}
          autoComplete='off'
          aria-label={meta.label}
          placeholder={row.secret ? (row.is_set ? 'Set: type to replace' : 'Not set') : undefined}
          title={!row.secret && current ? String(current) : undefined}
          value={row.secret ? String(edits[key] ?? '') : String(current ?? '')}
          onChange={(event) => edit(key, event.target.value)}
        />
      );
    }
    return <Row key={key} title={meta.label} hint={meta.hint} more={meta.more} edited={key in edits}>{control}</Row>;
  }

  function rowsOf(keys: string[]) {
    return keys.filter((key) => rows.some((row) => row.key === key)).map((key) => plainRow(key));
  }

  /** A list setting (one entry per line) as it is typed. */
  function lines(key: string) {
    const current = setting(key);
    return Array.isArray(current) ? current.join('\n') : String(current ?? '');
  }

  const thumbMode: string = setting('anime.thumb_mode') ?? 'percent';
  const thumbSeconds = Number(setting('anime.thumb_time_seconds') ?? 600);
  const tmdbKey = rows.find((row) => row.key === 'metadata.tmdb_api_key');

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Settings</h1>
          <p className='text-sm text-muted-foreground'>The Erai-raws automation, its sources, storage and pictures.</p>
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

      {loading ? (
        <Skeleton shape='card' className='h-[40rem] w-full' />
      ) : (
        // One card: the groups tile it, two abreast on a wide screen, touching, parted by hairlines.
        <Card className='grid gap-0 overflow-hidden [--card-padding:0] xl:grid-cols-2'>
          {status && (
            <Tile className='xl:col-span-2'>
              <div className='flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3'>
                {status.paused ? <AlertTriangle className='size-4 text-warning' aria-hidden='true' /> : <ShieldCheck className='size-4 text-success' aria-hidden='true' />}
                <span className='font-medium'>{status.paused ? 'Automation paused' : 'Automation ready'}</span>
                <StatusTag variant={status.paused ? 'warning' : 'success'}>{status.running ? 'Checking now' : status.enabled ? 'Enabled' : 'Disabled'}</StatusTag>
                <span className='text-[13px] text-muted-foreground'>{status.pause_reason || 'All safety checks pass.'}</span>
              </div>
              <dl className='grid grid-cols-2 gap-x-6 gap-y-2 px-4 pb-2 pt-3 sm:grid-cols-3 lg:grid-cols-5'>
                <Stat label='Library free'>{status.free_space_gib == null ? 'Unavailable' : status.free_space_gib + ' GiB'}</Stat>
                <Stat label='Downloads free'>{status.download_free_space_gib == null ? 'Checking' : status.download_free_space_gib + ' GiB'}</Stat>
                <Stat label='Reserve'>{status.min_free_space_gib} GiB</Stat>
                <Stat label='Last check'>{formatDate(status.last_success)}</Stat>
                <Stat label='Last cycle'>{status.last_enqueued} queued</Stat>
                <Stat label='Backfill'><StatusTag variant={status.backfill.complete ? 'success' : 'info'}>{status.backfill.phase}</StatusTag></Stat>
                <Stat label='Search branches'>{status.backfill.queries_completed.toLocaleString()}</Stat>
                <Stat label='1080p+ episodes'>{status.backfill.found_1080.toLocaleString()}</Stat>
                <Stat label='720p fallbacks'>{status.backfill.fallback_720.toLocaleString()}</Stat>
                <Stat label='Feed'>{status.feed_source}</Stat>
              </dl>
              {status.backfill.error && <p className='px-4 pb-2 text-xs text-warning'>{status.backfill.error} New feed episodes go on regardless.</p>}
            </Tile>
          )}

          {SECTIONS.filter((item) => item.keys).map((item) => (
            <Tile key={item.id} title={item.title} icon={item.icon} className={cn('border-t', item.id === 'automation' || item.id === 'storage' ? 'xl:border-r' : '')}>
              <ItemGroup className='gap-0'>{rowsOf(item.keys!)}</ItemGroup>
            </Tile>
          ))}

          <Tile title='Artwork' icon={Images} className='border-t xl:border-r'>
            <ItemGroup className='gap-0'>
              <Row
                title='Artwork from TMDB'
                hint="Backdrops, logos, landscape and season posters."
                more="Off keeps only AniList's poster and banner. Pictures chosen by hand (a show's Artwork) stay either way."
                edited={'anime.artwork_tmdb' in edits}
              >
                <Switch checked={Boolean(setting('anime.artwork_tmdb'))} onCheckedChange={(next) => edit('anime.artwork_tmdb', next)} aria-label='Artwork from TMDB' />
              </Row>
              {tmdbKey && (
                <Row title='TMDB API key' hint='v3 key or v4 read token.' more='From themoviedb.org, under Settings → API.' edited={tmdbKey.key in edits}>
                  <Input className='w-full sm:w-60' type='password' autoComplete='off' aria-label='TMDB API key' placeholder={tmdbKey.is_set ? 'Set: type to replace' : 'Not set'} value={String(edits[tmdbKey.key] ?? '')} onChange={(event) => edit(tmdbKey.key, event.target.value)} />
                </Row>
              )}
            </ItemGroup>
          </Tile>

          <Tile title='Search' icon={Search} className='border-t'>
            <div className='grid gap-3 px-4 pb-2.5 pt-1 sm:grid-cols-2'>
              {rows.some((row) => row.key === 'anime.publishers') && (
                <label className='flex min-w-0 flex-col gap-1.5'>
                  <span className='flex items-center gap-2 text-sm font-medium leading-snug text-foreground'>
                    Publishers
                    <More text='Release groups worth knowing, one per line: the publisher filter offers each. Matched without spaces or punctuation: "PHT Mini" finds [PHTMini].' />
                    {'anime.publishers' in edits && <span className='size-1.5 rounded-full bg-info' aria-label='changed' />}
                  </span>
                  <Textarea className='min-h-24 font-mono text-sm' aria-label='Publishers, one per line' value={lines('anime.publishers')} onChange={(event) => edit('anime.publishers', event.target.value)} />
                </label>
              )}
              {rows.some((row) => row.key === 'anime.nekobt_languages') && (
                <label className='flex min-w-0 flex-col gap-1.5'>
                  <span className='flex items-center gap-2 text-sm font-medium leading-snug text-foreground'>
                    nekobt languages
                    <More text="The subtitle and audio languages nekobt.to's filters offer, one code per line, as nekobt writes them: de, en, ja, pt-br, es-419, zh-hans." />
                    {'anime.nekobt_languages' in edits && <span className='size-1.5 rounded-full bg-info' aria-label='changed' />}
                  </span>
                  <Textarea className='min-h-24 font-mono text-sm' aria-label='nekobt languages, one code per line' value={lines('anime.nekobt_languages')} onChange={(event) => edit('anime.nekobt_languages', event.target.value)} />
                </label>
              )}
            </div>
          </Tile>

          <Tile
            title='Episode pictures'
            icon={Image}
            className='border-t xl:col-span-2'
            actions={(
              <Button variant='ghost' size='sm' onClick={() => void regenerate()} disabled={busy}>
                <RefreshCw data-icon='inline-start' /> Take all again
              </Button>
            )}
          >
            <div className='grid lg:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]'>
              <ItemGroup className='gap-0'>
                <Row
                  title='Take episode pictures'
                  hint="Each episode's picture in Jellyfin, from the video itself."
                  more="Jellyfin's own grabber takes its frame 10% in, usually the opening song; bankai writes <episode>-thumb.jpg instead. Off: no new pictures are taken; those already there stay."
                  edited={'anime.thumbs_enabled' in edits}
                >
                  <Switch checked={Boolean(setting('anime.thumbs_enabled'))} onCheckedChange={(next) => edit('anime.thumbs_enabled', next)} aria-label='Take episode pictures' />
                </Row>
                <Row
                  title='Where in the episode'
                  hint={thumbMode === 'time' ? 'A fixed time in; shorter episodes at the share.' : 'A share of its length.'}
                  more='Saving a new position or width takes every picture again, about 20 a minute.'
                  edited={['anime.thumb_mode', 'anime.thumb_percent', 'anime.thumb_time_seconds'].some((key) => key in edits)}
                >
                  <span className='flex flex-wrap items-center justify-end gap-2'>
                    <ToggleGroup aria-label='Where in the episode' value={[thumbMode]} onValueChange={(values) => { if (values[0]) edit('anime.thumb_mode', values[0]); }}>
                      <ToggleGroupItem value='percent'>Share</ToggleGroupItem>
                      <ToggleGroupItem value='time'>Time</ToggleGroupItem>
                    </ToggleGroup>
                    {thumbMode === 'time' && (
                      <span className='flex items-center gap-1.5'>
                        <Input className='w-14 text-right' type='number' min={0} aria-label='Minutes in' value={String(Math.floor(thumbSeconds / 60))} onChange={(event) => edit('anime.thumb_time_seconds', Math.max(0, Number(event.target.value)) * 60 + (thumbSeconds % 60))} />
                        <span className='text-xs text-muted-foreground'>min</span>
                        <Input className='w-14 text-right' type='number' min={0} max={59} aria-label='Seconds in' value={String(thumbSeconds % 60)} onChange={(event) => edit('anime.thumb_time_seconds', Math.floor(thumbSeconds / 60) * 60 + Math.min(59, Math.max(0, Number(event.target.value))))} />
                        <span className='text-xs text-muted-foreground'>s</span>
                      </span>
                    )}
                    <span className='flex items-center gap-1.5'>
                      <Input className='w-16 text-right' type='number' min={1} max={99} aria-label='Share of the episode' value={String(setting('anime.thumb_percent') ?? '')} onChange={(event) => edit('anime.thumb_percent', Number(event.target.value))} />
                      <span className='text-xs text-muted-foreground'>%</span>
                    </span>
                  </span>
                </Row>
                <Row title='Picture width' hint='The height follows the video; 960 is plenty.' edited={'anime.thumb_width' in edits}>
                  <span className='flex items-center gap-2'>
                    <Input className='w-24 text-right' type='number' min={320} max={3840} aria-label='Picture width' value={String(setting('anime.thumb_width') ?? '')} onChange={(event) => edit('anime.thumb_width', Number(event.target.value))} />
                    <span className='w-8 text-xs text-muted-foreground'>px</span>
                  </span>
                </Row>
              </ItemGroup>
              {/* The frame a library episode would get with the values beside it; nothing is written. */}
              <div className='flex flex-col gap-2 px-4 pb-2.5 pt-1'>
                <div className='relative flex aspect-video items-center justify-center overflow-hidden rounded-control border border-border/60 bg-field-surface'>
                  {preview ? <img src={preview.url} alt={'Frame of ' + preview.episode} className='h-full w-full object-contain' /> : !previewing && (
                    <span className='px-4 text-center text-xs text-muted-foreground'>The frame an episode would get. Nothing is saved.</span>
                  )}
                  {previewing && <span className='absolute inset-0 flex items-center justify-center bg-background/40'><Spinner label='Taking the frame' /></span>}
                </div>
                <div className='flex items-center justify-between gap-2'>
                  <span className='min-w-0 truncate text-xs text-muted-foreground' title={preview?.episode}>
                    {preview ? <><span className='font-mono'>{clock(preview.position)}</span> into {preview.episode}</> : 'Preview'}
                  </span>
                  <span className='flex shrink-0 gap-1'>
                    <Button variant='secondary' size='sm' onClick={() => void showPreview()} disabled={previewing}>
                      <Image data-icon='inline-start' /> {preview ? 'Again' : 'Show'}
                    </Button>
                    <Button variant='ghost' size='icon-sm' onClick={() => void showPreview(sample + 1)} disabled={previewing} aria-label='Another episode' title='Another episode'>
                      <Shuffle />
                    </Button>
                  </span>
                </div>
              </div>
            </div>
          </Tile>

          {others.length > 0 && (
            <Tile title='Other' icon={Settings2} className='border-t xl:col-span-2'>
              <ItemGroup className='gap-0'>{rowsOf(others.map((row) => row.key))}</ItemGroup>
            </Tile>
          )}
        </Card>
      )}

      {/* Changes wait here, in view wherever the page is scrolled, until saved or discarded. */}
      {dirty.length > 0 && (
        <div className='sticky bottom-4 z-10 flex flex-wrap items-center gap-2 self-center rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0'>
          <span className='px-1 text-xs text-muted-foreground'>
            <span className='font-mono tabular-nums text-foreground'>{dirty.length}</span> unsaved change{dirty.length === 1 ? '' : 's'}
          </span>
          <Button size='sm' variant='ghost' onClick={() => setEdits({})} disabled={busy}>
            <Undo2 data-icon='inline-start' /> Discard
          </Button>
          <Button size='sm' onClick={() => void save()} disabled={busy}>
            <Save data-icon='inline-start' /> {busy ? 'Saving…' : 'Save'}
          </Button>
        </div>
      )}
    </div>
  );
}
