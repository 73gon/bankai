import { useEffect, useMemo, useState } from 'react';
import { Check, Download, ExternalLink, FolderInput, Magnet, Search } from 'lucide-react';
import { TorrentLinkField } from '@/components/torrent-link-field';
import { toast } from '@/lib/toast';
import { api, type DirectKind, type DirectRelease, type DirectSearch, type DirectTarget, type DiscoverItem } from '@/lib/api';
import { AnimePoster } from '@/components/AnimePoster';
import { formatBytes, timeAgo } from '@/lib/utils';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';

type View = {
  query: string;
  kind: DirectKind;
  quality: string;
  include: string;
  exclude: string;
  minSeeders: string;
};

const QUALITIES = ['all', '2160p', '1080p', '720p', '480p'];

// The search and its filters, kept for this browser tab: leaving the page and
// coming back finds them -- and the last results -- as they were.
const VIEW_KEY = 'bankai:torrents:view';
const DEFAULT_VIEW: View = { query: '', kind: 'show', quality: 'all', include: '', exclude: '', minSeeders: '1' };
let lastResult: { key: string; result: DirectSearch } | null = null;

function storedView(): View {
  try {
    const saved = JSON.parse(sessionStorage.getItem(VIEW_KEY) ?? '{}');
    return {
      query: typeof saved.query === 'string' ? saved.query : '',
      kind: saved.kind === 'movie' ? 'movie' : 'show',
      quality: QUALITIES.includes(saved.quality) ? saved.quality : 'all',
      include: typeof saved.include === 'string' ? saved.include : '',
      exclude: typeof saved.exclude === 'string' ? saved.exclude : '',
      minSeeders: typeof saved.minSeeders === 'string' ? saved.minSeeders : DEFAULT_VIEW.minSeeders,
    };
  } catch {
    return { ...DEFAULT_VIEW };
  }
}

function viewKey(view: View) {
  return JSON.stringify(view);
}

function published(value: string | null) {
  const at = value ? Date.parse(value) : NaN;
  return Number.isNaN(at) ? '—' : timeAgo(at / 1000);
}

/** The folder a download lands in, as it will be named: shown before it starts. */
function preview(target: DirectTarget | undefined, kind: DirectKind, title: string, year: string, season: string) {
  if (!target || !title.trim()) return '';
  const name = year ? `${title.trim()} (${year})` : title.trim();
  return kind === 'movie'
    ? `${target.root}/${name}/${name}.mkv`
    : `${target.root}/${name}/Season ${String(Number(season) || 1).padStart(2, '0')}/${title.trim()} - S${String(Number(season) || 1).padStart(2, '0')}E01.mkv …`;
}

function DownloadDialog({ release, kind, targets, onClose }: {
  release: DirectRelease | null;
  kind: DirectKind;
  targets: DirectTarget[];
  onClose: () => void;
}) {
  // What it is comes from TMDB, as for Review's downloads: the folder is named as TMDB names the
  // title, so the library and Jellyfin match it. Searched by the release's name first; any search
  // may be typed. Only a title TMDB does not know is named by hand.
  const [asKind, setAsKind] = useState<DirectKind>(kind);
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<DiscoverItem[] | null>(null);
  const [tmdb, setTmdb] = useState<DiscoverItem | null>(null);
  const [byHand, setByHand] = useState(false);
  const [title, setTitle] = useState('');
  const [year, setYear] = useState('');
  const [season, setSeason] = useState('');
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);

  async function search(text: string, which: DirectKind, preferYear: number | null, choose: boolean) {
    if (!text.trim()) return;
    setMatches(null);
    try {
      const found = (await api.discoverSearch(text.trim(), which, 'title', 0, 12)).items.filter((item) => item.tmdb_id);
      setMatches(found);
      // Opening: the same year if the name says one (remakes share a name), else the first.
      if (choose) setTmdb(found.find((item) => preferYear && item.year === preferYear) ?? (preferYear ? null : found[0] ?? null));
    } catch (error: any) {
      toast.error(error.message);
      setMatches([]);
    }
  }

  useEffect(() => {
    if (!release) return;
    const guessed = release.guess;
    setAsKind(kind);
    setQuery(guessed.title);
    setTitle(guessed.title);
    setYear(guessed.year ? String(guessed.year) : '');
    setSeason(guessed.season ? String(guessed.season) : '');
    setTmdb(null);
    setByHand(false);
    void search(guessed.title, kind, guessed.year, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [release, kind]);

  const library = asKind === 'movie' ? 'movies' : 'shows';
  const choices = targets.filter((row) => row.library === library);
  const items = choices.map((row) => ({ value: row.root, label: row.label }));
  useEffect(() => {
    if (!release) return;
    const remembered = (() => {
      try { return localStorage.getItem(`bankai:torrents:root:${library}`); } catch { return null; }
    })();
    const pick = choices.find((row) => row.root === remembered && row.exists)
      ?? choices.find((row) => row.language === 'English' && row.exists)
      ?? choices.find((row) => row.exists);
    setRoot(pick?.root ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [release, library, targets]);

  // The name the folder gets: TMDB's, or typed when TMDB does not know it.
  const name = byHand ? title.trim() : tmdb?.name ?? '';
  const yearText = byHand ? year.trim() : tmdb?.year ? String(tmdb.year) : '';
  const yearNumber = yearText ? Number(yearText) : null;
  const target = choices.find((row) => row.root === root);
  const problem = !name
    ? byHand ? 'A title names the folder.' : 'Pick the title it is.'
    : yearNumber !== null && !(yearNumber >= 1900 && yearNumber <= 2100)
      ? 'The year is not a year.'
      : !target
        ? 'Pick the folder it goes to.'
        : null;

  async function start() {
    if (!release || problem || !target) return;
    setBusy(true);
    try {
      const answer = await api.directDownload({
        id: release.id,
        kind: asKind,
        root: target.root,
        title: name,
        year: yearNumber,
        season: asKind === 'show' && season.trim() ? Number(season) : null,
      });
      try { localStorage.setItem(`bankai:torrents:root:${library}`, target.root); } catch { /* per-browser convenience only */ }
      if (answer.status === 'duplicate') toast.info('That download is already queued.');
      else toast.success(`${name} is downloading into ${target.label}.`);
      onClose();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={release !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent size='lg' className='max-h-[90dvh]'>
        <DialogHeader>
          <DialogTitle>Download as it is</DialogTitle>
          <DialogDescription>
            Into the folder you pick, with its own audio and subtitles: no German dub, no sync, no review. A file the folder
            already has is never replaced.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-4'>
          {release && <p className='break-words rounded-item border border-field-edge bg-field-surface p-3 font-mono text-xs text-muted-foreground'>{release.title}</p>}

          {byHand ? (
            <div className='flex flex-col gap-2'>
              <div className='grid gap-3 sm:grid-cols-[1fr_8rem]'>
                <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                  Title
                  <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder='As Jellyfin should find it' autoFocus />
                </label>
                <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                  Year
                  <Input type='number' min='1900' max='2100' value={year} onChange={(event) => setYear(event.target.value)} />
                </label>
              </div>
              <button type='button' className='self-start text-xs text-muted-foreground underline underline-offset-4' onClick={() => setByHand(false)}>
                Pick it on TMDB instead
              </button>
            </div>
          ) : (
            <div className='flex flex-col gap-2'>
              <form className='flex items-center gap-2' onSubmit={(event) => { event.preventDefault(); void search(query, asKind, null, false); }}>
                <InputGroup className='min-w-0 flex-1'>
                  <InputGroupAddon><Search aria-hidden='true' /></InputGroupAddon>
                  <InputGroupInput aria-label='Search TMDB' value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Search TMDB' />
                </InputGroup>
                <ToggleGroup<DirectKind>
                  aria-label='Film or show'
                  value={[asKind]}
                  onValueChange={(values) => {
                    const next = values[0];
                    if (!next || next === asKind) return;
                    setAsKind(next);
                    setTmdb(null);
                    void search(query, next, release?.guess.year ?? null, true);
                  }}
                >
                  <ToggleGroupItem<DirectKind> value='movie'>Film</ToggleGroupItem>
                  <ToggleGroupItem<DirectKind> value='show'>Show</ToggleGroupItem>
                </ToggleGroup>
              </form>
              <div className='max-h-[min(13rem,30dvh)] overflow-y-auto rounded-item border border-field-edge'>
                {matches === null ? (
                  <div className='flex min-h-20 items-center justify-center'><Spinner /></div>
                ) : matches.length === 0 ? (
                  <p className='p-3 text-sm text-muted-foreground'>TMDB knows no {asKind === 'movie' ? 'film' : 'show'} of that name. Search another, or name it yourself.</p>
                ) : (
                  <ul className='flex flex-col'>
                    {matches.map((item) => {
                      const chosen = tmdb?.tmdb_id === item.tmdb_id;
                      return (
                        <li key={item.tmdb_id}>
                          <button
                            type='button'
                            aria-pressed={chosen}
                            onClick={() => setTmdb(item)}
                            className={'flex w-full items-center gap-3 p-2 text-left focus-ring ' + (chosen ? 'bg-control-pressed' : 'hover:bg-control-hover')}
                          >
                            <AnimePoster url={item.poster_url} title={item.name} className='w-9 shrink-0' />
                            <span className='min-w-0 flex-1'>
                              <span className='block truncate text-sm text-foreground'>{item.name} <span className='font-mono text-xs text-muted-foreground'>{item.year ?? ''}</span></span>
                              {item.overview && <span className='line-clamp-1 text-xs text-muted-foreground'>{item.overview}</span>}
                            </span>
                            {chosen && <Check className='size-4 shrink-0 text-success' aria-hidden='true' />}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              <button type='button' className='self-start text-xs text-muted-foreground underline underline-offset-4' onClick={() => { setByHand(true); if (tmdb) { setTitle(tmdb.name); setYear(tmdb.year ? String(tmdb.year) : ''); } }}>
                Not on TMDB? Name it yourself
              </button>
            </div>
          )}

          <div className='grid gap-3 sm:grid-cols-[1fr_8rem]'>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Folder
              <Select items={items} value={root} onValueChange={(value) => setRoot(value ?? '')}>
                <SelectTrigger aria-label='Folder'><SelectValue placeholder='Pick a folder' /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {choices.map((row) => (
                      <SelectItem key={row.root} value={row.root} disabled={!row.exists}>
                        {row.label} <span className='font-mono text-xs text-muted-foreground'>{row.root}</span>
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </label>
            {asKind === 'show' && (
              <label className='flex flex-col gap-1.5 text-xs text-muted-foreground' title='Only for files that name an episode alone; SxxEyy names go by themselves'>
                Season
                <Input type='number' min='0' value={season} onChange={(event) => setSeason(event.target.value)} placeholder='From names' />
              </label>
            )}
          </div>
          {preview(target, asKind, name, yearText, season) && (
            <p className='flex items-start gap-2 break-all font-mono text-xs text-foreground/80'>
              <FolderInput className='mt-0.5 size-3.5 shrink-0 text-muted-foreground' aria-hidden='true' />
              {preview(target, asKind, name, yearText, season)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          {problem && <span className='mr-auto text-xs text-muted-foreground'>{problem}</span>}
          <Button variant='secondary' onClick={onClose}>Cancel</Button>
          <Button disabled={Boolean(problem) || busy} onClick={() => void start()}>
            {busy ? <Spinner label='Queueing' /> : <Download data-icon='inline-start' />} Download
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Movies & Shows -> Torrents: any release Prowlarr finds, downloaded and filed as it is into the
 * folder picked for it -- English-only shows, say, which the German pipeline would not take.
 */
/** A release one has as a magnet link or a .torrent: taken on to the download dialog. */
function FromMagnetDialog({ open, onClose, onRelease }: { open: boolean; onClose: () => void; onRelease: (release: DirectRelease) => void }) {
  const [magnet, setMagnet] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setMagnet(''); }, [open]);
  const valid = /^magnet:\?.*xt=urn:btih:/i.test(magnet.trim());
  async function next() {
    setBusy(true);
    try {
      onRelease(await api.directMagnet(magnet.trim()));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !busy) onClose(); }}>
      <DialogContent size='lg'>
        <DialogHeader>
          <DialogTitle>Download from a magnet link or .torrent</DialogTitle>
          <DialogDescription>
            A film or show you have the torrent of. Next you pick its name, year and folder, as for a release found here;
            it is downloaded as it is, each file by its audio when the folder is "by language".
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <TorrentLinkField multiline autoFocus value={magnet} onChange={setMagnet} disabled={busy} />
          {magnet.trim() && !valid && <p className='mt-2 text-xs text-warning'>That is not a magnet link (magnet:?xt=urn:btih:…).</p>}
        </DialogBody>
        <DialogFooter>
          <Button variant='ghost' onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={() => void next()} disabled={!valid || busy}>
            {busy ? <Spinner /> : <Download data-icon='inline-start' />} Next
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function Torrents() {
  const [draft, setDraft] = useState<View>(storedView);
  const [view, setView] = useState<View | null>(() => (draft.query.trim() ? draft : null));
  const [result, setResult] = useState<DirectSearch | null>(() => (lastResult && view && lastResult.key === viewKey(view) ? lastResult.result : null));
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [targets, setTargets] = useState<DirectTarget[]>([]);
  const [picked, setPicked] = useState<DirectRelease | null>(null);
  // A release from a magnet or .torrent: its kind told from its name, not the search's.
  const [pickedKind, setPickedKind] = useState<DirectKind | null>(null);
  const [fromMagnet, setFromMagnet] = useState(false);

  useEffect(() => {
    api.directTargets().then((answer) => setTargets(answer.targets)).catch((error: any) => toast.error(error.message));
  }, []);

  useEffect(() => {
    try { sessionStorage.setItem(VIEW_KEY, JSON.stringify(draft)); } catch { /* per-tab convenience only */ }
  }, [draft]);

  useEffect(() => {
    if (!view) return;
    const key = viewKey(view);
    if (lastResult?.key === key) {
      setResult(lastResult.result);
      return;
    }
    let cancelled = false;
    // Another search's rows (or its "found nothing") must not stand for this one while it runs.
    setResult(null);
    setFailed(null);
    setLoading(true);
    api.directSearch(view.query.trim(), view.kind, {
      quality: view.quality,
      titleFilters: view.include.trim(),
      excludeFilters: view.exclude.trim(),
      minSeeders: Math.max(0, Number.parseInt(view.minSeeders || '0', 10) || 0),
    })
      .then((answer) => {
        if (cancelled) return;
        lastResult = { key, result: answer };
        setResult(answer);
      })
      .catch((error: any) => !cancelled && setFailed(error.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      // A hidden page's search is asked again when it is shown; no spinner is left behind.
      setLoading(false);
    };
  }, [view]);

  function search() {
    if (!draft.query.trim()) return;
    lastResult = null;
    setView({ ...draft });
  }

  function set<K extends keyof View>(key: K, value: View[K]) {
    setDraft((old) => ({ ...old, [key]: value }));
  }

  const qualityItems = useMemo(() => QUALITIES.map((value) => ({ value, label: value === 'all' ? 'Any quality' : value })), []);

  return (
    <div className='flex h-full min-h-0 flex-col gap-4 overflow-hidden'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div className='flex flex-wrap items-baseline gap-2'>
          <h1 className='font-heading text-2xl font-light tracking-tight'>Torrents</h1>
          <span className='text-sm text-muted-foreground'>— Any release Prowlarr finds, downloaded as it is into the folder you pick.</span>
        </div>
        <Button variant='secondary' onClick={() => setFromMagnet(true)}>
          <Magnet data-icon='inline-start' /> From magnet or .torrent
        </Button>
      </div>

      <div className='flex flex-wrap items-center gap-2'>
        <InputGroup className='min-w-64 flex-1 sm:max-w-sm'>
          <InputGroupAddon><Search aria-hidden='true' /></InputGroupAddon>
          <InputGroupInput
            value={draft.query}
            onChange={(event) => set('query', event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && search()}
            placeholder={draft.kind === 'show' ? 'Modern Family S01' : 'The Matrix 1999'}
            aria-label='Search Prowlarr'
          />
        </InputGroup>
        <ToggleGroup<DirectKind> aria-label='Category' value={[draft.kind]} onValueChange={(values) => values[0] && set('kind', values[0])}>
          <ToggleGroupItem<DirectKind> value='show'>Shows</ToggleGroupItem>
          <ToggleGroupItem<DirectKind> value='movie'>Movies</ToggleGroupItem>
        </ToggleGroup>
        <Select items={qualityItems} value={draft.quality} onValueChange={(value) => value && set('quality', value)}>
          <SelectTrigger className='w-36 shrink-0' aria-label='Quality'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {qualityItems.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Input className='w-44 shrink-0' value={draft.include} onChange={(event) => set('include', event.target.value)} onKeyDown={(event) => event.key === 'Enter' && search()} placeholder='Title has: x265, BluRay' aria-label='Title contains any of' title='Comma-separated; a release needs one of them in its name' />
        <Input className='w-44 shrink-0' value={draft.exclude} onChange={(event) => set('exclude', event.target.value)} onKeyDown={(event) => event.key === 'Enter' && search()} placeholder='Title lacks: German, CAM' aria-label='Title contains none of' title='Comma-separated; a release with any of them in its name is left out' />
        <Input className='w-28 shrink-0' type='number' min='0' value={draft.minSeeders} onChange={(event) => set('minSeeders', event.target.value)} onKeyDown={(event) => event.key === 'Enter' && search()} aria-label='Minimum seeders' title='Minimum seeders' placeholder='Seeders' />
        <Button onClick={search} disabled={loading || !draft.query.trim()}>
          {loading ? <Spinner label='Searching Prowlarr' /> : <Search data-icon='inline-start' />} Search
        </Button>
        {result && (
          <span className='ml-auto text-xs text-muted-foreground'>
            {result.items.length} of {result.found} releases
          </span>
        )}
      </div>

      <Table variant='plain' size='sm' className='min-w-[900px]' containerProps={{ className: 'min-h-0 flex-1 overflow-auto -mx-(--page-gutter) w-auto' }}>
        <TableHeader className='sticky top-0 z-10 bg-(--secondary)'>
          <TableRow>
            <TableHead>Release</TableHead>
            <TableHead>Quality</TableHead>
            <TableHead numeric>Size</TableHead>
            <TableHead numeric>Seeders</TableHead>
            <TableHead>Indexer</TableHead>
            <TableHead>Uploaded</TableHead>
            <TableHead><span className='sr-only'>Actions</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading && !result && (
            <TableRow>
              <TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>
                Searching Prowlarr… it answers once every indexer has, up to a minute when one is slow.
              </TableCell>
            </TableRow>
          )}
          {!loading && failed && (
            <TableRow>
              <TableCell colSpan={7} className='py-8 text-center'>
                <p className='text-destructive'>{failed}</p>
                <Button className='mt-3' size='sm' variant='secondary' onClick={search}>Search again</Button>
              </TableCell>
            </TableRow>
          )}
          {!loading && !failed && !result && (
            <TableRow><TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>Search for a movie or a show, a season with S01.</TableCell></TableRow>
          )}
          {result && result.items.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className='py-8 text-center text-muted-foreground'>
                {result.problem ?? (result.found ? `None of the ${result.found} releases passes the filters.` : 'Prowlarr found nothing.')}
              </TableCell>
            </TableRow>
          )}
          {result?.items.map((row) => (
            <TableRow key={row.id}>
              <TableCell className='max-w-[36rem] py-2'>
                <p className='truncate text-foreground' title={row.title}>{row.title}</p>
                {row.languages.length > 0 && <p className='text-[0.68rem] text-muted-foreground'>{row.languages.join(', ')}</p>}
              </TableCell>
              <TableCell className='whitespace-nowrap py-2 font-mono text-xs text-muted-foreground'>
                {[row.resolution, row.source, row.codec?.toUpperCase()].filter(Boolean).join(' · ') || '—'}
              </TableCell>
              <TableCell numeric className='py-2 text-xs text-muted-foreground'>{formatBytes(row.size_bytes)}</TableCell>
              <TableCell numeric className='py-2'>
                <StatusTag variant={row.seeders >= 10 ? 'success' : row.seeders > 0 ? 'warning' : 'muted'}>{row.seeders}</StatusTag>
              </TableCell>
              <TableCell className='whitespace-nowrap py-2 text-xs text-muted-foreground'>{row.indexer}</TableCell>
              <TableCell className='whitespace-nowrap py-2 text-xs text-muted-foreground'>{published(row.published)}</TableCell>
              <TableCell className='whitespace-nowrap py-2 text-right'>
                {row.info_url && (
                  <Button size='icon-sm' variant='ghost' aria-label='Open on the indexer' title='Open on the indexer' nativeButton={false} render={<a href={row.info_url} target='_blank' rel='noreferrer' />}>
                    <ExternalLink />
                  </Button>
                )}
                <Button size='sm' variant='secondary' onClick={() => { setPickedKind(null); setPicked(row); }}>
                  <Download data-icon='inline-start' /> Download
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <DownloadDialog release={picked} kind={pickedKind ?? result?.kind ?? draft.kind} targets={targets} onClose={() => { setPicked(null); setPickedKind(null); }} />
      <FromMagnetDialog
        open={fromMagnet}
        onClose={() => setFromMagnet(false)}
        onRelease={(release) => { setFromMagnet(false); setPickedKind(release.guess.kind); setPicked(release); }}
      />
    </div>
  );
}
