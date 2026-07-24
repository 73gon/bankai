import { useEffect, useMemo, useState } from 'react';
import { Compass, Loader2, Plus, Film, Tv, Search as SearchIcon, Check } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type PagedDiscover, type SearchResult } from '@/lib/api';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { CATALOG_PAGE_SIZES, loadCatalogPageSize, saveCatalogPageSize, type CatalogPageSize } from '@/lib/catalog';

const discoverCache = new Map<string, PagedDiscover>();
let discoverView = { kind: 'movie', page: 0 };
let serverNamesCache: Record<string, Set<string>> = {};

function discoverKey(kind: string, page: number, pageSize: number) {
  return `${kind}:${page}:${pageSize}`;
}

// Normalize a title for matching against media-server folder names:
// lowercase, drop a trailing year, strip everything but alphanumerics.
function normalizeTitle(name: string): string {
  return name
    .toLowerCase()
    .replace(/\(?\b(19|20)\d{2}\b\)?/g, '')
    .replace(/[^a-z0-9]/g, '');
}

function Poster({ item, onClick }: { item: DiscoverItem; onClick: () => void }) {
  const [err, setErr] = useState(false);
  return (
    <button
      onClick={onClick}
      className='group relative aspect-[2/3] overflow-hidden rounded-lg bg-secondary/40 text-left ring-1 ring-border/40 transition-transform hover:-translate-y-1 hover:ring-primary/50'
    >
      {item.poster_url && !err ? (
        <img src={api.posterUrl(item.poster_url)} onError={() => setErr(true)} className='h-full w-full object-cover' loading='lazy' />
      ) : (
        <div className='flex h-full w-full items-center justify-center text-muted-foreground'>
          {item.kind === 'movie' ? <Film className='h-8 w-8' /> : <Tv className='h-8 w-8' />}
        </div>
      )}
      <div className='absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-2.5 pt-8'>
        <div className='line-clamp-2 text-xs font-medium'>{item.name}</div>
        {item.year && <div className='text-[10px] text-muted-foreground'>{item.year}</div>}
      </div>
      {item.is_new && (
        <span className='absolute left-2 top-2 rounded bg-primary px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary-foreground shadow'>
          New
        </span>
      )}
      {item.available && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className='absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full bg-green-600 text-white shadow ring-2 ring-black/20'>
              <Check className='h-3.5 w-3.5' strokeWidth={3} />
            </span>
          </TooltipTrigger>
          <TooltipContent>Verified: the matching Filmpalast release currently has a reachable, supported German stream mirror.</TooltipContent>
        </Tooltip>
      )}
      <div className='absolute inset-0 flex items-center justify-center bg-primary/20 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100'>
        <Plus className='h-7 w-7' />
      </div>
    </button>
  );
}

export default function Discover() {
  const [kind, setKind] = useState(discoverView.kind);
  const [page, setPage] = useState(discoverView.page);
  const [pageSize, setPageSize] = useState<CatalogPageSize>(loadCatalogPageSize);
  const initial = discoverCache.get(discoverKey(discoverView.kind, discoverView.page, pageSize));
  const [items, setItems] = useState<DiscoverItem[]>(initial?.items ?? []);
  const [total, setTotal] = useState<number | null>(initial?.total ?? null);
  const [hasNext, setHasNext] = useState(initial?.has_next ?? false);
  const [loading, setLoading] = useState(!initial);
  const [configured, setConfigured] = useState(true);
  const [selected, setSelected] = useState<DiscoverItem | null>(null);
  const [season, setSeason] = useState('1');
  const [episodes, setEpisodes] = useState('');
  const [busy, setBusy] = useState(false);
  const [serverNames, setServerNames] = useState<Set<string>>(serverNamesCache[kind] ?? new Set());

  // Movie -> resolve German title -> filmpalast picker
  const [german, setGerman] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filmResults, setFilmResults] = useState<SearchResult[]>([]);
  const [loadingFilm, setLoadingFilm] = useState(false);
  const [busyUrl, setBusyUrl] = useState<string | null>(null);

  useEffect(() => {
    discoverView = { kind, page };
    const key = discoverKey(kind, page, pageSize);
    const cached = discoverCache.get(key);
    if (cached) {
      setConfigured(cached.configured);
      setItems(cached.items);
      setTotal(cached.total);
      setHasNext(cached.has_next);
      setLoading(false);
    } else {
      setLoading(true);
    }
    let cancelled = false;
    api
      .discoverTrending(kind, page, pageSize)
      .then((r) => {
        if (cancelled) return;
        discoverCache.set(key, r);
        setConfigured(r.configured);
        setItems(r.items);
        setTotal(r.total);
        setHasNext(r.has_next);
        const neighbours = [page - 1, page + 1].filter((value) => value >= 0 && (value < page || r.has_next));
        void Promise.all(
          neighbours.map(async (neighbour) => {
            const neighbourKey = discoverKey(kind, neighbour, pageSize);
            if (!discoverCache.has(neighbourKey)) {
              discoverCache.set(neighbourKey, await api.discoverTrending(kind, neighbour, pageSize));
            }
          }),
        ).catch(() => undefined);
      })
      .catch((e) => toast.error(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [kind, page, pageSize]);

  // Availability (working filmpalast mirror) is checked in the background, so
  // re-fetch a handful of times: verified titles gain a checkmark and ones
  // with no mirror drop out and get backfilled to keep the grid full.
  useEffect(() => {
    if (kind !== 'movie') return;
    let n = 0;
    let stop = false;
    const t = setInterval(async () => {
      n += 1;
      try {
        const r = await api.discoverTrending(kind, page, pageSize);
        if (stop) return;
        discoverCache.set(discoverKey(kind, page, pageSize), r);
        setItems(r.items);
        const pending = r.items.filter((it) => !it.checked).length;
        if (pending === 0 || n >= 10) clearInterval(t);
      } catch {
        /* ignore transient errors */
      }
    }, 6000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [kind, page, pageSize]);

  // Names already present on the media server, to hide from discovery.
  useEffect(() => {
    api
      .serverContents()
      .then((r) => {
        const present = (kind === 'movie' ? r.movies : r.shows).filter((t) => t.present);
        const names = new Set(present.map((t) => normalizeTitle(t.name)));
        serverNamesCache[kind] = names;
        setServerNames(names);
      })
      .catch(() => setServerNames(new Set()));
  }, [kind]);

  const visibleItems = useMemo(() => items.filter((it) => !serverNames.has(normalizeTitle(it.name))), [items, serverNames]);
  const totalPages = total == null ? null : Math.max(1, Math.ceil(total / pageSize));

  async function openItem(item: DiscoverItem) {
    setSelected(item);
    setGerman(null);
    const verifiedResult: SearchResult | null =
      item.available && item.filmpalast_url
        ? {
            site: 'filmpalast',
            title: item.name,
            year: item.year,
            kind: 'movie',
            url: item.filmpalast_url,
          }
        : null;
    setFilmResults(verifiedResult ? [verifiedResult] : []);
    setSeason('1');
    setEpisodes('');
    if (item.kind !== 'movie') return;
    // Movies: resolve the German title and search filmpalast so the user
    // picks the actual source (instead of blind-queuing by English name).
    setLoadingFilm(true);
    try {
      let name = item.name;
      if (item.tvdb_id) {
        const g = await api.discoverGerman(item.tvdb_id, item.kind);
        if (g.year) {
          setSelected((current) => (current ? { ...current, year: g.year, release_date: g.release_date } : current));
        }
        if (g.german) {
          name = g.german;
          setGerman(g.german);
        }
      }
      setSearchTerm(name);
      const r = await api.search(name, 'movie');
      const first = verifiedResult ? [{ ...verifiedResult, title: name }] : [];
      setFilmResults([
        ...first,
        ...r.results.filter((result) => !first.some((verified) => verified.url === result.url)),
      ]);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoadingFilm(false);
    }
  }

  async function reSearchMovie() {
    const term = searchTerm.trim();
    if (!term) return;
    // Accept a pasted stream link directly (no search) — recognise a URL and
    // build a single result the user can queue as-is.
    if (/^https?:\/\/\S+$/i.test(term)) {
      const site = /aniworld/i.test(term) ? 'aniworld' : /bs\.to/i.test(term) ? 'bs' : /kinox/i.test(term) ? 'kinox' : 'filmpalast';
      setFilmResults([{ site, title: selected?.name ?? term, year: selected?.year ?? null, kind: 'movie', url: term }]);
      return;
    }
    setLoadingFilm(true);
    try {
      const r = await api.search(term, 'movie');
      setFilmResults(r.results);
      if (r.results.length === 0) toast.info('No filmpalast match for that term');
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoadingFilm(false);
    }
  }

  async function queueMovie(r: SearchResult) {
    if (!selected) return;
    setBusyUrl(r.url);
    try {
      let year = selected.year ?? undefined;
      try {
        await api.queueMovie({
          title: selected.name,
          german: german ?? undefined,
          url: r.url,
          site: r.site,
          year,
        });
      } catch (err: any) {
        if (err?.message === 'year_required') {
          const entered = window.prompt(`Enter the release year for "${selected.name}":`, '');
          const y = entered ? parseInt(entered.trim(), 10) : NaN;
          if (!y || y < 1900 || y > 2100) {
            toast.error('A valid release year is required to queue this movie.');
            return;
          }
          year = y;
          await api.queueMovie({
            title: selected.name,
            german: german ?? undefined,
            url: r.url,
            site: r.site,
            year,
          });
        } else {
          throw err;
        }
      }
      toast.success(`Queued ${selected.name}${year ? ` (${year})` : ''}`);
      setSelected(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusyUrl(null);
    }
  }

  async function enqueueShow() {
    if (!selected) return;
    setBusy(true);
    try {
      const eps = episodes
        .split(',')
        .flatMap((p) => {
          const m = p.trim().match(/^(\d+)\s*-\s*(\d+)$/);
          if (m) {
            const out = [];
            for (let i = +m[1]; i <= +m[2]; i++) out.push(i);
            return out;
          }
          return p.trim() ? [Number(p.trim())] : [];
        })
        .filter((n) => !Number.isNaN(n));
      await api.queueShow({
        show: selected.name,
        season: Number(season) || 1,
        episodes: eps.length ? eps : undefined,
      });
      toast.success(`Queued ${selected.name}`);
      setSelected(null);
      setEpisodes('');
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className='space-y-6'>
      <header className='flex items-baseline gap-2'>
        <h1 className='text-2xl font-semibold'>Discover</h1>
        <span className='text-sm text-muted-foreground'>— Trending titles from TheTVDB.</span>
      </header>

      <Tabs
        value={kind}
        onValueChange={(value) => {
          setKind(value);
          setPage(0);
        }}
      >
        <TabsList className='border-0 bg-transparent shadow-none'>
          <TabsTrigger value='movie'>Movies</TabsTrigger>
          <TabsTrigger value='show'>Shows</TabsTrigger>
        </TabsList>

        <TabsContent value={kind}>
          {!configured ? (
            <EmptyState
              icon={Compass}
              title='TheTVDB key not configured'
              description='Add your TVDB API key in Settings to browse trending titles.'
            />
          ) : loading ? (
            <div className='grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8'>
              {Array.from({ length: Math.min(pageSize, 24) }).map((_, i) => (
                <Skeleton key={i} className='aspect-[2/3] rounded-lg' />
              ))}
            </div>
          ) : visibleItems.length === 0 ? (
            <EmptyState icon={Compass} title='Nothing to show' />
          ) : (
            <div className='grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8'>
              {visibleItems.map((it) => (
                <Poster key={`${it.kind}-${it.tvdb_id}-${it.name}`} item={it} onClick={() => openItem(it)} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {!loading && configured && (
        <div className='flex items-center justify-center gap-3'>
          <Button variant='secondary' disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
            Previous
          </Button>
          <span className='text-sm text-foreground'>
            Page {page + 1}{totalPages ? ` of ${totalPages}` : ''}
          </span>
          <Select
            value={String(pageSize)}
            onValueChange={(value) => {
              const next = Number(value) as CatalogPageSize;
              setPageSize(next);
              saveCatalogPageSize(next);
              setPage(0);
            }}
          >
            <SelectTrigger className='w-32' aria-label='Entries per page'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATALOG_PAGE_SIZES.map((size) => (
                <SelectItem key={size} value={String(size)}>{size} per page</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant='secondary' disabled={!hasNext} onClick={() => setPage((value) => value + 1)}>
            Next
          </Button>
        </div>
      )}

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className={selected?.kind === 'movie' ? 'max-w-2xl' : undefined}>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              {selected?.name}
              {german && german !== selected?.name && <Badge variant='accent'>DE: {german}</Badge>}
            </DialogTitle>
            <DialogDescription>
              {selected?.kind === 'movie'
                ? loadingFilm
                  ? 'Searching filmpalast…'
                  : 'Pick the matching filmpalast entry to queue.'
                : 'Pick a season and optional episode list to queue.'}
            </DialogDescription>
          </DialogHeader>

          {selected?.kind === 'show' ? (
            <>
              <div className='grid grid-cols-2 gap-3'>
                <div className='space-y-1'>
                  <label className='text-xs text-muted-foreground'>Season</label>
                  <Input value={season} onChange={(e) => setSeason(e.target.value)} type='number' min={1} />
                </div>
                <div className='space-y-1'>
                  <label className='text-xs text-muted-foreground'>Episodes (e.g. 1-9 or blank=all)</label>
                  <Input value={episodes} onChange={(e) => setEpisodes(e.target.value)} placeholder='all' />
                </div>
              </div>
              <DialogFooter>
                <Button onClick={enqueueShow} disabled={busy}>
                  {busy ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                  Queue
                </Button>
              </DialogFooter>
            </>
          ) : (
            <div className='space-y-3'>
              <div className='space-y-1'>
                <label className='text-xs text-muted-foreground'>filmpalast search term</label>
                <div className='flex gap-2'>
                  <Input
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && reSearchMovie()}
                    placeholder='Edit the search term…'
                    className='flex-1'
                  />
                  <Button variant='secondary' onClick={reSearchMovie} disabled={loadingFilm || !searchTerm.trim()}>
                    {loadingFilm ? <Loader2 className='h-4 w-4 animate-spin' /> : <SearchIcon className='h-4 w-4' />}
                    Search
                  </Button>
                </div>
              </div>

              {loadingFilm ? (
                <div className='space-y-2'>
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className='h-14' />
                  ))}
                </div>
              ) : filmResults.length === 0 ? (
                <EmptyState icon={SearchIcon} title='No filmpalast match' description='Try editing the search term above.' />
              ) : (
                <div className='max-h-[55vh] space-y-2 overflow-auto pr-1'>
                  {filmResults.map((r) => (
                    <div key={`${r.site}-${r.url}`} className='flex items-center justify-between gap-3 rounded-lg p-3 ring-1 ring-border/50'>
                      <div className='min-w-0'>
                        <div className='flex items-center gap-2'>
                          <span className='truncate font-medium'>{r.title}</span>
                          {r.year && <span className='text-xs text-muted-foreground'>{r.year}</span>}
                        </div>
                        <Badge variant='muted' className='mt-1'>
                          {r.site}
                        </Badge>
                        {selected?.filmpalast_url === r.url && (
                          <Badge variant='success' className='ml-2 mt-1'>
                            Verified German source
                          </Badge>
                        )}
                      </div>
                      <Button size='sm' onClick={() => queueMovie(r)} disabled={busyUrl === r.url}>
                        {busyUrl === r.url ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                        Queue
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
