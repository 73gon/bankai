import { useEffect, useMemo, useState } from 'react';
import { CalendarClock, ExternalLink, Film, Loader2, Plus, Search, Tv } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type RecentRelease, type RecentReleasePage } from '@/lib/api';
import { GermanRelease } from '@/components/GermanRelease';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState } from '@/components/ui/empty';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

const pageCache = new Map<number, RecentReleasePage>();

function Poster({ item }: { item: RecentRelease }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className='aspect-[2/3] overflow-hidden rounded-md bg-secondary/40'>
      {item.poster_url && !failed ? (
        <img
          src={api.posterUrl(item.poster_url)}
          alt=''
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
  const [data, setData] = useState<RecentReleasePage | null>(() => pageCache.get(0) ?? null);
  const [loading, setLoading] = useState(!pageCache.has(0));
  const [kind, setKind] = useState<'all' | 'movie' | 'episode'>('all');
  const [selected, setSelected] = useState<RecentRelease | null>(null);
  const [tvdbQuery, setTvdbQuery] = useState('');
  const [matches, setMatches] = useState<DiscoverItem[]>([]);
  const [matching, setMatching] = useState(false);
  const [queueing, setQueueing] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let prefetchTimer: number | undefined;
    const cached = pageCache.get(page);
    if (cached) {
      setData(cached);
      setLoading(false);
    } else {
      setLoading(true);
    }
    api.recentReleases(page)
      .then((result) => {
        if (cancelled) return;
        pageCache.set(page, result);
        setData(result);
        setLoading(false);
        prefetchTimer = window.setTimeout(async () => {
          const neighbours = [page - 1, page + 1].filter((value) => value >= 0 && (value < page || result.has_next));
          for (const neighbour of neighbours) {
            if (cancelled || pageCache.has(neighbour)) continue;
            try {
              pageCache.set(neighbour, await api.recentReleases(neighbour));
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
  }, [page]);

  const visibleItems = useMemo(
    () => (data?.items ?? []).filter((item) => kind === 'all' || item.kind === kind),
    [data, kind],
  );

  async function findTvdb(item: RecentRelease, query = item.title) {
    setSelected(item);
    setTvdbQuery(query);
    setMatches([]);
    setMatching(true);
    try {
      const result = await api.discoverSearch(query, 'movie', 'title', 0, 10);
      setMatches(result.items);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setMatching(false);
    }
  }

  async function queueMovie(match: DiscoverItem) {
    if (!selected || !match.tvdb_id) return;
    setQueueing(match.tvdb_id);
    try {
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
    <div className='space-y-6'>
      <header className='flex flex-wrap items-baseline gap-2'>
        <h1 className='text-2xl font-semibold'>Recently Released</h1>
        <span className='text-sm text-muted-foreground'>— Latest German releases from Filmpalast.</span>
      </header>

      <div className='flex flex-wrap items-center justify-between gap-3'>
        <Tabs value={kind} onValueChange={(value) => setKind(value as typeof kind)}>
          <TabsList className='border-0 bg-transparent shadow-none'>
            <TabsTrigger value='all'>All</TabsTrigger>
            <TabsTrigger value='movie'>Movies</TabsTrigger>
            <TabsTrigger value='episode'>Episodes</TabsTrigger>
          </TabsList>
        </Tabs>
        {data && (
          <span className='text-xs text-muted-foreground'>
            Filmpalast pages {data.source_page_start}–{data.source_page_end} · {data.items.length} releases
          </span>
        )}
      </div>

      {loading && !data ? (
        <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
          {Array.from({ length: 9 }).map((_, index) => <Skeleton key={index} className='h-64 rounded-lg' />)}
        </div>
      ) : visibleItems.length === 0 ? (
        <EmptyState icon={CalendarClock} title='No recent releases found' description='Try another page or filter.' />
      ) : (
        <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
          {visibleItems.map((item) => (
            <Card key={item.url} className='grid grid-cols-[7rem_minmax(0,1fr)] overflow-hidden'>
              <div className='p-4 pr-0'><Poster item={item} /></div>
              <div className='flex min-w-0 flex-col'>
                <CardHeader className='pb-3'>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Badge variant={item.kind === 'movie' ? 'review' : 'info'}>
                      {item.kind === 'movie' ? 'Movie' : 'Episode'}
                    </Badge>
                    {item.year && <span className='text-xs text-muted-foreground'>{item.year}</span>}
                    {item.runtime_minutes && <span className='text-xs text-muted-foreground'>{item.runtime_minutes} min</span>}
                  </div>
                  <CardTitle className='line-clamp-2 font-serif text-base leading-6'>{item.title}</CardTitle>
                </CardHeader>
                <CardContent className='flex-1 pb-3'>
                  <GermanRelease value={item.release_name} className='mt-0' />
                </CardContent>
                <CardFooter className='gap-2 pt-0'>
                  <Button size='sm' variant='secondary' onClick={() => window.open(item.url, '_blank', 'noopener,noreferrer')}>
                    <ExternalLink data-icon='inline-start' /> Source
                  </Button>
                  {item.kind === 'movie' && (
                    <Button size='sm' onClick={() => void findTvdb(item)}>
                      <Plus data-icon='inline-start' /> Add
                    </Button>
                  )}
                </CardFooter>
              </div>
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
            <DialogTitle>Choose the movie identity</DialogTitle>
            <DialogDescription>
              Select the matching TVDB movie. Bankai will keep the exact Filmpalast release below as the German source.
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
            <div className='space-y-2'>{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className='h-16' />)}</div>
          ) : matches.length === 0 ? (
            <EmptyState icon={Search} title='No TVDB match yet' description='Adjust the title above and search again.' />
          ) : (
            <div className='space-y-2'>
              {matches.map((match) => (
                <div key={match.tvdb_id ?? match.name} className='flex items-center justify-between gap-3 rounded-lg border border-border/60 p-3'>
                  <div className='min-w-0'>
                    <div className='truncate font-medium text-foreground'>{match.name}</div>
                    <div className='text-xs text-muted-foreground'>{match.year ?? 'Year unknown'}</div>
                  </div>
                  <Button size='sm' disabled={!match.tvdb_id || queueing !== null} onClick={() => void queueMovie(match)}>
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
