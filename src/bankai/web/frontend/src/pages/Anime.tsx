import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  ExternalLink,
  Film,
  Search,
  ShieldCheck,
  Sparkles,
  Tv,
  Users,
} from 'lucide-react';
import { toast } from 'sonner';
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

let animeView = { query: '', page: 0, filters: DEFAULT_FILTERS };
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
  return (
    <Card>
      <CardContent className='flex flex-col gap-4 p-4 sm:flex-row sm:items-stretch'>
        <Poster match={entry.tvdb} />
        <div className='flex min-w-0 flex-1 flex-col gap-3'>
          <div className='flex flex-col gap-1'>
            <div className='flex flex-wrap items-center gap-2'>
              <h2 className='text-base font-semibold text-foreground'>
                {entry.tvdb?.english_title ?? 'TVDB match needed'}
              </h2>
              {entry.tvdb?.year && <span className='text-sm text-muted-foreground'>{entry.tvdb.year}</span>}
              {entry.tvdb && <Badge variant='info'>{entry.tvdb.kind === 'movie' ? 'Movie' : 'Show'}</Badge>}
              {entry.trusted && (
                <Badge variant='success'>
                  <ShieldCheck data-icon='inline-start' /> Trusted
                </Badge>
              )}
              {entry.remake && <Badge variant='warning'>Remake</Badge>}
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
            <Button size='sm' onClick={onDownload}>
              <Download data-icon='inline-start' /> Select and download
            </Button>
          </div>
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
      await api.animeDownload(selectedEntry, selectedMatch);
      toast.success(`${selectedMatch.english_title} was added to the queue from Nyaa.`);
      setSelectedEntry(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className='flex min-h-full flex-col gap-6'>
      <div className='flex flex-wrap items-baseline gap-2'>
        <h1 className='font-serif text-2xl font-semibold tracking-tight'>Anime</h1>
        <span className='text-sm text-muted-foreground'>— Browse Nyaa and download TVDB-organized anime directly.</span>
      </div>

      <Card>
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
        <div className='flex flex-wrap items-center gap-2 text-xs text-muted-foreground'>
          <span>Also searched:</span>
          {aliases.map((alias) => <Badge key={alias} variant='secondary'>{alias}</Badge>)}
        </div>
      )}

      {loading && !result ? (
        <EmptyState icon={Sparkles} title='Loading Nyaa releases' description='Resolving TVDB titles and posters…' />
      ) : result?.items.length ? (
        <div className='flex flex-col gap-3'>
          {result.items.map((entry) => <ResultCard key={entry.info_hash} entry={entry} onDownload={() => openDownload(entry)} />)}
        </div>
      ) : (
        <EmptyState icon={Search} title='No matching Nyaa releases' description='Try fewer filters, another title alias, or the All anime category.' />
      )}

      <div className='flex items-center justify-between border-t border-border/60 pt-4'>
        <Button variant='secondary' onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0 || loading}>
          <ArrowLeft data-icon='inline-start' /> Previous
        </Button>
        <span className='text-sm text-foreground'>Page {page + 1}</span>
        <Button variant='secondary' onClick={() => setPage((value) => value + 1)} disabled={!result?.has_next || loading}>
          Next <ArrowRight data-icon='inline-end' />
        </Button>
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
