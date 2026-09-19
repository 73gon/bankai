import { useEffect, useMemo, useState } from 'react';
import { Film, Tv, RefreshCw, Search, LayoutGrid, Rows3, ExternalLink, HardDrive } from 'lucide-react';
import { toast } from 'sonner';
import { api, type MasMovie, type MasShow } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { AnimePoster } from '@/components/AnimePoster';
import { Meter, rampParts } from '@/components/ui/meter';

function formatSize(bytes: number) {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

type View = 'grid' | 'table';
const VIEW_KEY = 'bankai.mas.library.view';

function storedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'grid';
  } catch {
    return 'grid';
  }
}

type Row = MasMovie | MasShow;

function isShow(row: Row): row is MasShow {
  return 'downloaded_count' in row;
}

/** What a card says under its title: episodes for a series, size for a film. */
function summary(row: Row): string {
  if (isShow(row)) return `${row.downloaded_count}/${row.total_count}`;
  return formatSize(row.size);
}

function PosterCard({ row, onOpen }: { row: Row; onOpen: () => void }) {
  const show = isShow(row) ? row : null;
  return (
    <button type='button' onClick={onOpen} aria-label={'View ' + row.title} className='block w-full text-left'>
      <Card className='poster-card h-full overflow-hidden border-border'>
        <AnimePoster url={row.poster_url} title={row.title} />
        <div className='flex flex-col gap-1.5 p-3'>
          <p className='truncate text-[13px] font-medium leading-tight' title={row.title}>{row.title}</p>
          <div className='flex items-center justify-between gap-2 text-[11px] text-muted-foreground'>
            <span className='font-mono tabular-nums'>{summary(row)}</span>
            {row.year ? <span className='font-mono tabular-nums'>{row.year}</span> : null}
          </div>
          {show && show.total_count > 0 && (
            <Meter parts={rampParts(show.downloaded_count, show.total_count)} total={show.total_count} />
          )}
          {/* Two roots means the same title is stored twice over. */}
          {row.roots.length > 1 && <Badge variant='warning'>{row.roots.length} copies</Badge>}
        </div>
      </Card>
    </button>
  );
}

export default function MasLibrary() {
  const [movies, setMovies] = useState<MasMovie[]>([]);
  const [shows, setShows] = useState<MasShow[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [view, setView] = useState<View>(storedView);
  const [selected, setSelected] = useState<Row | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* a remembered view is a convenience, not state worth failing over */
    }
  }, [view]);

  async function load(rescan = false) {
    setLoading(true);
    try {
      // Episodes come with the page: the drawer opens on a row already held,
      // and a second round trip per click is slower than one larger answer.
      const result = await api.masLibrary(true, rescan);
      setMovies(result.movies);
      setShows(result.shows);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const term = query.trim().toLowerCase();
  const visibleMovies = useMemo(
    () => movies.filter((row) => !term || row.title.toLowerCase().includes(term)),
    [movies, term],
  );
  const visibleShows = useMemo(
    () => shows.filter((row) => !term || row.title.toLowerCase().includes(term)),
    [shows, term],
  );

  function Panel({ rows, empty }: { rows: Row[]; empty: string }) {
    if (loading) return <div className='flex min-h-40 items-center justify-center'><Spinner /></div>;
    if (rows.length === 0) {
      return <EmptyState icon={HardDrive} title={empty} description='Add a directory in Settings, then rescan.' />;
    }
    if (view === 'grid') {
      return (
        <div className='grid gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {rows.map((row) => <PosterCard key={row.key} row={row} onOpen={() => setSelected(row)} />)}
        </div>
      );
    }
    return (
      <div className='overflow-x-auto'>
        <table className='w-full border-collapse text-sm'>
          <thead>
            <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
              <th className='py-2.5 pr-3 font-medium'>Title</th>
              <th className='px-3 py-2.5 font-medium'>Year</th>
              <th className='px-3 py-2.5 font-medium'>Held</th>
              <th className='py-2.5 pl-3 text-right font-medium'>Size</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.key}
                tabIndex={0}
                onClick={() => setSelected(row)}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(row); } }}
                className='data-row cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/40 focus-visible:outline-none'
              >
                <td className='py-2.5 pr-3'>{row.title}</td>
                <td className='px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground'>{row.year ?? '—'}</td>
                <td className='px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground'>{summary(row)}</td>
                <td className='py-2.5 pl-3 text-right font-mono text-xs tabular-nums text-muted-foreground'>{formatSize(row.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const active = selected;
  const activeShow = active && isShow(active) ? active : null;
  const seasons = useMemo(() => {
    if (!activeShow) return [];
    return [...new Set(activeShow.episodes.map((entry) => entry.season_number))]
      .sort((a, b) => (a ?? -1) - (b ?? -1));
  }, [activeShow]);

  const totals = {
    movies: visibleMovies.length,
    shows: visibleShows.length,
    size: [...visibleMovies, ...visibleShows].reduce((sum, row) => sum + row.size, 0),
  };

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='label-mono'>Movies &amp; Shows</p>
          <h1 className='font-serif text-3xl'>Library</h1>
        </div>
        <div className='flex items-center gap-2'>
          <div className='relative'>
            <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
            <Input aria-label='Search the library' placeholder='Search titles…' value={query} onChange={(event) => setQuery(event.target.value)} className='w-60 pl-9' />
          </div>
          <Button variant={view === 'grid' ? 'secondary' : 'ghost'} size='icon' aria-label='Grid view' title='Grid view' onClick={() => setView('grid')}><LayoutGrid /></Button>
          <Button variant={view === 'table' ? 'secondary' : 'ghost'} size='icon' aria-label='Table view' title='Table view' onClick={() => setView('table')}><Rows3 /></Button>
          <Button variant='secondary' onClick={() => void load(true)} disabled={loading}>
            <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} /> Rescan
          </Button>
        </div>
      </div>

      <Tabs defaultValue='movies'>
        <TabsList>
          <TabsTrigger value='movies'><Film data-icon='inline-start' /> Movies</TabsTrigger>
          <TabsTrigger value='shows'><Tv data-icon='inline-start' /> Shows</TabsTrigger>
        </TabsList>
        <TabsContent value='movies'><Panel rows={visibleMovies} empty='No movies found' /></TabsContent>
        <TabsContent value='shows'><Panel rows={visibleShows} empty='No shows found' /></TabsContent>
      </Tabs>

      {!loading && (
        <div className='sticky bottom-0 -mx-1 flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border bg-background/95 px-1 py-2.5 text-xs text-muted-foreground backdrop-blur'>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.movies}</span> movies</span>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.shows}</span> shows</span>
          <span><span className='font-mono tabular-nums text-foreground'>{formatSize(totals.size)}</span> stored</span>
        </div>
      )}

      <Drawer open={Boolean(active)} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DrawerContent aria-describedby={undefined}>
          {active && (
            <>
              <DrawerHeader>
                <div className='flex items-start gap-4 pr-8'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
                  <div className='flex min-w-0 flex-col gap-2'>
                    <DrawerTitle className='text-base font-semibold leading-tight'>
                      {active.title}{active.year ? ' (' + active.year + ')' : ''}
                    </DrawerTitle>
                    <p className='text-xs text-muted-foreground'>
                      {activeShow
                        ? `${activeShow.downloaded_count}/${activeShow.total_count} episodes · ${activeShow.season_count} seasons · ${formatSize(active.size)}`
                        : `${(active as MasMovie).file_count} file${(active as MasMovie).file_count === 1 ? '' : 's'} · ${formatSize(active.size)}`}
                    </p>
                    {active.roots.length > 1 && (
                      <p className='text-xs text-warning'>Stored under {active.roots.length} roots — likely a duplicate.</p>
                    )}
                    {active.tvdb_id && (
                      <div>
                        <Button asChild variant='secondary' size='sm'>
                          <a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer'>
                            <ExternalLink data-icon='inline-start' /> TVDB
                          </a>
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </DrawerHeader>

              {activeShow ? (
                <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col'>
                  <TabsList className='flex h-auto flex-wrap justify-start px-5 pt-3'>
                    {seasons.map((season) => (
                      <TabsTrigger key={String(season)} value={String(season)}>
                        {season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                  {seasons.map((season) => (
                    <TabsContent key={String(season)} value={String(season)} className='min-h-0 flex-1 overflow-y-auto px-5 pb-4'>
                      <table className='w-full border-collapse text-sm'>
                        <tbody>
                          {activeShow.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                            <tr key={entry.path || String(entry.season_number) + ':' + entry.episode} className='border-b border-border/70 last:border-0'>
                              <td className='w-10 py-2.5 pr-3 font-mono text-xs tabular-nums'>{entry.episode ?? '—'}</td>
                              <td className='px-3 py-2.5 text-xs text-muted-foreground'>
                                {entry.episode_title && <p className='text-sm text-foreground'>{entry.episode_title}</p>}
                                {!entry.missing && <p className='font-mono text-[0.68rem]'>{entry.name}</p>}
                                {entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}
                              </td>
                              <td className='py-2.5 pl-3 text-right font-mono text-xs tabular-nums text-muted-foreground'>
                                {entry.missing ? '—' : formatSize(entry.size)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TabsContent>
                  ))}
                </Tabs>
              ) : (
                <div className='min-h-0 flex-1 overflow-y-auto px-5 py-4'>
                  <ul className='flex flex-col gap-1'>
                    {(active as MasMovie).files.map((file) => (
                      <li key={file.path} className='flex items-center justify-between gap-3 rounded-md px-2 py-1.5'>
                        <span className='min-w-0 flex-1 truncate font-mono text-[0.68rem]' title={file.path}>{file.rel_path}</span>
                        <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(file.size)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </DrawerContent>
      </Drawer>
    </div>
  );
}
