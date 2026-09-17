import { useEffect, useMemo, useState } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, Check, FileVideo, LayoutGrid, Rows3 } from 'lucide-react';
import { toast } from 'sonner';
import { api, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry, type AnimeTVDBMatch } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@/lib/utils';

function formatSize(bytes: number) {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

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
    <div className='flex items-center gap-2' title={title}>
      <div className='flex h-1.5 w-16 overflow-hidden rounded-full bg-secondary'>
        {hevc > 0 && <span className='bg-success' style={{ flexGrow: hevc }} />}
        {avc > 0 && <span className='bg-warning' style={{ flexGrow: avc }} />}
        {dubbed > 0 && <span className='bg-transfer' style={{ flexGrow: dubbed }} />}
        {unknown > 0 && <span className='bg-input' style={{ flexGrow: unknown }} />}
      </div>
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{hevc}/{total}</span>
    </div>
  );
}

function Progress({ show }: { show: AnimeLibraryShow }) {
  const total = show.total_count || show.downloaded_count || 1;
  const percent = Math.max(0, Math.min(100, (show.downloaded_count / total) * 100));
  const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
  return (
    <div className='flex items-center gap-2'>
      <div className='h-1.5 w-20 overflow-hidden rounded-full bg-secondary'>
        <div className={cn('h-full rounded-full', tone.className)} style={{ width: `${percent}%` }} />
      </div>
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

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* remembering the choice is a convenience, not a requirement */
    }
  }, [view]);

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
  const visible = useMemo(() => shows.filter((show) => show.title.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [shows, query]);
  const totals = useMemo(() => visible.reduce((acc, show) => ({
    size: acc.size + show.size,
    episodes: acc.episodes + show.episode_count,
    hevc: acc.hevc + (show.hevc_count ?? 0),
    avc: acc.avc + (show.avc_count ?? 0),
    dubbed: acc.dubbed + (show.german_dub_count ?? 0),
  }), { size: 0, episodes: 0, hevc: 0, avc: 0, dubbed: 0 }), [visible]);
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
          <div className='flex items-center gap-0.5 rounded-lg border border-border bg-secondary/40 p-0.5' role='group' aria-label='Library view'>
            {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
              <button
                key={value}
                type='button'
                aria-pressed={view === value}
                aria-label={label + ' view'}
                onClick={() => setView(value)}
                className={cn(
                  'segment inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
                  view === value ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <Icon className='size-3.5' />
                {label}
              </button>
            ))}
          </div>
          <Button variant='secondary' onClick={() => void load()} disabled={loading}><RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} /> Rescan</Button>
        </div>
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : visible.length === 0 ? (
        <EmptyState icon={HardDrive} title={shows.length ? 'No matching Anime' : 'No Anime in your library yet'} description='Completed and staged episodes are grouped into their TVDB shows.' />
      ) : view === 'grid' ? (
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {visible.map((show) => {
            const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
            return (
              <button key={show.key} type='button' onClick={() => void openShow(show.key)} aria-label={'View ' + show.title} className='block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background'>
                <Card className='poster-card h-full overflow-hidden border-border'>
                  <div className='relative block w-full'>
                    <AnimePoster url={show.poster_url} title={show.title} />
                    <span className='absolute inset-x-0 bottom-0 bg-linear-to-t from-black via-black/75 to-transparent px-3 pb-2.5 pt-10 text-left text-sm font-medium leading-tight text-white'>{show.title}</span>
                    {show.finished && <span className='absolute right-2 top-2 rounded-full bg-background/90 p-1 text-success' aria-label='Finished show, all episodes downloaded'><Check className='size-4' /></span>}
                    <span className={cn('absolute inset-x-0 top-0 h-0.5', tone.className)} />
                  </div>
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
      ) : (
        <div className='overflow-x-auto rounded-lg border border-border'>
          <table className='w-full min-w-[820px] border-collapse text-sm'>
            <thead>
              <tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'>
                <th className='px-3 py-2.5 font-medium'>Series</th>
                <th className='px-3 py-2.5 font-medium'>Seasons</th>
                <th className='px-3 py-2.5 font-medium'>Episodes</th>
                <th className='px-3 py-2.5 font-medium'>Encode</th>
                <th className='px-3 py-2.5 text-right font-medium'>Size</th>
                <th className='px-3 py-2.5 font-medium'>State</th>
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
        <div className='sticky bottom-0 -mx-1 flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border bg-background/95 px-1 py-2.5 text-xs text-muted-foreground backdrop-blur'>
          <span><span className='font-mono tabular-nums text-foreground'>{visible.length}</span> series{visible.length !== shows.length ? ` of ${shows.length}` : ''}</span>
          <span><span className='font-mono tabular-nums text-foreground'>{totals.episodes.toLocaleString()}</span> episodes</span>
          <span><span className='font-mono tabular-nums text-foreground'>{formatSize(totals.size)}</span> stored</span>
          <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-success' /><span className='font-mono tabular-nums text-foreground'>{totals.hevc}</span> HEVC</span>
          <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-warning' /><span className='font-mono tabular-nums text-foreground'>{totals.avc}</span> AVC</span>
          {totals.dubbed > 0 && <span className='flex items-center gap-1.5'><span className='size-1.5 rounded-full bg-transfer' /><span className='font-mono tabular-nums text-foreground'>{totals.dubbed}</span> German dub</span>}
        </div>
      )}

      <Dialog open={Boolean(selected)} onOpenChange={(open) => { if (!open) { setSelected(null); setSelectedShow(null); } }}>
        <DialogContent className='flex h-[94dvh] w-[96vw] max-w-none flex-col overflow-hidden'>
          {detailLoading && <div className='flex min-h-64 flex-1 items-center justify-center'><Spinner /></div>}
          {active && (
            <>
              <DialogHeader className='shrink-0'>
                <div className='flex items-start gap-5'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-24 shrink-0' />
                  <div className='flex flex-col gap-3'>
                    <DialogTitle>{active.title}{active.year ? ' (' + active.year + ')' : ''}</DialogTitle>
                    <DialogDescription>{active.downloaded_count}/{active.total_count} episodes · {active.season_count} seasons · {formatSize(active.size)} · TVDB ordering</DialogDescription>
                    <div className='flex flex-wrap items-center gap-2'>
                      {active.tvdb_id && <Button asChild variant='outline' size='sm'><a href={'https://thetvdb.com/dereferrer/series/' + active.tvdb_id} target='_blank' rel='noreferrer'><ExternalLink data-icon='inline-start' /> TVDB</a></Button>}
                      {Boolean(active.avc_count) && (
                        <Button
                          size='sm'
                          variant='secondary'
                          disabled={!active.tvdb_id || upgrading === active.key}
                          onClick={() => void upgradeToHevc(active)}
                          title='Download HEVC versions of this show&apos;s AVC episodes and replace them'
                        >
                          <FileVideo data-icon='inline-start' />
                          {upgrading === active.key ? 'Queueing…' : `Upgrade ${active.avc_count} to HEVC`}
                        </Button>
                      )}
                    </div>
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
                            <td className='py-2.5 pl-3 text-right'>{entry.missing ? <Button size='sm' variant='secondary' disabled={!active.tvdb_id} onClick={() => void searchMissing(active, entry)}><Search data-icon='inline-start' /> Search</Button> : entry.staged ? (
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
            {searching ? <div className='flex h-40 items-center justify-center'><Spinner /></div> : results.length ? <div className='flex flex-col gap-2'>
              {results.map((entry) => <Card key={entry.info_hash}><CardContent className='flex flex-wrap items-center justify-between gap-3 p-3'>
                <div className='flex min-w-0 flex-1 flex-col gap-1'><p className='break-words font-mono text-xs'>{entry.title}</p><p className='text-xs text-muted-foreground'>{entry.quality} · {entry.size} · {entry.seeders} seeds · {entry.publisher}</p></div>
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
