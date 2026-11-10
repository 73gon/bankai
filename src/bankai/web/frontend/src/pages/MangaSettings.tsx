import { useEffect, useMemo, useState } from 'react';
import { BookOpen, RefreshCw, Save } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type MangaStatus, type SettingRow } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Skeleton } from '@zonsh/ui';
import { Switch } from '@zonsh/ui';

const LABELS: Record<string, { label: string; description: string; suffix?: string }> = {
  'manga.enabled': { label: 'Look for releases', description: 'Read nyaa’s English literature uploads and search the series you follow. Nothing is ever downloaded without your approval in the review.' },
  'manga.novels': { label: 'Light novels', description: 'Look for light novels too, not only manga. They are filed into their own folder.' },
  'manga.library_dir': { label: 'Manga folder', description: 'Where manga are filed: a folder per series, "Vol 01.cbz" inside. OpenPanel’s manga library.' },
  'manga.novels_dir': { label: 'Light novel folder', description: 'Where light novels are filed, the same way.' },
  'manga.poll_interval_seconds': { label: 'Newest uploads every', description: 'How often nyaa’s newest English literature uploads are read.', suffix: 'seconds' },
  'manga.search_interval_days': { label: 'Each series searched every', description: 'How often each followed series is searched for by name, to find older releases and packs.', suffix: 'days' },
  'manga.searches_per_pass': { label: 'Series searched per pass', description: 'How many followed series are searched each time the newest uploads are read.' },
  'manga.request_delay_seconds': { label: 'Pause between nyaa requests', description: 'Kept polite: nyaa is asked one search at a time.', suffix: 'seconds' },
  'manga.min_free_space_gib': { label: 'Free-space reserve', description: 'A download is refused when it would leave less than this free on the download or library disk.', suffix: 'GiB' },
  'manga.qbittorrent_category': { label: 'qBittorrent category', description: 'Manga downloads get their own category, so the anime automation never touches them.' },
  'manga.openpanel_url': { label: 'OpenPanel address', description: 'For example http://keller:6515. OpenPanel scans only at startup or when asked: with its address and an admin login, bankai asks it to scan after every download.' },
  'manga.openpanel_username': { label: 'OpenPanel admin username', description: 'An OpenPanel admin account’s name.' },
  'manga.openpanel_password': { label: 'OpenPanel admin password', description: 'Its password. Kept in config.toml.' },
};

const STATUS_WORDS: Record<string, string> = {
  review: 'to review',
  queued: 'waiting for qBittorrent',
  downloading: 'downloading',
  publishing: 'filing',
  done: 'filed',
  failed: 'failed',
  discarded: 'discarded',
  owned: 'already in the library',
};

function formatDate(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString() : 'Never';
}

export default function MangaSettings() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [status, setStatus] = useState<MangaStatus | null>(null);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [settings, manga] = await Promise.all([api.settings(), api.mangaStatus()]);
      const order = Object.keys(LABELS);
      setRows(settings.settings.filter((row) => row.key in LABELS).sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key)));
      setStatus(manga);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void api.mangaStatus().then(setStatus).catch(() => {}), 10000);
    return () => window.clearInterval(timer);
  }, []);
  const dirty = useMemo(() => Object.keys(edits), [edits]);

  async function save() {
    setBusy(true);
    try {
      for (const key of dirty) await api.setSetting(key, edits[key]);
      toast.success('Manga settings saved');
      setEdits({});
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function lookNow() {
    setBusy(true);
    try {
      const answer = await api.runManga();
      toast.success(answer.started ? 'Looking for releases now' : 'Already looking');
      setStatus(await api.mangaStatus());
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  const looking = Boolean(status?.progress?.step);
  const counts = Object.entries(status?.counts ?? {}).filter(([, count]) => count > 0);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Settings</h1>
          <p className='text-sm text-muted-foreground'>Where manga and light novels go, how bankai looks for them, and OpenPanel.</p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button variant='secondary' onClick={() => void lookNow()} disabled={busy || !status?.enabled || looking}>
            <RefreshCw data-icon='inline-start' className={cn(looking && 'animate-spin')} /> Look now
          </Button>
          <Button onClick={() => void save()} disabled={busy || dirty.length === 0}>
            <Save data-icon='inline-start' /> Save {dirty.length || ''}
          </Button>
        </div>
      </div>

      {loading && !status ? <Skeleton shape='card' className='h-44 w-full' /> : status && (
        <Card className={status.last_error ? 'border-warning/40' : 'border-success/40'}>
          <CardHeader>
            <div className='flex flex-wrap items-center justify-between gap-3'>
              <div className='flex flex-col gap-1'>
                <CardTitle className='flex items-center gap-2 text-xl sm:text-xl'><BookOpen /> {status.enabled ? 'Looking for releases' : 'Not looking'}</CardTitle>
                <CardDescription>
                  {status.last_error ? 'nyaa could not be read last time: ' + status.last_error
                    : looking ? (status.progress.step === 'search' ? 'Searching for ' + status.progress.series : 'Looking now')
                    : 'Following ' + status.followed + ' series; ' + status.searched + ' searched by name so far.'}
                </CardDescription>
              </div>
              <StatusTag variant={status.enabled ? 'success' : 'muted'}>{status.enabled ? 'Enabled' : 'Off'}</StatusTag>
            </div>
          </CardHeader>
          <CardContent className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <div><p className='text-xs text-muted-foreground'>Series followed</p><p className='font-mono text-lg'>{status.followed}</p></div>
            <div><p className='text-xs text-muted-foreground'>Series to review</p><p className='font-mono text-lg'>{status.review}</p></div>
            <div><p className='text-xs text-muted-foreground'>Last look</p><p className='text-sm'>{formatDate(status.last_poll)}</p></div>
            <div><p className='text-xs text-muted-foreground'>OpenPanel</p><p className='text-sm'>{status.openpanel ? 'Told to scan after downloads' : 'Not set up'}</p></div>
            {counts.length > 0 && (
              <div className='sm:col-span-2 lg:col-span-4'>
                <p className='text-xs text-muted-foreground'>Releases found</p>
                <p className='text-sm'>{counts.map(([key, count]) => count + ' ' + (STATUS_WORDS[key] ?? key)).join(' · ')}</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className='text-xl sm:text-xl'>Manga and light novels</CardTitle>
          <CardDescription>
            Followed are the manga and light novels AniList links to the anime in your library (source, adaptation or alternative version),
            the series in your folders, and those you follow from Search. Every release found waits in the review.
          </CardDescription>
        </CardHeader>
        <CardContent className='grid gap-5 md:grid-cols-2'>
          {rows.map((row) => {
            const meta = LABELS[row.key];
            const current = row.key in edits ? edits[row.key] : row.value;
            const isBoolean = typeof row.value === 'boolean';
            const isNumber = typeof row.value === 'number';
            return (
              <label key={row.key} className='flex items-start justify-between gap-4 rounded-control border border-border/60 p-4'>
                <span className='flex flex-col gap-1'>
                  <span className='font-medium'>{meta.label}</span>
                  <span className='text-xs leading-relaxed text-muted-foreground'>{meta.description}</span>
                </span>
                {isBoolean ? (
                  <Switch checked={Boolean(current)} onCheckedChange={(next) => setEdits((old) => ({ ...old, [row.key]: next }))} aria-label={meta.label} />
                ) : (
                  <span className='flex min-w-36 items-center gap-2'>
                    <Input
                      type={row.secret ? 'password' : isNumber ? 'number' : 'text'}
                      placeholder={row.secret ? (row.is_set ? 'set -- type to replace' : 'not set') : undefined}
                      autoComplete='off'
                      min={isNumber ? 0 : undefined}
                      value={String(current ?? '')}
                      onChange={(event) => setEdits((old) => ({ ...old, [row.key]: isNumber ? Number(event.target.value) : event.target.value }))}
                    />
                    {meta.suffix && <span className='text-xs text-muted-foreground'>{meta.suffix}</span>}
                  </span>
                )}
              </label>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
