import { useEffect, useMemo, useState } from 'react';
import { Film, Tv, RefreshCw, Search, LayoutGrid, Rows3, ExternalLink, HardDrive } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type MasMovie, type MasShow } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Spinner } from '@/zon/ui/spinner';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/zon/ui/drawer';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Meter } from '@/zon/ui/meter';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { AnimePoster } from '@/components/AnimePoster';

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

/** The meter's ink ramps as a series fills: red, then amber, then complete. */
const RAMP = {
  low: '[&_[data-slot=meter-indicator]]:bg-destructive',
  mid: '[&_[data-slot=meter-indicator]]:bg-warning',
  high: '[&_[data-slot=meter-indicator]]:bg-success',
} as const;

function rampIndicator(value: number, total: number): string {
  const ratio = total > 0 ? value / total : 0;
  return ratio >= 0.85 ? RAMP.high : ratio >= 0.5 ? RAMP.mid : RAMP.low;
}

function PosterCard({ row, onOpen }: { row: Row; onOpen: () => void }) {
  const show = isShow(row) ? row : null;
  return (
    <button type='button' onClick={onOpen} aria-label={'View ' + row.title} className='block w-full text-left'>
      <Card className='poster-card h-full gap-0 overflow-hidden [--card-padding:0]'>
        <AnimePoster url={row.poster_url} title={row.title} />
        <div className='flex flex-col gap-1.5 p-3'>
          <p className='truncate text-[13px] font-medium leading-tight' title={row.title}>{row.title}</p>
          <div className='flex items-center justify-between gap-2 text-[11px] text-muted-foreground'>
            <span className='font-mono tabular-nums'>{summary(row)}</span>
            {row.year ? <span className='font-mono tabular-nums'>{row.year}</span> : null}
          </div>
          {show && show.total_count > 0 && (
            <Meter
              value={show.downloaded_count}
              max={show.total_count}
              aria-label={`${show.downloaded_count} of ${show.total_count} episodes`}
              className={rampIndicator(show.downloaded_count, show.total_count)}
            />
          )}
          {/* Two roots means the same title is stored twice over. */}
          {row.roots.length > 1 && <StatusTag variant='warning'>{row.roots.length} copies</StatusTag>}
        </div>
      </Card>
    </button>
  );
}

export default function MasLibrary() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [movies, setMovies] = useState<MasMovie[]>(() => recall<{ movies: MasMovie[] }>(pagePaths.masLibrary)?.movies ?? []);
  const [shows, setShows] = useState<MasShow[]>(() => recall<{ shows: MasShow[] }>(pagePaths.masLibrary)?.shows ?? []);
  // Only a spinner with nothing to show: otherwise the held answer stays up while it refreshes.
  const [loading, setLoading] = useState(() => recall(pagePaths.masLibrary) === undefined);
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
    if (rescan) setLoading(true);
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
      return (
        <Empty>
          <EmptyMedia><HardDrive /></EmptyMedia>
          <EmptyTitle>{empty}</EmptyTitle>
          <EmptyDescription>Add a directory in Settings, then rescan.</EmptyDescription>
        </Empty>
      );
    }
    if (view === 'grid') {
      return (
        <div className='grid gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {rows.map((row) => <PosterCard key={row.key} row={row} onOpen={() => setSelected(row)} />)}
        </div>
      );
    }
    return (
      <Table dense>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Year</TableHead>
            <TableHead>Held</TableHead>
            <TableHead numeric>Size</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={row.key}
              tabIndex={0}
              onClick={() => setSelected(row)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(row); } }}
              className='data-row cursor-pointer focus-ring'
            >
              <TableCell className='text-foreground'>{row.title}</TableCell>
              <TableCell className='font-mono text-xs tabular-nums text-muted-foreground'>{row.year ?? '—'}</TableCell>
              <TableCell className='font-mono text-xs tabular-nums text-muted-foreground'>{summary(row)}</TableCell>
              <TableCell numeric className='text-xs text-muted-foreground'>{formatSize(row.size)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
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
          <p className='eyebrow'>Movies &amp; Shows</p>
          <h1 className='font-heading text-3xl font-light'>Library</h1>
        </div>
        <div className='flex items-center gap-2'>
          <InputGroup className='w-60'>
            <InputGroupAddon><Search /></InputGroupAddon>
            <InputGroupInput aria-label='Search the library' placeholder='Search titles…' value={query} onChange={(event) => setQuery(event.target.value)} />
          </InputGroup>
          <Button variant={view === 'grid' ? 'secondary' : 'ghost'} size='icon' aria-label='Grid view' aria-pressed={view === 'grid'} title='Grid view' onClick={() => setView('grid')}><LayoutGrid /></Button>
          <Button variant={view === 'table' ? 'secondary' : 'ghost'} size='icon' aria-label='Table view' aria-pressed={view === 'table'} title='Table view' onClick={() => setView('table')}><Rows3 /></Button>
          <Button variant='secondary' onClick={() => void load(true)} disabled={loading}>
            <RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} /> Rescan
          </Button>
        </div>
      </div>

      <Tabs defaultValue='movies'>
        <TabsList>
          <TabsTab value='movies'><Film data-icon='inline-start' /> Movies</TabsTab>
          <TabsTab value='shows'><Tv data-icon='inline-start' /> Shows</TabsTab>
        </TabsList>
        <TabsPanel value='movies'><Panel rows={visibleMovies} empty='No movies found' /></TabsPanel>
        <TabsPanel value='shows'><Panel rows={visibleShows} empty='No shows found' /></TabsPanel>
      </Tabs>

      {!loading && (
        <div className='sticky bottom-0 -mx-1 flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border/60 bg-background/95 px-1 py-2.5 text-xs text-muted-foreground backdrop-blur'>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.movies}</span> movies</span>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.shows}</span> shows</span>
          <span><span className='font-mono tabular-nums text-foreground'>{formatSize(totals.size)}</span> stored</span>
        </div>
      )}

      <Drawer side='right' open={Boolean(active)} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DrawerContent className='w-full md:w-1/2'>
          {active && (
            <>
              <DrawerHeader>
                <div className='flex items-start gap-4'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
                  <div className='flex min-w-0 flex-col gap-2'>
                    <DrawerTitle className='text-xl leading-tight'>
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
                        <Button
                          variant='secondary'
                          size='sm'
                          nativeButton={false}
                          render={<a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer' />}
                        >
                          <ExternalLink data-icon='inline-start' /> TVDB
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </DrawerHeader>

              {activeShow ? (
                <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col'>
                  <TabsList variant='underline' className='flex-wrap justify-start gap-y-0'>
                    {seasons.map((season) => (
                      <TabsTab key={String(season)} value={String(season)}>
                        {season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}
                      </TabsTab>
                    ))}
                  </TabsList>
                  {seasons.map((season) => (
                    <TabsPanel key={String(season)} value={String(season)} className='min-h-0 flex-1'>
                      <table className='w-full border-collapse text-sm'>
                        <tbody>
                          {activeShow.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                            <tr key={entry.path || String(entry.season_number) + ':' + entry.episode} className='border-b border-border/60 last:border-0'>
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
                    </TabsPanel>
                  ))}
                </Tabs>
              ) : (
                <div className='min-h-0 flex-1'>
                  <ul className='flex flex-col gap-1'>
                    {(active as MasMovie).files.map((file) => (
                      <li key={file.path} className='flex items-center justify-between gap-3 rounded-item px-2 py-1.5'>
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
