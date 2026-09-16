import { useEffect, useMemo, useState } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, Check } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry, type AnimeTVDBMatch } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { AnimePoster } from '@/components/AnimePoster';

function formatSize(bytes: number) {
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

export default function AnimeLibrary() {
  const [shows, setShows] = useState<AnimeLibraryShow[]>([]);
  const [root, setRoot] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedShow, setSelectedShow] = useState<AnimeLibraryShow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [transferring, setTransferring] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [results, setResults] = useState<AnimeEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatch, setSearchMatch] = useState<AnimeTVDBMatch | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const result = await api.animeLibrary();
      setShows(result.shows);
      setRoot(result.root);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function transfer(entry: AnimeLibraryEntry) {
    setTransferring(entry.path);
    try {
      await api.transfer(entry.path);
      toast.success('Anime transfer started');
      await load();
      if (selected) await openShow(selected);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setTransferring(null);
    }
  }

  async function openShow(key: string) {
    setSelected(key);
    setSelectedShow(null);
    setDetailLoading(true);
    try {
      const result = await api.animeLibraryShow(key);
      setSelectedShow(result.shows[0] || null);
      if (!result.shows.length) toast.error('This Anime is no longer in the library index.');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDetailLoading(false);
    }
  }

  async function searchMissing(show: AnimeLibraryShow, episode: AnimeLibraryEpisode, q?: string) {
    if (!show.tvdb_id || episode.season_number == null || episode.episode == null) return;
    setSearchTarget({ show, episode });
    setSearching(true);
    setResults([]);
    setSearchMatch(null);
    try {
      const result = await api.animeEpisodeSearch(show.tvdb_id, episode.season_number, episode.episode, q);
      setResults(result.items);
      setSearchMatch(result.match);
      if (!q) setSearchQuery(result.queries[0] || show.title);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSearching(false);
    }
  }

  async function downloadMissing(entry: AnimeEntry) {
    if (!searchTarget || !searchMatch) return;
    setDownloading(entry.info_hash);
    try {
      await api.animeDownload(entry, searchMatch, { season: searchTarget.episode.season_number, episode: searchTarget.episode.episode });
      toast.success('Episode added to the Anime queue');
      setSearchTarget(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDownloading(null);
    }
  }

  useEffect(() => { void load(); }, []);
  const visible = useMemo(() => shows.filter((show) => show.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [shows, query]);
  const totalSize = useMemo(() => shows.reduce((sum, show) => sum + show.size, 0), [shows]);
  const episodeCount = useMemo(() => shows.reduce((sum, show) => sum + show.episode_count, 0), [shows]);
  const active = selectedShow;
  const seasons = active ? Array.from(new Set(active.episodes.map((episode) => episode.season_number))).sort((a, b) => (a ?? -1) - (b ?? -1)) : [];

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Library</h1>
          <p className='break-all text-sm text-muted-foreground'>{root || 'Dedicated anime destination'}</p>
        </div>
        <div className='flex items-center gap-3'>
          <Input aria-label='Search Anime library' placeholder='Search your Anime…' value={query} onChange={(event) => setQuery(event.target.value)} className='max-w-72' />
          <Button variant='secondary' onClick={() => void load()} disabled={loading}><RefreshCw data-icon='inline-start' /> Rescan</Button>
        </div>
      </div>

      <div className='grid gap-3 sm:grid-cols-3'>
        {[['Series', String(shows.length)], ['Episodes', String(episodeCount)], ['Stored', formatSize(totalSize)]].map(([title, value]) => (
          <Card key={title}><CardHeader><CardDescription>{title}</CardDescription><CardTitle>{value}</CardTitle></CardHeader></Card>
        ))}
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : visible.length === 0 ? (
        <EmptyState icon={HardDrive} title={shows.length ? 'No matching Anime' : 'No Anime in your library yet'} description='Completed and staged episodes are grouped into their TVDB shows.' />
      ) : (
        <div className='grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {visible.map((show) => (
            <button key={show.key} type='button' onClick={() => void openShow(show.key)} aria-label={'View ' + show.title} className='block w-full rounded-lg text-left focus-visible:outline-2 focus-visible:outline-ring'>
              <Card className='h-full overflow-hidden' style={{ borderBottomWidth: 6, borderBottomColor: 'var(--' + ({ complete: 'success', upcoming: 'transfer', partial: 'warning', empty: 'destructive', unknown: 'border' }[show.completion_state]) + ')' }}>
              <div className='relative block w-full'>
                <AnimePoster url={show.poster_url} title={show.title} />
                <span className='absolute inset-x-0 bottom-0 bg-linear-to-t from-black via-black/80 to-transparent px-3 pb-3 pt-12 text-left text-sm font-semibold text-white'>{show.title}{show.year ? ' (' + show.year + ')' : ''}</span>
                {show.finished && <span className='absolute right-2 top-2 rounded-full bg-background/90 p-1.5 text-success' aria-label='Finished show, all episodes downloaded'><Check className='size-5' /></span>}
              </div>
              <CardHeader>
                <CardDescription>{show.season_count} season{show.season_count === 1 ? '' : 's'}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-wrap gap-2'>
                <span className='w-full text-sm'>{show.downloaded_count} out of {show.total_count} episodes downloaded</span>
                {show.staged_count > 0 && <Badge variant='warning'>{show.staged_count} staged</Badge>}
                <span className='text-xs text-muted-foreground'>{formatSize(show.size)}</span>
              </CardContent>
              </Card>
            </button>
          ))}
        </div>
      )}

      <Dialog open={Boolean(selected)} onOpenChange={(open) => { if (!open) { setSelected(null); setSelectedShow(null); } }}>
        <DialogContent className='flex h-[94dvh] w-[96vw] max-w-none flex-col overflow-hidden'>
          {detailLoading && <div className='flex min-h-64 flex-1 items-center justify-center'><Spinner /></div>}
          {active && (
            <>
              <DialogHeader className='shrink-0'>
                <div className='flex items-start gap-5'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-28 shrink-0' />
                  <div className='flex flex-col gap-3'>
                    <DialogTitle>{active.title}{active.year ? ' (' + active.year + ')' : ''}</DialogTitle>
                    <DialogDescription>{active.downloaded_count} out of {active.total_count} episodes downloaded · {active.season_count} seasons · {formatSize(active.size)} · TVDB ordering</DialogDescription>
                    {active.tvdb_id && <Button asChild variant='outline' size='sm'><a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> TVDB</a></Button>}
                  </div>
                </div>
              </DialogHeader>
              <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col'>
                <TabsList className='flex h-auto flex-wrap justify-start'>
                  {seasons.map((season) => <TabsTrigger key={String(season)} value={String(season)}>{season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}</TabsTrigger>)}
                </TabsList>
                {seasons.map((season) => (
                  <TabsContent key={String(season)} value={String(season)} className='min-h-0 flex-1 overflow-y-auto'>
                    <div className='overflow-x-auto'>
                      <table className='w-full min-w-[580px] border-collapse text-sm'>
                        <thead><tr className='border-b border-border text-left text-muted-foreground'><th className='py-3 pr-3'>Episode</th><th className='px-3 py-3'>Episode / File</th><th className='px-3 py-3 text-right'>Size</th><th className='py-3 pl-3 text-right'>State</th></tr></thead>
                        <tbody>{active.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                          <tr key={entry.path || String(entry.season_number) + ':' + entry.episode} className='border-b border-border/60 last:border-0'>
                            <td className='py-3 pr-3 font-mono'>{entry.episode ?? '—'}</td>
                            <td className='px-3 py-3 text-xs text-muted-foreground'>{entry.episode_title && <p className='text-sm text-foreground'>{entry.episode_title}</p>}{!entry.missing && <p>{entry.name}</p>}{entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}</td>
                            <td className='px-3 py-3 text-right font-mono text-xs'>{entry.missing ? '—' : formatSize(entry.size)}</td>
                            <td className='py-3 pl-3 text-right'>{entry.missing ? <Button size='sm' variant='secondary' disabled={!active.tvdb_id} onClick={() => void searchMissing(active, entry)}><Search data-icon='inline-start' /> Search</Button> : entry.staged ? (
                              <Button size='sm' variant='secondary' onClick={() => void transfer(entry)} disabled={transferring === entry.path || entry.transfer_status === 'transferring'}><ArrowRight data-icon='inline-start' /> {entry.transfer_status === 'transferring' ? 'Transferring' : 'Transfer'}</Button>
                            ) : <Badge variant='success'>In library</Badge>}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </TabsContent>
                ))}
              </Tabs>
            </>
          )}
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(searchTarget)} onOpenChange={(open) => { if (!open) setSearchTarget(null); }}>
        <DialogContent className='flex h-[90dvh] w-[92vw] max-w-none flex-col overflow-hidden'>
          <DialogHeader>
            <DialogTitle>Search {searchTarget?.show.title} — S{String(searchTarget?.episode.season_number || 1).padStart(2, '0')}E{String(searchTarget?.episode.episode || 1).padStart(2, '0')}</DialogTitle>
            <DialogDescription>Choose a release for this TVDB episode. Results are checked against the show's episode mapping.</DialogDescription>
          </DialogHeader>
          <form className='flex gap-2' onSubmit={(event) => { event.preventDefault(); if (searchTarget) void searchMissing(searchTarget.show, searchTarget.episode, searchQuery); }}>
            <Input aria-label='Episode release search' value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} className='flex-1' />
            <Button type='submit' disabled={searching}><Search data-icon='inline-start' /> Search</Button>
          </form>
          <div className='min-h-0 flex-1 overflow-y-auto'>
            {searching ? <div className='flex h-40 items-center justify-center'><Spinner /></div> : results.length ? <div className='flex flex-col gap-3'>
              {results.map((entry) => <Card key={entry.info_hash}><CardContent className='flex flex-wrap items-center justify-between gap-3 p-4'>
                <div className='flex min-w-0 flex-1 flex-col gap-2'><p className='break-words font-mono text-sm'>{entry.title}</p><p className='text-xs text-muted-foreground'>{entry.quality} · {entry.size} · {entry.seeders} seeds · {entry.publisher}</p></div>
                <Button asChild size='sm' variant='outline'><a href={entry.detail_url} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> Description</a></Button>
                <Button size='sm' disabled={Boolean(downloading)} onClick={() => void downloadMissing(entry)}><Download data-icon='inline-start' /> {downloading === entry.info_hash ? 'Queuing…' : 'Download'}</Button>
              </CardContent></Card>)}
            </div> : <EmptyState icon={Search} title='No matching releases found' description='Try an alternate Erai title above. If numbering is unresolved, select the show mapping in Anime Settings.' />}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
