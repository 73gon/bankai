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
  const [range, setRange] = useState('');
  const [loadingEps, setLoadingEps] = useState(false);

  // Live filter from TVDB as the user types (debounced).
  useEffect(() => {
    window.clearTimeout(debounce.current);
    if (q.trim().length < 2) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    debounce.current = window.setTimeout(() => {
      api
        .discoverSearch(q.trim(), kind)
        .then((r) => {
          setConfigured(r.configured);
          setItems(r.items);
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
    if (!selected || !searchTerm.trim()) return;
    setLoadingFilm(true);
    setPicked(null);
    try {
      const r = await api.search(searchTerm.trim(), selected.kind === 'movie' ? 'movie' : 'show');
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
      await api.queueMovie({
        title: selected.name,
        german: german ?? undefined,
        url: r.url,
        site: r.site,
      });
      toast.success(`Queued ${selected.name}`);
      setSelected(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function loadEpisodes(r: SearchResult, s: string) {
    setPicked(r);
    setLoadingEps(true);
    setSelectedEps(new Set());
    try {
      const res = await api.episodes(r.title, Number(s) || 1, r.site);
      setEpisodes(res.episodes);
    } catch (e: any) {
      toast.error(e.message);
      setEpisodes([]);
    } finally {
      setLoadingEps(false);
    }
  }

  function applyRange() {
    const next = new Set(selectedEps);
    range.split(',').forEach((p) => {
      const m = p.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) for (let i = +m[1]; i <= +m[2]; i++) next.add(i);
      else if (p.trim()) next.add(Number(p.trim()));
    });
    setSelectedEps(next);
  }

  async function queueShow() {
    if (!picked) return;
    setBusy(picked.url);
    try {
      await api.queueShow({
        show: picked.title,
        season: Number(season) || 1,
        episodes: selectedEps.size ? Array.from(selectedEps).sort((a, b) => a - b) : undefined,
        site: picked.site,
      });
      toast.success(`Queued ${picked.title} S${season}`);
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
          ) : (
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
                    {selected?.kind === 'movie' ? (
                      <Button size='sm' onClick={() => queueMovie(r)} disabled={busy === r.url}>
                        {busy === r.url ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                        Queue
                      </Button>
                    ) : (
                      <Button size='sm' variant='secondary' onClick={() => loadEpisodes(r, season)}>
                        Episodes
                      </Button>
                    )}
                  </div>

                  {selected?.kind === 'show' && picked?.url === r.url && (
                    <div className='border-t border-border/50 p-3'>
                      <div className='mb-3 flex flex-wrap items-end gap-3'>
                        <div className='space-y-1'>
                          <label className='text-xs text-muted-foreground'>Season</label>
                          <Input
                            type='number'
                            min={1}
                            value={season}
                            onChange={(e) => setSeason(e.target.value)}
                            onBlur={() => loadEpisodes(r, season)}
                            className='w-24'
                          />
                        </div>
                        <div className='space-y-1'>
                          <label className='text-xs text-muted-foreground'>Range (e.g. 1-9)</label>
                          <div className='flex gap-2'>
                            <Input value={range} onChange={(e) => setRange(e.target.value)} className='w-28' />
                            <Button size='sm' variant='outline' onClick={applyRange}>
                              Add
                            </Button>
                          </div>
                        </div>
                        <Button size='sm' onClick={queueShow} disabled={busy === r.url}>
                          {busy === r.url ? <Loader2 className='h-4 w-4 animate-spin' /> : <Plus className='h-4 w-4' />}
                          Queue {selectedEps.size ? `(${selectedEps.size})` : 'all'}
                        </Button>
                      </div>

                      {loadingEps ? (
                        <div className='text-sm text-muted-foreground'>Loading episodes…</div>
                      ) : episodes.length === 0 ? (
                        <div className='text-sm text-muted-foreground'>No episodes found for this season.</div>
                      ) : (
                        <div className='flex flex-wrap gap-2'>
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
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
