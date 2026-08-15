import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { CalendarClock, ChevronRight, CirclePlay, Film, Files, Loader2, Plus, Search, Tv, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type FilmpalastDetails, type FilmpalastFeed, type RecentRelease, type RecentReleasePage } from '@/lib/api';
import { GermanRelease } from '@/components/GermanRelease';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

const pageCache = new Map<string, RecentReleasePage>();

function pageCacheKey(feed: FilmpalastFeed, page: number) {
  return `${feed}:${page}`;
}

function episodeIdentity(item: RecentRelease) {
  const source = `${item.title} ${item.release_name ?? ''} ${item.url}`;
  const match = source.match(/\bS(\d{1,2})E(\d{1,3})\b/i);
  if (!match) return null;
  const seriesTitle = item.title
    .replace(/\s*S\d{1,2}E\d{1,3}.*$/i, '')
    .replace(/\s*\((?:19|20)\d{2}\)\s*$/, '')
    .trim();
  return {
    seriesTitle: seriesTitle || item.title,
    season: Number(match[1]),
    episode: Number(match[2]),
  };
}

function Poster({ item }: { item: RecentRelease }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className='aspect-[2/3] overflow-hidden rounded-md bg-secondary/40'>
      {item.poster_url && !failed ? (
        <img
          src={api.posterUrl(item.poster_url)}
          alt={`${item.title} cover`}
          className='size-full object-cover'
          loading='lazy'
          decoding='async'
          onError={() => setFailed(true)}
        />
      ) : (
        <div className='flex size-full items-center justify-center text-muted-foreground'>
          {item.kind === 'movie' ? <Film className='size-8' /> : <Tv className='size-8' />}
        </div>
      )}
    </div>
  );
}

export default function Recent() {
  const [page, setPage] = useState(0);
  const [feed, setFeed] = useState<FilmpalastFeed>('new');
  const [data, setData] = useState<RecentReleasePage | null>(() => pageCache.get(pageCacheKey('new', 0)) ?? null);
  const [loading, setLoading] = useState(!pageCache.has(pageCacheKey('new', 0)));
  const [kind, setKind] = useState<'all' | 'movie' | 'episode'>('all');
  const [selected, setSelected] = useState<RecentRelease | null>(null);
  const [tvdbQuery, setTvdbQuery] = useState('');
  const [matches, setMatches] = useState<DiscoverItem[]>([]);
  const [matching, setMatching] = useState(false);
  const [queueing, setQueueing] = useState<number | null>(null);
  const [browseQuery, setBrowseQuery] = useState('');
  const [searchResults, setSearchResults] = useState<RecentRelease[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [details, setDetails] = useState<FilmpalastDetails | null>(null);
  const [activeMirror, setActiveMirror] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let prefetchTimer: number | undefined;
    const key = pageCacheKey(feed, page);
    const cached = pageCache.get(key);
    if (cached) {
      setData(cached);
      setLoading(false);
    } else {
      setData(null);
      setLoading(true);
    }
    api.recentReleases(page, feed)
      .then((result) => {
        if (cancelled) return;
        pageCache.set(key, result);
        setData(result);
        setLoading(false);
        prefetchTimer = window.setTimeout(async () => {
          const neighbours = [page - 1, page + 1].filter((value) => value >= 0 && (value < page || result.has_next));
          for (const neighbour of neighbours) {
            const neighbourKey = pageCacheKey(feed, neighbour);
            if (cancelled || pageCache.has(neighbourKey)) continue;
            try {
              pageCache.set(neighbourKey, await api.recentReleases(neighbour, feed));
            } catch {
              return;
            }
          }
        }, 800);
      })
      .catch((error: any) => {
        if (!cancelled) {
          setLoading(false);
          toast.error(error.message);
        }
      });
    return () => {
      cancelled = true;
      if (prefetchTimer) window.clearTimeout(prefetchTimer);
    };
  }, [feed, page]);

  const visibleItems = useMemo(
    () => (searchResults ?? data?.items ?? []).filter((item) => kind === 'all' || item.kind === kind),
    [data, kind, searchResults],
  );

  async function searchFilmpalast(event: FormEvent) {
    event.preventDefault();
    const query = browseQuery.trim();
    if (!query) return;
    setSearching(true);
    try {
      const result = await api.search(query, 'all', 'filmpalast');
      setSearchResults(result.results.map((item) => ({
        site: item.site,
        title: item.title,
        url: item.url,
        kind: item.kind === 'episode' ? 'episode' : 'movie',
        year: item.year,
        poster_url: item.poster_url ?? null,
        release_name: item.release_name ?? null,
        runtime_minutes: item.runtime_minutes ?? null,
      })));
      setPage(0);
      setKind('all');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSearching(false);
    }
  }

  function clearSearch() {
    setBrowseQuery('');
    setSearchResults(null);
  }

  async function openDetails(item: Pick<RecentRelease, 'url' | 'title'>) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetails(null);
    setActiveMirror(null);
    try {
      const result = await api.filmpalastDetails(item.url);
      setDetails(result);
      setActiveMirror(result.mirrors.find((mirror) => mirror.supported)?.url ?? result.mirrors[0]?.url ?? null);
    } catch (error: any) {
      toast.error(error.message);
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  }

  async function findTvdb(item: RecentRelease, query?: string) {
    const identity = item.kind === 'episode' ? episodeIdentity(item) : null;
    const term = query ?? identity?.seriesTitle ?? item.title;
    setSelected(item);
    setTvdbQuery(term);
    setMatches([]);
    setMatching(true);
    try {
      const result = await api.discoverSearch(
        term,
        item.kind === 'episode' ? 'show' : 'movie',
        'title',
        0,
        10,
      );
      setMatches(result.items);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setMatching(false);
    }
  }

  async function queueSelection(match: DiscoverItem) {
    if (!selected || !match.tvdb_id) return;
    setQueueing(match.tvdb_id);
    try {
      const details = await api.discoverGerman(
        match.tvdb_id,
        selected.kind === 'episode' ? 'show' : 'movie',
      );
      const canonicalName = details.english || match.name;
      if (selected.kind === 'episode') {
        const identity = episodeIdentity(selected);
        if (!identity) {
          toast.error('The season and episode could not be detected for this release.');
          return;
        }
        await api.queueShow({
          show: canonicalName,
          season: identity.season,
          site: selected.site,
          custom_episodes: [{
            episode: identity.episode,
            title: selected.title,
            url: selected.url,
          }],
        });
        toast.success(
          `Queued ${canonicalName} S${String(identity.season).padStart(2, '0')}E${String(identity.episode).padStart(2, '0')}`,
        );
        setSelected(null);
        return;
      }
      let year = details.year ?? match.year ?? selected.year ?? undefined;
      if (!year) {
        const entered = window.prompt(`Enter the release year for "${match.name}":`, '');
        const parsed = entered ? Number.parseInt(entered, 10) : Number.NaN;
        if (!parsed || parsed < 1900 || parsed > 2100) {
          toast.error('A valid release year is required to queue this movie.');
          return;
        }
        year = parsed;
      }
      await api.queueMovie({
        title: canonicalName,
        german: selected.title,
        url: selected.url,
        site: selected.site,
        year,
      });
      toast.success(`Queued ${canonicalName} (${year})`);
      setSelected(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setQueueing(null);
    }
  }

  return (
    <div className='flex flex-col gap-6'>
      <header className='flex flex-wrap items-baseline gap-2'>
        <h1 className='text-2xl font-semibold'>Filmpalast</h1>
        <span className='text-sm text-muted-foreground'>— Browse German new releases, movies, shows, and top titles.</span>
      </header>

      <form className='flex max-w-3xl gap-2' onSubmit={searchFilmpalast}>
        <div className='relative flex-1'>
          <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
          <Input
            className='pl-9 pr-9'
            value={browseQuery}
            onChange={(event) => setBrowseQuery(event.target.value)}
            placeholder='Search Filmpalast movies and shows…'
          />
          {searchResults && (
            <button type='button' className='absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground' onClick={clearSearch} aria-label='Clear Filmpalast search'>
              <X className='size-4' />
            </button>
          )}
        </div>
        <Button type='submit' disabled={!browseQuery.trim() || searching}>
          {searching ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Search data-icon='inline-start' />}
          Search
        </Button>
      </form>

      <div className='flex flex-col gap-3'>
        <Tabs
          value={feed}
          onValueChange={(value) => {
            const next = value as FilmpalastFeed;
            setFeed(next);
            setPage(0);
            setSearchResults(null);
            setBrowseQuery('');
            if (next === 'shows') setKind('episode');
            else if (next === 'movies' || next === 'top') setKind('movie');
            else setKind('all');
          }}
        >
          <TabsList className='h-auto w-full justify-start overflow-x-auto sm:w-fit'>
            <TabsTrigger value='new'>New</TabsTrigger>
            <TabsTrigger value='movies'>Movies</TabsTrigger>
            <TabsTrigger value='shows'>Shows</TabsTrigger>
            <TabsTrigger value='top'>Top titles</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className='flex flex-wrap items-center justify-between gap-3'>
        <Tabs value={kind} onValueChange={(value) => setKind(value as typeof kind)}>
          <TabsList className='border-0 bg-transparent shadow-none'>
            <TabsTrigger value='all'>All</TabsTrigger>
            <TabsTrigger value='movie'>Movies</TabsTrigger>
            <TabsTrigger value='episode'>Episodes</TabsTrigger>
          </TabsList>
        </Tabs>
        {searchResults ? (
          <span className='text-xs text-muted-foreground'>{searchResults.length} search results</span>
        ) : data && (
          <span className='text-xs text-muted-foreground'>
            Pages {data.source_page_start}–{data.source_page_end} · {data.items.length} releases
          </span>
        )}
      </div>

      {(loading && !data) || searching ? (
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10'>
          {Array.from({ length: 10 }).map((_, index) => <Skeleton key={index} className='aspect-[2/3] rounded-lg' />)}
        </div>
      ) : visibleItems.length === 0 ? (
        <EmptyState
          icon={searchResults ? Search : CalendarClock}
          title={searchResults ? 'No Filmpalast matches' : 'No recent releases found'}
          description={searchResults ? 'Try a shorter title or different spelling.' : 'Try another page or filter.'}
        />
      ) : (
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10'>
          {visibleItems.map((item) => (
            <Card key={item.url} className='flex min-w-0 flex-col overflow-hidden transition-colors hover:border-foreground/25'>
              <CardContent className='cursor-pointer p-0' onClick={() => void openDetails(item)}>
                <Poster item={item} />
              </CardContent>
              <CardHeader className='flex-1 gap-2 p-4 pb-3'>
                <div className='flex flex-wrap items-center gap-2'>
                  <Badge variant={item.kind === 'movie' ? 'review' : 'info'}>
                    {item.kind === 'movie' ? 'Movie' : 'Episode'}
                  </Badge>
                  {item.year && <span className='text-xs text-muted-foreground'>{item.year}</span>}
                  {item.runtime_minutes && <span className='text-xs text-muted-foreground'>{item.runtime_minutes} min</span>}
                </div>
                <CardTitle className='line-clamp-2 text-base leading-6'>{item.title}</CardTitle>
              </CardHeader>
              <CardFooter className='gap-2 p-4 pt-0'>
                <Button className='flex-1' size='sm' variant='secondary' onClick={() => void openDetails(item)}>
                  <CirclePlay data-icon='inline-start' /> Details
                </Button>
                <Button className='flex-1' size='sm' onClick={() => void findTvdb(item)}>
                  <Plus data-icon='inline-start' /> Add
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      {!searchResults && (
        <div className='flex items-center justify-center gap-3'>
          <Button variant='secondary' disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>
            Previous
          </Button>
          <span className='text-sm text-foreground'>Page {page + 1}</span>
          <Button variant='secondary' disabled={!data?.has_next || loading} onClick={() => setPage((value) => value + 1)}>
            Next
          </Button>
        </div>
      )}

      <Dialog open={detailOpen} onOpenChange={(open) => {
        setDetailOpen(open);
        if (!open) {
          setDetails(null);
          setActiveMirror(null);
        }
      }}>
        <DialogContent className='max-h-[92vh] max-w-5xl overflow-y-auto'>
          {detailLoading ? (
            <div className='flex min-h-72 items-center justify-center text-muted-foreground'>
              <Loader2 className='mr-2 size-5 animate-spin' /> Loading title and mirrors…
            </div>
          ) : details && (
            <>
              <DialogHeader>
                <div className='flex flex-wrap items-center gap-2'>
                  <Badge variant={details.kind === 'movie' ? 'review' : 'info'}>{details.kind === 'movie' ? 'Movie' : 'Show / episode'}</Badge>
                  {details.year && <span className='text-xs text-muted-foreground'>{details.year}</span>}
                  {details.runtime_minutes && <span className='text-xs text-muted-foreground'>{details.runtime_minutes} min</span>}
                </div>
                <DialogTitle>{details.title}</DialogTitle>
                <DialogDescription>Choose a mirror below to watch it here, or open the player separately.</DialogDescription>
              </DialogHeader>

              <div className='rounded-lg border border-border/60 bg-secondary/20 p-3'>
                <div className='mb-1 flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground'><Files className='size-3.5' /> File title</div>
                {details.release_name ? (
                  <div className='break-all font-mono text-sm text-foreground'>{details.release_name}</div>
                ) : (
                  <div className='text-sm text-muted-foreground'>Filmpalast does not publish a file title for this page.</div>
                )}
              </div>

              {details.episodes.length > 0 && details.mirrors.length === 0 && (
                <div className='space-y-2'>
                  <div className='text-sm font-medium text-foreground'>Episodes</div>
                  <div className='grid max-h-64 gap-2 overflow-y-auto sm:grid-cols-2'>
                    {details.episodes.map((episode) => (
                      <Button
                        key={`${episode.season}-${episode.episode}-${episode.url}`}
                        className='h-auto justify-between py-3 text-left'
                        variant='secondary'
                        onClick={() => void openDetails({ url: episode.url, title: episode.title ?? details.title })}
                      >
                        <span className='min-w-0'>
                          <span className='block font-mono text-xs'>S{String(episode.season).padStart(2, '0')}E{String(episode.episode).padStart(2, '0')}</span>
                          {episode.title && <span className='block truncate text-xs text-muted-foreground'>{episode.title}</span>}
                        </span>
                        <ChevronRight className='size-4 shrink-0' />
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              {details.mirrors.length > 0 ? (
                <div className='space-y-3'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <div className='flex flex-wrap gap-2'>
                      {details.mirrors.map((mirror, index) => (
                        <Button
                          key={mirror.url}
                          size='sm'
                          variant={activeMirror === mirror.url ? 'default' : 'secondary'}
                          onClick={() => setActiveMirror(mirror.url)}
                        >
                          Mirror {index + 1} · {mirror.host}
                          {!mirror.supported && <span className='text-[10px] opacity-60'>fallback</span>}
                        </Button>
                      ))}
                    </div>
                    {activeMirror && (
                      <Button size='sm' variant='secondary' onClick={() => window.open(activeMirror, '_blank', 'noopener,noreferrer')}>
                        Open separately
                      </Button>
                    )}
                  </div>
                  {activeMirror && (
                    <div className='aspect-video overflow-hidden rounded-lg border border-border bg-black'>
                      <iframe
                        key={activeMirror}
                        src={activeMirror}
                        title={`${details.title} player`}
                        className='size-full'
                        allow='autoplay; encrypted-media; fullscreen; picture-in-picture'
                        allowFullScreen
                        referrerPolicy='origin'
                        sandbox='allow-scripts allow-same-origin allow-forms allow-presentation'
                      />
                    </div>
                  )}
                </div>
              ) : details.episodes.length === 0 ? (
                <EmptyState icon={CirclePlay} title='No mirrors found' description='This Filmpalast page currently has no playable mirror links.' />
              ) : null}
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className='max-h-[90vh] max-w-3xl overflow-y-auto'>
          <DialogHeader>
            <DialogTitle>Choose the {selected?.kind === 'episode' ? 'show' : 'movie'} identity</DialogTitle>
            <DialogDescription>
              Select the matching TVDB {selected?.kind === 'episode' ? 'show' : 'movie'}. Bankai will keep the exact Filmpalast release below as the German source.
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className='rounded-lg border border-border/60 bg-secondary/20 p-3'>
              <div className='font-medium text-foreground'>{selected.title}</div>
              <GermanRelease value={selected.release_name} />
            </div>
          )}
          <div className='flex gap-2'>
            <Input
              value={tvdbQuery}
              onChange={(event) => setTvdbQuery(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && selected && void findTvdb(selected, tvdbQuery.trim())}
              placeholder='Search TVDB title…'
            />
            <Button variant='secondary' disabled={!tvdbQuery.trim() || matching} onClick={() => selected && void findTvdb(selected, tvdbQuery.trim())}>
              {matching ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Search data-icon='inline-start' />}
              Search
            </Button>
          </div>
          {matching ? (
            <div className='flex flex-col gap-2'>{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className='h-16' />)}</div>
          ) : matches.length === 0 ? (
            <EmptyState icon={Search} title='No TVDB match yet' description='Adjust the title above and search again.' />
          ) : (
            <div className='flex flex-col gap-2'>
              {matches.map((match) => (
                <div key={match.tvdb_id ?? match.name} className='flex items-center justify-between gap-3 rounded-lg border border-border/60 p-3'>
                  <div className='min-w-0'>
                    <div className='truncate font-medium text-foreground'>{match.name}</div>
                    <div className='text-xs text-muted-foreground'>{match.year ?? 'Year unknown'}</div>
                  </div>
                  <Button size='sm' disabled={!match.tvdb_id || queueing !== null} onClick={() => void queueSelection(match)}>
                    {queueing === match.tvdb_id ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Plus data-icon='inline-start' />}
                    Queue
                  </Button>
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
