import { createPortal } from 'react-dom';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Download, ExternalLink, HardDrive, Magnet, RefreshCw, Search, Star, Trash2, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  api,
  type DirectDownloadBody,
  type DirectRelease,
  type DirectSearch,
  type DirectTarget,
  type FollowDetail,
  type FollowEpisode,
  type FollowPreview,
  type FollowQuality,
  type FollowRoots,
  type FollowState,
  type MovieReleases,
} from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/utils';
import { StatusTag, type StatusVariant } from '@/components/status-tag';
import { Button } from '@zonsh/ui';
import { Checkbox } from '@zonsh/ui';
import { MagnetReplaceDialog } from '@/components/MagnetReplaceDialog';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@zonsh/ui';

const QUALITIES: FollowQuality[] = ['2160p', '1080p', '720p', '480p'];
const QUALITY_ITEMS = QUALITIES.map((value) => ({ value, label: value }));

const STATE_LABEL: Record<FollowState, string> = {
  german: 'German',
  english: 'English',
  downloading: 'Downloading',
  missing: 'Missing',
  upcoming: 'Not aired',
};
const STATE_VARIANT: Record<FollowState, StatusVariant> = {
  german: 'success',
  english: 'info',
  downloading: 'torrent',
  missing: 'warning',
  upcoming: 'muted',
};

const code = (season: number, episode?: number | null) =>
  'S' + String(season).padStart(2, '0') + (episode ? 'E' + String(episode).padStart(2, '0') : '');

function FolderSelect({ label, value, targets, onChange }: { label: string; value: string | undefined; targets: DirectTarget[]; onChange: (root: string) => void }) {
  const items = targets.map((row) => ({ value: row.root, label: row.label }));
  return (
    <label className='flex min-w-0 flex-col gap-1 text-xs text-muted-foreground'>
      {label}
      <Select items={items} value={value ?? ''} onValueChange={(next) => next && onChange(next)}>
        <SelectTrigger className='w-full min-w-0' aria-label={label}><SelectValue placeholder='Pick a folder' /></SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {targets.map((row) => (
              <SelectItem key={row.root} value={row.root} disabled={!row.exists}>{row.label} — {row.root}</SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </label>
  );
}

function QualitySelect({ value, onChange }: { value: FollowQuality; onChange: (value: FollowQuality) => void }) {
  return (
    <label className='flex flex-col gap-1 text-xs text-muted-foreground'>
      Quality
      <Select items={QUALITY_ITEMS} value={value} onValueChange={(next) => next && onChange(next as FollowQuality)}>
        <SelectTrigger className='w-28' aria-label='Quality'><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectGroup>{QUALITY_ITEMS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectGroup>
        </SelectContent>
      </Select>
    </label>
  );
}

/**
 * Following a show in English: its seasons and episodes, what the library has,
 * an estimate of what downloading the rest takes, and the two folders it goes to.
 */
export function FollowPanel({ tmdbId, onChange }: { tmdbId: number; onChange?: () => void }) {
  const [quality, setQuality] = useState<FollowQuality>('1080p');
  const [preview, setPreview] = useState<FollowPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [roots, setRoots] = useState<FollowRoots>({});
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.followPreview(tmdbId, quality)
      .then((answer) => {
        if (cancelled) return;
        setPreview(answer);
        setRoots((old) => (old.German && old.English ? old : answer.roots));
        if (answer.followed && answer.followed.quality !== quality && !preview) setQuality(answer.followed.quality);
      })
      .catch((error: any) => !cancelled && toast.error(error.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tmdbId, quality]);

  const targets = preview?.targets ?? [];
  const byLanguage = (language: string) => targets.filter((row) => row.language === language);

  async function save() {
    if (!roots.German || !roots.English) return;
    setBusy(true);
    try {
      const row = await api.follow({ tmdb_id: tmdbId, quality, root_german: roots.German, root_english: roots.English });
      toast.success(preview?.followed ? 'Saved' : `Following ${preview?.title} — the first downloads start in a moment`);
      // The answer is all that changed: asking for the preview again would search the indexers once more.
      setPreview((old) => (old ? { ...old, followed: row, roots: row.roots } : old));
      onChange?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      await api.unfollow(tmdbId);
      toast.success('No longer followed; the files stay');
      onChange?.();
      setPreview(await api.followPreview(tmdbId, quality));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading && !preview) {
    return (
      <div className='flex min-h-40 flex-col items-center justify-center gap-2 text-sm text-muted-foreground'>
        <Spinner /> Counting episodes and looking at the releases…
      </div>
    );
  }
  if (!preview) return null;
  const { counts, estimate } = preview;
  const toGet = counts.missing;
  const stored = preview.followed;
  const changed = !!stored && (stored.quality !== quality || stored.roots.German !== roots.German || stored.roots.English !== roots.English);

  return (
    <div className='flex flex-col gap-4'>
      <div className='grid gap-3 sm:grid-cols-3'>
        <div className='flex flex-col gap-0.5 rounded-control surface-container p-3'>
          <span className='eyebrow text-[11px]'>Show</span>
          <span className='font-mono text-lg tabular-nums text-foreground'>{preview.seasons.length} seasons</span>
          <span className='text-xs text-muted-foreground'>
            {counts.episodes} episodes{counts.aired < counts.episodes ? `, ${counts.aired} aired` : ''}{preview.status ? ` · ${preview.status}` : ''}
          </span>
        </div>
        <div className='flex flex-col gap-0.5 rounded-control surface-container p-3'>
          <span className='eyebrow text-[11px]'>In the library</span>
          <span className='font-mono text-lg tabular-nums text-foreground'>{counts.german + counts.english} of {counts.aired}</span>
          <span className='text-xs text-muted-foreground'>
            {counts.german} German · {counts.english} English{counts.downloading ? ` · ${counts.downloading} downloading` : ''}
          </span>
        </div>
        <div className='flex flex-col gap-0.5 rounded-control surface-container p-3'>
          <span className='eyebrow text-[11px]'>To download</span>
          <span className='font-mono text-lg tabular-nums text-foreground'>≈ {formatBytes(estimate.bytes)}</span>
          <span className='text-xs text-muted-foreground'>
            {toGet} episodes, ~{formatBytes(estimate.per_episode)} each
          </span>
        </div>
      </div>
      <p className='text-xs text-muted-foreground'>
        {estimate.basis === 'releases'
          ? `Estimated from ${estimate.samples} ${quality} releases on the indexers.`
          : 'Estimated from the running time: the indexers gave too little to go by.'}
        {preview.problem ? ` ${preview.problem}` : ''}
      </p>

      <Table dense>
        <TableHeader>
          <TableRow>
            <TableHead>Season</TableHead>
            <TableHead numeric>Episodes</TableHead>
            <TableHead numeric>German</TableHead>
            <TableHead numeric>English</TableHead>
            <TableHead numeric>To download</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {preview.seasons.map((row) => (
            <TableRow key={row.season}>
              <TableCell className='font-mono text-xs'>{code(row.season)}</TableCell>
              <TableCell numeric className='text-xs text-muted-foreground'>{row.aired < row.episodes ? `${row.aired}/${row.episodes}` : row.episodes}</TableCell>
              <TableCell numeric className='text-xs'>{row.german || '—'}</TableCell>
              <TableCell numeric className='text-xs'>{row.english || '—'}</TableCell>
              <TableCell numeric className='text-xs'>{row.missing || '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <div className='grid gap-3 sm:grid-cols-[auto_1fr_1fr]'>
        <QualitySelect value={quality} onChange={setQuality} />
        <FolderSelect label='With German audio' value={roots.German} targets={byLanguage('German')} onChange={(root) => setRoots((old) => ({ ...old, German: root }))} />
        <FolderSelect label='English only' value={roots.English} targets={byLanguage('English')} onChange={(root) => setRoots((old) => ({ ...old, English: root }))} />
      </div>
      <p className='text-xs text-muted-foreground'>
        Every aired episode is downloaded, a release with German first. Each file goes by its own audio: with German into the
        German folder, English only into the English one. An English-only episode is replaced once a German release turns up.
        New episodes are picked up as they air.
      </p>
      {preview.followed ? (
        <div className='flex flex-wrap items-center justify-end gap-2'>
          <span className='mr-auto flex items-center gap-1.5 text-xs text-muted-foreground'>
            <Check className='size-3.5 text-success' /> Following. Progress per episode: Library → Movies &amp; Shows → {preview.title}.
          </span>
          <Button variant='ghost' onClick={stop} disabled={busy}><Trash2 data-icon='inline-start' /> Unfollow</Button>
          {changed && (
            <Button variant='secondary' onClick={save} disabled={busy || loading || !roots.German || !roots.English}>
              {busy && <Spinner />} Save changes
            </Button>
          )}
          <Button onClick={() => navigate(`/mas/library?follow=${tmdbId}`)}><HardDrive data-icon='inline-start' /> Open in library</Button>
        </div>
      ) : (
        <div className='flex justify-end'>
          <Button onClick={save} disabled={busy || loading || !roots.German || !roots.English}>
            {busy ? <Spinner /> : <Download data-icon='inline-start' />}
            {toGet ? `Follow & download ${toGet} episodes` : 'Follow'}
          </Button>
        </div>
      )}
    </div>
  );
}

/** A film's releases, the best first: one click downloads it into the German or English folder by its audio. */
export function MovieTorrentPanel({ tmdbId }: { tmdbId: number }) {
  const [quality, setQuality] = useState<FollowQuality>('1080p');
  const [answer, setAnswer] = useState<MovieReleases | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.movieReleases(tmdbId, quality)
      .then((result) => !cancelled && setAnswer(result))
      .catch((error: any) => !cancelled && toast.error(error.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [tmdbId, quality]);

  async function download(row: DirectRelease) {
    if (!answer) return;
    setBusy(row.id);
    try {
      const job = await api.directDownload({
        id: row.id, kind: 'movie', root: 'by-language', title: answer.title, year: answer.year, season: null,
        names: answer.german_title ? [answer.german_title] : [],
      });
      toast.success(job.status === 'duplicate' ? 'Already queued' : `Downloading ${row.title}`);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  const held = answer ? [...answer.held.German.map((path) => ['German', path]), ...answer.held.English.map((path) => ['English', path])] : [];

  return (
    <div className='flex flex-col gap-3'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <QualitySelect value={quality} onChange={setQuality} />
        <p className='max-w-md text-xs text-muted-foreground'>
          With German audio it goes into the German movies folder, English only into the English one; a German copy replaces an English one.
        </p>
      </div>
      {held.length > 0 && (
        <div className='flex flex-col gap-1 rounded-control surface-container p-3 text-xs'>
          <span className='eyebrow text-[11px]'>In the library</span>
          {held.map(([language, path]) => (
            <span key={path} className='flex items-center gap-2'>
              <StatusTag variant={language === 'German' ? 'success' : 'info'}>{language}</StatusTag>
              <span className='truncate font-mono text-[0.68rem] text-muted-foreground' title={path}>{path}</span>
            </span>
          ))}
        </div>
      )}
      {loading && !answer ? (
        <div className='flex min-h-32 items-center justify-center gap-2 text-sm text-muted-foreground'><Spinner /> Searching Prowlarr…</div>
      ) : answer && answer.items.length === 0 ? (
        <p className='py-6 text-center text-sm text-muted-foreground'>{answer.problem ?? `None of the ${answer.found} releases is this film.`}</p>
      ) : (
        <ReleaseTable
          rows={(answer?.items ?? []).slice(0, 25)}
          best={answer?.best ?? null}
          busy={busy}
          onDownload={download}
        />
      )}
    </div>
  );
}

function ReleaseTable({ rows, best, busy, onDownload }: { rows: (DirectRelease & { german?: boolean; fits?: boolean })[]; best: string | null; busy: string | null; onDownload: (row: DirectRelease) => void }) {
  return (
    <Table dense>
      <TableHeader>
        <TableRow>
          <TableHead>Release</TableHead>
          <TableHead numeric>Size</TableHead>
          <TableHead numeric>Seeders</TableHead>
          <TableHead><span className='sr-only'>Actions</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id} className={row.fits === false ? 'opacity-60' : ''}>
            <TableCell className='max-w-[30rem] py-2'>
              <p className='flex items-center gap-1.5 text-foreground'>
                {row.id === best && <Star className='size-3.5 shrink-0 text-warning' aria-label='Best match' />}
                <span className='truncate' title={row.title}>{row.title}</span>
              </p>
              <p className='flex flex-wrap gap-1 pt-0.5 text-[0.68rem] text-muted-foreground'>
                {row.languages.includes('German') && <StatusTag variant='success'>German</StatusTag>}
                <span>{[row.resolution, row.source, row.codec?.toUpperCase(), row.indexer].filter(Boolean).join(' · ')}</span>
              </p>
            </TableCell>
            <TableCell numeric className='py-2 text-xs text-muted-foreground'>{formatBytes(row.size_bytes)}</TableCell>
            <TableCell numeric className='py-2'>
              <StatusTag variant={row.seeders >= 10 ? 'success' : row.seeders > 0 ? 'warning' : 'muted'}>{row.seeders}</StatusTag>
            </TableCell>
            <TableCell className='whitespace-nowrap py-2 text-right'>
              {row.info_url && (
                <Button size='icon-sm' variant='ghost' aria-label='Open on the indexer' title='Open on the indexer' nativeButton={false} render={<a href={row.info_url} target='_blank' rel='noreferrer' />}>
                  <ExternalLink />
                </Button>
              )}
              <Button size='sm' variant={row.id === best ? 'primary' : 'secondary'} onClick={() => onDownload(row)} disabled={busy !== null}>
                {busy === row.id ? <Spinner /> : <Download data-icon='inline-start' />} Download
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** A Prowlarr search for one episode or season of a followed show; the release picked is filed by language. */
export function TorrentPickerDialog({ open, onClose, query, download, title }: {
  open: boolean;
  onClose: () => void;
  query: string;
  download: Omit<DirectDownloadBody, 'id'>;
  title: string;
}) {
  const [draft, setDraft] = useState(query);
  const [result, setResult] = useState<DirectSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function search(text: string) {
    if (!text.trim()) return;
    setLoading(true);
    try {
      setResult(await api.directSearch(text.trim(), 'show', { minSeeders: 1 }));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    setDraft(query);
    setResult(null);
    void search(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, query]);

  const rows = useMemo(() => {
    const items = result?.items ?? [];
    // German first, then seeders: the order the automation picks in.
    return [...items].sort((a, b) => Number(b.languages.includes('German')) - Number(a.languages.includes('German')) || b.seeders - a.seeders);
  }, [result]);

  async function pick(row: DirectRelease) {
    setBusy(row.id);
    try {
      const job = await api.directDownload({ ...download, id: row.id });
      toast.success(job.status === 'duplicate' ? 'Already queued' : `Downloading ${row.title}`);
      onClose();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent size='lg' className='max-h-[90vh] max-w-4xl overflow-y-auto'>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>Pick the release. Files with German go into the German folder, English-only ones into the English folder.</DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          <div className='flex gap-2'>
            <InputGroup className='flex-1'>
              <InputGroupAddon><Search aria-hidden='true' /></InputGroupAddon>
              <InputGroupInput value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && search(draft)} aria-label='Search Prowlarr' />
            </InputGroup>
            <Button variant='secondary' onClick={() => search(draft)} disabled={loading}>{loading ? <Spinner /> : <Search data-icon='inline-start' />} Search</Button>
          </div>
          {loading && !result ? (
            <div className='flex min-h-32 items-center justify-center gap-2 text-sm text-muted-foreground'><Spinner /> Searching Prowlarr…</div>
          ) : result && rows.length === 0 ? (
            <p className='py-6 text-center text-sm text-muted-foreground'>{result.problem ?? 'Prowlarr found nothing.'}</p>
          ) : (
            <ReleaseTable rows={rows.slice(0, 40)} best={null} busy={busy} onDownload={pick} />
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

/** A followed show in the library drawer: every episode with what the library has of it, searchable one by one. */
/**
 * A followed show's episodes by season. ``actions``: an element (the drawer header's button row)
 * its Search season / Look now / Settings buttons are drawn into, beside Artwork and TMDB.
 */
export function FollowedShowView({ tmdbId, onChanged, actions, cardKey }: { tmdbId: number; onChanged?: () => void; actions?: HTMLElement | null; cardKey?: string | null }) {
  const [detail, setDetail] = useState<FollowDetail | null>(null);
  const [tab, setTab] = useState<string | null>(null);
  // Files ticked for Replace with magnet link (only with the library card they are on).
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [magnetOpen, setMagnetOpen] = useState(false);
  const [picker, setPicker] = useState<{ query: string; title: string; season: number; episodes: string[] } | null>(null);
  const [settings, setSettings] = useState(false);

  async function load() {
    try {
      setDetail(await api.followDetail(tmdbId));
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tmdbId]);

  async function lookNow() {
    try {
      await api.followRun(tmdbId);
      toast.success('Looking for the missing episodes now');
      window.setTimeout(() => void load(), 15_000);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  const seasons = useMemo(() => [...new Set((detail?.episodes ?? []).map((row) => row.season))].sort((a, b) => a - b), [detail]);
  if (!detail) return <div className='flex min-h-40 items-center justify-center'><Spinner /></div>;
  const show = detail.show;
  const current = tab && seasons.map(String).includes(tab) ? Number(tab) : (seasons.find((season) => season > 0) ?? seasons[0]);
  function pick(paths: string[], on: boolean) {
    setPicked((before) => {
      const next = new Set(before);
      for (const path of paths) {
        if (on) next.add(path);
        else next.delete(path);
      }
      return next;
    });
  }
  const tick = cardKey ? (paths: string[], label: string) => (
    <Checkbox
      nativeButton
      render={<button type='button' />}
      className='align-middle'
      aria-label={label}
      checked={paths.length > 0 && paths.every((path) => picked.has(path))}
      indeterminate={paths.some((path) => picked.has(path)) && !paths.every((path) => picked.has(path))}
      onCheckedChange={(on) => pick(paths, on)}
    />
  ) : null;

  function searchFor(season: number, episode?: number) {
    const wanted = detail!.episodes
      .filter((row) => row.season === season && (episode ? row.episode === episode : row.state === 'missing' || row.state === 'english'))
      .map((row) => code(row.season, row.episode));
    setPicker({
      query: `${show.title} ${code(season, episode)}`,
      title: `${show.title} ${code(season, episode)}`,
      season,
      episodes: wanted,
    });
  }

  function lastSearch(season: number) {
    const rows = Object.entries(detail!.searches).filter(([key]) => key.startsWith(code(season)));
    if (!rows.length) return null;
    const [key, last] = rows.sort((a, b) => b[1].at - a[1].at)[0];
    return `${key} searched ${timeAgo(last.at)}: ${last.found ? (last.held ? `${last.release} — held, ${last.held}` : last.release) : 'nothing found'}`;
  }

  return (
    <div className='flex min-h-0 flex-1 flex-col gap-3'>
      {/* Each episode row says its language and quality, so no summary of them here; the buttons
          sit in the drawer's header, beside Artwork and TMDB. */}
      {actions && createPortal(
        <>
          {current !== undefined && current > 0 && (() => {
            const open = detail.episodes.filter((row) => row.season === current && (row.state === 'missing' || row.state === 'english')).length;
            return (
              <Button
                size='sm'
                variant='secondary'
                onClick={() => searchFor(current)}
                disabled={open === 0}
                title={lastSearch(current) ?? `Search for a release of season ${current}: its missing episodes, or better copies of the English ones`}
              >
                <Search data-icon='inline-start' /> Search season {current}
              </Button>
            );
          })()}
          <Button size='sm' variant='secondary' onClick={lookNow} title='Look for the missing episodes now'><RefreshCw data-icon='inline-start' /> Look now</Button>
          <Button size='sm' variant='ghost' onClick={() => setSettings(true)}>Follow settings</Button>
        </>,
        actions,
      )}
      {detail.active.length > 0 && (
        <ul className='flex flex-col gap-1 rounded-control surface-container p-2 text-xs'>
          {detail.active.map((job) => (
            <li key={job.id} className='flex items-center gap-2'>
              <StatusTag variant='torrent'>{job.status}</StatusTag>
              <span className='truncate text-muted-foreground' title={job.release}>{job.release}</span>
            </li>
          ))}
        </ul>
      )}
      {detail.failed.length > 0 && (
        <ul className='flex flex-col gap-1 rounded-control surface-container p-2 text-xs'>
          {detail.failed.map((job) => (
            <li key={job.id} className='flex items-center gap-2'>
              <StatusTag variant='destructive'>failed</StatusTag>
              <span className='min-w-0 truncate text-muted-foreground' title={job.release}>{job.release}</span>
              {job.reason && <span className='ml-auto shrink-0 truncate text-muted-foreground/70' title={job.reason}>{job.reason}</span>}
            </li>
          ))}
          <li className='text-muted-foreground/70'>A release that failed is not picked again; the next search takes another.</li>
        </ul>
      )}
      {seasons.length === 0 ? (
        <p className='text-sm text-muted-foreground'>TMDB lists no episodes for this show.</p>
      ) : (
        <Tabs key={tmdbId} value={String(current)} onValueChange={(value) => setTab(String(value))} className='flex min-h-0 flex-1 flex-col gap-3'>
          <TabsList className='h-auto max-w-full flex-wrap'>
            {seasons.map((season) => (
              <TabsTab key={season} value={String(season)}>{season === 0 ? 'Specials' : 'Season ' + season}</TabsTab>
            ))}
          </TabsList>
          {seasons.map((season) => {
            const rows = detail.episodes.filter((row) => row.season === season);
            const files = [...new Set(rows.flatMap((row) => row.paths))];
            return (
              <TabsPanel key={season} value={String(season)} className='flex min-h-0 flex-1 flex-col gap-2'>
                {tick && files.length > 0 && (
                  <label className='flex items-center gap-2 text-xs text-muted-foreground'>{tick(files, 'Select every file of this season')} Select the season</label>
                )}
                <table className='w-full border-collapse text-sm'>
                  <tbody>
                    {rows.map((row) => (
                      <EpisodeRow
                        key={`${row.season}:${row.episode}`}
                        row={row}
                        onSearch={() => searchFor(row.season, row.episode)}
                        tick={tick && row.paths.length ? tick(row.paths, `Select ${code(row.season, row.episode)}`) : null}
                      />
                    ))}
                  </tbody>
                </table>
              </TabsPanel>
            );
          })}
        </Tabs>
      )}
      <TorrentPickerDialog
        open={picker !== null}
        onClose={() => { setPicker(null); window.setTimeout(() => void load(), 3000); }}
        query={picker?.query ?? ''}
        title={picker?.title ?? ''}
        download={{
          kind: 'show', root: 'by-language', title: show.title, year: show.year, season: picker?.season ?? null,
          follow: show.tmdb_id, episodes: picker?.episodes ?? [], names: show.names,
        }}
      />
      {picked.size > 0 && (
        <div className='sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay'>
          <span className='text-xs text-muted-foreground'>
            <span className='font-mono tabular-nums text-foreground'>{picked.size}</span> file{picked.size === 1 ? '' : 's'} selected
          </span>
          <Button size='sm' onClick={() => setMagnetOpen(true)} title='Replace the selected files with the torrent of a magnet link'>
            <Magnet data-icon='inline-start' /> Replace with magnet link…
          </Button>
          <Button size='sm' variant='ghost' className='ml-auto' onClick={() => setPicked(new Set())}>
            <X data-icon='inline-start' /> Clear
          </Button>
        </div>
      )}
      <MagnetReplaceDialog
        open={magnetOpen}
        count={picked.size}
        what='file'
        paths={[...picked]}
        scope='show'
        onClose={() => setMagnetOpen(false)}
        onSubmit={async (magnet) => {
          if (!cardKey) return { ok: false };
          try {
            await api.masReplaceMagnet('show', cardKey, [...picked], magnet);
            toast.success(`${show.title}: the torrent is queued; each file it brings replaces the selected one of its episode`);
            setPicked(new Set());
            window.setTimeout(() => void load(), 3000);
            return { ok: true };
          } catch (error: any) {
            toast.error(error.message);
            return { ok: false };
          }
        }}
      />
      <Dialog open={settings} onOpenChange={setSettings}>
        <DialogContent size='lg' className='max-h-[90vh] max-w-3xl overflow-y-auto'>
          <DialogHeader><DialogTitle>{show.title}</DialogTitle></DialogHeader>
          <DialogBody>
            <FollowPanel tmdbId={tmdbId} onChange={() => { void load(); onChanged?.(); }} />
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function EpisodeRow({ row, onSearch, tick }: { row: FollowEpisode; onSearch: () => void; tick?: React.ReactNode }) {
  const file = row.paths[0]?.split('/').pop();
  return (
    <tr className='border-b border-border/60 last:border-0'>
      <td className='w-8 py-2 pr-1'>{tick}</td>
      <td className='w-10 py-2 pr-3 font-mono text-xs tabular-nums'>{row.episode}</td>
      <td className='px-3 py-2 text-xs text-muted-foreground'>
        {row.name && <p className='text-sm text-foreground'>{row.name}</p>}
        {file ? <p className='truncate font-mono text-[0.68rem]' title={row.paths[0]}>{file}</p> : row.aired && <p>{row.aired}</p>}
      </td>
      <td className='w-16 py-2 pl-3 text-right font-mono text-xs tabular-nums text-muted-foreground'>{row.quality ?? ''}</td>
      <td className='w-44 py-2 pl-3 text-right'>
        <span className='inline-flex items-center gap-1.5'>
          {row.state === 'english' && row.english_subs === false && (
            <StatusTag variant='warning' title='English audio without English subtitles: a release that has them is looked for'>No subtitles</StatusTag>
          )}
          <StatusTag variant={STATE_VARIANT[row.state]}>{STATE_LABEL[row.state]}</StatusTag>
        </span>
      </td>
      <td className='w-12 py-2 pl-2 text-right'>
        {(row.state === 'missing' || row.state === 'english') && row.season > 0 && (
          <Button size='icon-sm' variant='ghost' aria-label={row.state === 'english' ? 'Search for a German release' : 'Search for this episode'} title={row.state === 'english' ? 'Search for a German release' : 'Search for this episode'} onClick={onSearch}>
            <Search />
          </Button>
        )}
      </td>
    </tr>
  );
}
