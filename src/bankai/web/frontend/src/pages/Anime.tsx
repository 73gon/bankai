import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  Film,
  Link2,
  Search,
  ShieldCheck,
  Tv,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  api,
  type AniListAnime,
  type AnimeAniListMatch,
  type AnimeEntry,
  type AnimeSearchOptions,
  type AnimeSearchPage,
} from '@/lib/api';
import { anilistName, anilistUrl, formatLabel } from '@/lib/anilist';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/zon/ui/dialog';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Input } from '@/zon/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Skeleton } from '@/zon/ui/skeleton';
import { Spinner } from '@/zon/ui/spinner';
import { Tag } from '@/zon/ui/tag';

type Filters = {
  category: string;
  quality: string;
  publisher: string;
  titleFilters: string;
  descriptionFilters: string;
  minSeeders: string;
};

const DEFAULT_FILTERS: Filters = {
  category: '1_0',
  quality: 'all',
  publisher: '',
  titleFilters: '',
  descriptionFilters: '',
  minSeeders: '0',
};

const ANIME_FILTERS_KEY = 'bankai:anime-filters';

// Labels for each select, so its trigger shows the label rather than the value.
const CATEGORY_ITEMS = [
  { value: '1_0', label: 'All anime' },
  { value: '1_2', label: 'English-translated' },
  { value: '1_3', label: 'Non-English-translated' },
  { value: '1_4', label: 'Raw' },
  { value: '1_1', label: 'Anime music video' },
];
const QUALITY_ITEMS = [
  { value: 'all', label: 'All qualities' },
  { value: '2160p', label: '2160p' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '480p', label: '480p' },
];
const KIND_ITEMS = [
  { value: 'show', label: 'Show episodes' },
  { value: 'movie', label: 'Movie' },
];

function loadAnimeFilters(): Filters {
  try {
    const saved = JSON.parse(localStorage.getItem(ANIME_FILTERS_KEY) || '{}') as Partial<Filters>;
    return Object.fromEntries(
      Object.entries(DEFAULT_FILTERS).map(([key, fallback]) => [
        key,
        typeof saved[key as keyof Filters] === 'string' ? saved[key as keyof Filters] : fallback,
      ]),
    ) as Filters;
  } catch {
    return { ...DEFAULT_FILTERS };
  }
}

let animeView = { query: '', page: 0, filters: loadAnimeFilters() };
const animeCache = new Map<string, AnimeSearchPage>();

function cacheKey(query: string, page: number, filters: Filters) {
  return JSON.stringify([query, page, filters]);
}

function searchOptions(page: number, filters: Filters): AnimeSearchOptions {
  return {
    category: filters.category,
    page,
    quality: filters.quality,
    publisher: filters.publisher.trim(),
    titleFilters: filters.titleFilters.trim(),
    descriptionFilters: filters.descriptionFilters.trim(),
    minSeeders: Math.max(0, Number.parseInt(filters.minSeeders || '0', 10) || 0),
  };
}

/** The AniList entry a download is filed under, however it was chosen. */
type Chosen = {
  anilist_id: number;
  /** How it is shown: English, else romaji. */
  name: string;
  /** The romanised title, as releases are named. */
  romaji: string | null;
  year: number | null;
  /** AniList's format: TV, MOVIE, OVA, ... */
  format: string | null;
  cover: string | null;
};

/** The entry told from a release's title. */
function fromMatch(match: AnimeAniListMatch): Chosen {
  return {
    anilist_id: match.anilist_id,
    name: match.name || match.title,
    romaji: match.title,
    year: match.year ?? null,
    format: match.format ?? null,
    cover: match.cover,
  };
}

/** The entry picked by hand. */
function fromPick(anime: AniListAnime): Chosen {
  return {
    anilist_id: anime.anilist_id,
    name: anilistName(anime),
    romaji: anime.romaji,
    year: anime.year,
    format: anime.format,
    cover: anime.cover,
  };
}

function isMovie(format: string | null | undefined) {
  return (format ?? '').toUpperCase() === 'MOVIE';
}

function Poster({ url, title, movie }: { url: string | null | undefined; title: string; movie: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className='flex aspect-[2/3] w-20 shrink-0 items-center justify-center overflow-hidden rounded-item border border-field-edge bg-field-surface sm:w-24'>
      {url && !failed ? (
        <img
          src={api.posterUrl(url)}
          alt={`${title} poster`}
          className='h-full w-full object-cover'
          loading='lazy'
          onError={() => setFailed(true)}
        />
      ) : movie ? (
        <Film className='text-muted-foreground' />
      ) : (
        <Tv className='text-muted-foreground' />
      )}
    </div>
  );
}

function ResultCard({ entry, onDownload }: { entry: AnimeEntry; onDownload: () => void }) {
  const [descriptionOpen, setDescriptionOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [descriptionLoading, setDescriptionLoading] = useState(false);

  async function toggleDescription() {
    if (descriptionOpen) {
      setDescriptionOpen(false);
      return;
    }
    setDescriptionOpen(true);
    if (description) return;
    setDescriptionLoading(true);
    try {
      const detail = await api.animeDetail(entry.detail_url);
      setDescription(detail.description || 'This Nyaa release does not include a description.');
    } catch (error: any) {
      toast.error(error.message);
      setDescriptionOpen(false);
    } finally {
      setDescriptionLoading(false);
    }
  }

  return (
    <Card className='[--card-padding:16px]'>
      <CardContent className='flex flex-col gap-4 sm:flex-row sm:items-stretch'>
        <Poster url={entry.anilist?.cover} title={entry.anilist ? entry.anilist.name || entry.anilist.title : entry.title} movie={isMovie(entry.anilist?.format)} />
        <div className='flex min-w-0 flex-1 flex-col gap-3'>
          <div className='flex flex-col gap-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h2 className='text-base font-semibold text-foreground'>
                {entry.anilist ? entry.anilist.name || entry.anilist.title : 'AniList match needed'}
                {entry.anilist?.episode != null && ` — Episode ${entry.anilist.episode}`}
              </h2>
              {entry.anilist?.year && <span className='text-sm text-muted-foreground'>{entry.anilist.year}</span>}
              {entry.anilist?.format && <StatusTag variant='info'>{formatLabel(entry.anilist.format)}</StatusTag>}
              {entry.trusted && (
                <StatusTag variant='success'>
                  <ShieldCheck data-icon='inline-start' /> Trusted
                </StatusTag>
              )}
              {entry.remake && (
                <StatusTag
                  variant='warning'
                  className='cursor-help'
                  title='Nyaa marks this as a replacement or reupload of an earlier torrent, often with corrections or updated files.'
                >
                  Remake
                </StatusTag>
              )}
            </div>
            {entry.anilist?.name && entry.anilist.name !== entry.anilist.title && (
              <p className='text-sm text-muted-foreground'>{entry.anilist.title}</p>
            )}
          </div>

          <p className='break-words font-mono text-sm text-foreground/90'>{entry.title}</p>

          <div className='flex flex-wrap items-center gap-2'>
            {entry.quality && <StatusTag>{entry.quality}</StatusTag>}
            {entry.publisher && <StatusTag variant='secondary'>{entry.publisher}</StatusTag>}
            <StatusTag variant='success'>{entry.seeders} seeders</StatusTag>
            <StatusTag variant='muted'>{entry.leechers} leechers</StatusTag>
            <StatusTag variant='muted'>{entry.size}</StatusTag>
            <span className='text-xs text-muted-foreground'>{entry.downloads.toLocaleString()} downloads</span>
          </div>

          <div className='mt-auto flex flex-wrap items-center gap-2'>
            <Button variant='secondary' size='sm' nativeButton={false} render={<a href={entry.detail_url} target='_blank' rel='noreferrer' />}>
              <ExternalLink data-icon='inline-start' /> Open on Nyaa
            </Button>
            <Button
              variant='secondary'
              size='sm'
              onClick={() => void toggleDescription()}
              aria-expanded={descriptionOpen}
            >
              {descriptionLoading ? <Spinner label='Loading description' /> : descriptionOpen ? <ChevronUp data-icon='inline-start' /> : <ChevronDown data-icon='inline-start' />}
              Description
            </Button>
            <Button variant='secondary' size='sm' onClick={onDownload}>
              <Download data-icon='inline-start' /> Select and download
            </Button>
          </div>

          {descriptionOpen && (
            <div className='flex flex-col gap-3 overflow-x-auto rounded-item border border-field-edge bg-field-surface p-3 text-sm leading-relaxed text-foreground'>
              {descriptionLoading ? (
                'Loading description…'
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    h1: (props) => <h1 className='font-heading text-xl font-light' {...props} />,
                    h2: (props) => <h2 className='font-heading text-lg font-light' {...props} />,
                    h3: (props) => <h3 className='font-heading text-base font-light' {...props} />,
                    p: (props) => <p className='whitespace-pre-wrap' {...props} />,
                    a: (props) => <a className='text-info underline underline-offset-4' target='_blank' rel='noreferrer' {...props} />,
                    ul: (props) => <ul className='list-disc pl-5' {...props} />,
                    ol: (props) => <ol className='list-decimal pl-5' {...props} />,
                    table: (props) => <table className='w-full border-collapse text-left text-xs' {...props} />,
                    th: (props) => <th className='border border-border bg-control-surface px-3 py-2 font-medium' {...props} />,
                    td: (props) => <td className='border border-border px-3 py-2 align-top' {...props} />,
                  }}
                >
                  {description}
                </ReactMarkdown>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function Anime() {
  const [queryInput, setQueryInput] = useState(animeView.query);
  const [query, setQuery] = useState(animeView.query);
  const [draftFilters, setDraftFilters] = useState<Filters>({ ...animeView.filters });
  const [filters, setFilters] = useState<Filters>({ ...animeView.filters });
  const [page, setPage] = useState(animeView.page);
  const initial = animeCache.get(cacheKey(animeView.query, animeView.page, animeView.filters));
  const [result, setResult] = useState<AnimeSearchPage | null>(initial ?? null);
  const [loading, setLoading] = useState(!initial);
  const [selectedEntry, setSelectedEntry] = useState<AnimeEntry | null>(null);
  const [chosen, setChosen] = useState<Chosen | null>(null);
  const [kind, setKind] = useState<'show' | 'movie'>('show');
  // The AniList picker, opened with what to search for first.
  const [pickFor, setPickFor] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [manualEpisode, setManualEpisode] = useState('');

  useEffect(() => {
    try {
      localStorage.setItem(ANIME_FILTERS_KEY, JSON.stringify(draftFilters));
    } catch {
      /* localStorage can be unavailable in locked-down browsers. */
    }
  }, [draftFilters]);

  useEffect(() => {
    animeView = { query, page, filters };
    const key = cacheKey(query, page, filters);
    const cached = animeCache.get(key);
    if (cached) {
      setResult(cached);
      setLoading(false);
    } else {
      setLoading(true);
    }
    let cancelled = false;
    api
      .animeSearch(query, searchOptions(page, filters))
      .then((response) => {
        if (cancelled) return;
        animeCache.set(key, response);
        setResult(response);
        const neighbours = [page - 1, page + 1].filter(
          (candidate) => candidate >= 0 && (candidate < page || response.has_next),
        );
        void Promise.all(
          neighbours.map(async (candidate) => {
            const neighbourKey = cacheKey(query, candidate, filters);
            if (!animeCache.has(neighbourKey)) {
              animeCache.set(neighbourKey, await api.animeSearch(query, searchOptions(candidate, filters)));
            }
          }),
        ).catch(() => undefined);
      })
      .catch((error) => !cancelled && toast.error(error.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [filters, page, query]);

  const aliases = useMemo(() => result?.aliases.filter((alias) => alias.toLocaleLowerCase() !== query.toLocaleLowerCase()) ?? [], [query, result]);

  function applySearch() {
    setQuery(queryInput.trim());
    setFilters({ ...draftFilters });
    setPage(0);
  }

  function choose(anime: Chosen) {
    setChosen(anime);
    setKind(isMovie(anime.format) ? 'movie' : 'show');
  }

  function openDownload(entry: AnimeEntry) {
    setSelectedEntry(entry);
    setChosen(null);
    setManualEpisode(entry.anilist?.episode == null ? '' : String(entry.anilist.episode));
    if (entry.anilist) {
      choose(fromMatch(entry.anilist));
    } else {
      // Nothing told from the title: the AniList entry is picked first.
      setPickFor(entry.title.replace(/^(\s*\[[^\]]+\])+\s*/, '').replace(/\s+-\s+\d+.*$/, ''));
    }
  }

  async function pickAnime(anime: AniListAnime) {
    choose(fromPick(anime));
    setPickFor(null);
  }

  async function downloadSelected() {
    if (!selectedEntry || !chosen) return;
    const episode = kind === 'show' && manualEpisode.trim() ? Number.parseInt(manualEpisode, 10) : null;
    if (episode != null && !(episode >= 1)) {
      toast.error('The episode must be a positive number.');
      return;
    }
    setDownloading(true);
    try {
      // A show goes on into the library, as every other anime download does; staged, it waited
      // for a Transfer click per episode (Fullmetal Alchemist: Brotherhood, 64 of them).
      await api.animeDownload(selectedEntry, chosen.anilist_id, kind, episode, kind === 'show');
      toast.success(`${chosen.name} was added to the queue from Nyaa.`);
      setSelectedEntry(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className='flex h-full min-h-0 flex-col gap-4 overflow-hidden'>
      <div className='flex flex-wrap items-baseline gap-2'>
        <h1 className='font-heading text-2xl font-light tracking-tight'>Anime</h1>
        <span className='text-sm text-muted-foreground'>— Browse Nyaa and download anime filed by its AniList entry.</span>
      </div>

      <Card className='shrink-0 gap-4 [--card-padding:20px]'>
        <CardHeader>
          <CardTitle className='text-xl sm:text-xl'>Find a release</CardTitle>
          <CardDescription>
            Search with an English or Japanese title. Matching aliases are searched together; comma-separated title and description terms are OR-linked.
          </CardDescription>
        </CardHeader>
        <CardContent className='flex flex-col gap-4'>
          <div className='flex flex-col gap-2 sm:flex-row'>
            <Input
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && applySearch()}
              placeholder='Anime title in English or Japanese — leave empty to browse'
              aria-label='Anime title'
              className='flex-1'
            />
            <Button onClick={applySearch} disabled={loading}>
              {loading ? <Spinner label='Searching Nyaa' /> : <Search data-icon='inline-start' />} Search Nyaa
            </Button>
          </div>

          <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-3'>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Nyaa category
              <Select items={CATEGORY_ITEMS} value={draftFilters.category} onValueChange={(value) => value !== null && setDraftFilters((old) => ({ ...old, category: value }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value='1_0'>All anime</SelectItem>
                    <SelectItem value='1_2'>English-translated</SelectItem>
                    <SelectItem value='1_3'>Non-English-translated</SelectItem>
                    <SelectItem value='1_4'>Raw</SelectItem>
                    <SelectItem value='1_1'>Anime music video</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </label>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Quality
              <Select items={QUALITY_ITEMS} value={draftFilters.quality} onValueChange={(value) => value !== null && setDraftFilters((old) => ({ ...old, quality: value }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value='all'>All qualities</SelectItem>
                    <SelectItem value='2160p'>2160p</SelectItem>
                    <SelectItem value='1080p'>1080p</SelectItem>
                    <SelectItem value='720p'>720p</SelectItem>
                    <SelectItem value='480p'>480p</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </label>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Publisher / release group
              <Input value={draftFilters.publisher} onChange={(event) => setDraftFilters((old) => ({ ...old, publisher: event.target.value }))} placeholder='e.g. SubsPlease' />
            </label>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Title contains — OR
              <Input value={draftFilters.titleFilters} onChange={(event) => setDraftFilters((old) => ({ ...old, titleFilters: event.target.value }))} placeholder='GER, German, Dual Audio' />
            </label>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Description contains — OR
              <Input value={draftFilters.descriptionFilters} onChange={(event) => setDraftFilters((old) => ({ ...old, descriptionFilters: event.target.value }))} placeholder='Deutsch, German subtitles' />
            </label>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Minimum seeders
              <Input type='number' min='0' value={draftFilters.minSeeders} onChange={(event) => setDraftFilters((old) => ({ ...old, minSeeders: event.target.value }))} />
            </label>
          </div>
        </CardContent>
      </Card>

      {aliases.length > 0 && (
        <div className='flex shrink-0 flex-wrap items-center gap-2 text-xs text-muted-foreground'>
          <span>Also searched:</span>
          {aliases.map((alias) => <Tag key={alias} variant='muted'>{alias}</Tag>)}
        </div>
      )}

      <div className='flex min-h-0 flex-1 flex-col overflow-hidden rounded-card surface-container'>
        {loading && result && (
          <div className='flex shrink-0 items-center gap-2 border-b border-border/60 bg-control-surface px-4 py-2 text-sm text-foreground'>
            <Spinner label='Loading the requested Nyaa entries' /> Loading the requested Nyaa entries…
          </div>
        )}
        <div className='min-h-0 flex-1 overflow-y-auto p-3'>
          {loading && !result ? (
            <div className='flex flex-col gap-3' aria-label='Loading Nyaa releases'>
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} shape='card' className='h-44 w-full' />
              ))}
            </div>
          ) : result?.items.length ? (
            <div className='flex flex-col gap-3'>
              {result.items.map((entry) => <ResultCard key={entry.info_hash} entry={entry} onDownload={() => openDownload(entry)} />)}
            </div>
          ) : (
            <Empty>
              <EmptyMedia><Search /></EmptyMedia>
              <EmptyTitle>No matching Nyaa releases</EmptyTitle>
              <EmptyDescription>Try fewer filters, another title alias, or the All anime category.</EmptyDescription>
            </Empty>
          )}
        </div>
        <div className='flex shrink-0 items-center justify-between border-t border-border/60 bg-control-surface p-3'>
          <Button variant='secondary' onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0 || loading}>
            <ArrowLeft data-icon='inline-start' /> Previous
          </Button>
          <span className='text-sm text-foreground'>
            Page {page + 1} · {result?.items.length ?? 0} {(result?.items.length ?? 0) === 1 ? 'entry' : 'entries'}
          </span>
          <Button variant='secondary' onClick={() => setPage((value) => value + 1)} disabled={!result?.has_next || loading}>
            Next <ArrowRight data-icon='inline-end' />
          </Button>
        </div>
      </div>

      <Dialog open={selectedEntry !== null && pickFor === null} onOpenChange={(open) => !open && setSelectedEntry(null)}>
        <DialogContent size='lg' className='max-h-[90vh]'>
          <DialogHeader>
            <DialogTitle>Download as this AniList entry</DialogTitle>
            <DialogDescription>
              The selected Nyaa torrent is downloaded directly. A show&apos;s episodes are filed under its AniList entry, numbered as AniList numbers them; a movie is copied as one file. Audio sync and remuxing are skipped.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className='flex flex-col gap-4'>
          {selectedEntry && <p className='break-words rounded-item border border-field-edge bg-field-surface p-3 font-mono text-sm text-foreground'>{selectedEntry.title}</p>}

          {chosen ? (
            <div className='flex gap-3 rounded-button border border-field-edge bg-field-surface p-3'>
              <Poster url={chosen.cover} title={chosen.name} movie={kind === 'movie'} />
              <div className='flex min-w-0 flex-1 flex-col gap-1'>
                <span className='font-medium text-foreground'>{chosen.name}</span>
                {chosen.romaji && chosen.romaji !== chosen.name && <span className='text-sm text-muted-foreground'>{chosen.romaji}</span>}
                <span className='font-mono text-xs text-muted-foreground'>
                  {[formatLabel(chosen.format), chosen.year, 'id ' + chosen.anilist_id].filter(Boolean).join(' · ')}
                </span>
                <div className='mt-auto flex flex-wrap items-center gap-2 pt-2'>
                  <Button variant='secondary' size='sm' nativeButton={false} render={<a href={anilistUrl(chosen.anilist_id)} target='_blank' rel='noreferrer' />}>
                    <ExternalLink data-icon='inline-start' /> AniList
                  </Button>
                  <Button variant='secondary' size='sm' onClick={() => setPickFor(chosen.romaji || chosen.name)}>
                    <Link2 data-icon='inline-start' /> Change AniList entry
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <Empty className='py-10 md:py-10'>
              <EmptyMedia><Search /></EmptyMedia>
              <EmptyTitle>No AniList entry chosen</EmptyTitle>
              <EmptyDescription>Pick the AniList entry this release belongs to.</EmptyDescription>
            </Empty>
          )}

          {chosen && (
            <div className='grid gap-3 sm:grid-cols-2'>
              <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                Download as
                <Select items={KIND_ITEMS} value={kind} onValueChange={(value) => value !== null && setKind(value as 'show' | 'movie')}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value='show'>Show episodes</SelectItem>
                      <SelectItem value='movie'>Movie</SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </label>
              {kind === 'show' && (
                <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                  AniList episode
                  <Input
                    type='number'
                    min='1'
                    value={manualEpisode}
                    onChange={(event) => setManualEpisode(event.target.value)}
                    placeholder='From each file name'
                  />
                </label>
              )}
              {kind === 'show' && (
                <p className='text-xs text-muted-foreground sm:col-span-2'>
                  Leave the episode empty for a batch: each file is numbered by its own name. A set episode applies only to a torrent with one video file.
                </p>
              )}
            </div>
          )}
          </DialogBody>

          <DialogFooter>
            <Button variant='secondary' onClick={() => setSelectedEntry(null)}>Cancel</Button>
            {!chosen && (
              <Button variant='secondary' onClick={() => selectedEntry && setPickFor(selectedEntry.title)}>
                <Search data-icon='inline-start' /> Find on AniList
              </Button>
            )}
            <Button onClick={() => void downloadSelected()} disabled={!chosen || downloading}>
              {downloading ? <Spinner label='Downloading' /> : <Download data-icon='inline-start' />}
              Download {kind === 'movie' ? 'movie' : 'show'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AniListPickDialog
        query={pickFor}
        onClose={() => setPickFor(null)}
        onPick={pickAnime}
        title='Choose the AniList entry'
        description='The download is filed under the entry you pick, and later releases of this name follow it.'
        action='Choose'
      />
    </div>
  );
}
