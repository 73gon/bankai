import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  Film,
  Search,
  ShieldCheck,
  Tv,
  Users,
} from 'lucide-react';
import { toast } from 'sonner';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  api,
  type AnimeEntry,
  type AnimeSearchOptions,
  type AnimeSearchPage,
  type AnimeTVDBMatch,
} from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

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

function Poster({ match }: { match: AnimeTVDBMatch | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className='flex aspect-[2/3] w-20 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/10 bg-black/30 sm:w-24'>
      {match?.poster_url && !failed ? (
        <img
          src={api.posterUrl(match.poster_url)}
          alt={`${match.english_title} poster`}
          className='h-full w-full object-cover'
          loading='lazy'
          onError={() => setFailed(true)}
        />
      ) : match?.kind === 'movie' ? (
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
    <Card>
      <CardContent className='flex flex-col gap-4 p-4 sm:flex-row sm:items-stretch'>
        <Poster match={entry.tvdb} />
        <div className='flex min-w-0 flex-1 flex-col gap-3'>
          <div className='flex flex-col gap-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h2 className='text-base font-semibold text-foreground'>
                {entry.tvdb?.english_title ?? 'TVDB match needed'} — Season {entry.season ?? 'N/A'} · Episode {entry.episode ?? 'N/A'}
              </h2>
              {entry.tvdb?.year && <span className='text-sm text-muted-foreground'>{entry.tvdb.year}</span>}
              {entry.tvdb && <Badge variant='info'>{entry.tvdb.kind === 'movie' ? 'Movie' : 'Show'}</Badge>}
              {entry.trusted && (
                <Badge variant='success'>
                  <ShieldCheck data-icon='inline-start' /> Trusted
                </Badge>
              )}
              {entry.remake && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant='warning' className='cursor-help'>Remake</Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    Nyaa marks this as a replacement or reupload of an earlier torrent, often with corrections or updated files.
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
            {entry.tvdb?.japanese_title && (
              <p className='text-sm text-muted-foreground'>{entry.tvdb.japanese_title}</p>
            )}
          </div>

          <p className='break-words font-mono text-sm text-foreground/90'>{entry.title}</p>

          <div className='flex flex-wrap items-center gap-2'>
            {entry.quality && <Badge>{entry.quality}</Badge>}
            {entry.publisher && <Badge variant='secondary'>{entry.publisher}</Badge>}
            <Badge variant='success'>{entry.seeders} seeders</Badge>
            <Badge variant='muted'>{entry.leechers} leechers</Badge>
            <Badge variant='muted'>{entry.size}</Badge>
            <span className='text-xs text-muted-foreground'>{entry.downloads.toLocaleString()} downloads</span>
          </div>

          <div className='mt-auto flex flex-wrap items-center gap-2'>
            <Button variant='secondary' size='sm' asChild>
              <a href={entry.detail_url} target='_blank' rel='noreferrer'>
                <ExternalLink data-icon='inline-start' /> Open on Nyaa
              </a>
            </Button>
            <Button
              variant='secondary'
              size='sm'
              onClick={() => void toggleDescription()}
              aria-expanded={descriptionOpen}
            >
              {descriptionLoading ? <Spinner /> : descriptionOpen ? <ChevronUp data-icon='inline-start' /> : <ChevronDown data-icon='inline-start' />}
              Description
            </Button>
            <Button size='sm' onClick={onDownload}>
              <Download data-icon='inline-start' /> Select and download
            </Button>
          </div>

          {descriptionOpen && (
            <div className='flex flex-col gap-3 overflow-x-auto rounded-md border border-white/10 bg-black/20 p-3 text-sm leading-relaxed text-foreground'>
              {descriptionLoading ? (
                'Loading description…'
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    h1: (props) => <h1 className='font-serif text-xl font-semibold' {...props} />,
                    h2: (props) => <h2 className='font-serif text-lg font-semibold' {...props} />,
                    h3: (props) => <h3 className='font-serif text-base font-semibold' {...props} />,
                    p: (props) => <p className='whitespace-pre-wrap' {...props} />,
                    a: (props) => <a className='text-info underline underline-offset-4' target='_blank' rel='noreferrer' {...props} />,
                    ul: (props) => <ul className='list-disc pl-5' {...props} />,
                    ol: (props) => <ol className='list-decimal pl-5' {...props} />,
                    table: (props) => <table className='w-full border-collapse text-left text-xs' {...props} />,
                    th: (props) => <th className='border border-border bg-secondary px-3 py-2 font-medium' {...props} />,
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
  const [selectedMatch, setSelectedMatch] = useState<AnimeTVDBMatch | null>(null);
  const [tvdbQuery, setTvdbQuery] = useState('');
  const [tvdbResults, setTvdbResults] = useState<AnimeTVDBMatch[]>([]);
  const [tvdbLoading, setTvdbLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [manualSeason, setManualSeason] = useState('');
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

  function openDownload(entry: AnimeEntry) {
    setSelectedEntry(entry);
    setSelectedMatch(entry.tvdb);
    setTvdbResults(entry.tvdb ? [entry.tvdb] : []);
    setTvdbQuery(entry.tvdb?.english_title ?? '');
    setManualSeason(entry.season == null ? '' : String(entry.season));
    setManualEpisode(entry.episode == null ? '' : String(entry.episode));
  }

  async function findTvdb() {
    const clean = tvdbQuery.trim();
    if (clean.length < 2) return;
    setTvdbLoading(true);
    try {
      const response = await api.animeTvdb(clean);
      setTvdbResults(response.items);
      if (response.items.length === 0) toast.info('No TVDB entries matched that title.');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setTvdbLoading(false);
    }
  }

  async function downloadSelected() {
    if (!selectedEntry || !selectedMatch) return;
    setDownloading(true);
    try {
      const season = manualSeason.trim() ? Number.parseInt(manualSeason, 10) : null;
      const episode = manualEpisode.trim() ? Number.parseInt(manualEpisode, 10) : null;
      if ((season != null && season < 1) || (episode != null && episode < 1)) {
        toast.error('Season and episode must be positive numbers.');
        return;
      }
      await api.animeDownload(selectedEntry, selectedMatch, { season, episode });
      toast.success(`${selectedMatch.english_title} was added to the queue from Nyaa.`);
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
        <h1 className='font-serif text-2xl font-semibold tracking-tight'>Anime</h1>
        <span className='text-sm text-muted-foreground'>— Browse Nyaa and download TVDB-organized anime directly.</span>
      </div>

      <Card className='shrink-0'>
        <CardHeader>
          <CardTitle>Find a release</CardTitle>
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
              {loading ? <Spinner /> : <Search data-icon='inline-start' />} Search Nyaa
            </Button>
          </div>

          <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-3'>
            <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
              Nyaa category
              <Select value={draftFilters.category} onValueChange={(value) => setDraftFilters((old) => ({ ...old, category: value }))}>
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
              <Select value={draftFilters.quality} onValueChange={(value) => setDraftFilters((old) => ({ ...old, quality: value }))}>
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
          {aliases.map((alias) => <Badge key={alias} variant='secondary'>{alias}</Badge>)}
        </div>
      )}

      <div className='flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/60 bg-card/30'>
        {loading && result && (
          <div className='flex shrink-0 items-center gap-2 border-b border-border/60 bg-card/80 px-4 py-2 text-sm text-foreground'>
            <Spinner /> Loading the requested Nyaa entries…
          </div>
        )}
        <div className='min-h-0 flex-1 overflow-y-auto p-3'>
          {loading && !result ? (
            <div className='flex flex-col gap-3' aria-label='Loading Nyaa releases'>
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className='h-44 w-full rounded-xl' />
              ))}
            </div>
          ) : result?.items.length ? (
            <div className='flex flex-col gap-3'>
              {result.items.map((entry) => <ResultCard key={entry.info_hash} entry={entry} onDownload={() => openDownload(entry)} />)}
            </div>
          ) : (
            <EmptyState icon={Search} title='No matching Nyaa releases' description='Try fewer filters, another title alias, or the All anime category.' />
          )}
        </div>
        <div className='flex shrink-0 items-center justify-between border-t border-border/60 bg-card/70 p-3'>
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

      <Dialog open={selectedEntry !== null} onOpenChange={(open) => !open && setSelectedEntry(null)}>
        <DialogContent className='max-h-[90vh] max-w-4xl overflow-y-auto'>
          <DialogHeader>
            <DialogTitle>Choose the TVDB anime</DialogTitle>
            <DialogDescription>
              The selected Nyaa torrent is downloaded directly. Shows are renamed into TVDB seasons and episodes; movies are copied as one file. Audio sync and remuxing are skipped.
            </DialogDescription>
          </DialogHeader>

          {selectedEntry && <p className='break-words rounded-md border border-white/10 bg-black/20 p-3 font-mono text-sm text-foreground'>{selectedEntry.title}</p>}

          <div className='flex flex-col gap-2 sm:flex-row'>
            <Input
              value={tvdbQuery}
              onChange={(event) => setTvdbQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && void findTvdb()}
              placeholder='Correct English or Japanese TVDB title'
              aria-label='TVDB anime title'
              className='flex-1'
            />
            <Button variant='secondary' onClick={() => void findTvdb()} disabled={tvdbLoading || tvdbQuery.trim().length < 2}>
              {tvdbLoading ? <Spinner /> : <Search data-icon='inline-start' />} Find TVDB entry
            </Button>
          </div>

          <div className='grid gap-3 sm:grid-cols-2'>
            {tvdbResults.map((match) => {
              const active = selectedMatch?.tvdb_id === match.tvdb_id && selectedMatch.kind === match.kind;
              return (
                <button
                  type='button'
                  key={`${match.kind}:${match.tvdb_id}`}
                  onClick={() => setSelectedMatch(match)}
                  className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-left transition-colors ${active ? 'border-primary bg-primary/10' : 'border-white/10 bg-black/20 hover:border-white/25'}`}
                >
                  <Poster match={match} />
                  <span className='flex min-w-0 flex-col gap-1'>
                    <span className='font-medium text-foreground'>{match.english_title}</span>
                    {match.japanese_title && <span className='text-sm text-muted-foreground'>{match.japanese_title}</span>}
                    <span className='flex flex-wrap items-center gap-2 text-xs text-muted-foreground'>
                      {match.kind === 'movie' ? <Film /> : <Tv />}
                      {match.kind === 'movie' ? 'Movie' : 'Show'}
                      {match.year && ` · ${match.year}`}
                      <span>TVDB {match.tvdb_id}</span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          {selectedMatch?.kind === 'show' && (
            <div className='grid gap-3 sm:grid-cols-2'>
              <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                Season override
                <Input
                  type='number'
                  min='1'
                  value={manualSeason}
                  onChange={(event) => setManualSeason(event.target.value)}
                  placeholder='Automatic'
                />
              </label>
              <label className='flex flex-col gap-1.5 text-xs text-muted-foreground'>
                Episode override
                <Input
                  type='number'
                  min='1'
                  value={manualEpisode}
                  onChange={(event) => setManualEpisode(event.target.value)}
                  placeholder='Automatic'
                />
              </label>
              <p className='text-xs text-muted-foreground sm:col-span-2'>
                Leave either field empty to use the release filename and TVDB numbering. A manual episode applies only when the torrent contains one video file.
              </p>
            </div>
          )}

          {tvdbResults.length === 0 && !tvdbLoading && (
            <EmptyState icon={Users} title='Select a TVDB entry first' description='Search for the anime so its English title, poster, seasons, and episode numbers can be used.' className='py-10' />
          )}

          <DialogFooter>
            <Button variant='secondary' onClick={() => setSelectedEntry(null)}>Cancel</Button>
            <Button onClick={() => void downloadSelected()} disabled={!selectedMatch || downloading}>
              {downloading ? <Spinner /> : <Download data-icon='inline-start' />}
              Download {selectedMatch?.kind === 'movie' ? 'movie' : 'show'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
