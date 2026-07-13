import { useEffect, useRef, useState } from 'react';
import { Search as SearchIcon, Loader2, Plus, Film, Tv } from 'lucide-react';
import { toast } from 'sonner';
import { api, type DiscoverItem, type SearchResult, type EpisodeItem } from '@/lib/api';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

// Parse "S02E01" / "Staffel 2" / "Season 2" out of a filmpalast title or URL.
function parseSeason(s: string): number | null {
  const m =
    s.match(/s(\d{1,2})\s*e\d{1,3}/i) ||
    s.match(/\bstaffel\s*(\d+)/i) ||
    s.match(/\bseason\s*(\d+)/i);
  return m ? Number(m[1]) : null;
}

// Strip a trailing year and any season/episode marker to get the series name.
function cleanSeriesTitle(s: string): string {
  return s
    .replace(/\s*[\(\[]?\b(19|20)\d{2}\b[\)\]]?/g, '')
    .replace(/\s*[-–:]?\s*(s\d{1,2}\s*e\d{1,3}|staffel\s*\d+|season\s*\d+).*$/i, '')
    .trim();
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
    </button>
  );
}

export default function Search() {
  const [kind, setKind] = useState<'movie' | 'show'>('movie');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<DiscoverItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [configured, setConfigured] = useState(true);
  const debounce = useRef<number | undefined>(undefined);

  // selected TVDB title -> resolve German name -> filmpalast matches
  const [selected, setSelected] = useState<DiscoverItem | null>(null);
  const [german, setGerman] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filmResults, setFilmResults] = useState<SearchResult[]>([]);
  const [loadingFilm, setLoadingFilm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // show episode picking
  const [picked, setPicked] = useState<SearchResult | null>(null);
  const [season, setSeason] = useState('1');
  const [episodes, setEpisodes] = useState<EpisodeItem[]>([]);
  const [selectedEps, setSelectedEps] = useState<Set<number>>(new Set());
  const [loadingEps, setLoadingEps] = useState(false);
  // seasons detected from the filmpalast results + resolved series name
  const [showSeasons, setShowSeasons] = useState<number[]>([]);
  const [seriesTitle, setSeriesTitle] = useState('');

  // Live filter from TVDB as the user types (debounced).
  useEffect(() => {
    window.clearTimeout(debounce.current);
    const raw = q.trim();
    if (raw.length < 2) {
      setItems([]);
      setLoading(false);
      return;
    }
    // A standalone 4-digit number is treated as a year filter (exactly 4
    // digits — 3 or 5 don't count). The rest is the title query.
    const ym = raw.match(/(?<!\d)(\d{4})(?!\d)/);
    const year = ym ? parseInt(ym[1], 10) : null;
    const titleQuery = year ? raw.replace(ym![0], '').replace(/\s+/g, ' ').trim() : raw;
    setLoading(true);
    debounce.current = window.setTimeout(() => {
      api
        .discoverSearch(titleQuery || raw, kind)
        .then((r) => {
          setConfigured(r.configured);
          setItems(year ? r.items.filter((i) => i.year === year) : r.items);
        })
        .catch((e) => toast.error(e.message))
        .finally(() => setLoading(false));
    }, 350);
    return () => window.clearTimeout(debounce.current);
  }, [q, kind]);

  async function openTitle(item: DiscoverItem) {
    setSelected(item);
    setGerman(null);
    setFilmResults([]);
    setPicked(null);
    setShowSeasons([]);
    setSeriesTitle('');
    setEpisodes([]);
    setSelectedEps(new Set());
    setLoadingFilm(true);
    try {
      let name = item.name;
      if (item.tvdb_id) {
        const g = await api.discoverGerman(item.tvdb_id, item.kind);
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
    // A pasted stream link is used directly instead of searching.
    if (selected.kind === 'movie' && /^https?:\/\/\S+$/i.test(term)) {
      const site = /aniworld/i.test(term)
        ? 'aniworld'
        : /bs\.to/i.test(term)
          ? 'bs'
          : /kinox/i.test(term)
            ? 'kinox'
            : 'filmpalast';
      setFilmResults([{ site, title: selected.name, year: selected.year ?? null, kind: 'movie', url: term }]);
      return;
    }
    setLoadingFilm(true);
    try {
      const r = await api.search(term, selected.kind === 'movie' ? 'movie' : 'show');
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

  // When filmpalast results arrive for a show, auto-detect the season(s),
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
    loadEpisodes(series, String(firstSeason), cleaned, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filmResults, selected]);

  function pickSeason(s: number) {
    setSeason(String(s));
    if (picked) loadEpisodes(picked, String(s), seriesTitle, true);
  }

  async function queueShow() {
    if (!picked) return;
    setBusy(picked.url);
    try {
      const show = (seriesTitle || picked.title).trim();
      await api.queueShow({
        show,
        season: Number(season) || 1,
        episodes: selectedEps.size ? Array.from(selectedEps).sort((a, b) => a - b) : undefined,
        site: picked.site,
      });
      toast.success(`Queued ${show} S${season} (${selectedEps.size || 'all'})`);
      setSelected(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className='space-y-6'>
      <header>
        <h1 className='text-2xl font-semibold'>Search</h1>
        <p className='text-sm text-muted-foreground'>Find a {kind} on TheTVDB, then match it on filmpalast.</p>
      </header>

      <div className='flex flex-wrap items-center gap-3'>
        <Tabs
          value={kind}
          onValueChange={(v) => {
            setKind(v as 'movie' | 'show');
            setItems([]);
            setSelected(null);
          }}
        >
          <TabsList>
            <TabsTrigger value='movie'>Movie</TabsTrigger>
            <TabsTrigger value='show'>Show</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className='relative flex-1'>
          <SearchIcon className='absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground' />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={kind === 'movie' ? 'Start typing a movie…' : 'Start typing a show…'}
            className='pl-9'
            autoFocus
          />
        </div>
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
      ) : items.length === 0 ? (
        <EmptyState icon={SearchIcon} title='No results' description='Try a different title.' />
      ) : (
        <div className='grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6'>
          {items.map((it) => (
            <Poster key={`${it.kind}-${it.tvdb_id}-${it.name}`} item={it} onClick={() => openTitle(it)} />
          ))}
        </div>
      )}

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className='max-w-2xl'>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2'>
              {selected?.name}
              {german && german !== selected?.name && <Badge variant='accent'>DE: {german}</Badge>}
            </DialogTitle>
            <DialogDescription>{loadingFilm ? 'Searching filmpalast…' : 'Pick the matching filmpalast entry to queue.'}</DialogDescription>
          </DialogHeader>

          <div className='space-y-1'>
            <label className='text-xs text-muted-foreground'>filmpalast search term</label>
            <div className='flex gap-2'>
              <Input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && reSearch()}
                placeholder='Edit the search term…'
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
          ) : filmResults.length === 0 ? (
            <EmptyState icon={SearchIcon} title='No filmpalast match' description='This title may not be available on filmpalast.' />
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
                        {r.site}
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
            // Show flow: season auto-detected, all episodes preselected — just confirm.
            <div className='space-y-3 rounded-lg p-1'>
              <div className='flex flex-wrap items-center gap-2'>
                <span className='text-sm font-medium'>{seriesTitle || selected?.name}</span>
                <Badge variant='muted'>{picked?.site || 'filmpalast'}</Badge>
              </div>

              <div className='flex flex-wrap items-center gap-2'>
                <span className='text-xs text-muted-foreground'>Season</span>
                {(showSeasons.length ? showSeasons : [Number(season) || 1]).map((s) => (
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
                ))}
                <div className='flex items-center gap-1'>
                  <Input
                    type='number'
                    min={1}
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                    onBlur={() => picked && loadEpisodes(picked, season, seriesTitle, true)}
                    className='h-8 w-20'
                  />
                </div>
              </div>

              {loadingEps ? (
                <div className='text-sm text-muted-foreground'>Detecting episodes…</div>
              ) : episodes.length === 0 ? (
                <div className='text-sm text-muted-foreground'>No episodes found for this season.</div>
              ) : (
                <>
                  <div className='flex items-center gap-2'>
                    <Button size='sm' variant='outline' onClick={() => setSelectedEps(new Set(episodes.map((e) => e.episode)))}>
                      Select all
                    </Button>
                    <Button size='sm' variant='outline' onClick={() => setSelectedEps(new Set())}>
                      Clear
                    </Button>
                    <span className='text-xs text-muted-foreground'>
                      {selectedEps.size} of {episodes.length} selected
                    </span>
                  </div>
                  <div className='flex max-h-[35vh] flex-wrap gap-2 overflow-auto'>
                    {episodes.map((ep) => {
                      const on = selectedEps.has(ep.episode);
                      return (
                        <button
                          key={ep.episode}
                          onClick={() => {
                            const next = new Set(selectedEps);
                            on ? next.delete(ep.episode) : next.add(ep.episode);
                            setSelectedEps(next);
                          }}
                          className={
                            'rounded-md px-2.5 py-1 text-xs font-medium ring-1 transition-colors ' +
                            (on
                              ? 'bg-primary/20 text-primary-foreground ring-primary/40'
                              : 'bg-secondary/40 text-muted-foreground ring-border/40 hover:text-foreground')
                          }
                          title={ep.title || undefined}
                        >
                          E{String(ep.episode).padStart(2, '0')}
                        </button>
                      );
                    })}
                  </div>
                </>
              )}

              <div className='flex justify-end'>
                <Button onClick={queueShow} disabled={!!busy || episodes.length === 0}>
                  {busy ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                  Queue {selectedEps.size ? `${selectedEps.size} episode${selectedEps.size === 1 ? '' : 's'}` : 'all'}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
