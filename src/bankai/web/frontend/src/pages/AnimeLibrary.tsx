import { useEffect, useMemo, useState } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, FileVideo, LayoutGrid, Rows3, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry, type AnimeTVDBMatch } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { AnimePoster } from '@/components/AnimePoster';
import { Meter, rampParts } from '@/components/ui/meter';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/ui/sort-header';
import { cn } from '@/lib/utils';

function formatSize(bytes: number) {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

type LibrarySortKey = 'title' | 'seasons' | 'episodes' | 'encode' | 'size' | 'state';

const LIBRARY_SORTERS: Record<LibrarySortKey, (show: AnimeLibraryShow) => number | string> = {
  title: (show) => show.title.toLocaleLowerCase(),
  seasons: (show) => show.season_count,
  episodes: (show) => show.downloaded_count,
  // By how much of the show is the newer encode, not by a raw count, so a
  // long series part-converted does not outrank a short one fully converted.
  encode: (show) => {
    const known = (show.hevc_count ?? 0) + (show.avc_count ?? 0);
    return known ? (show.hevc_count ?? 0) / known : -1;
  },
  size: (show) => show.size,
  state: (show) => show.completion_state,
};

const LIBRARY_FIRST_DIRECTION: Record<LibrarySortKey, SortDir> = {
  title: 'asc', seasons: 'desc', episodes: 'desc', encode: 'desc', size: 'desc', state: 'asc',
};

type View = 'grid' | 'table';
const VIEW_KEY = 'bankai.anime.library.view';

function storedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'grid';
  } catch {
    // Private windows and blocked site data both throw here; the grid is a
    // perfectly good default to fall back on.
    return 'grid';
  }
}

const COMPLETION: Record<string, { label: string; className: string }> = {
  complete: { label: 'Complete', className: 'bg-success' },
  upcoming: { label: 'Airing', className: 'bg-transfer' },
  partial: { label: 'Partial', className: 'bg-warning' },
  empty: { label: 'Empty', className: 'bg-destructive' },
  unknown: { label: 'Unknown', className: 'bg-muted-foreground' },
};

/** How a show's episodes divide between encodes, as one compact bar.
 *
 *  The counts are the reason the upgrade exists, so they belong on the row
 *  rather than two clicks away. German dubs are shown apart from plain AVC
 *  because they are deliberately not replaceable.
 */
function CodecMix({ show }: { show: AnimeLibraryShow }) {
  const hevc = show.hevc_count ?? 0;
  const avc = show.avc_count ?? 0;
  const dubbed = show.german_dub_count ?? 0;
  const identified = hevc + avc + dubbed;
  const unknown = Math.max(0, show.downloaded_count - identified);
  const total = identified + unknown;
  if (!total) return <span className='text-xs text-muted-foreground'>—</span>;
  const title = [
    hevc && `${hevc} HEVC`,
    avc && `${avc} AVC`,
    dubbed && `${dubbed} German dub`,
    unknown && `${unknown} not identified yet`,
  ].filter(Boolean).join(' · ');
  return (
    <div className='flex items-center gap-2'>
      <Meter
        className='w-[74px]'
        total={total}
        title={title}
        parts={[
          { value: hevc, className: 'bg-trend' },
          { value: avc, className: 'bg-warning' },
          { value: dubbed, className: 'bg-transfer' },
          { value: unknown, className: 'bg-trend-muted' },
        ]}
      />
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{hevc}/{total}</span>
    </div>
  );
}

function Progress({ show }: { show: AnimeLibraryShow }) {
  const total = show.total_count || show.downloaded_count || 1;
  return (
    <div className='flex items-center gap-2'>
      <Meter
        className='w-[74px]'
        total={total}
        title={`${show.downloaded_count} of ${show.total_count} episodes`}
        parts={rampParts(show.downloaded_count, total)}
      />
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
        {show.downloaded_count}/{show.total_count}
      </span>
    </div>
  );
}

export default function AnimeLibrary() {
  const [shows, setShows] = useState<AnimeLibraryShow[]>([]);
  const [root, setRoot] = useState('');
  const [query, setQuery] = useState('');
  const [view, setView] = useState<View>(storedView);
  const [sort, setSort] = useState<SortState<LibrarySortKey> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedShow, setSelectedShow] = useState<AnimeLibraryShow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [transferring, setTransferring] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [results, setResults] = useState<AnimeEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatch, setSearchMatch] = useState<AnimeTVDBMatch | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<AnimeLibraryShow | null>(null);
  const [removing, setRemoving] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* remembering the choice is a convenience, not a requirement */
    }
  }, [view]);

  async function load(rescan = false) {
    setLoading(true);
    try {
      const result = await api.animeLibrary(rescan);
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
    setDetailLoading(true);
    try {
      const result = await api.animeLibraryShow(key);
      setSelectedShow(result.shows[0] ?? null);
    } catch (error: any) {
      toast.error(error.message);
      setSelectedShow(null);
    } finally {
      setDetailLoading(false);
    }
  }

  async function upgradeToHevc(show: AnimeLibraryShow) {
    if (!show.tvdb_id) return;
    setUpgrading(show.key);
    try {
      const result = await api.upgradeShowToHevc(show.tvdb_id, show.title);
      if (result.queued === 0) {
        toast.info(result.no_replacement > 0
          ? 'No HEVC release is available for the remaining ' + result.no_replacement + ' episode(s)'
          : 'Nothing to upgrade — every episode is already HEVC');
      } else {
        toast.success(
          'Queued ' + result.queued + ' HEVC replacement' + (result.queued === 1 ? '' : 's')
          + (result.no_replacement ? ' · ' + result.no_replacement + ' without a replacement' : '')
          + (result.german_dub_kept ? ' · ' + result.german_dub_kept + ' German dub kept' : ''),
        );
      }
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setUpgrading(null);
    }
  }

  async function removeShow() {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      const result = await api.removeAnimeLibraryShow(removeTarget.key);
      toast.success(
        removeTarget.title + ' removed and blacklisted — deleted ' + result.deleted_files + ' file'
        + (result.deleted_files === 1 ? '' : 's')
        + (result.freed_bytes ? ' (' + formatSize(result.freed_bytes) + ')' : '')
        + (result.removed_torrents ? ', ' + result.removed_torrents + ' torrent' + (result.removed_torrents === 1 ? '' : 's') : ''),
      );
      setRemoveTarget(null);
      setSelected(null);
      setSelectedShow(null);
      await load(true);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRemoving(false);
    }
  }

  async function searchMissing(show: AnimeLibraryShow, episode: AnimeLibraryEpisode, q?: string) {
    if (!show.tvdb_id || episode.season_number == null || episode.episode == null) return;
    setSearchTarget({ show, episode });
    setSearching(true);
    setResults([]);
    try {
      const result = await api.animeEpisodeSearch(show.tvdb_id, episode.season_number, episode.episode, q);
      setResults(result.items);
      setSearchMatch(result.match);
      setSearchQuery(q ?? result.queries[0] ?? '');
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
  const visible = useMemo(() => {
    const matched = shows.filter((show) => show.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    if (!sort) return matched;
    const pick = LIBRARY_SORTERS[sort.key];
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...matched].sort((left, right) => {
      const a = pick(left);
      const b = pick(right);
      if (typeof a === 'string' || typeof b === 'string') {
        return String(a).localeCompare(String(b)) * factor;
      }
      return (a === b ? 0 : a < b ? -1 : 1) * factor;
    });
  }, [shows, query, sort]);
  const totals = useMemo(() => visible.reduce((acc, show) => ({
    size: acc.size + show.size,
    episodes: acc.episodes + show.episode_count,
    hevc: acc.hevc + (show.hevc_count ?? 0),
    avc: acc.avc + (show.avc_count ?? 0),
    dubbed: acc.dubbed + (show.german_dub_count ?? 0),
  }), { size: 0, episodes: 0, hevc: 0, avc: 0, dubbed: 0 }), [visible]);
  function toggleSort(key: LibrarySortKey) {
    setSort((current) => nextSort(current, key, LIBRARY_FIRST_DIRECTION));
  }

  const active = selectedShow;
  const seasons = active ? Array.from(new Set(active.episodes.map((episode) => episode.season_number))).sort((a, b) => (a ?? -1) - (b ?? -1)) : [];

  return (
    <div className='flex min-h-0 flex-1 flex-col gap-5'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Library</h1>
          <p className='break-all font-mono text-xs text-muted-foreground'>{root || 'Dedicated anime destination'}</p>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <div className='relative'>
            <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
            <Input aria-label='Search Anime library' placeholder='Search your Anime…' value={query} onChange={(event) => setQuery(event.target.value)} className='w-60 pl-9' />
          </div>
          <ToggleGroup label='Library view'>
            {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
              <ToggleGroupItem
                key={value}
                icon={Icon}
                label={label}
                selected={view === value}
                onClick={() => setView(value)}
              />
            ))}
          </ToggleGroup>
          <Button variant='secondary' onClick={() => void load(true)} disabled={loading}><RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} /> Rescan</Button>
        </div>
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : visible.length === 0 ? (
        <EmptyState icon={HardDrive} title={shows.length ? 'No matching Anime' : 'No Anime in your library yet'} description='Completed and staged episodes are grouped into their TVDB shows.' />
      ) : view === 'grid' ? (
        <div className='min-h-0 flex-1 overflow-y-auto'>
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {visible.map((show) => {
            const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
            return (
              <button key={show.key} type='button' onClick={() => void openShow(show.key)} aria-label={'View ' + show.title} className='block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background'>
                <Card className='poster-card h-full overflow-hidden border-border'>
                  <div className='relative block w-full'>
                    <AnimePoster url={show.poster_url} title={show.title} />
                    <span className='absolute inset-x-0 bottom-0 bg-linear-to-t from-black via-black/75 to-transparent px-3 pb-2.5 pt-10 text-left text-sm font-medium leading-tight text-white'>{show.title}</span>
                  </div>
                  {/* The completion colour, as the rule between the cover and
                      what is written under it. */}
                  <span className={cn('block h-0.5 w-full', tone.className)} title={tone.label} aria-hidden='true' />
                  <CardContent className='flex flex-col gap-2 p-3'>
                    <Progress show={show} />
                    <div className='flex items-center justify-between gap-2'>
                      <CodecMix show={show} />
                      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(show.size)}</span>
                    </div>
                  </CardContent>
                </Card>
              </button>
            );
          })}
        </div>
        </div>
      ) : (
        <div className='panel min-h-0 flex-1 overflow-auto'>
          <table className='w-full min-w-[820px] border-collapse text-sm'>
            <thead className='sticky top-0 z-10 bg-card'>
              <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                <SortHeader label='Series' column='title' sort={sort} onSort={toggleSort} />
                <SortHeader label='Seasons' column='seasons' sort={sort} onSort={toggleSort} />
                <SortHeader label='Episodes' column='episodes' sort={sort} onSort={toggleSort} />
                <SortHeader label='Encode' column='encode' sort={sort} onSort={toggleSort} />
                <SortHeader label='Size' column='size' sort={sort} onSort={toggleSort} align='right' />
                <SortHeader label='State' column='state' sort={sort} onSort={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {visible.map((show) => {
                const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
                return (
                  <tr
                    key={show.key}
                    tabIndex={0}
                    onClick={() => void openShow(show.key)}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void openShow(show.key); } }}
                    className='data-row cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60'
                  >
                    <td className='px-3 py-2'>
                      <div className='flex items-center gap-2.5'>
                        <AnimePoster url={show.poster_url} title={show.title} className='w-8 shrink-0 rounded' />
                        <div className='min-w-0'>
                          <p className='truncate font-medium text-foreground' title={show.title}>{show.title}</p>
                          {show.year ? <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.year}</p> : null}
                        </div>
                      </div>
                    </td>
                    <td className='px-3 py-2 font-mono text-xs tabular-nums text-muted-foreground'>{show.season_count}</td>
                    <td className='px-3 py-2'><Progress show={show} /></td>
                    <td className='px-3 py-2'><CodecMix show={show} /></td>
                    <td className='px-3 py-2 text-right font-mono text-xs tabular-nums'>{formatSize(show.size)}</td>
                    <td className='px-3 py-2'>
                      <div className='flex items-center gap-1.5'>
                        <span className={cn('size-1.5 rounded-full', tone.className)} />
                        <span className='text-xs text-muted-foreground'>{tone.label}</span>
                        {show.staged_count > 0 && <Badge variant='warning'>{show.staged_count} staged</Badge>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!loading && visible.length > 0 && (
        <footer className='flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-2.5 text-xs text-muted-foreground'>
          <span><span className='font-mono tabular-nums text-foreground'>{visible.length}</span> series{visible.length !== shows.length ? ` of ${shows.length}` : ''}</span>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.episodes.toLocaleString()}</span> episodes</span>
          <span><span className='font-mono tabular-nums text-foreground'>{formatSize(totals.size)}</span> stored</span>
          <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-success' /><span className='font-mono tabular-nums text-foreground'>{totals.hevc}</span> HEVC</span>
          <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-warning' /><span className='font-mono tabular-nums text-foreground'>{totals.avc}</span> AVC</span>
          {totals.dubbed > 0 && <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-transfer' /><span className='font-mono tabular-nums text-foreground'>{totals.dubbed}</span> German dub</span>}
        </footer>
      )}

      <Drawer open={Boolean(selected)} onOpenChange={(open) => { if (!open) { setSelected(null); setSelectedShow(null); } }}>
        <DrawerContent aria-describedby={undefined}>
          {detailLoading && <div className='flex min-h-64 flex-1 items-center justify-center'><Spinner /></div>}
          {active && (
            <>
              <DrawerHeader>
                <div className='flex items-start gap-4 pr-8'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
                  <div className='flex min-w-0 flex-col gap-2'>
                    <DrawerTitle className='text-base font-semibold leading-tight'>{active.title}{active.year ? ' (' + active.year + ')' : ''}</DrawerTitle>
                    {active.source_title && active.source_title !== active.title && (
                      <p className='font-mono text-[0.7rem] leading-tight text-muted-foreground' title='The name Erai-raws publishes this show under'>
                        {active.source_title}
                      </p>
                    )}
                    <p className='text-xs text-muted-foreground'>{active.downloaded_count}/{active.total_count} episodes · {active.season_count} seasons · {formatSize(active.size)}</p>
                    <div className='flex flex-wrap items-center gap-2'>
                      {active.tvdb_id && <Button asChild variant='secondary' size='sm'><a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> TVDB</a></Button>}
                      {Boolean(active.avc_count) && (
                        <Button
                          size='sm'
                          disabled={!active.tvdb_id || upgrading === active.key}
                          onClick={() => void upgradeToHevc(active)}
                          title='Download HEVC versions of this show&apos;s AVC episodes and replace them'
                        >
                          <FileVideo data-icon='inline-start' />
                          {upgrading === active.key ? 'Queueing…' : `Upgrade ${active.avc_count} to HEVC`}
                        </Button>
                      )}
                      <Button size='sm' variant='destructive' onClick={() => setRemoveTarget(active)} title='Delete this show from disk and never download it again'>
                        <Trash2 data-icon='inline-start' /> Remove and blacklist
                      </Button>
                    </div>
                  </div>
                </div>
              </DrawerHeader>
              <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col'>
                <TabsList className='flex h-auto flex-wrap justify-start px-5 pt-3'>
                  {seasons.map((season) => <TabsTrigger key={String(season)} value={String(season)}>{season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}</TabsTrigger>)}
                </TabsList>
                {seasons.map((season) => (
                  <TabsContent key={String(season)} value={String(season)} className='min-h-0 flex-1 overflow-y-auto px-5 pb-4'>
                    <div className='overflow-x-auto'>
                      <table className='w-full border-collapse text-sm'>
                        <thead><tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'><th className='py-2.5 pr-3 font-medium'>Ep</th><th className='px-3 py-2.5 font-medium'>Episode / File</th><th className='px-3 py-2.5 font-medium'>Encode</th><th className='px-3 py-2.5 text-right font-medium'>Size</th><th className='py-2.5 pl-3 text-right font-medium'>State</th></tr></thead>
                        <tbody>{active.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                          <tr key={entry.path || String(entry.season_number) + ':' + entry.episode} className='border-b border-border/70 last:border-0'>
                            <td className='py-2.5 pr-3 font-mono text-xs tabular-nums'>{entry.episode ?? '—'}</td>
                            <td className='px-3 py-2.5 text-xs text-muted-foreground'>{entry.episode_title && <p className='text-sm text-foreground'>{entry.episode_title}</p>}{!entry.missing && <p className='font-mono text-[0.68rem]'>{entry.name}</p>}{entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}</td>
                            <td className='px-3 py-2.5'>
                              <div className='flex flex-wrap items-center gap-1.5'>
                                {entry.codec ? <Badge variant={entry.codec === 'hevc' ? 'success' : 'warning'}>{entry.codec.toUpperCase()}</Badge> : <span className='text-xs text-muted-foreground'>—</span>}
                                {entry.german_dub && <Badge variant='transfer' title='Carries a German dub — never replaced by an HEVC upgrade'>GER dub</Badge>}
                              </div>
                            </td>
                            <td className='px-3 py-2.5 text-right font-mono text-xs tabular-nums'>{entry.missing ? '—' : formatSize(entry.size)}</td>
                            <td className='py-2.5 pl-3 text-right'>{entry.missing ? <Button size='icon' variant='secondary' disabled={!active.tvdb_id} aria-label='Search for a release' title='Search for a release' onClick={() => void searchMissing(active, entry)}><Search /></Button> : entry.staged ? (
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
        </DrawerContent>
      </Drawer>
      {/* Deleting a whole show is not undoable, so it is never one click. */}
      <Dialog open={Boolean(removeTarget)} onOpenChange={(open) => { if (!open && !removing) setRemoveTarget(null); }}>
        <DialogContent className='max-w-md'>
          <DialogHeader>
            <DialogTitle>Remove {removeTarget?.title}?</DialogTitle>
            <DialogDescription>
              This deletes {removeTarget?.downloaded_count ?? 0} episode{removeTarget?.downloaded_count === 1 ? '' : 's'}
              {removeTarget ? ' (' + formatSize(removeTarget.size) + ')' : ''} from disk, removes its torrents,
              and blacklists the show so no season of it is downloaded again. It moves to the Blacklist tab,
              where you can restore it. The deleted files cannot be recovered.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setRemoveTarget(null)} disabled={removing}>Cancel</Button>
            <Button variant='destructive' onClick={() => void removeShow()} disabled={removing}>
              <Trash2 data-icon='inline-start' /> {removing ? 'Removing…' : 'Delete and blacklist'}
            </Button>
          </DialogFooter>
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
            {searching ? <div className='flex h-40 items-center justify-center'><Spinner /></div> : results.length ? <div className='flex flex-col gap-2'>
              {results.map((entry) => <Card key={entry.info_hash}><CardContent className='flex flex-wrap items-center justify-between gap-3 p-3'>
                <div className='flex min-w-0 flex-1 flex-col gap-1'><p className='break-words font-mono text-xs'>{entry.title}</p><p className='text-xs text-muted-foreground'>{entry.quality} · {entry.size} · {entry.seeders} seeds · {entry.publisher}</p></div>
                <Button asChild size='sm' variant='secondary'><a href={entry.detail_url} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> Description</a></Button>
                <Button size='sm' disabled={Boolean(downloading)} onClick={() => void downloadMissing(entry)}><Download data-icon='inline-start' /> {downloading === entry.info_hash ? 'Queuing…' : 'Download'}</Button>
              </CardContent></Card>)}
            </div> : <EmptyState icon={Search} title='No matching releases found' description='Try an alternate Erai title above. If numbering is unresolved, select the show mapping in Anime Settings.' />}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
