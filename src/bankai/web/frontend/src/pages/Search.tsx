import { useEffect, useRef, useState } from 'react';
import { Search as SearchIcon, Loader2, Plus, Film, Tv, Link2, Check } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type DiscoverSearchBy, type PagedDiscover, type SearchResult, type EpisodeItem } from '@/lib/api';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { CATALOG_PAGE_SIZES, loadCatalogPageSize, saveCatalogPageSize, type CatalogPageSize } from '@/lib/catalog';

const SEARCH_HIDE_LIBRARY_KEY = 'bankai:search-hide-library';
const searchPageCache = new Map<string, PagedDiscover>();

function searchCacheKey(query: string, kind: string, by: string, page: number, pageSize: number) {
  return `${kind}:${by}:${query.toLocaleLowerCase()}:${page}:${pageSize}`;
}

// Parse "S02E01" / "Staffel 2" / "Season 2" out of a source title or URL.
function parseSeason(s: string): number | null {
  const m = s.match(/s(\d{1,2})\s*e\d{1,3}/i) || s.match(/\bstaffel\s*(\d+)/i) || s.match(/\bseason\s*(\d+)/i);
  return m ? Number(m[1]) : null;
}

// Strip a trailing year and any season/episode marker to get the series name.
function cleanSeriesTitle(s: string): string {
  return s
    .replace(/\s*[\(\[]?\b(19|20)\d{2}\b[\)\]]?/g, '')
    .replace(/\s*[-–:]?\s*(s\d{1,2}\s*e\d{1,3}|staffel\s*\d+|season\s*\d+).*$/i, '')
    .trim();
}

function siteLabel(site: string): string {
  if (site === 'burningseries' || site === 'bs.to') return 'Burning Series';
  if (site === 'filmpalast') return 'Filmpalast';
  if (site === 'unknown' || site === 'custom') return 'Custom mirror';
  return site;
}

function streamSiteFromUrl(raw: string): string {
  try {
    const host = new URL(raw).hostname.toLowerCase();
    const isHost = (domain: string) => host === domain || host.endsWith(`.${domain}`);
    if (isHost('filmpalast.to')) return 'filmpalast';
    if (['burningseries.ac', 'bs.to', 'bs.cine.to'].some(isHost)) return 'burningseries';
    if (isHost('aniworld.to')) return 'aniworld';
    if (isHost('kinox.to')) return 'kinox';
  } catch {
    // The caller validates the full URL before queueing.
  }
  return 'unknown';
}

function validStreamUrl(raw: string): boolean {
  try {
    const url = new URL(raw.trim());
    return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname;
  } catch {
    return false;
  }
}

function uniqueShowSources(results: SearchResult[]): SearchResult[] {
  const seen = new Set<string>();
  return results.filter((result) => {
    if (seen.has(result.site)) return false;
    seen.add(result.site);
    return true;
  });
}

function Poster({ item, onClick, priority = false, eager = false }: { item: DiscoverItem; onClick: () => void; priority?: boolean; eager?: boolean }) {
  const [err, setErr] = useState(false);
  return (
    <button
      onClick={onClick}
      className='group relative aspect-[2/3] overflow-hidden rounded-lg bg-secondary/40 text-left ring-1 ring-border/40 transition-transform hover:-translate-y-1 hover:ring-primary/50'
    >
      {item.poster_url && !err ? (
        <img
          src={api.posterUrl(item.poster_url)}
          onError={() => setErr(true)}
          className='h-full w-full object-cover'
          loading={eager ? 'eager' : 'lazy'}
          fetchPriority={priority ? 'high' : 'auto'}
          decoding='async'
        />
      ) : (
        <div className='flex h-full w-full items-center justify-center text-muted-foreground'>
          {item.kind === 'movie' ? <Film className='h-8 w-8' /> : <Tv className='h-8 w-8' />}
        </div>
      )}
      {item.added && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant='success' className='absolute right-2 top-2 gap-1 border-success/50 bg-card/95 shadow-lg backdrop-blur-sm'>
              <Check className='h-3 w-3' strokeWidth={3} /> Added
            </Badge>
          </TooltipTrigger>
          <TooltipContent>Already added to the queue or library.</TooltipContent>
        </Tooltip>
      )}
      <div className='absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-2.5 pt-8'>
        <div className='line-clamp-2 text-xs font-medium'>{item.name}</div>
        {item.year && <div className='text-[10px] text-muted-foreground'>{item.year}</div>}
      </div>
    </button>
  );
}

export default function Search() {
  const [kind, setKind] = useState<'movie' | 'show'>('movie');
  const [searchBy, setSearchBy] = useState<DiscoverSearchBy>('title');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<DiscoverItem[]>([]);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [pageSize, setPageSize] = useState<CatalogPageSize>(loadCatalogPageSize);
  const [hideLibrary, setHideLibrary] = useState(() => {
    try {
      return localStorage.getItem(SEARCH_HIDE_LIBRARY_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const [loading, setLoading] = useState(false);
  const [configured, setConfigured] = useState(true);
  const debounce = useRef<number | undefined>(undefined);

  // selected TVDB title -> resolve German name -> matching stream sources
  const [selected, setSelected] = useState<DiscoverItem | null>(null);
  const [german, setGerman] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filmResults, setFilmResults] = useState<SearchResult[]>([]);
  const [loadingFilm, setLoadingFilm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [directMovieUrl, setDirectMovieUrl] = useState('');

  // show episode picking
  const [picked, setPicked] = useState<SearchResult | null>(null);
  const [season, setSeason] = useState('1');
  const [episodes, setEpisodes] = useState<EpisodeItem[]>([]);
  const [selectedEps, setSelectedEps] = useState<Set<number>>(new Set());
  const [loadingEps, setLoadingEps] = useState(false);
  // seasons detected from the source results + resolved series name
  const [showSeasons, setShowSeasons] = useState<number[]>([]);
  const [seriesTitle, setSeriesTitle] = useState('');
  const [customMode, setCustomMode] = useState(false);
  const [customUrls, setCustomUrls] = useState<Record<number, string>>({});

  // Live filter from TVDB as the user types (debounced).
  useEffect(() => {
    window.clearTimeout(debounce.current);
    let cancelled = false;
    const raw = q.trim();
    if (raw.length < 2) {
      setItems([]);
      setLoading(false);
      return;
    }
    // A standalone 4-digit number is treated as a year filter (exactly 4
    // digits — 3 or 5 don't count). The rest is the title query.
    const ym = searchBy === 'title' ? raw.match(/(?<!\d)(\d{4})(?!\d)/) : null;
    const year = ym ? parseInt(ym[1], 10) : null;
    const titleQuery = year ? raw.replace(ym![0], '').replace(/\s+/g, ' ').trim() : raw;
    const key = searchCacheKey(titleQuery || raw, kind, searchBy, page, pageSize);
    const cached = searchPageCache.get(key);
    if (cached) {
      setConfigured(cached.configured);
      setItems(year ? cached.items.filter((item) => item.year === year) : cached.items);
      setTotal(cached.total);
      setHasNext(cached.has_next);
      setLoading(false);
    } else {
      setLoading(true);
    }
    debounce.current = window.setTimeout(() => {
      api
        .discoverSearch(titleQuery || raw, kind, searchBy, page, pageSize)
        .then((r) => {
          if (cancelled) return;
          searchPageCache.set(key, r);
          setConfigured(r.configured);
          setItems(year ? r.items.filter((i) => i.year === year) : r.items);
          setTotal(r.total);
          setHasNext(r.has_next);
          const neighbours = [page - 1, page + 1].filter((value) => value >= 0 && (value < page || r.has_next));
          void Promise.all(
            neighbours.map(async (neighbour) => {
              const neighbourKey = searchCacheKey(titleQuery || raw, kind, searchBy, neighbour, pageSize);
              if (!searchPageCache.has(neighbourKey)) {
                searchPageCache.set(neighbourKey, await api.discoverSearch(titleQuery || raw, kind, searchBy, neighbour, pageSize));
              }
            }),
          ).catch(() => undefined);
        })
        .catch((e) => {
          if (!cancelled) toast.error(e.message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(debounce.current);
    };
  }, [q, kind, searchBy, page, pageSize]);

  useEffect(() => {
    try {
      localStorage.setItem(SEARCH_HIDE_LIBRARY_KEY, String(hideLibrary));
    } catch {
      /* ignore */
    }
  }, [hideLibrary]);

  const visibleItems = hideLibrary ? items.filter((item) => !item.in_library) : items;
  const totalPages = total == null ? null : Math.max(1, Math.ceil(total / pageSize));

  async function openTitle(item: DiscoverItem) {
    setSelected(item);
    setGerman(null);
    setFilmResults([]);
    setPicked(null);
    setShowSeasons([]);
    setSeriesTitle('');
    setEpisodes([]);
    setSelectedEps(new Set());
    setCustomMode(false);
    setCustomUrls({});
    setDirectMovieUrl('');
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
      const r = await api.search(name, item.kind === 'movie' ? 'movie' : 'show');
      setFilmResults(r.results);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoadingFilm(false);
    }
  }

  async function reSearch() {
    if (!selected) return;
    const term = searchTerm.trim();
    if (!term) return;
    setPicked(null);
    setCustomMode(false);
    setCustomUrls({});
    // A pasted stream link is used directly instead of searching.
    if (selected.kind === 'movie' && /^https?:\/\/\S+$/i.test(term)) {
      const site = streamSiteFromUrl(term);
      setFilmResults([{ site, title: selected.name, year: selected.year ?? null, kind: 'movie', url: term }]);
      return;
    }
    setLoadingFilm(true);
    try {
      const r = await api.search(term, selected.kind === 'movie' ? 'movie' : 'show');
      setFilmResults(r.results);
      if (r.results.length === 0) toast.info('No German stream source matched that term');
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoadingFilm(false);
    }
  }

  async function queueMovie(r: SearchResult) {
    if (!selected) return;
    setBusy(r.url);
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
      setBusy(null);
    }
  }

  async function loadEpisodes(r: SearchResult, s: string, showName?: string, preselectAll = false) {
    setPicked(r);
    setLoadingEps(true);
    if (!preselectAll) setSelectedEps(new Set());
    try {
      const name = (showName || cleanSeriesTitle(r.title) || r.title).trim();
      const res = await api.episodes(name, Number(s) || 1, r.site);
      setEpisodes(res.episodes);
      if (preselectAll) setSelectedEps(new Set(res.episodes.map((e) => e.episode)));
    } catch (e: any) {
      toast.error(e.message);
      setEpisodes([]);
    } finally {
      setLoadingEps(false);
    }
  }

  // When source results arrive for a show, auto-detect the season(s),
  // resolve the series name, and preselect every episode of the first
  // season so the user only has to confirm (or deselect a few).
  useEffect(() => {
    if (!selected || selected.kind !== 'show' || filmResults.length === 0) return;
    const seasons = new Set<number>();
    for (const r of filmResults) {
      const s = parseSeason(r.title) ?? parseSeason(r.url);
      if (s) seasons.add(s);
    }
    const list = Array.from(seasons).sort((a, b) => a - b);
    setShowSeasons(list);
    const series = filmResults[0];
    const cleaned = cleanSeriesTitle(series.title) || searchTerm || series.title;
    setSeriesTitle(cleaned);
    const firstSeason = list[0] ?? 1;
    setSeason(String(firstSeason));
    setCustomMode(false);
    setCustomUrls({});
    loadEpisodes(series, String(firstSeason), cleaned, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filmResults, selected]);

  function pickSeason(s: number) {
    setSeason(String(s));
    setCustomUrls({});
    if (picked) loadEpisodes(picked, String(s), seriesTitle, !customMode);
  }

  function queueDirectMovie() {
    if (!selected) return;
    const url = directMovieUrl.trim();
    if (!validStreamUrl(url)) {
      toast.error('Paste a valid http(s) German source link.');
      return;
    }
    void queueMovie({
      site: streamSiteFromUrl(url),
      title: selected.name,
      year: selected.year ?? null,
      kind: 'movie',
      url,
    });
  }

  function selectCustomSource() {
    setCustomMode(true);
    setSelectedEps(new Set(Object.entries(customUrls).filter(([, url]) => url.trim()).map(([episode]) => Number(episode))));
    const source = picked ?? uniqueShowSources(filmResults)[0];
    if (!episodes.length && source) {
      const cleaned = cleanSeriesTitle(source.title) || searchTerm || source.title;
      setSeriesTitle(cleaned);
      loadEpisodes(source, season, cleaned, false);
    }
  }

  function setCustomEpisodeUrl(episode: number, url: string) {
    setCustomUrls((current) => ({ ...current, [episode]: url }));
    setSelectedEps((current) => {
      const next = new Set(current);
      url.trim() ? next.add(episode) : next.delete(episode);
      return next;
    });
  }

  async function queueShow() {
    if (!picked) return;
    const selectedEpisodes = Array.from(selectedEps).sort((a, b) => a - b);
    if (customMode) {
      if (selectedEpisodes.length === 0) {
        toast.error('Paste at least one episode mirror link.');
        return;
      }
      const invalid = selectedEpisodes.find((episode) => !validStreamUrl(customUrls[episode] || ''));
      if (invalid != null) {
        toast.error(`Episode ${invalid} needs a valid http(s) mirror link.`);
        return;
      }
    }
    setBusy(customMode ? 'custom' : picked.url);
    try {
      const show = (seriesTitle || picked.title).trim();
      await api.queueShow({
        show,
        season: Number(season) || 1,
        episodes: customMode ? undefined : selectedEpisodes.length ? selectedEpisodes : undefined,
        site: customMode ? undefined : picked.site,
        custom_episodes: customMode
          ? selectedEpisodes.map((episode) => ({
              episode,
              title: episodes.find((item) => item.episode === episode)?.title || undefined,
              url: customUrls[episode].trim(),
            }))
          : undefined,
      });
      toast.success(`Queued ${show} S${season} (${customMode ? selectedEpisodes.length : selectedEps.size || 'all'})`);
      setSelected(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className='space-y-6'>
      <header className='flex items-baseline gap-2'>
        <h1 className='shrink-0 text-2xl font-semibold'>Search</h1>
        <span className='text-sm text-muted-foreground'>—{' '}
          {kind === 'show'
            ? 'Find a show on TheTVDB, then match it to a German stream source.'
            : searchBy === 'person'
              ? 'Find movies by cast or crew on TheTVDB, then match one to a German stream source.'
              : searchBy === 'studio'
                ? 'Find movies from a studio or production company on TheTVDB.'
                : 'Find a movie on TheTVDB, then match it to a German stream source.'}
        </span>
      </header>

      <div className='flex flex-wrap items-center gap-3'>
        <Tabs
          value={kind}
          onValueChange={(v) => {
            setKind(v as 'movie' | 'show');
            if (v === 'show') setSearchBy('title');
            setItems([]);
            setSelected(null);
            setPage(0);
          }}
        >
          <TabsList className='border-0 bg-transparent shadow-none'>
            <TabsTrigger value='movie'>Movie</TabsTrigger>
            <TabsTrigger value='show'>Show</TabsTrigger>
          </TabsList>
        </Tabs>
        {kind === 'movie' && (
          <>
            <Separator orientation='vertical' className='h-7' />
            <Tabs
              value={searchBy}
              onValueChange={(value) => {
                setSearchBy(value as DiscoverSearchBy);
                setItems([]);
                setSelected(null);
                setPage(0);
              }}
              aria-label='Search movies by'
            >
              <TabsList className='border-0 bg-transparent shadow-none'>
                <TabsTrigger value='title'>Title</TabsTrigger>
                <TabsTrigger value='person'>Person</TabsTrigger>
                <TabsTrigger value='studio'>Studio</TabsTrigger>
              </TabsList>
            </Tabs>
          </>
        )}
        <div className='relative flex-1'>
          <SearchIcon className='absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground' />
          <Input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
            placeholder={
              kind === 'show'
                ? 'Start typing a show…'
                : searchBy === 'person'
                  ? 'Type an actor, director, or crew member…'
                  : searchBy === 'studio'
                    ? 'Type a studio or production company…'
                    : 'Start typing a movie…'
            }
            className='pl-9'
            autoFocus
          />
        </div>
        <label className='flex items-center gap-2 whitespace-nowrap text-sm text-foreground'>
          <Switch checked={hideLibrary} onCheckedChange={setHideLibrary} aria-label='Hide movies already in library' />
          Hide library titles
        </label>
      </div>

      {!configured ? (
        <EmptyState icon={SearchIcon} title='TheTVDB key not configured' description='Add your TVDB API key in Settings to search.' />
      ) : loading ? (
        <div className='grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6'>
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className='aspect-[2/3] rounded-lg' />
          ))}
        </div>
      ) : q.trim().length < 2 ? (
        <EmptyState icon={SearchIcon} title='Type to search' description='Results appear as you type.' />
      ) : visibleItems.length === 0 ? (
        <EmptyState
          icon={SearchIcon}
          title='No results'
          description={searchBy === 'person' ? 'Try the person’s full name.' : searchBy === 'studio' ? 'Try the company’s full name.' : 'Try a different title.'}
        />
      ) : (
        <div className='grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6'>
          {visibleItems.map((it, index) => (
            <Poster
              key={`${it.kind}-${it.tvdb_id}-${it.name}`}
              item={it}
              onClick={() => openTitle(it)}
              priority={index === 0}
              eager={index < 6}
            />
          ))}
        </div>
      )}

      {!loading && q.trim().length >= 2 && (
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
        <DialogContent className='max-h-[90vh] max-w-3xl overflow-y-auto'>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              {selected?.name}
              {german && german !== selected?.name && <Badge variant='accent'>DE: {german}</Badge>}
            </DialogTitle>
            <DialogDescription>
              {loadingFilm ? 'Searching German stream sources…' : 'Pick a matching result or use your own German source link below.'}
            </DialogDescription>
          </DialogHeader>

          <div className='flex flex-col gap-1'>
            <label className='text-xs text-muted-foreground'>Search German source title</label>
            <div className='flex gap-2'>
              <Input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && reSearch()}
                placeholder='Search the German movie title…'
                className='flex-1'
              />
              <Button variant='secondary' onClick={reSearch} disabled={loadingFilm || !searchTerm.trim()}>
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
          ) : filmResults.length === 0 && selected?.kind === 'movie' ? (
            <EmptyState icon={SearchIcon} title='No German source match' description='Try another title or use the direct German source field below.' />
          ) : selected?.kind === 'movie' ? (
            <div className='max-h-[55vh] space-y-2 overflow-auto pr-1'>
              {filmResults.map((r) => (
                <div key={`${r.site}-${r.url}`} className='rounded-lg ring-1 ring-border/50'>
                  <div className='flex items-center justify-between gap-3 p-3'>
                    <div className='min-w-0'>
                      <div className='flex items-center gap-2'>
                        <span className='truncate font-medium'>{r.title}</span>
                        {r.year && <span className='text-xs text-muted-foreground'>{r.year}</span>}
                      </div>
                      <Badge variant='muted' className='mt-1'>
                        {siteLabel(r.site)}
                      </Badge>
                    </div>
                    <Button size='sm' onClick={() => queueMovie(r)} disabled={busy === r.url}>
                      {busy === r.url ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                      Queue
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            // Show flow: choose a scraper source or provide one mirror URL per episode.
            <div className='space-y-3 rounded-lg p-1'>
              <div className='space-y-1.5'>
                <span className='text-xs text-muted-foreground'>Source matches</span>
                <div className='grid gap-2 py-0.5 sm:grid-cols-3'>
                  {uniqueShowSources(filmResults).map((r) => {
                    const active = !customMode && picked?.site === r.site && picked?.url === r.url;
                    return (
                      <Tooltip key={`${r.site}-${r.url}`}>
                        <TooltipTrigger asChild>
                          <button
                            type='button'
                            onClick={() => {
                              setCustomMode(false);
                              const cleaned = cleanSeriesTitle(r.title) || searchTerm || r.title;
                              setSeriesTitle(cleaned);
                              loadEpisodes(r, season, cleaned, true);
                            }}
                            className={'min-w-0 cursor-pointer rounded-md px-3 py-2 text-left text-xs ring-1 transition-colors ' + (active ? 'bg-primary/20 text-foreground ring-primary/50' : 'bg-secondary/40 text-muted-foreground ring-border/50 hover:text-foreground')}
                          >
                            <span className='block font-medium'>{siteLabel(r.site)}</span>
                            <span className='block truncate opacity-75'>{r.title}</span>
                          </button>
                        </TooltipTrigger>
                        <TooltipContent>{r.title}</TooltipContent>
                      </Tooltip>
                    );
                  })}
                  <button
                    type='button'
                    onClick={selectCustomSource}
                    className={
                      'rounded-md px-3 py-2 text-left text-xs ring-1 transition-colors ' +
                      (customMode
                        ? 'bg-transfer/20 text-foreground ring-transfer/50'
                        : 'bg-secondary/40 text-muted-foreground ring-border/50 hover:text-foreground')
                    }
                  >
                    <span className='flex items-center gap-1.5 font-medium'>
                      <Link2 className='h-3.5 w-3.5' /> Custom
                    </span>
                    <span className='block opacity-75'>One mirror link per episode</span>
                  </button>
                </div>
              </div>

              <div className='flex flex-wrap items-center gap-2'>
                <span className='text-sm font-medium'>{seriesTitle || selected?.name}</span>
                <Badge variant={customMode ? 'transfer' : 'muted'}>{customMode ? 'Custom mirrors' : siteLabel(picked?.site || 'source')}</Badge>
              </div>

              <div className='flex flex-wrap items-center gap-2'>
                <span className='text-xs text-muted-foreground'>Season</span>
                {showSeasons.length > 0 ? (
                  showSeasons.map((s) => (
                    <button
                      key={s}
                      onClick={() => pickSeason(s)}
                      className={
                        'rounded-md px-2.5 py-1 text-xs font-medium ring-1 transition-colors ' +
                        (String(s) === season
                          ? 'bg-primary/20 text-primary-foreground ring-primary/40'
                          : 'bg-secondary/40 text-muted-foreground ring-border/40 hover:text-foreground')
                      }
                    >
                      S{String(s).padStart(2, '0')}
                    </button>
                  ))
                ) : (
                  <Input
                    type='number'
                    min={1}
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                    onBlur={() => picked && loadEpisodes(picked, season, seriesTitle, !customMode)}
                    className='h-8 w-20'
                  />
                )}
              </div>

              {loadingEps ? (
                <div className='text-sm text-muted-foreground'>Detecting episodes…</div>
              ) : episodes.length === 0 ? (
                <div className='rounded-md border border-border/50 bg-secondary/25 p-3 text-sm text-muted-foreground'>
                  {filmResults.length === 0
                    ? 'No supported source supplied the episode list. Search a matching show source first, then switch to Custom to paste mirror links.'
                    : 'No episodes found for this season.'}
                </div>
              ) : (
                <>
                  <div className='flex items-center gap-2'>
                    {!customMode && (
                      <Button size='sm' variant='outline' onClick={() => setSelectedEps(new Set(episodes.map((e) => e.episode)))}>
                        Select all
                      </Button>
                    )}
                    <Button
                      size='sm'
                      variant='outline'
                      onClick={() => {
                        setSelectedEps(new Set());
                        if (customMode) setCustomUrls({});
                      }}
                    >
                      {customMode ? 'Clear links' : 'Clear'}
                    </Button>
                    <span className='text-xs text-muted-foreground'>
                      {customMode ? 'Pasting a link selects its episode' : `${selectedEps.size} of ${episodes.length} selected`}
                    </span>
                  </div>
                  <div className='max-h-[38vh] space-y-2 overflow-y-auto pr-1'>
                    {episodes.map((ep) => {
                      const on = selectedEps.has(ep.episode);
                      return (
                        <div
                          key={ep.episode}
                          className={
                            'grid gap-2 rounded-md p-2.5 ring-1 transition-colors sm:grid-cols-[minmax(12rem,0.8fr)_minmax(16rem,1.2fr)] sm:items-center ' +
                            (on
                              ? customMode
                                ? 'bg-transfer/10 ring-transfer/40'
                                : 'bg-primary/15 ring-primary/40'
                              : 'bg-secondary/30 ring-border/40')
                          }
                        >
                          <button
                            type='button'
                            onClick={() => {
                              const next = new Set(selectedEps);
                              on ? next.delete(ep.episode) : next.add(ep.episode);
                              setSelectedEps(next);
                            }}
                            className='flex min-w-0 items-center gap-3 rounded-sm text-left'
                          >
                            <span
                              className={
                                'shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold ring-1 ' +
                                (on
                                  ? customMode
                                    ? 'bg-transfer/20 text-transfer ring-transfer/40'
                                    : 'bg-primary/20 text-foreground ring-primary/40'
                                  : 'bg-secondary/50 text-muted-foreground ring-border/50')
                              }
                            >
                              E{String(ep.episode).padStart(2, '0')}
                            </span>
                            <span className='min-w-0 truncate text-sm text-foreground'>{ep.title || `Episode ${ep.episode}`}</span>
                          </button>
                          {customMode && (
                            <Input
                              type='url'
                              value={customUrls[ep.episode] || ''}
                              onChange={(event) => setCustomEpisodeUrl(ep.episode, event.target.value)}
                              placeholder='https://voe.sx/...'
                              aria-label={`Mirror URL for episode ${ep.episode}`}
                              className='h-9 font-mono text-xs'
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              <div className='flex justify-end'>
                <Button onClick={queueShow} disabled={!!busy || episodes.length === 0 || (customMode && selectedEps.size === 0)}>
                  {busy ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                  Queue {selectedEps.size ? `${selectedEps.size} episode${selectedEps.size === 1 ? '' : 's'}` : customMode ? 'episode links' : 'all'}
                </Button>
              </div>
            </div>
          )}

          {selected?.kind === 'movie' && (
            <div className='flex flex-col gap-3'>
              <div className='flex items-center gap-3'>
                <Separator className='flex-1' />
                <span className='text-xs font-medium text-muted-foreground'>or use your own source</span>
                <Separator className='flex-1' />
              </div>
              <div className='flex flex-col gap-2 rounded-lg border border-border/60 bg-secondary/20 p-3'>
                <div className='flex items-center gap-2 text-sm font-medium text-foreground'>
                  <Link2 /> Use your own German source
                </div>
                <p className='text-xs text-muted-foreground'>
                  Paste a direct VOE, Vidmoly, Vinovo, Filmpalast, or other German video link. It will be used instead of the unavailable automatic result.
                </p>
                <div className='flex flex-col gap-2 sm:flex-row'>
                  <Input
                    type='url'
                    value={directMovieUrl}
                    onChange={(event) => setDirectMovieUrl(event.target.value)}
                    onKeyDown={(event) => event.key === 'Enter' && queueDirectMovie()}
                    placeholder='https://voe.sx/e/…'
                    aria-label='Direct German movie source URL'
                    className='flex-1'
                  />
                  <Button
                    onClick={queueDirectMovie}
                    disabled={!directMovieUrl.trim() || busy === directMovieUrl.trim()}
                  >
                    {busy === directMovieUrl.trim() ? <Loader2 className='animate-spin' /> : <Plus data-icon='inline-start' />}
                    Queue with link
                  </Button>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
