import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Film, Tv, Search, ExternalLink, HardDrive, Magnet, Link2, Undo2, Image, X } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import { api, pagePaths, recall, type AnimeLibraryEpisode, type DiscoverItem, type FollowOverview, type MasMovie, type MasShow } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@zonsh/ui';
import { cn } from '@zonsh/ui';
import { SortHeader, nextSort, type SortState } from '@/components/sort-header';
import { SegmentMeter, rampParts } from '@/components/segment-meter';
import { EpisodeBar, airedCount, airedStrip, describeStrip, type StripTier } from '@/components/episode-bar';
import { CORNER_TAG, FooterStat, LibraryBody, LibraryFooter, LibraryHeader, PosterCard, TitlePage, formatSize, useLibraryAddress, useLibraryView } from '@/components/library/library-page';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@zonsh/ui';
import { AnimePoster } from '@/components/AnimePoster';
import { FollowPanel, FollowedShowView } from '@/components/EnglishTorrent';
import { ArtworkDialog, type MasArtworkTarget } from '@/components/ArtworkDialog';
import { MagnetReplaceDialog } from '@/components/MagnetReplaceDialog';
import { Checkbox } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from '@zonsh/ui';


type Row = MasMovie | MasShow;

function isShow(row: Row): row is MasShow {
  return 'downloaded_count' in row;
}

const COMPLETION: Record<string, { label: string; className: string }> = {
  complete: { label: 'Complete', className: 'bg-success' },
  upcoming: { label: 'Airing', className: 'bg-transfer' },
  partial: { label: 'Partial', className: 'bg-warning' },
  empty: { label: 'Empty', className: 'bg-destructive' },
  unknown: { label: 'Unknown', className: 'bg-muted-foreground' },
};

/** A followed show with nothing on disk yet: a card of its own, filled from the follow list. */
function followCard(row: FollowOverview): MasShow {
  const held = row.counts.german + row.counts.english;
  return {
    key: 'follow:' + row.tmdb_id,
    folders: [],
    title: row.title,
    tmdb_id: row.tmdb_id,
    year: row.year,
    poster_url: row.poster_url,
    episode_count: held,
    season_count: 0,
    size: 0,
    roots: [],
    downloaded_count: held,
    total_count: row.episodes - row.counts.upcoming,
    completion_state: held ? 'partial' : 'empty',
    finished: false,
    metadata_available: true,
    episodes: [],
    german_count: row.counts.german,
    english_count: row.counts.english,
  };
}

// What each letter of a show's strip is on the bar: the language of its episodes.
const LANGUAGE_TIERS: Record<string, StripTier> = {
  g: { label: 'German dub', className: 'bg-transfer' },
  e: { label: 'English', className: 'bg-info' },
  n: { label: 'English without English subtitles', className: 'bg-warning' },
  u: { label: 'tracks not read yet', className: 'bg-muted-foreground/50' },
};

/** Episodes and their languages as one bar, as the anime library has it: where in
 *  the show the library has episodes, each stretch coloured by what can be heard and
 *  read (purple German dub, blue English, yellow English without English subtitles).
 *  The number is the episodes on disk. */
function ShowEpisodes({ show, className }: { show: MasShow; className?: string }) {
  const count = (
    <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.downloaded_count}/{show.total_count}</span>
  );
  if (!show.strip?.length) {
    // A followed show with nothing on disk yet, or an answer from before the strip: the plain ratio.
    const total = show.total_count || show.downloaded_count || 1;
    return (
      <div className='flex items-center gap-2'>
        <SegmentMeter className={cn('w-[110px]', className)} total={total} title={`${show.downloaded_count} of ${show.total_count} episodes`} parts={rampParts(show.downloaded_count, total)} />
        {count}
      </div>
    );
  }
  // What has not aired yet (TBA) is not on the bar, nor in its count.
  const aired = airedStrip(show.strip);
  const total = airedCount(show.strip);
  const label = [
    `${show.downloaded_count} of ${total} aired episodes`,
    ...aired.map((season) => `${season.label}: ${describeStrip(season.states, LANGUAGE_TIERS)}`),
  ].join('\n');
  return (
    <div className='flex items-center gap-2'>
      <EpisodeBar strip={show.strip} tiers={LANGUAGE_TIERS} className={className} label={label} />
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.downloaded_count}/{total}</span>
    </div>
  );
}

/** A film's (or an episode's) audio: German dub, English only, or not read yet. */
/** German or English, as the followed shows' episodes say it: the same badge everywhere. */
function Dub({ german }: { german: boolean | null | undefined }) {
  if (german == null) return <span className='text-xs text-muted-foreground' title='Its tracks are not read yet'>—</span>;
  return (
    <StatusTag variant={german ? 'success' : 'info'} title={german ? 'Carries a German dub' : 'No German audio'}>
      {german ? 'German' : 'English'}
    </StatusTag>
  );
}

function Quality({ value }: { value: string | null | undefined }) {
  return <span className='font-mono text-xs tabular-nums text-muted-foreground'>{value ?? '—'}</span>;
}

/**
 * Pick the TMDB title a folder is. Needed where the folder name alone is not enough:
 * TMDB has two "Avatar: The Last Airbender" (2005, 2024) and the folder gives no year.
 */
function MatchDialog({ row, onClose, onMatched }: { row: Row | null; onClose: () => void; onMatched: () => void }) {
  const kind = row && isShow(row) ? 'show' : 'movie';
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DiscoverItem[] | null>(null);

  useEffect(() => {
    if (!row) return;
    // The folder's name without a trailing year: the year narrows nothing TMDB's search would not.
    const start = row.folders[0]?.replace(/\s*\(\d{4}\)\s*$/, '') ?? row.title;
    setQuery(start);
    void search(start);
  }, [row?.key]);

  async function search(text: string) {
    if (!text.trim()) return;
    setResults(null);
    try {
      setResults((await api.discoverSearch(text.trim(), kind, 'title', 0, 20)).items.filter((item) => item.tmdb_id));
    } catch (error: any) {
      toast.error(error.message);
      setResults([]);
    }
  }

  async function choose(item: DiscoverItem | null) {
    if (!row) return;
    // The dialog closes at once; a toast follows the match.
    onClose();
    void inBackground(api.masLibraryMatch({
      kind, key: row.key, tmdb_id: item?.tmdb_id ?? null,
      name: item?.name, year: item?.year ?? null, poster_url: item?.poster_url ?? null,
    }), {
      loading: 'Matching ' + (row.folders[0] ?? row.title) + '…',
      success: item ? `${row.folders[0] ?? row.title} is ${item.name}${item.year ? ' (' + item.year + ')' : ''}` : 'Back to the automatic match',
    }).then(onMatched, () => undefined);
  }

  return (
    <Dialog open={row !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent size='lg' className='max-h-[90vh] max-w-3xl overflow-y-auto'>
        <DialogHeader><DialogTitle>Match {row?.folders[0] ?? row?.title}</DialogTitle></DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          <p className='text-xs text-muted-foreground'>
            Pick the TMDB {kind === 'show' ? 'series' : 'film'} this folder is. It gets that cover and,
            for a series, that episode list. The files stay where they are.
          </p>
          <form className='flex gap-2' onSubmit={(event) => { event.preventDefault(); void search(query); }}>
            <InputGroup className='flex-1'>
              <InputGroupAddon><Search /></InputGroupAddon>
              <InputGroupInput value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Title' />
            </InputGroup>
            <Button type='submit' variant='secondary'>Search</Button>
          </form>
          {results === null ? (
            <div className='flex min-h-32 items-center justify-center'><Spinner /></div>
          ) : results.length === 0 ? (
            <p className='text-sm text-muted-foreground'>TMDB found nothing by that name.</p>
          ) : (
            <ul className='grid grid-cols-2 gap-3 sm:grid-cols-4'>
              {results.map((item) => {
                const current = row?.tmdb_id === item.tmdb_id;
                return (
                  <li key={item.tmdb_id}>
                    <button
                      type='button'
                      onClick={() => void choose(item)}
                      className={cn(
                        'flex w-full flex-col gap-1.5 rounded-item p-1.5 text-left transition-colors hover:bg-muted disabled:opacity-60',
                        current && 'ring-2 ring-primary',
                      )}
                    >
                      <AnimePoster url={item.poster_url} title={item.name} className='w-full' />
                      <span className='text-sm leading-tight'>{item.name}</span>
                      <span className='font-mono text-xs tabular-nums text-muted-foreground'>{item.year ?? '—'}{current ? ' · current' : ''}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {row?.matched_by_hand && (
            <div>
              <Button variant='ghost' size='sm' onClick={() => void choose(null)}>
                <Undo2 data-icon='inline-start' /> Back to the automatic match
              </Button>
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Whether following a show would fetch anything, and what to call it. Following downloads
 * every aired episode missing and each new one as it airs; a finished show the library
 * holds whole has nothing left to get, so it gets no button.
 */
function followHint(show: MasShow): { label: string; title: string } | null {
  const missing = show.total_count - show.downloaded_count;
  if (missing > 0) {
    return { label: `Follow · get ${missing} missing`, title: 'Download the missing episodes from torrents, and each new one as it airs' };
  }
  if (!show.finished) {
    return { label: 'Follow new episodes', title: 'The library has every episode so far; new ones are downloaded from torrents as they air' };
  }
  return null;
}

function TitleCard({ row, onOpen, following }: { row: Row; onOpen: () => void; following?: boolean }) {
  const show = isShow(row) ? row : null;
  const tone = show ? COMPLETION[show.completion_state] ?? COMPLETION.unknown : null;
  return (
    <PosterCard
      cover={row.poster_url}
      title={row.title}
      year={row.year}
      onOpen={onOpen}
      rule={tone}
      topLeft={following
        ? <StatusTag variant='torrent' className={CORNER_TAG}>Following</StatusTag>
        : !row.tmdb_id ? <StatusTag variant='muted' className={CORNER_TAG}>Not matched</StatusTag> : null}
      // Two roots means the same title is stored twice over (a followed show keeps German and English apart).
      topRight={row.roots.length > 1 && !following ? <StatusTag variant='warning' className={CORNER_TAG}>{row.roots.length} copies</StatusTag> : null}
    >
      <div className='flex items-center justify-between gap-2'>
        {show ? (
          <ShowEpisodes show={show} className='w-[96px]' />
        ) : (
          <div className='flex items-center gap-3'>
            <Quality value={(row as MasMovie).quality} />
            <Dub german={(row as MasMovie).german} />
          </div>
        )}
        <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(row.size)}</span>
      </div>
    </PosterCard>
  );
}

type MovieSort = 'title' | 'year' | 'quality' | 'german' | 'size';
type ShowSort = 'title' | 'seasons' | 'episodes' | 'size' | 'state';
const MOVIE_FIRST: Record<MovieSort, 'asc' | 'desc'> = { title: 'asc', year: 'desc', quality: 'desc', german: 'desc', size: 'desc' };
const SHOW_FIRST: Record<ShowSort, 'asc' | 'desc'> = { title: 'asc', seasons: 'desc', episodes: 'desc', size: 'desc', state: 'asc' };
const QUALITY_ORDER: Record<string, number> = { SD: 1, '720p': 2, '1080p': 3, '2160p': 4 };
const STATE_ORDER: Record<string, number> = { complete: 0, upcoming: 1, partial: 2, empty: 3, unknown: 4 };

function sorted<T extends Row>(rows: T[], sort: SortState<string> | null, value: (row: T, key: string) => number | string): T[] {
  if (!sort) return rows;
  const sign = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = value(a, sort.key);
    const y = value(b, sort.key);
    const order = typeof x === 'string' && typeof y === 'string' ? x.localeCompare(y) : Number(x) - Number(y);
    return order * sign || a.title.localeCompare(b.title);
  });
}

function movieValue(row: MasMovie, key: string): number | string {
  if (key === 'year') return row.year ?? 0;
  if (key === 'quality') return QUALITY_ORDER[row.quality ?? ''] ?? 0;
  if (key === 'german') return row.german == null ? -1 : Number(row.german);
  if (key === 'size') return row.size;
  return row.title;
}

function showValue(row: MasShow, key: string): number | string {
  if (key === 'seasons') return row.season_count;
  if (key === 'episodes') return row.downloaded_count;
  if (key === 'size') return row.size;
  if (key === 'state') return STATE_ORDER[row.completion_state] ?? 9;
  return row.title;
}

// The title cell of a fitting table: the columns that step out flow in after it, so the
// poster and the name sit together as one inline piece that wraps only at the last width.
// At module scope: a component defined in the page is a new type on every render, and the
// fitting table measures every row again whenever its cells are remounted. The cells that move
// in sit on its middle (align-middle), beside the name rather than level with the poster's top.
function TitleCell({ row }: { row: Row }) {
  return (
    <TableCell>
      <span className='inline-flex max-w-full items-center gap-2.5 align-middle'>
        <AnimePoster url={row.poster_url} title={row.title} className='w-8 shrink-0 rounded' />
        <span className='flex min-w-0 flex-col'>
          <span className='font-medium text-foreground'>{row.title}</span>
          {row.year ? <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{row.year}</span> : null}
        </span>
      </span>
    </TableCell>
  );
}

export default function MasLibrary() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [movies, setMovies] = useState<MasMovie[]>(() => recall<{ movies: MasMovie[] }>(pagePaths.masLibrary)?.movies ?? []);
  const [shows, setShows] = useState<MasShow[]>(() => recall<{ shows: MasShow[] }>(pagePaths.masLibrary)?.shows ?? []);
  // Only a spinner with nothing to show: otherwise the held answer stays up while it refreshes.
  const [loading, setLoading] = useState(() => recall(pagePaths.masLibrary) === undefined);
  // This visit's own answer is in (the held one may be older than a title opened from it).
  const [fresh, setFresh] = useState(false);
  const [query, setQuery] = useState('');
  const { view, setView, wide, split } = useLibraryView('mas');
  const [follows, setFollows] = useState<FollowOverview[]>([]);
  // Until the follow list is in, a followed show would flash Match and Follow buttons it does not have.
  const [followsLoaded, setFollowsLoaded] = useState(false);
  const [following, setFollowing] = useState<number | null>(null);
  const [matching, setMatching] = useState<Row | null>(null);
  const [artwork, setArtwork] = useState<(MasArtworkTarget & { title: string }) | null>(null);
  // The title's button row: a followed show's own buttons are drawn into it.
  const [headerActions, setHeaderActions] = useState<HTMLDivElement | null>(null);
  // Files ticked in the open title (Replace with magnet link), for the card they belong to.
  const [picked, setPicked] = useState<{ key: string; paths: string[] }>({ key: '', paths: [] });
  const [magnetOpen, setMagnetOpen] = useState(false);
  // Only the titles to sort out: not matched to TMDB, or stored twice.
  const [only, setOnly] = useState<'unmatched' | 'duplicates' | null>(null);
  const [tab, setTab] = useState<'movies' | 'shows'>(() => (Number(new URLSearchParams(window.location.search).get('follow')) ? 'shows' : 'movies'));
  const [movieSort, setMovieSort] = useState<SortState<MovieSort> | null>(null);
  const [showSort, setShowSort] = useState<SortState<ShowSort> | null>(null);
  // The open title lives in the address (?movie=<key> or ?show=<key>): see useLibraryAddress.
  // ?follow=<tmdb id>: the Torrent tab's "Open in library" lands on that show.
  const [followParams] = useSearchParams();
  const opening = Number(followParams.get('follow')) || null;
  const address = useLibraryAddress('mas', ['movie', 'show'], { split, ready: !opening });
  const { search: params, setSearch: setParams, location } = address;
  const movieKey = address.current?.param === 'movie' ? address.current.key : null;
  const showKey = address.current?.param === 'show' ? address.current.key : null;
  const openKey = movieKey ?? showKey;
  const goRow = (row: Row) => address.open(isShow(row) ? 'show' : 'movie', row.key);
  const closeRow = address.close;

  async function loadFollows() {
    try {
      setFollows((await api.followList()).shows);
      setFollowsLoaded(true);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function load(rescan = false) {
    void loadFollows();
    if (rescan) setLoading(true);
    try {
      // Episodes come with the page: a title opens on a row already held,
      // and a second round trip per click is slower than one larger answer.
      const result = await api.masLibrary(true, rescan);
      setMovies(result.movies);
      setShows(result.shows);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
      setFresh(true);
    }
  }

  useEffect(() => { void load(); }, []);

  // The list follows the typing a moment behind, so the search field never waits for it.
  const term = useDeferredValue(query.trim().toLowerCase());
  const visibleMovies = useMemo(
    () => movies.filter((row) => !term || row.title.toLowerCase().includes(term)),
    [movies, term],
  );
  const followed = useMemo(() => new Set(follows.map((row) => row.tmdb_id)), [follows]);
  const allShows = useMemo(() => {
    // A followed show with no file yet still gets its card.
    const onDisk = new Set(shows.map((row) => row.tmdb_id).filter(Boolean));
    return [...shows, ...follows.filter((row) => !onDisk.has(row.tmdb_id)).map(followCard)]
      .sort((a, b) => a.title.localeCompare(b.title));
  }, [shows, follows]);
  const visibleShows = useMemo(
    () => allShows.filter((row) => !term || row.title.toLowerCase().includes(term)),
    [allShows, term],
  );

  // The open title, from the address: looked up in the whole library, not
  // only what the search leaves, so typing never closes it.
  const active: Row | null = movieKey
    ? movies.find((row) => row.key === movieKey) ?? null
    : showKey ? allShows.find((row) => row.key === showKey) ?? null : null;

  // The list beside an open title is of its kind.
  useEffect(() => {
    if (movieKey) setTab('movies');
    else if (showKey) setTab('shows');
  }, [movieKey, showKey]);

  useEffect(() => {
    if (!opening) return;
    const row = allShows.find((show) => isShow(show) && show.tmdb_id === opening);
    if (!row) return;
    const next = new URLSearchParams(params);
    next.delete('follow');
    next.delete('movie');
    next.set('show', row.key);
    setParams(next, { replace: true });
  }, [opening, allShows]);

  // A title in the split view that is not in the library (removed, matched to another,
  // remembered from before) is closed rather than left open on nothing -- and so forgotten.
  useEffect(() => {
    if (!split || !fresh || !followsLoaded || !openKey || active) return;
    if (showKey?.startsWith('follow:') && allShows.some((row) => isShow(row) && row.tmdb_id === Number(showKey.slice('follow:'.length)))) return;
    const next = new URLSearchParams(params);
    next.delete('movie');
    next.delete('show');
    setParams(next, { replace: true });
  }, [split, fresh, followsLoaded, openKey, active, allShows]);

  // A followed show without files is the card `follow:<tmdb>`; once its first
  // episode lands it is its folder's card, and the address follows it there.
  useEffect(() => {
    if (!showKey?.startsWith('follow:') || allShows.some((row) => row.key === showKey)) return;
    const tmdb = Number(showKey.slice('follow:'.length));
    const row = allShows.find((show) => isShow(show) && show.tmdb_id === tmdb);
    if (!row) return;
    const next = new URLSearchParams(params);
    next.set('show', row.key);
    setParams(next, { replace: true, state: location.state });
  }, [showKey, allShows]);

  // A plain function, not a component: one defined in here is a new type on every render, and
  // React then rebuilt the table -- scrolled back to the top whenever a dialog opened or closed.
  function panel(rows: Row[], empty: string, table: React.ReactNode) {
    return (
      <LibraryBody
        loading={loading}
        count={rows.length}
        empty={{ title: empty, description: 'Add a directory in Settings, then rescan.' }}
        view={view}
        split={split}
        grid={() => rows.map((row) => <TitleCard key={row.key} row={row} following={isShow(row) && !!row.tmdb_id && followed.has(row.tmdb_id)} onOpen={() => goRow(row)} />)}
        table={() => table}
        detail={showDetail}
        splitId='mas'
        open={Boolean(active)}
        onClose={closeRow}
        splitEmpty={{ title: 'Pick a title', description: 'Its files, or its seasons and episodes, open here beside the list.' }}
      />
    );
  }

  const isUnmatched = (row: Row) => !row.tmdb_id && !row.key.startsWith('follow:');
  // A followed show keeps German and English apart on purpose: that is not a duplicate.
  const isDuplicate = (row: Row) => row.roots.length > 1 && !(isShow(row) && row.tmdb_id && followed.has(row.tmdb_id));
  function onlyRows<T extends Row>(rows: T[]): T[] {
    if (only === 'unmatched') return rows.filter(isUnmatched);
    if (only === 'duplicates') return rows.filter(isDuplicate);
    return rows;
  }
  const tabRows: Row[] = tab === 'movies' ? visibleMovies : visibleShows;
  const unmatchedCount = tabRows.filter(isUnmatched).length;
  const duplicateCount = tabRows.filter(isDuplicate).length;
  // A filter with nothing left in it lets go, rather than show an empty list.
  useEffect(() => {
    if ((only === 'unmatched' && unmatchedCount === 0) || (only === 'duplicates' && duplicateCount === 0)) setOnly(null);
  }, [only, unmatchedCount, duplicateCount]);

  function rowProps(row: Row) {
    return {
      tabIndex: 0,
      onClick: () => goRow(row),
      onKeyDown: (event: React.KeyboardEvent) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); goRow(row); } },
      'aria-current': split && row.key === active?.key ? ('true' as const) : undefined,
      className: 'data-row cursor-pointer items-center aria-current:bg-control-surface focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-control-focus',
    };
  }

  // In its own container, as the anime library's: plain, across the page's whole width
  // through its gutter (index.css), the header staying put as the rows scroll. Fitted to its
  // width: as it narrows -- the split view's divider -- the lesser columns move in after the
  // title, one by one, and a column is only left out when nothing else is left to move.
  // The tables' scrolling bodies paint the ground (page-ground), as the app's scrolling area: opaque, they scroll on the compositor (App.tsx).
  const tableContainer = { className: split ? 'h-full' : 'min-h-0 flex-1' };
  const movieRows = sorted(onlyRows(visibleMovies), movieSort, movieValue);
  const showRows = sorted(onlyRows(visibleShows as MasShow[]), showSort, showValue);
  const sortMovies = (key: MovieSort) => setMovieSort(nextSort(movieSort, key, MOVIE_FIRST));
  const sortShows = (key: ShowSort) => setShowSort(nextSort(showSort, key, SHOW_FIRST));

  const moviesTable = (
    <Table fit variant='plain' className='page-ground' containerProps={tableContainer}>
      <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
        <TableRow>
          <SortHeader label='Title' column='title' sort={movieSort} onSort={sortMovies} />
          {/* The title cell names the year under the title already: the first to go, and nothing is lost. */}
          <SortHeader label='Year' column='year' sort={movieSort} onSort={sortMovies} priority={1} />
          <SortHeader label='Quality' column='quality' sort={movieSort} onSort={sortMovies} inline priority={4} />
          <SortHeader label='Audio' column='german' sort={movieSort} onSort={sortMovies} inline priority={3} />
          <SortHeader label='Size' column='size' sort={movieSort} onSort={sortMovies} align='right' inline priority={2} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {movieRows.map((row) => (
          <TableRow key={row.key} {...rowProps(row)}>
            <TitleCell row={row} />
            <TableCell className='font-mono text-xs tabular-nums text-muted-foreground'>{row.year ?? '—'}</TableCell>
            <TableCell className='align-middle'><Quality value={row.quality} /></TableCell>
            <TableCell className='align-middle'>
              <div className='flex items-center gap-1.5 whitespace-nowrap'>
                <Dub german={row.german} />
                {row.roots.length > 1 && <StatusTag variant='warning'>{row.roots.length} copies</StatusTag>}
                {!row.tmdb_id && <StatusTag variant='muted'>Not matched</StatusTag>}
              </div>
            </TableCell>
            <TableCell numeric className='align-middle text-xs text-foreground'>{formatSize(row.size)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );

  const showsTable = (
    <Table fit variant='plain' className='page-ground' containerProps={tableContainer}>
      <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
        <TableRow>
          <SortHeader label='Series' column='title' sort={showSort} onSort={sortShows} />
          <SortHeader label='Seasons' column='seasons' sort={showSort} onSort={sortShows} inline priority={1} />
          <SortHeader label='Episodes' column='episodes' sort={showSort} onSort={sortShows} inline priority={3} />
          <SortHeader label='Size' column='size' sort={showSort} onSort={sortShows} align='right' inline priority={2} />
          {/* The last resort: its badges are on the title's page too, and the bar shows what is missing. */}
          <SortHeader label='State' column='state' sort={showSort} onSort={sortShows} priority={4} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {showRows.map((row) => {
          const tone = COMPLETION[row.completion_state] ?? COMPLETION.unknown;
          const isFollowed = !!row.tmdb_id && followed.has(row.tmdb_id);
          return (
            <TableRow key={row.key} {...rowProps(row)}>
              <TitleCell row={row} />
              <TableCell className='align-middle font-mono text-xs tabular-nums text-muted-foreground'>
                {row.season_count ? `${row.season_count} season${row.season_count === 1 ? '' : 's'}` : '—'}
              </TableCell>
              <TableCell className='align-middle'><ShowEpisodes show={row} /></TableCell>
              <TableCell numeric className='align-middle text-xs text-foreground'>{row.size ? formatSize(row.size) : '—'}</TableCell>
              <TableCell>
                <div className='flex flex-wrap items-center gap-1.5'>
                  <span className={cn('size-1.5 rounded-full', tone.className)} />
                  <span className='text-xs text-muted-foreground'>{tone.label}</span>
                  {isFollowed && <StatusTag variant='torrent'>Following</StatusTag>}
                  {row.roots.length > 1 && !isFollowed && <StatusTag variant='warning'>{row.roots.length} copies</StatusTag>}
                  {!row.tmdb_id && !row.key.startsWith('follow:') && <StatusTag variant='muted'>Not matched</StatusTag>}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );

  const activeShow = active && isShow(active) ? active : null;
  const pickedPaths = new Set(active && picked.key === active.key ? picked.paths : []);
  function pick(paths: string[], on: boolean) {
    if (!active) return;
    setPicked((current) => {
      const base = new Set(current.key === active.key ? current.paths : []);
      for (const path of paths) {
        if (on) base.add(path);
        else base.delete(path);
      }
      return { key: active.key, paths: [...base] };
    });
  }
  // A real button: Base UI's span checkbox lost its click to a drawer's swipe once; kept as a button.
  const tick = (paths: string[], label: string) => (
    <Checkbox
      nativeButton
      render={<button type='button' />}
      className='align-middle'
      aria-label={label}
      checked={paths.length > 0 && paths.every((path) => pickedPaths.has(path))}
      indeterminate={paths.some((path) => pickedPaths.has(path)) && !paths.every((path) => pickedPaths.has(path))}
      onCheckedChange={(on) => pick(paths, on)}
    />
  );
  const seasons = useMemo(() => {
    if (!activeShow) return [];
    return [...new Set(activeShow.episodes.map((entry) => entry.season_number))]
      .sort((a, b) => (a ?? -1) - (b ?? -1));
  }, [activeShow]);

  const totals = {
    movies: visibleMovies.length,
    shows: visibleShows.length,
    size: [...visibleMovies, ...visibleShows].reduce((sum, row) => sum + row.size, 0),
    germanMovies: visibleMovies.filter((row) => row.german).length,
    germanEpisodes: (visibleShows as MasShow[]).reduce((sum, row) => sum + (row.german_count ?? 0), 0),
  };

  // One title: its own page (?movie= / ?show=), or the right half of the split view.
  // relative: a followed show's own selection bar is placed inside it.
  const showDetail = (
    <div className='relative flex flex-col gap-6'>
      {active && (
        <>
          <div className='flex flex-col gap-2'>
            <div className='flex items-start gap-4'>
              <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
              <div className='flex min-w-0 flex-col gap-2'>
                <h2 className='font-heading text-xl leading-tight font-light tracking-[-0.02em] text-balance text-foreground'>
                  {active.title}{active.year ? ' (' + active.year + ')' : ''}
                </h2>
                <p className='text-xs text-muted-foreground'>
                  {activeShow
                    ? `${activeShow.downloaded_count}/${activeShow.total_count} episodes${activeShow.season_count ? ` · ${activeShow.season_count} seasons` : ''} · ${formatSize(active.size)}`
                    : `${(active as MasMovie).file_count} file${(active as MasMovie).file_count === 1 ? '' : 's'} · ${formatSize(active.size)}`}
                </p>
                {active.roots.length > 1 && !(active.tmdb_id && followed.has(active.tmdb_id)) && (
                  <p className='text-xs text-warning'>Stored under {active.roots.length} roots — likely a duplicate.</p>
                )}
                {/* Several folders taken for one title: match one of them to another, and it
                    becomes a card of its own (the rest stay as they are). */}
                {active.folders.length > 1 && !(activeShow && activeShow.tmdb_id && followed.has(activeShow.tmdb_id)) && (
                  <ul className='flex flex-col gap-1'>
                    {active.folders.map((folder) => (
                      <li key={folder} className='flex items-center gap-2 text-xs'>
                        <span className='min-w-0 truncate font-mono text-[0.68rem] text-muted-foreground' title={folder}>{folder}</span>
                        <Button
                          size='sm'
                          variant='ghost'
                          onClick={() => setMatching({ ...active, key: folder, folders: [folder], tmdb_id: null, matched_by_hand: false } as Row)}
                          title={`Match only ${folder} to a TMDB title`}
                        >
                          <Link2 data-icon='inline-start' /> Match…
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                {!active.tmdb_id && (
                  <p className='text-xs text-warning'>
                    Not matched to TMDB: the folder name fits more than one title, or none. Match it to get its cover{activeShow ? ' and episode list' : ''}.
                  </p>
                )}
                <div ref={setHeaderActions} className='flex flex-wrap gap-2'>
                  {followsLoaded && !(activeShow && activeShow.tmdb_id && followed.has(activeShow.tmdb_id)) && (
                    <Button variant={active.tmdb_id ? 'ghost' : 'primary'} size='sm' onClick={() => setMatching(active)}>
                      <Link2 data-icon='inline-start' /> {active.tmdb_id ? 'Change match' : 'Match to TMDB…'}
                    </Button>
                  )}
                  {followsLoaded && activeShow && activeShow.tmdb_id && !followed.has(activeShow.tmdb_id) && followHint(activeShow) && (
                    <Button variant='secondary' size='sm' onClick={() => setFollowing(activeShow.tmdb_id)} title={followHint(activeShow)!.title}>
                      <Magnet data-icon='inline-start' /> {followHint(activeShow)!.label}
                    </Button>
                  )}
                  {active.tmdb_id && !active.key.startsWith('follow:') && (
                    <Button variant='secondary' size='sm' onClick={() => setArtwork({ kind: activeShow ? 'show' : 'movie', key: active.key, title: active.title })}>
                      <Image data-icon='inline-start' /> Artwork
                    </Button>
                  )}
                  {active.tmdb_id && (
                    <Button
                      variant='secondary'
                      size='sm'
                      nativeButton={false}
                      render={<a href={`https://www.themoviedb.org/${activeShow ? 'tv' : 'movie'}/${active.tmdb_id}`} target='_blank' rel='noreferrer' />}
                    >
                      <ExternalLink data-icon='inline-start' /> TMDB
                    </Button>
                  )}
                </div>
              </div>
            </div>
          </div>

          {activeShow && activeShow.tmdb_id && followed.has(activeShow.tmdb_id) ? (
            <FollowedShowView tmdbId={activeShow.tmdb_id} onChanged={() => void loadFollows()} actions={headerActions} cardKey={activeShow.key.startsWith('follow:') ? null : activeShow.key} />
          ) : activeShow ? (
            <Tabs key={active.key} defaultValue={String(seasons[0])} className='flex min-h-0 flex-1 flex-col gap-3'>
              <TabsList className='h-auto max-w-full flex-wrap'>
                {seasons.map((season) => (
                  <TabsTab key={String(season)} value={String(season)}>
                    {season === null ? 'Other files' : season === 0 ? 'Specials' : 'Season ' + season}
                  </TabsTab>
                ))}
              </TabsList>
              {seasons.map((season) => (
                <TabsPanel key={String(season)} value={String(season)} className='flex flex-col'>
                  {(() => {
                    const files = [...new Set(activeShow.episodes.filter((entry) => entry.season_number === season && !entry.missing && entry.path).map((entry) => entry.path))];
                    return files.length > 0 && (
                      <label className='flex items-center gap-2 pb-1 text-xs text-muted-foreground'>
                        {tick(files, 'Select every file of this season')} Select the season
                      </label>
                    );
                  })()}
                  <table className='w-full border-collapse text-sm'>
                    <tbody>
                      {activeShow.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                        <tr key={entry.path ? entry.path + ':' + entry.episode : String(entry.season_number) + ':' + entry.episode} className='border-b border-border/60 last:border-0'>
                          <td className='w-8 py-2.5 pr-1'>{!entry.missing && entry.path && tick([entry.path], 'Select ' + entry.name)}</td>
                          <td className='w-10 py-2.5 pr-3 font-mono text-xs tabular-nums'>{entry.episode ?? '—'}</td>
                          <td className='px-3 py-2.5 text-xs text-muted-foreground'>
                            {entry.episode_title && <p className='text-sm text-foreground'>{entry.episode_title}</p>}
                            {!entry.missing && (entry.part_of
                              ? <p className='font-mono text-[0.68rem]' title='One file holds several episodes'>in {entry.part_of}</p>
                              : <p className='font-mono text-[0.68rem]'>{entry.name}</p>)}
                            {entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}
                          </td>
                          <td className='w-16 py-2.5 pl-3 text-right'><Quality value={entry.missing ? null : entry.quality} /></td>
                          <td className='w-24 whitespace-nowrap py-2.5 pl-3'>
                            {/* As the bar and a followed show's episodes say it: English without English subtitles. */}
                            {!entry.missing && (
                              <span className='inline-flex items-center gap-1.5'>
                                {!entry.german_dub && entry.english_subs === false && (
                                  <StatusTag variant='warning' title='English audio without English subtitles, in the file or beside it'>No subtitles</StatusTag>
                                )}
                                <Dub german={entry.german_dub} />
                              </span>
                            )}
                          </td>
                          <td className='w-24 py-2.5 pl-3 text-right font-mono text-xs tabular-nums text-muted-foreground'>
                            {entry.missing || entry.part_of ? '—' : formatSize(entry.size)}
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
                    {tick([file.path], 'Select ' + file.rel_path)}
                    <span className='min-w-0 flex-1 truncate font-mono text-[0.68rem]' title={file.path}>{file.rel_path}</span>
                    <span className='flex shrink-0 items-center gap-3'>
                      <Quality value={(file as AnimeLibraryEpisode).quality} />
                      <Dub german={(file as { german?: boolean | null }).german} />
                      <span className='w-20 text-right font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(file.size)}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {/* Under the rows and kept in sight at the bottom, as the anime library's: ticking never moves the rows. */}
          {pickedPaths.size > 0 && (
            <div className='sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay'>
              <span className='text-xs text-muted-foreground'>
                <span className='font-mono tabular-nums text-foreground'>{pickedPaths.size}</span> file{pickedPaths.size === 1 ? '' : 's'} selected
              </span>
              <Button size='sm' onClick={() => setMagnetOpen(true)} title='Replace the selected files with the torrent of a magnet link'>
                <Magnet data-icon='inline-start' /> Replace with magnet link…
              </Button>
              <Button size='sm' variant='ghost' className='ml-auto' onClick={() => setPicked({ key: '', paths: [] })}>
                <X data-icon='inline-start' /> Clear
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );

  // Every dialog, whichever way the page is laid out.
  const dialogs = (
    <>
      <ArtworkDialog
        mas={artwork}
        title={artwork?.title ?? ''}
        onClose={() => setArtwork(null)}
        onChanged={() => undefined}
      />

      <MagnetReplaceDialog
        open={magnetOpen}
        count={pickedPaths.size}
        what={activeShow ? 'file' : 'film file'}
        paths={[...pickedPaths]}
        scope={activeShow ? 'show' : 'movie'}
        onClose={() => setMagnetOpen(false)}
        onSubmit={async (magnet) => {
          if (!active) return { ok: false };
          try {
            await api.masReplaceMagnet(activeShow ? 'show' : 'movie', active.key, [...pickedPaths], magnet);
            toast.success(`${active.title}: the torrent is queued; each file it brings replaces the selected one of its episode`);
            setPicked({ key: '', paths: [] });
            return { ok: true };
          } catch (error: any) {
            toast.error(error.message);
            return { ok: false };
          }
        }}
      />

      <MatchDialog row={matching} onClose={() => setMatching(null)} onMatched={() => { closeRow(); void load(); }} />

      <Dialog open={following !== null} onOpenChange={(open) => { if (!open) setFollowing(null); }}>
        <DialogContent size='lg' className='max-h-[90vh] max-w-3xl overflow-y-auto'>
          <DialogHeader><DialogTitle>Follow {active?.title}</DialogTitle></DialogHeader>
          <DialogBody>
            {following !== null && <FollowPanel tmdbId={following} onChange={() => { void loadFollows(); setFollowing(null); }} />}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );

  // A title opened outside the split view is a page of its own.
  if (openKey && !split) {
    return (
      <TitlePage onBack={closeRow}>
        {active ? showDetail : loading ? (
          <div className='flex min-h-40 items-center justify-center'><Spinner /></div>
        ) : (
          <Empty>
            <EmptyMedia><HardDrive /></EmptyMedia>
            <EmptyTitle>Not in the library</EmptyTitle>
            <EmptyDescription>This title is no longer in the library, or it was matched to another one.</EmptyDescription>
          </Empty>
        )}
        {dialogs}
      </TitlePage>
    );
  }

  return (
    <div className='flex h-full min-h-0 flex-col gap-4'>
      <LibraryHeader
        title='Library'
        search={{ value: query, onChange: setQuery, placeholder: 'Search titles…', label: 'Search the library' }}
        view={view}
        onView={setView}
        wide={wide}
        onRescan={() => void load(true)}
        rescanning={loading}
      />

      <Tabs value={tab} onValueChange={(value) => { setTab(value as 'movies' | 'shows'); setOnly(null); }} className='min-h-0 flex-1 gap-3'>
        <div className='flex flex-wrap items-center gap-2'>
          <TabsList>
            <TabsTab value='movies'><Film data-icon='inline-start' /> Movies</TabsTab>
            <TabsTab value='shows'><Tv data-icon='inline-start' /> Shows</TabsTab>
          </TabsList>
          {/* Shown only while there is something of the kind to sort out. */}
          {unmatchedCount > 0 && (
            <Button size='sm' variant={only === 'unmatched' ? 'secondary' : 'ghost'} aria-pressed={only === 'unmatched'} onClick={() => setOnly(only === 'unmatched' ? null : 'unmatched')} title='Only the titles not matched to TMDB'>
              Unmatched <span className='font-mono tabular-nums text-muted-foreground'>{unmatchedCount}</span>
            </Button>
          )}
          {duplicateCount > 0 && (
            <Button size='sm' variant={only === 'duplicates' ? 'secondary' : 'ghost'} aria-pressed={only === 'duplicates'} onClick={() => setOnly(only === 'duplicates' ? null : 'duplicates')} title='Only the titles stored under more than one root'>
              Duplicates <span className='font-mono tabular-nums text-muted-foreground'>{duplicateCount}</span>
            </Button>
          )}
        </div>
        <TabsPanel value='movies' className='flex min-h-0 flex-col'>{panel(onlyRows(visibleMovies), only ? 'Nothing left to sort out here' : 'No movies found', moviesTable)}</TabsPanel>
        <TabsPanel value='shows' className='flex min-h-0 flex-col'>{panel(onlyRows(visibleShows), only ? 'Nothing left to sort out here' : 'No shows found', showsTable)}</TabsPanel>
      </Tabs>

      {!loading && (
        <LibraryFooter>
          <FooterStat value={totals.movies}>movies</FooterStat>
          <FooterStat value={totals.shows}>shows</FooterStat>
          <FooterStat value={formatSize(totals.size)}>stored</FooterStat>
          <FooterStat value={totals.germanMovies} dot='bg-transfer'>
            films and <span className='font-mono tabular-nums text-foreground'>{totals.germanEpisodes.toLocaleString()}</span> episodes German dub
          </FooterStat>
        </LibraryFooter>
      )}

      {dialogs}
    </div>
  );
}
