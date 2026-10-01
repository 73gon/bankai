import { useEffect, useMemo, useState, useRef } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, FileVideo, LayoutGrid, Rows3, Trash2, Ban, Link2, MoreHorizontal } from 'lucide-react';
import { toast } from 'sonner';
import { api, pagePaths, recall, type AniDBAnime, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry, type AnimeTVDBMatch, type EpisodeNumbering } from '@/lib/api';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { AnimePoster } from '@/components/AnimePoster';
import { AniDBLinkDialog } from '@/components/AniDBLinkDialog';
import { Meter, rampParts } from '@/components/ui/meter';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
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
  // By how much of the show is at the better tiers, not by a raw count, so a
  // long series part-converted does not outrank a short one fully converted.
  // A German dub is the top tier: it weighs more than HEVC.
  encode: (show) => {
    const hevc = show.hevc_count ?? 0;
    const dubbed = show.german_dub_count ?? 0;
    const known = hevc + dubbed + (show.avc_count ?? 0);
    return known ? (hevc + 2 * dubbed) / (2 * known) : -1;
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
 *  rather than two clicks away. Each episode is in exactly one tier: German
 *  dub (the top one, whatever its codec), HEVC, AVC. The number is the
 *  episodes that need nothing more: dubs and HEVC.
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
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{hevc + dubbed}/{total}</span>
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
  // Started from the last answer this browser saw, refreshed straight after.
  const [shows, setShows] = useState<AnimeLibraryShow[]>(() => recall<{ shows: AnimeLibraryShow[] }>(pagePaths.animeLibrary)?.shows ?? []);
  const [root, setRoot] = useState(() => recall<{ root: string }>(pagePaths.animeLibrary)?.root ?? '');
  const [query, setQuery] = useState('');
  // Blacklisted shows still on disk are hidden unless asked for.
  const [showBlacklisted, setShowBlacklisted] = useState(false);
  // Only the shows not yet linked to an AniDB entry.
  const [onlyUnlinked, setOnlyUnlinked] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [view, setView] = useState<View>(storedView);
  const [sort, setSort] = useState<SortState<LibrarySortKey> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedShow, setSelectedShow] = useState<AnimeLibraryShow | null>(null);
  // The show whose folders are being linked to an AniDB entry.
  const [linkFor, setLinkFor] = useState<AnimeLibraryShow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // Only a spinner with nothing to show: otherwise the held answer stays up while it refreshes.
  const [loading, setLoading] = useState(() => recall(pagePaths.animeLibrary) === undefined);
  const [transferring, setTransferring] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [results, setResults] = useState<AnimeEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchMatch, setSearchMatch] = useState<AnimeTVDBMatch | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<AnimeLibraryShow | null>(null);
  // Shows being removed in the background: kept off the page until done.
  const removing = useRef<Set<string>>(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* remembering the choice is a convenience, not a requirement */
    }
  }, [view]);

  async function load(rescan = false) {
    if (rescan) setLoading(true);
    try {
      const result = await api.animeLibrary(rescan);
      setShows(result.shows.filter((show) => !removing.current.has(show.key)));
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

  async function linkShow(anime: AniDBAnime) {
    if (!linkFor) return;
    const show = linkFor;
    try {
      await api.linkLibraryShow(show.folders?.length ? show.folders : [show.key], anime.anidb_id);
      toast.success(show.title + ' is linked to ' + anime.title);
      setLinkFor(null);
      void load();
      if (selected === show.key) void openShow(show.key);
    } catch (error: any) {
      toast.error(error.message);
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

  async function setNumbering(show: AnimeLibraryShow, mode: EpisodeNumbering) {
    try {
      await api.setAnimeNumbering(show.key, show.tvdb_id ?? null, mode);
      await openShow(show.key);
      await load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function removeShow() {
    const target = removeTarget;
    if (!target) return;
    // Off the page at once; deleting runs in the background, where the
    // Dashboard shows it under "Your actions".
    removing.current.add(target.key);
    setShows((current) => current.filter((show) => show.key !== target.key));
    setRemoveTarget(null);
    setSelected(null);
    setSelectedShow(null);
    toast.info('Removing ' + target.title + ' in the background…');
    try {
      const result = await api.removeAnimeLibraryShow(target.key, target.title);
      toast.success(
        target.title + ' removed and blacklisted — deleted ' + result.deleted_files + ' file'
        + (result.deleted_files === 1 ? '' : 's')
        + (result.freed_bytes ? ' (' + formatSize(result.freed_bytes) + ')' : '')
        + (result.removed_torrents ? ', ' + result.removed_torrents + ' torrent' + (result.removed_torrents === 1 ? '' : 's') : ''),
      );
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      removing.current.delete(target.key);
      void load();
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
    const matched = shows.filter((show) =>
      (showBlacklisted || !show.blacklisted)
      && (!onlyUnlinked || !show.anidb_id)
      && show.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
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
  }, [shows, query, sort, showBlacklisted, onlyUnlinked]);
  const blacklistedCount = useMemo(() => shows.filter((show) => show.blacklisted).length, [shows]);
  const unlinkedCount = useMemo(() => shows.filter((show) => !show.anidb_id && (showBlacklisted || !show.blacklisted)).length, [shows, showBlacklisted]);
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
  // Read absolutely, a season tab is one of the user's own arc folders: named after it.
  const seasonLabel = (season: number | null) => {
    if (season === null) return 'Other files';
    if (active?.numbering === 'absolute_flat') return 'All episodes';
    // An AniDB card's tabs are its entries or its season folders, each named.
    if (active?.numbering === 'anidb') {
      const label = active.episodes.find((episode) => episode.season_number === season)?.season;
      if (label) return label;
    }
    if (active?.numbering === 'absolute') {
      const folder = active.episodes.find((episode) => episode.season_number === season)?.season;
      if (folder) return folder;
    }
    return season === 0 ? 'Specials' : 'Season ' + season;
  };

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
          {(unlinkedCount > 0 || onlyUnlinked) && (
            <Button
              variant={onlyUnlinked ? 'default' : 'secondary'}
              aria-pressed={onlyUnlinked}
              onClick={() => setOnlyUnlinked((current) => !current)}
              title='Shows not linked to an AniDB entry yet; link them from their details'
            >
              <Link2 data-icon='inline-start' /> Not linked ({unlinkedCount})
            </Button>
          )}
          {blacklistedCount > 0 && (
            <Button
              variant='secondary'
              aria-pressed={showBlacklisted}
              onClick={() => setShowBlacklisted((current) => !current)}
              title='Shows you blacklisted whose files are still in the library. Delete them from the show drawer to remove them.'
            >
              <Ban data-icon='inline-start' /> {showBlacklisted ? 'Hide' : 'Show'} blacklisted ({blacklistedCount})
            </Button>
          )}
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
                    {show.blacklisted && <Badge variant='destructive' className='absolute left-2 top-2 bg-background/85 backdrop-blur-sm'>Blacklisted</Badge>}
                    {!show.anidb_id && <Badge variant='warning' className='absolute right-2 top-2 bg-background/85 backdrop-blur-sm' title='Not linked to an AniDB entry yet'>No AniDB</Badge>}
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
        <div className='table-bleed min-h-0 flex-1 overflow-auto'>
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
                        {show.blacklisted && <Badge variant='destructive'>Blacklisted</Badge>}
                        {!show.anidb_id && <Badge variant='warning' title='Not linked to an AniDB entry yet'>No AniDB</Badge>}
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
        <DrawerContent aria-describedby={undefined} className='md:w-3/4'>
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
                      {!active.anidb_id && (
                        <Button
                          size='sm'
                          onClick={() => setLinkFor(active)}
                          title='Pick the AniDB entry of this show: for its files Shoko has not matched, and those still to come'
                        >
                          <Link2 data-icon='inline-start' /> Link to AniDB
                        </Button>
                      )}
                      {active.anidb_id && !Object.keys(active.season_anidb ?? {}).length && <Button asChild variant='secondary' size='sm'><a href={'https://anidb.net/anime/' + active.anidb_id} target='_blank' rel='noreferrer' title={(active.anidb_ids?.length ?? 0) > 1 ? 'The first of its ' + active.anidb_ids!.length + ' AniDB entries' : 'This show on AniDB'}><ExternalLink data-icon='inline-start' /> AniDB</a></Button>}
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
                      {active.numbering !== 'anidb' && <Select value={active.numbering ?? 'season'} onValueChange={(value) => void setNumbering(active, value as EpisodeNumbering)}>
                        <SelectTrigger data-size='sm' className='w-60' aria-label='How episode numbers are read' title='How this show&apos;s episode numbers are read'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value='season'>Per season (S01E05)</SelectItem>
                            <SelectItem value='absolute'>Absolute, seasons are arcs</SelectItem>
                            <SelectItem value='absolute_flat'>Absolute, one list</SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>}
                      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
                        <PopoverTrigger asChild>
                          <Button size='icon' variant='secondary' className='size-8' aria-label='More actions for this show' title='More actions'>
                            <MoreHorizontal />
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent align='end' className='flex w-60 flex-col gap-0.5 p-1'>
                          {active.anidb_id && (
                            <Button
                              variant='ghost'
                              size='sm'
                              className='justify-start'
                              onClick={() => { setMenuOpen(false); setLinkFor(active); }}
                            >
                              <Search data-icon='inline-start' /> Change AniDB anime
                            </Button>
                          )}
                          <Button
                            variant='ghost'
                            size='sm'
                            className='justify-start text-destructive hover:text-destructive'
                            onClick={() => { setMenuOpen(false); setRemoveTarget(active); }}
                            title='Delete this show from disk and never download it again'
                          >
                            <Trash2 data-icon='inline-start' /> Remove and blacklist
                          </Button>
                        </PopoverContent>
                      </Popover>
                    </div>
                  </div>
                </div>
              </DrawerHeader>
              <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col'>
                <TabsList className='flex h-auto flex-wrap justify-start px-5 pt-3'>
                  {seasons.map((season) => <TabsTrigger key={String(season)} value={String(season)}>{seasonLabel(season)}</TabsTrigger>)}
                </TabsList>
                {seasons.map((season) => (
                  <TabsContent key={String(season)} value={String(season)} className='min-h-0 flex-1 overflow-y-auto px-5 pb-4'>
                    {season !== null && (active.season_anidb?.[String(season)]?.length ?? 0) > 0 && (
                      <p className='flex flex-wrap items-center gap-x-3 gap-y-1 pt-3 text-xs text-muted-foreground'>
                        <span>AniDB:</span>
                        {active.season_anidb![String(season)].map((entry) => (
                          <a
                            key={entry.anidb_id}
                            href={'https://anidb.net/anime/' + entry.anidb_id}
                            target='_blank'
                            rel='noreferrer'
                            className='inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline'
                          >
                            {entry.title} <ExternalLink className='size-3' />
                          </a>
                        ))}
                      </p>
                    )}
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
                            <td className='py-2.5 pl-3 text-right'>{entry.missing ? <Button size='icon' variant='secondary' disabled={!active.tvdb_id || active.numbering === 'anidb'} aria-label='Search for a release' title={active.numbering === 'anidb' ? 'Searching by AniDB episode is not available yet' : 'Search for a release'} onClick={() => void searchMissing(active, entry)}><Search /></Button> : entry.staged ? (
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
      <Dialog open={Boolean(removeTarget)} onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}>
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
            <Button variant='secondary' onClick={() => setRemoveTarget(null)}>Cancel</Button>
            <Button variant='destructive' onClick={() => void removeShow()}>
              <Trash2 data-icon='inline-start' /> Delete and blacklist
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
      <AniDBLinkDialog name={linkFor ? linkFor.source_title || linkFor.title : null} onClose={() => setLinkFor(null)} onPick={linkShow} />
    </div>
  );
}
