import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, ExternalLink, Film, Loader2, Plus, Search, Tv } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type FilmpalastFeed, type RecentRelease, type RecentReleasePage } from '@/lib/api';
import { GermanRelease } from '@/components/GermanRelease';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

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
    () => (data?.items ?? []).filter((item) => kind === 'all' || item.kind === kind),
    [data, kind],
  );

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
      if (selected.kind === 'episode') {
        const identity = episodeIdentity(selected);
        if (!identity) {
          toast.error('The season and episode could not be detected for this release.');
          return;
        }
        await api.queueShow({
          show: match.name,
          season: identity.season,
          site: selected.site,
          custom_episodes: [{
            episode: identity.episode,
            title: selected.title,
            url: selected.url,
          }],
        });
        toast.success(
          `Queued ${match.name} S${String(identity.season).padStart(2, '0')}E${String(identity.episode).padStart(2, '0')}`,
        );
        setSelected(null);
        return;
      }
      let year = match.year ?? selected.year ?? undefined;
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
        title: match.name,
        german: selected.title,
        url: selected.url,
        site: selected.site,
        year,
      });
      toast.success(`Queued ${match.name} (${year})`);
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

      <div className='flex flex-wrap items-center justify-between gap-3'>
        <Tabs value={kind} onValueChange={(value) => setKind(value as typeof kind)}>
          <TabsList className='border-0 bg-transparent shadow-none'>
            <TabsTrigger value='all'>All</TabsTrigger>
            <TabsTrigger value='movie'>Movies</TabsTrigger>
            <TabsTrigger value='episode'>Episodes</TabsTrigger>
          </TabsList>
        </Tabs>
        <Select
          value={feed}
          onValueChange={(value) => {
            const next = value as FilmpalastFeed;
            setFeed(next);
            setPage(0);
            if (next === 'shows') setKind('episode');
            else if (next === 'movies' || next === 'top') setKind('movie');
            else setKind('all');
          }}
        >
          <SelectTrigger className='w-44' aria-label='Filmpalast section'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value='new'>Filmpalast New</SelectItem>
              <SelectItem value='movies'>Movies</SelectItem>
              <SelectItem value='shows'>Shows</SelectItem>
              <SelectItem value='top'>Top</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        {data && (
          <span className='text-xs text-muted-foreground'>
            Pages {data.source_page_start}–{data.source_page_end} · {data.items.length} releases
          </span>
        )}
      </div>

      {loading && !data ? (
        <div className='grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5'>
          {Array.from({ length: 10 }).map((_, index) => <Skeleton key={index} className='aspect-[2/3] rounded-lg' />)}
        </div>
      ) : visibleItems.length === 0 ? (
        <EmptyState icon={CalendarClock} title='No recent releases found' description='Try another page or filter.' />
      ) : (
        <div className='grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5'>
          {visibleItems.map((item) => (
            <Card key={item.url} className='flex min-w-0 flex-col overflow-hidden'>
              <CardContent className='p-0'>
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
                <Button className='flex-1' size='sm' variant='secondary' onClick={() => window.open(item.url, '_blank', 'noopener,noreferrer')}>
                  <ExternalLink data-icon='inline-start' /> Source
                </Button>
                <Button className='flex-1' size='sm' onClick={() => void findTvdb(item)}>
                  <Plus data-icon='inline-start' /> Add
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      <div className='flex items-center justify-center gap-3'>
        <Button variant='secondary' disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>
          Previous
        </Button>
        <span className='text-sm text-foreground'>Page {page + 1}</span>
        <Button variant='secondary' disabled={!data?.has_next || loading} onClick={() => setPage((value) => value + 1)}>
          Next
        </Button>
      </div>

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
