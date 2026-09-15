import { useEffect, useMemo, useState } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeLibraryEntry, type AnimeLibraryShow } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
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
  const [loading, setLoading] = useState(true);
  const [transferring, setTransferring] = useState<string | null>(null);

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
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setTransferring(null);
    }
  }

  useEffect(() => { void load(); }, []);
  const visible = useMemo(() => shows.filter((show) => show.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [shows, query]);
  const totalSize = useMemo(() => shows.reduce((sum, show) => sum + show.size, 0), [shows]);
  const episodeCount = useMemo(() => shows.reduce((sum, show) => sum + show.episode_count, 0), [shows]);
  const active = shows.find((show) => show.key === selected);
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
            <Card key={show.key} className='overflow-hidden'>
              <button onClick={() => setSelected(show.key)} aria-label={'View ' + show.title} className='block w-full rounded-md focus-visible:outline-2 focus-visible:outline-ring'>
                <AnimePoster url={show.poster_url} title={show.title} />
              </button>
              <CardHeader>
                <CardTitle className='line-clamp-2'>{show.title}</CardTitle>
                <CardDescription>{show.year || 'TVDB Anime'} · {show.season_count} season{show.season_count === 1 ? '' : 's'}</CardDescription>
              </CardHeader>
              <CardContent className='flex flex-wrap gap-2'>
                <Badge variant='secondary'>{show.episode_count} episodes</Badge>
                {show.staged_count > 0 && <Badge variant='warning'>{show.staged_count} staged</Badge>}
                <span className='text-xs text-muted-foreground'>{formatSize(show.size)}</span>
              </CardContent>
              <CardFooter><Button variant='secondary' size='sm' className='w-full' onClick={() => setSelected(show.key)}>View details</Button></CardFooter>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={Boolean(active)} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className='max-h-[90vh] max-w-5xl overflow-y-auto'>
          {active && (
            <>
              <DialogHeader>
                <div className='flex items-start gap-5'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-28 shrink-0' />
                  <div className='flex flex-col gap-3'>
                    <DialogTitle>{active.title}{active.year ? ' (' + active.year + ')' : ''}</DialogTitle>
                    <DialogDescription>{active.episode_count} episodes · {active.season_count} seasons · {formatSize(active.size)} · TVDB ordering</DialogDescription>
                    {active.tvdb_id && <Button asChild variant='outline' size='sm'><a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> TVDB</a></Button>}
                  </div>
                </div>
              </DialogHeader>
              <Tabs key={active.key} defaultValue={String(seasons[0])}>
                <TabsList className='flex h-auto flex-wrap justify-start'>
                  {seasons.map((season) => <TabsTrigger key={String(season)} value={String(season)}>{season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}</TabsTrigger>)}
                </TabsList>
                {seasons.map((season) => (
                  <TabsContent key={String(season)} value={String(season)}>
                    <div className='overflow-x-auto'>
                      <table className='w-full min-w-[580px] border-collapse text-sm'>
                        <thead><tr className='border-b border-border text-left text-muted-foreground'><th className='py-3 pr-3'>Episode</th><th className='px-3 py-3'>File</th><th className='px-3 py-3 text-right'>Size</th><th className='py-3 pl-3 text-right'>State</th></tr></thead>
                        <tbody>{active.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                          <tr key={entry.path} className='border-b border-border/60 last:border-0'>
                            <td className='py-3 pr-3 font-mono'>{entry.episode ?? '—'}</td>
                            <td className='px-3 py-3 text-xs text-muted-foreground'>{entry.name}</td>
                            <td className='px-3 py-3 text-right font-mono text-xs'>{formatSize(entry.size)}</td>
                            <td className='py-3 pl-3 text-right'>{entry.staged ? (
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
    </div>
  );
}