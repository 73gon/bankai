import { Fragment, useDeferredValue, useEffect, useMemo, useState, useRef, useSyncExternalStore } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { HardDrive, RefreshCw, ArrowLeft, ArrowRight, Columns2, CircleArrowUp, ExternalLink, Search, Download, FileVideo, Images, LayoutGrid, Rows3, Trash2, Ban, Link2, Network, Pencil, RotateCcw, TextCursorInput, Split, Unlink, X, Magnet, ShieldCheck } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type AniListAnime, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry, type AnimeBulkKind, type AnimeBulkShow, type HevcUpgradePlan, type MissingRelease } from '@/lib/api';
import { anilistName, anilistUrl } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { MagnetReplaceDialog } from '@/components/MagnetReplaceDialog';
import { HevcUpgradePlans, approvedOf } from '@/components/hevc-upgrade-plans';
import { ArtworkDialog } from '@/components/ArtworkDialog';
import { SeasonArcsDialog } from '@/components/SeasonArcsDialog';
import { CollectionGraph, type GraphTarget } from '@/components/CollectionGraph';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { SegmentMeter, rampParts } from '@/components/segment-meter';
import { EpisodeBar, airedCount, airedStrip, describeStrip, type StripTier } from '@/components/episode-bar';
import { LibrarySplit } from '@/components/library-split';
import { useRememberedOpen } from '@/lib/prefs';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Input } from '@/zon/ui/input';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Skeleton } from '@/zon/ui/skeleton';
import { Spinner } from '@/zon/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';
import { Toggle } from '@/zon/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';

function formatSize(bytes: number) {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

type LibrarySortKey = 'title' | 'seasons' | 'episodes' | 'size' | 'state';

const LIBRARY_SORTERS: Record<LibrarySortKey, (show: AnimeLibraryShow) => number | string> = {
  title: (show) => show.title.toLocaleLowerCase(),
  seasons: (show) => show.season_count,
  episodes: (show) => show.downloaded_count,
  size: (show) => show.size,
  state: (show) => show.completion_state,
};

const LIBRARY_FIRST_DIRECTION: Record<LibrarySortKey, SortDir> = {
  title: 'asc', seasons: 'desc', episodes: 'desc', size: 'desc', state: 'asc',
};

// 'split': the table beside the open show, to try out against the show's own page.
type View = 'grid' | 'table' | 'split';

// The library table's lesser columns, by the table's own width (not the window's).
/** The day a release came out, from Nyaa's RFC date ("Thu, 05 Sep 2024 12:00:00 -0000"). */
function publishedDay(value: string | null): string {
  const day = value ? new Date(value) : null;
  return day && !Number.isNaN(day.getTime()) ? day.toISOString().slice(0, 10) : '—';
}

/** A release's encode, as its name gives it. */
function isHevc(title: string): boolean {
  return /\b(?:HEVC|x265|H\.?265)\b/i.test(title);
}

// A drawer is asked for once the pointer has rested this long on its show,
// and a click takes that answer while it is younger than PREFETCH_MS.
const PREFETCH_DELAY_MS = 90;
const PREFETCH_MS = 15_000;

const VIEW_KEY = 'bankai.anime.library.view';
const FILTERS_KEY = 'bankai.anime.library.filters';

type StoredFilters = {
  query: string;
  onlyBlacklisted: boolean;
  onlyUnmatched: boolean;
  sort: SortState<LibrarySortKey> | null;
};

/** The filter and order last used, so leaving the page and coming back keeps them. */
function storedFilters(): StoredFilters {
  const empty: StoredFilters = { query: '', onlyBlacklisted: false, onlyUnmatched: false, sort: null };
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) ?? '{}');
    return {
      query: typeof saved.query === 'string' ? saved.query : '',
      onlyBlacklisted: saved.onlyBlacklisted === true,
      onlyUnmatched: saved.onlyUnmatched === true,
      sort: saved.sort && typeof saved.sort.key === 'string' && saved.sort.key in LIBRARY_SORTERS && (saved.sort.dir === 'asc' || saved.sort.dir === 'desc') ? saved.sort : null,
    };
  } catch {
    return empty;
  }
}

function storedView(): View {
  try {
    const saved = localStorage.getItem(VIEW_KEY);
    return saved === 'table' || saved === 'split' ? saved : 'grid';
  } catch {
    // Private windows and blocked site data both throw here; the grid is a
    // perfectly good default to fall back on.
    return 'grid';
  }
}

// The split view needs the room; narrower, a show opens as its page.
const WIDE_QUERY = '(min-width: 64rem)';
function subscribeWide(onChange: () => void) {
  const query = window.matchMedia(WIDE_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}
function useWide(): boolean {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE_QUERY).matches, () => true);
}

const COMPLETION: Record<string, { label: string; className: string }> = {
  complete: { label: 'Complete', className: 'bg-success' },
  upcoming: { label: 'Airing', className: 'bg-transfer' },
  partial: { label: 'Partial', className: 'bg-warning' },
  empty: { label: 'Empty', className: 'bg-destructive' },
  unknown: { label: 'Unknown', className: 'bg-muted-foreground' },
};

// What each letter of a show's strip is on the episode bar: its encode.
const ENCODE_TIERS: Record<string, StripTier> = {
  h: { label: 'HEVC', className: 'bg-success' },
  a: { label: 'AVC', className: 'bg-warning' },
  d: { label: 'German dub', className: 'bg-transfer' },
  u: { label: 'codec not read yet', className: 'bg-muted-foreground/50' },
};

/** Episodes and their encodes as one bar: the show's episodes end to end,
 *  season after season, lit where the library has them in the colour of their
 *  encode (green HEVC, amber AVC, purple German dub, grey not read yet). The
 *  number is the episodes on disk. */
function Progress({ show, className, fill = false }: { show: AnimeLibraryShow; className?: string; fill?: boolean }) {
  // `fill`: the bar takes what its row leaves (a grid card), not a fixed width.
  const bar = fill ? cn('w-auto min-w-12 flex-1 shrink', className) : className;
  const row = cn('flex items-center gap-2', fill && 'min-w-0 flex-1');
  const count = (
    <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
      {show.downloaded_count}/{show.total_count}
    </span>
  );
  if (!show.strip?.length) {
    // Shows without a strip (an answer from before it existed): the plain ratio.
    const total = show.total_count || show.downloaded_count || 1;
    return (
      <div className={row}>
        <SegmentMeter className={cn('w-[110px]', bar)} total={total} title={`${show.downloaded_count} of ${show.total_count} episodes`} parts={rampParts(show.downloaded_count, total)} />
        {count}
      </div>
    );
  }
  // What has not aired yet (TBA) is not on the bar, nor in its count.
  const aired = airedStrip(show.strip);
  const total = airedCount(show.strip);
  const label = [
    `${show.downloaded_count} of ${total} aired episodes`,
    ...aired.map((season) => `${season.label}: ${describeStrip(season.states, ENCODE_TIERS)}`),
  ].join('; ');
  return (
    <div className={row}>
      <EpisodeBar strip={show.strip} tiers={ENCODE_TIERS} className={bar} label={label} />
      <span className='shrink-0 font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.downloaded_count}/{total}</span>
    </div>
  );
}

/** A show's state: its completion and what is on offer or wrong with it. */
function StateTags({ show }: { show: AnimeLibraryShow }) {
  const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
  return (
    <span className='flex flex-wrap items-center gap-1.5 font-body'>
      <span className={cn('size-1.5 rounded-full', tone.className)} />
      <span className='text-xs text-muted-foreground'>{tone.label}</span>
      <UpgradeTag show={show} />
      {(show.missing_available_count ?? 0) > 0 && <StatusTag variant='info' title='Missing episodes with a German-subtitled release found. Open the show and choose Download remaining episodes.'>{show.missing_available_count} available</StatusTag>}
      {(show.no_german_count ?? 0) > 0 && <StatusTag variant='warning' title='Episodes on disk with neither German subtitles nor a German dub'>{show.no_german_count} without German</StatusTag>}
      {(show.verify_wrong ?? 0) > 0 && <StatusTag variant='destructive' title='The episode check found files whose picture is another episode than the one they are filed as. Open the show to see which.'>{show.verify_wrong} wrong episode{show.verify_wrong === 1 ? '' : 's'}</StatusTag>}
      {show.staged_count > 0 && <StatusTag variant='warning'>{show.staged_count} staged</StatusTag>}
      {show.blacklisted && <StatusTag variant='destructive'>Blacklisted</StatusTag>}
      {isUnmatched(show) && <StatusTag variant='warning' title='No AniList entry is known for these files: match it from its details'>Unmatched</StatusTag>}
    </span>
  );
}

/** Whether a show has an HEVC upgrade: found (with German subtitles) or being
 *  downloaded. Nothing otherwise -- not looked at yet, or none found: the
 *  search runs by itself in the background (the dashboard shows it), and how
 *  many episodes are AVC is the encode bar's to say. */
function UpgradeTag({ show }: { show: AnimeLibraryShow }) {
  const upgrading = show.upgrading_count ?? 0;
  const available = show.upgrade_available_count ?? 0;
  const checked = show.hevc_checked_at ? new Date(show.hevc_checked_at * 1000).toLocaleDateString() : null;
  const when = checked ? ' (looked ' + checked + ')' : '';
  return (
    <>
      {upgrading > 0 && <StatusTag variant='transfer' title='HEVC versions of these episodes are downloading; the AVC files go once they land'>Upgrading {upgrading}</StatusTag>}
      {available > 0 && <StatusTag variant='info' title={'An Erai-raws HEVC release with German subtitles exists for ' + available + ' AVC episode(s)' + when + '. Open the show and choose Upgrade to HEVC.'}>Upgrade for {available}</StatusTag>}
    </>
  );
}

/** What the episode check says of one file, beside its State: only a wrong one. The check runs in the
 *  background; a file found right needs no mark (a green shield on every row was noise, Nov 2026). */
function VerifyTag({ verify }: { verify?: AnimeLibraryEpisode['verify'] }) {
  if (verify?.verdict !== 'wrong') return null;
  return <StatusTag variant='destructive' title={verifyWhy(verify)}>Wrong episode</StatusTag>;
}

/** The check's findings in words, for a tag's title and the row's note. */
function verifyWhy(verify: NonNullable<AnimeLibraryEpisode['verify']>): string {
  const parts: string[] = [];
  const picture = verify.picture;
  if (picture?.state === 'ok') parts.push(`Its picture is TMDB ${picture.expected} "${picture.expected_name}" (${picture.score?.toFixed(2)})`);
  else if (picture?.state === 'wrong') parts.push(`Its picture is TMDB ${picture.match} "${picture.match_name}" (${picture.match_score?.toFixed(2)}), not ${picture.expected} "${picture.expected_name}" (${picture.score?.toFixed(2) ?? '–'})`);
  else if (picture?.state === 'unsure') parts.push(`No TMDB still of ${picture.expected} "${picture.expected_name}" clearly among its frames`);
  else if (picture?.state === 'none') parts.push('No picture check: ' + (picture.why ?? 'no stills'));
  const length = verify.length;
  if (length) parts.push(`${Math.round(length.seconds / 60)} min, ${length.source} says ${length.expected_minutes}` + (length.ok ? '' : ' — length off'));
  for (const issue of verify.issues ?? []) parts.push(issue);
  if (verify.error) parts.push('Could not be checked: ' + verify.error);
  if (verify.verdict === 'confirmed') parts.unshift('You confirmed this is the episode' + (verify.confirmed_at ? ' on ' + new Date(verify.confirmed_at * 1000).toLocaleDateString() : ''));
  if (verify.checked_at) parts.push('checked ' + new Date(verify.checked_at * 1000).toLocaleDateString());
  return parts.join(' · ');
}

/** Under a wrong file's name: what its picture shows, and the way to put it right -- make it that
 *  episode, delete it when the library has that episode already, or say it is the right one after all.
 *  Index facts (two files on one episode, past the entry's end) show whatever the picture says. */
function VerifyNote({ show, episode, busy, onRefile, onDelete, onConfirm }: {
  show: AnimeLibraryShow;
  episode: AnimeLibraryEpisode;
  busy: boolean;
  onRefile: (show: AnimeLibraryShow, episode: AnimeLibraryEpisode, anilistId: number, number: number, title: string) => void;
  onDelete: () => void;
  onConfirm: (episode: AnimeLibraryEpisode, right: boolean) => void;
}) {
  const verify = episode.verify;
  // Only what is wrong: a file found right, or confirmed by you, says nothing.
  if (!verify || verify.verdict === 'confirmed' || (verify.verdict !== 'wrong' && !verify.issues?.length)) return null;
  const picture = verify.picture;
  const is = verify.verdict === 'wrong' ? picture?.is : undefined;
  const title = is ? show.collection_entries?.find((item) => item.anilist_id === is[0])?.title
    ?? Object.values(show.season_anilist ?? {}).flat().find((item) => item.anilist_id === is[0])?.title
    ?? 'AniList ' + is[0] : '';
  const twin = is ? show.episodes.find((row) => row.anilist_id === is[0] && row.anilist_episode === is[1] && !row.missing && row.path !== episode.path) : undefined;
  const same = is && is[0] === episode.anilist_id && is[1] === episode.anilist_episode;
  return (
    <p className={cn('mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.7rem]', verify.verdict === 'wrong' ? 'text-destructive' : 'text-warning')}>
      <span className='min-w-0'>
        {verify.verdict === 'wrong' && picture?.state === 'wrong'
          ? `Shows ${is ? title + ' ep ' + is[1] : 'TMDB ' + picture.match} "${picture.match_name}"`
          : verify.issues?.length ? verify.issues.join(' · ')
          : picture?.state === 'unsure' ? 'Its picture matches no TMDB still clearly'
          : verify.length && !verify.length.ok ? `${Math.round(verify.length.seconds / 60)} min, ${verify.length.source} says ${verify.length.expected_minutes}`
          : verifyWhy(verify)}
        {twin && <> — a second copy of {twin.name}</>}
      </span>
      {is && !same && (twin ? (
        <Button size='sm' variant='ghost' className='h-5 px-1.5 text-[0.7rem]' disabled={busy} onClick={onDelete} title={'The library has ' + twin.name + ' already: delete this copy'}>
          <Trash2 data-icon='inline-start' /> Delete this copy
        </Button>
      ) : (
        <Button size='sm' variant='ghost' className='h-5 px-1.5 text-[0.7rem]' disabled={busy} onClick={() => onRefile(show, episode, is[0], is[1], title)} title='File it as the episode its picture shows; it moves into place'>
          <Link2 data-icon='inline-start' /> Make it {title} ep {is[1]}
        </Button>
      ))}
      {verify.verdict === 'wrong' && (
        <Button size='sm' variant='ghost' className='h-5 px-1.5 text-[0.7rem]' disabled={busy} onClick={() => onConfirm(episode, true)} title='You checked: it is the episode it is filed as. Kept while the file stays as it is.'>
          <ShieldCheck data-icon='inline-start' /> It's the right episode
        </Button>
      )}
    </p>
  );
}

/** Whole numbers as runs: [5, 6, 7, 10] -> "5–7, 10". */
function numberRuns(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b);
  const runs: string[] = [];
  let start = sorted[0];
  let previous = start;
  for (const value of [...sorted.slice(1), Number.NaN]) {
    if (value === previous + 1) {
      previous = value;
      continue;
    }
    runs.push(start === previous ? String(start) : start + '–' + previous);
    start = previous = value;
  }
  return sorted.length ? runs.join(', ') : '';
}

/** The missing episodes a found release covers, a line per season tab of the show: [tab, "5–7, 10"]. */
function coveredEpisodes(release: MissingRelease): Array<[string, string]> {
  return episodeRuns(release.episodes);
}

/** Episodes a line per season tab of the show: [tab, "5–7, 10"]. */
function episodeRuns(episodes: MissingRelease['episodes']): Array<[string, string]> {
  const byTab = new Map<string, number[]>();
  for (const row of episodes) {
    const tab = row.season ?? 'Episodes';
    byTab.set(tab, [...(byTab.get(tab) ?? []), row.episode ?? row.anilist_episode]);
  }
  return [...byTab].map(([tab, numbers]) => [tab, numberRuns(numbers)]);
}

/** A card of files no AniList entry is known for: offered "Match on AniList". */
function isUnmatched(show: AnimeLibraryShow) {
  return show.unmatched ?? !show.anilist_id;
}

type EntryRef = { anilist_id: number; title: string };

/** What the AniList picker is open for: an Unmatched card's folders, or one entry of a card to swap. */
type PickTarget =
  | { mode: 'match'; show: AnimeLibraryShow }
  | { mode: 'rematch'; show: AnimeLibraryShow; from: EntryRef }
  /** Only choosing the entry selected files are matched to; the match dialog does the rest. */
  | { mode: 'assign'; query: string };

/** The selected files of a card that "Match to AniList entry…" is open for, in the card's order. */
type AssignTarget = { show: AnimeLibraryShow; rows: AnimeLibraryEpisode[] };

/** The entries a card's files can be matched to without searching: its collection's and its tabs'. */
function knownEntries(show: AnimeLibraryShow): EntryRef[] {
  const seen = new Set<number>();
  const out: EntryRef[] = [];
  const add = (entry: EntryRef) => {
    if (seen.has(entry.anilist_id)) return;
    seen.add(entry.anilist_id);
    out.push({ anilist_id: entry.anilist_id, title: entry.title });
  };
  for (const entry of show.collection_entries ?? []) add(entry);
  for (const entries of Object.values(show.season_anilist ?? {})) for (const entry of entries) add(entry);
  return out;
}

/** A row that is a file on disk, and so can be selected. */
function hasFile(episode: AnimeLibraryEpisode) {
  return !episode.missing && Boolean(episode.path);
}

/** The entry "Change AniList entry" swaps: the open tab's, when the tab is one entry. */
function tabEntry(show: AnimeLibraryShow, tab: string): EntryRef | null {
  const here = show.season_anilist?.[tab] ?? [];
  if (here.length === 1) return here[0];
  // A card of one entry whose tabs name none: that entry.
  if (here.length === 0 && (show.anilist_ids?.length ?? 0) <= 1 && show.anilist_id != null) {
    return { anilist_id: show.anilist_id, title: show.source_title || show.title };
  }
  return null;
}

/** The drawer's shape while a show loads: poster and title, the action row, the season tabs, episode rows. */
function DrawerSkeleton() {
  return (
    <div className='flex flex-col gap-6' aria-busy='true' aria-label='Loading the show'>
      <div className='flex items-start gap-4'>
        <Skeleton shape='item' className='aspect-[2/3] w-16' />
        <div className='flex min-w-0 flex-1 flex-col gap-2 pt-1'>
          <Skeleton className='w-72 max-w-full text-[22px]' />
          <Skeleton className='w-40 text-xs' />
          <Skeleton className='w-56 text-xs' />
          <div className='mt-1 flex flex-wrap gap-2'>
            {[20, 24, 36, 22, 28].map((width) => <Skeleton key={width} shape='button' className='min-h-8' style={{ width: width * 4 }} />)}
          </div>
        </div>
      </div>
      <div className='flex flex-col gap-3'>
        <Skeleton shape='field' className='w-80 max-w-full' />
        {Array.from({ length: 10 }, (_, index) => <Skeleton key={index} shape='item' className='h-8 w-full' />)}
      </div>
    </div>
  );
}

export default function AnimeLibrary() {
  // Started from the last answer this browser saw, refreshed straight after.
  const [shows, setShows] = useState<AnimeLibraryShow[]>(() => recall<{ shows: AnimeLibraryShow[] }>(pagePaths.animeLibrary)?.shows ?? []);
  const [root, setRoot] = useState(() => recall<{ root: string }>(pagePaths.animeLibrary)?.root ?? '');
  const [query, setQuery] = useState(() => storedFilters().query);
  // Only the blacklisted shows still on disk. They are always listed, with their badge:
  // the library shows everything its folders hold.
  const [onlyBlacklisted, setOnlyBlacklisted] = useState(() => storedFilters().onlyBlacklisted);
  // Only the cards no AniList entry is known for.
  const [onlyUnmatched, setOnlyUnmatched] = useState(() => storedFilters().onlyUnmatched);
  const [view, setView] = useState<View>(storedView);
  const [sort, setSort] = useState<SortState<LibrarySortKey> | null>(() => storedFilters().sort);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedShow, setSelectedShow] = useState<AnimeLibraryShow | null>(null);
  // What the AniList picker is open for.
  const [pickFor, setPickFor] = useState<PickTarget | null>(null);
  // The open season tab of the show, and the card it was opened on.
  const [tab, setTab] = useState<{ key: string; value: string } | null>(null);
  // The collection being renamed, and the name typed for it.
  const [renameFor, setRenameFor] = useState<AnimeLibraryShow | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renaming, setRenaming] = useState(false);
  // The season being named: its card, its tab, the entry it opens with, and the name typed.
  const [seasonRename, setSeasonRename] = useState<{ show: AnimeLibraryShow; season: number; entry: EntryRef; label: string } | null>(null);
  const [seasonName, setSeasonName] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  // Only a spinner with nothing to show: otherwise the held answer stays up while it refreshes.
  const [loading, setLoading] = useState(() => recall(pagePaths.animeLibrary) === undefined);
  const [transferring, setTransferring] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  // The approval an upgrade waits for: what each release would replace (null while it is looked up).
  const [upgradeFor, setUpgradeFor] = useState<{ show: AnimeLibraryShow; plans: HevcUpgradePlan[] | null; note: string | null } | null>(null);
  const [upgradePicked, setUpgradePicked] = useState<Set<string>>(new Set());
  const [searchTarget, setSearchTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [results, setResults] = useState<AnimeEntry[]>([]);
  const [searching, setSearching] = useState(false);
  // What the episode search keeps: both on unless switched off, as the automation takes them.
  const [only1080, setOnly1080] = useState(true);
  const [onlyHevc, setOnlyHevc] = useState(true);
  const shownResults = useMemo(
    () => results.filter((entry) => (!only1080 || entry.quality === '1080p') && (!onlyHevc || isHevc(entry.title))),
    [results, only1080, onlyHevc],
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [downloading, setDownloading] = useState<string | null>(null);
  // The show to remove, and whether it is blacklisted too.
  const [removeTarget, setRemoveTarget] = useState<{ show: AnimeLibraryShow; blacklist: boolean } | null>(null);
  // The episode files ticked in the show, and the card they are of.
  const [checked, setChecked] = useState<{ key: string; paths: string[] }>({ key: '', paths: [] });
  // The ticked files, while the Replace with magnet link dialog is open.
  const [magnetFor, setMagnetFor] = useState<string[] | null>(null);
  // "Match to AniList entry…": the files, the entry, the first episode and each file's episode.
  const [assignFor, setAssignFor] = useState<AssignTarget | null>(null);
  const [assignEntry, setAssignEntry] = useState<EntryRef | null>(null);
  const [assignFirst, setAssignFirst] = useState('1');
  const [assignEpisodes, setAssignEpisodes] = useState<string[]>([]);
  const [assigning, setAssigning] = useState(false);
  const [episodeTarget, setEpisodeTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [deletingEpisode, setDeletingEpisode] = useState(false);
  const [titling, setTitling] = useState(false);
  // The collection whose relation graph is open.
  const [graphFor, setGraphFor] = useState<GraphTarget>(null);
  // The collection whose Artwork dialog is open.
  const [artworkFor, setArtworkFor] = useState<AnimeLibraryShow | null>(null);
  // The collection whose Seasons (arcs) dialog is open.
  const [arcsFor, setArcsFor] = useState<AnimeLibraryShow | null>(null);
  // Shows being removed in the background: kept off the page until done.
  const removing = useRef<Set<string>>(new Set());
  // "Download N remaining episodes": the show, what the background search found
  // for it, and the releases ticked (all of them to begin with).
  const [missingFor, setMissingFor] = useState<AnimeLibraryShow | null>(null);
  const [missingReleases, setMissingReleases] = useState<MissingRelease[] | null>(null);
  const [missingPicked, setMissingPicked] = useState<Set<string>>(new Set());
  const [missingQueuing, setMissingQueuing] = useState(false);
  // "Upgrade all" / "Download all available": which one the dialog is open for,
  // the shows it would take (all ticked to begin with), and the one running.
  const [bulkKind, setBulkKind] = useState<AnimeBulkKind | null>(null);
  const [bulkShows, setBulkShows] = useState<AnimeBulkShow[] | null>(null);
  const [bulkPicked, setBulkPicked] = useState<Set<string>>(new Set());
  const [bulkRunning, setBulkRunning] = useState<AnimeBulkKind | null>(null);
  // Upgrade all: each show's releases and what they replace, by show key (null while looked up), and the ticked ones.
  const [bulkPlans, setBulkPlans] = useState<Record<string, HevcUpgradePlan[] | null>>({});
  const [bulkPlanPicked, setBulkPlanPicked] = useState<Set<string>>(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* remembering the choice is a convenience, not a requirement */
    }
  }, [view]);

  useEffect(() => {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({ query, onlyBlacklisted, onlyUnmatched, sort } satisfies StoredFilters));
    } catch {
      /* as with the view: a convenience */
    }
  }, [query, onlyBlacklisted, onlyUnmatched, sort]);

  async function load(rescan = false): Promise<AnimeLibraryShow[] | null> {
    if (rescan) setLoading(true);
    try {
      const result = await api.animeLibrary(rescan);
      // The answer's own list when nothing is being removed: an unchanged answer is then the
      // same list, and the page is left as it is (a filtered copy laid out every row again).
      const fresh = removing.current.size ? result.shows.filter((show) => !removing.current.has(show.key)) : result.shows;
      setShows(fresh);
      setRoot(result.root);
      return fresh;
    } catch (error: any) {
      toast.error(error.message);
      return null;
    } finally {
      setLoading(false);
    }
  }

  /** After a change that may rename the open card: open the card it became, or close the show.
   *  Several ways to find it are tried in order. */
  async function reopen(...finds: Array<(show: AnimeLibraryShow) => boolean>) {
    const fresh = await load();
    let card: AnimeLibraryShow | undefined;
    for (const find of finds) {
      card = fresh?.find(find);
      if (card) break;
    }
    if (card) {
      if (card.key !== showKey) {
        // Renamed: the address follows, without a step to go back to.
        const next = new URLSearchParams(params);
        next.set('show', card.key);
        setParams(next, { replace: true, state: location.state });
      }
      await openShow(card.key);
    } else {
      closeShow();
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

  /** The picked entry: an Unmatched card's files become its episodes, or one entry's files move to it. */
  async function pickEntry(anime: AniListAnime) {
    const target = pickFor;
    if (!target) return;
    const name = anilistName(anime);
    if (target.mode === 'assign') {
      // Only the choice: the match dialog still asks for the episode numbers.
      setAssignEntry({ anilist_id: anime.anilist_id, title: name });
      setPickFor(null);
      return;
    }
    try {
      if (target.mode === 'match') {
        const folders = target.show.folders?.length ? target.show.folders : [target.show.key];
        const result = await api.matchLibraryFolders(folders, anime.anilist_id);
        const skipped = result.skipped?.length ?? 0;
        if (skipped && !(result.matched?.length ?? 0)) {
          toast.error(
            'Nothing matched: the file names carry no episode number. Tick the files and use "Match to AniList entry…" to number them.',
          );
          return;
        }
        if (skipped) toast.warning(skipped + ' file' + (skipped === 1 ? '' : 's') + ' had no episode number and stayed unmatched; tick them and use "Match to AniList entry…".');
        else toast.success(target.show.title + ' matched to ' + name);
      } else {
        if (target.from.anilist_id === anime.anilist_id) {
          toast.info(target.from.title + ' is already that entry');
          return;
        }
        await api.rematchEntry(target.from.anilist_id, anime.anilist_id);
        toast.success(target.from.title + ' is now ' + name + '; its files are moved to match');
      }
      setPickFor(null);
      // The card is named after its entries, so it may come back under another key.
      if (selected) await reopen((show) => show.anilist_id === anime.anilist_id || Boolean(show.anilist_ids?.includes(anime.anilist_id)));
      else void load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  // A show the pointer rests on has its drawer asked for then, so a click finds
  // it in or on its way. Only a click takes it, and only while it is young:
  // after a change the open show is always asked for afresh.
  const prefetched = useRef(new Map<string, { at: number; answer: Promise<AnimeLibraryShow | null> }>());
  const prefetchTimer = useRef<number | undefined>(undefined);

  function prefetchShow(key: string) {
    window.clearTimeout(prefetchTimer.current);
    prefetchTimer.current = window.setTimeout(() => {
      const held = prefetched.current.get(key);
      if (held && Date.now() - held.at < PREFETCH_MS) return;
      const answer = api.animeLibraryShow(key).then((result) => result.shows[0] ?? null);
      answer.catch(() => prefetched.current.delete(key));
      prefetched.current.set(key, { at: Date.now(), answer });
    }, PREFETCH_DELAY_MS);
  }

  function stopPrefetch() {
    window.clearTimeout(prefetchTimer.current);
  }

  function takePrefetched(key: string): Promise<AnimeLibraryShow | null> | null {
    const held = prefetched.current.get(key);
    prefetched.current.delete(key);
    return held && Date.now() - held.at < PREFETCH_MS ? held.answer : null;
  }

  async function openShow(key: string, clicked = false) {
    // Another show: the one open goes at once, so it neither lingers nor moves while the next loads.
    if (selectedShow?.key !== key) {
      setSelectedShow(null);
      setTab(null);
      clearChecked();
    }
    setSelected(key);
    setDetailLoading(true);
    try {
      const ready = clicked ? takePrefetched(key) : null;
      const found = ready ? await ready : (await api.animeLibraryShow(key)).shows[0] ?? null;
      setSelectedShow(found);
      // Gone since (deleted, renamed): closed, so a remembered one is forgotten too.
      if (!found) {
        toast.info('That show is no longer in the library');
        closeShow();
      }
    } catch (error: any) {
      toast.error(error.message);
      setSelectedShow(null);
    } finally {
      setDetailLoading(false);
    }
  }

  /** Looks what an upgrade would download and replace; nothing is queued until that is approved. */
  async function upgradeToHevc(show: AnimeLibraryShow) {
    if (!show.anilist_ids?.length) return;
    setUpgradeFor({ show, plans: null, note: null });
    setUpgradePicked(new Set());
    try {
      const result = await api.previewHevcUpgrade(show.anilist_ids);
      // Say what was found, not what is assumed: "already HEVC" only when it is.
      const note = result.plans.length ? null : result.no_replacement > 0
        ? 'No HEVC release with German subtitles was found for ' + result.no_replacement + ' AVC episode(s).'
        : result.not_probed
          ? result.not_probed + ' episode(s) have not been checked for their codec yet; try again later.'
          : result.german_dub_kept
            ? 'Nothing to upgrade: the AVC episodes left carry a German dub.'
            : 'Nothing to upgrade — every episode is already HEVC.';
      setUpgradeFor((current) => current && current.show.key === show.key ? { show, plans: result.plans, note } : current);
      setUpgradePicked(new Set(result.plans.map((plan) => plan.info_hash)));
    } catch (error: any) {
      toast.error(error.message);
      setUpgradeFor(null);
    }
  }

  async function approveUpgrade() {
    if (!upgradeFor?.plans) return;
    const { show, plans } = upgradeFor;
    setUpgrading(show.key);
    try {
      const result = await api.upgradeShowToHevc(show.anilist_ids ?? [], approvedOf(plans, upgradePicked));
      setUpgradeFor(null);
      if (result.queued === 0) {
        toast.info(result.changed
          ? 'Nothing queued: the files changed since you looked; look again'
          : 'Nothing queued: no approved release carries German subtitles any more');
      } else {
        toast.success(
          'Queued ' + result.queued + ' HEVC replacement' + (result.queued === 1 ? '' : 's')
          + (result.changed ? ' · ' + result.changed + ' left out, their files changed' : '')
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
    const { show: target, blacklist } = removeTarget;
    // Off the page at once; deleting runs in the background, where the
    // Dashboard shows it under "Your actions".
    removing.current.add(target.key);
    setShows((current) => current.filter((show) => show.key !== target.key));
    setRemoveTarget(null);
    closeShow();
    toast.info('Removing ' + target.title + ' in the background…');
    try {
      const result = await api.removeAnimeLibraryShow(target.key, target.title, blacklist);
      toast.success(
        target.title + (blacklist ? ' removed and blacklisted' : ' removed') + ' — deleted ' + result.deleted_files + ' file'
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

  /** Name and picture the collection after one of its entries; the library and Jellyfin follow. */
  async function setTitle(show: AnimeLibraryShow, anilistId: number) {
    setTitling(true);
    try {
      const result = await api.setCollectionTitle(show.key, anilistId);
      toast.success('Collection now shown as ' + (result.name || 'the chosen entry'));
      // The card's key is its name, which may just have changed.
      const cid = show.collection_id;
      await reopen((card) => (cid != null ? card.collection_id === cid : card.key === (result.name || show.key)));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setTitling(false);
    }
  }

  function openRename(show: AnimeLibraryShow) {
    setRenameValue(show.title);
    setRenameFor(show);
  }

  function openSeasonRename(show: AnimeLibraryShow, season: number, entry: EntryRef) {
    const label = show.episodes.find((episode) => episode.season_number === season)?.season ?? 'Season ' + season;
    setSeasonName(show.season_names?.[String(entry.anilist_id)] ?? label);
    setSeasonRename({ show, season, entry, label });
  }

  /** A name of the user's own for a season; null gives it its entry's again. */
  async function renameSeason(name: string | null) {
    const target = seasonRename;
    if (!target || target.show.collection_id == null) return;
    const cid = target.show.collection_id;
    setRenaming(true);
    try {
      await api.animeSeasonName(cid, target.entry.anilist_id, name);
      toast.success(name ? 'Season renamed to ' + name : 'Season named after its entry again');
      setSeasonRename(null);
      await reopen((card) => card.collection_id === cid);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRenaming(false);
    }
  }

  /** A name of the user's own for the collection; null gives it back AniList's. */
  async function renameCollection(name: string | null) {
    const show = renameFor;
    if (!show || show.collection_id == null) return;
    const cid = show.collection_id;
    setRenaming(true);
    try {
      const result = await api.collectionName(cid, name);
      toast.success(name ? 'Collection renamed to ' + name : 'Collection named after AniList again' + (result.name ? ': ' + result.name : ''));
      setRenameFor(null);
      await reopen((card) => card.collection_id === cid);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRenaming(false);
    }
  }

  /** The ticked files of the open card that are still on disk, in the card's order. */
  function checkedRows(show: AnimeLibraryShow | null): AnimeLibraryEpisode[] {
    if (!show || checked.key !== show.key || !checked.paths.length) return [];
    const ticked = new Set(checked.paths);
    return show.episodes.filter((episode) => hasFile(episode) && ticked.has(episode.path));
  }

  function toggleChecked(show: AnimeLibraryShow, paths: string[], on: boolean) {
    setChecked((current) => {
      const mine = current.key === show.key ? current.paths : [];
      const rest = mine.filter((path) => !paths.includes(path));
      return { key: show.key, paths: on ? [...rest, ...paths] : rest };
    });
  }

  function clearChecked() {
    setChecked({ key: '', paths: [] });
  }

  function openAssign(show: AnimeLibraryShow, rows: AnimeLibraryEpisode[]) {
    if (!rows.length) return;
    const ids = new Set(rows.map((row) => row.anilist_id ?? null));
    const only = ids.size === 1 ? [...ids][0] : null;
    setAssignEntry(only != null ? knownEntries(show).find((entry) => entry.anilist_id === only) ?? null : null);
    const first = rows[0].anilist_episode ?? 1;
    setAssignFirst(String(first));
    // Matched files keep their own number; the rest count on from the first.
    setAssignEpisodes(rows.map((row, index) => String(row.anilist_episode ?? first + index)));
    setAssignFor({ show, rows });
  }

  /** A new first episode numbers every file consecutively from it. */
  function changeAssignFirst(value: string) {
    setAssignFirst(value);
    const first = Number(value);
    if (!assignFor || !value.trim() || !Number.isInteger(first) || first < 0) return;
    setAssignEpisodes(assignFor.rows.map((_, index) => String(first + index)));
  }

  async function assign() {
    const target = assignFor;
    const entry = assignEntry;
    if (!target || !entry) return;
    const numbers = assignEpisodes.map((value) => Number(value));
    if (numbers.some((value, index) => !assignEpisodes[index].trim() || !Number.isInteger(value) || value < 0)) {
      toast.error('Every file needs a whole episode number');
      return;
    }
    if (new Set(numbers).size !== numbers.length) {
      toast.error('Two files cannot be the same episode');
      return;
    }
    setAssigning(true);
    try {
      const result = await api.assignFiles(target.show.key, target.rows.map((row) => row.path), entry.anilist_id, numbers);
      const count = result.assigned ?? target.rows.length;
      toast.success(
        count + ' file' + (count === 1 ? '' : 's') + ' matched to ' + entry.title
        + (result.moved ? ' · ' + result.moved + ' moved into place' : ''),
      );
      setAssignFor(null);
      clearChecked();
      const id = entry.anilist_id;
      // The card stays open while files are left on it; else the card the files went to.
      await reopen(
        (card) => card.key === target.show.key,
        (card) => card.anilist_id === id || Boolean(card.anilist_ids?.includes(id)),
      );
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setAssigning(false);
    }
  }

  /** The user's word on a file the check calls wrong: it is the episode it is filed as (or taken back). */
  async function confirmEpisode(episode: AnimeLibraryEpisode, right: boolean) {
    setAssigning(true);
    try {
      await api.confirmAnimeEpisodes([episode.path], right);
      toast.success(right ? episode.name + ' stays as it is filed' : 'The episode check decides ' + episode.name + ' again');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setAssigning(false);
    }
  }

  /** A file the check found to be another episode becomes that one. */
  async function refile(show: AnimeLibraryShow, episode: AnimeLibraryEpisode, anilistId: number, number: number, title: string) {
    setAssigning(true);
    try {
      const result = await api.assignFiles(show.key, [episode.path], anilistId, [number]);
      toast.success(episode.name + ' is now ' + title + ' episode ' + number + (result.moved ? ' · moved into place' : ''));
      await reopen((card) => card.key === show.key, (card) => card.anilist_id === anilistId || Boolean(card.anilist_ids?.includes(anilistId)));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setAssigning(false);
    }
  }

  async function unmatch(show: AnimeLibraryShow, rows: AnimeLibraryEpisode[]) {
    if (!rows.length) return;
    setAssigning(true);
    try {
      const result = await api.assignFiles(show.key, rows.map((row) => row.path), null);
      const count = result.unmatched ?? rows.length;
      toast.success(count + ' file' + (count === 1 ? '' : 's') + ' unmatched; they are on an Unmatched card now');
      clearChecked();
      const folders = new Set(show.folders ?? []);
      await reopen(
        (card) => card.key === show.key,
        (card) => isUnmatched(card) && (card.folders ?? []).some((folder) => folders.has(folder)),
      );
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setAssigning(false);
    }
  }

  async function deleteEpisode() {
    const target = episodeTarget;
    if (!target) return;
    setDeletingEpisode(true);
    try {
      const result = await api.deleteAnimeEpisodes(target.show.key, [target.episode.path]);
      toast.success('Deleted ' + target.episode.name + (result.freed_bytes ? ' (' + formatSize(result.freed_bytes) + ')' : ''));
      setEpisodeTarget(null);
      await openShow(target.show.key);
      void load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDeletingEpisode(false);
    }
  }

  /** The AniList entry a row of the show is an episode of: its own, else its tab's first entry. */
  function entryOf(show: AnimeLibraryShow, episode: AnimeLibraryEpisode): number | null {
    if (episode.anilist_id != null) return episode.anilist_id;
    if (episode.season_number == null) return null;
    return show.season_anilist?.[String(episode.season_number)]?.[0]?.anilist_id ?? null;
  }

  async function searchMissing(show: AnimeLibraryShow, episode: AnimeLibraryEpisode, q?: string) {
    const anilistId = entryOf(show, episode);
    // The entry's own episode number: in a season made of several entries the
    // row's number counts on from the entries before it.
    const number = episode.anilist_episode ?? episode.episode;
    if (anilistId == null || number == null) return;
    setSearchTarget({ show, episode });
    setSearching(true);
    setResults([]);
    try {
      const result = await api.animeEpisodeSearch(anilistId, number, q);
      setResults(result.items);
      setSearchQuery(q ?? result.queries[0] ?? '');
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSearching(false);
    }
  }

  async function downloadMissing(entry: AnimeEntry) {
    const anilistId = searchTarget ? entryOf(searchTarget.show, searchTarget.episode) : null;
    if (!searchTarget || anilistId == null) return;
    setDownloading(entry.info_hash);
    try {
      // A missing episode of a show in the library: straight into it, as the automation's go.
      await api.animeDownload(entry, anilistId, 'show', searchTarget.episode.episode, true);
      toast.success('Episode added to the Anime queue');
      setSearchTarget(null);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setDownloading(null);
    }
  }

  async function openMissing(show: AnimeLibraryShow) {
    setMissingFor(show);
    setMissingReleases(null);
    setMissingPicked(new Set());
    try {
      const result = await api.missingReleases(show.key);
      setMissingReleases(result.items);
      setMissingPicked(new Set(result.items.map((item) => item.info_hash)));
    } catch (error: any) {
      toast.error(error.message);
      setMissingFor(null);
    }
  }

  /** Only now is anything downloaded: the ticked releases, straight into the library. */
  async function downloadMissingReleases() {
    const show = missingFor;
    if (!show || !missingReleases) return;
    const hashes = missingReleases.map((item) => item.info_hash).filter((hash) => missingPicked.has(hash));
    if (!hashes.length) return;
    setMissingQueuing(true);
    try {
      const result = await api.downloadMissingReleases(show.key, hashes);
      toast.success(
        'Queued ' + result.queued + ' download' + (result.queued === 1 ? '' : 's')
        + ' for ' + result.episodes + ' episode' + (result.episodes === 1 ? '' : 's')
        + (result.duplicates ? ' · ' + result.duplicates + ' already queued' : ''),
      );
      setMissingFor(null);
      await openShow(show.key);
      void load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setMissingQueuing(false);
    }
  }

  async function openBulk(kind: AnimeBulkKind) {
    setBulkKind(kind);
    setBulkShows(null);
    setBulkPicked(new Set());
    try {
      const result = await api.animeBulkPlan(kind);
      setBulkShows(result.shows);
      setBulkPicked(new Set(result.shows.map((row) => row.key)));
      if (kind === 'upgrade') void loadBulkPlans(result.shows);
    } catch (error: any) {
      toast.error(error.message);
      setBulkKind(null);
    }
  }

  /** Each show's releases and the files they replace: as the last look found them, else looked up again, two at a time. */
  async function loadBulkPlans(rows: AnimeBulkShow[]) {
    const known = Object.fromEntries(rows.map((row) => [row.key, row.plans ?? null]));
    setBulkPlans(known);
    setBulkPlanPicked(new Set(rows.flatMap((row) => (row.plans ?? []).map((plan) => plan.info_hash))));
    const pending = rows.filter((row) => !row.plans);
    const next = async (): Promise<void> => {
      const row = pending.shift();
      if (!row) return;
      try {
        const result = await api.previewHevcUpgrade(row.anilist_ids);
        setBulkPlans((current) => ({ ...current, [row.key]: result.plans }));
        setBulkPlanPicked((current) => new Set([...current, ...result.plans.map((plan) => plan.info_hash)]));
      } catch {
        setBulkPlans((current) => ({ ...current, [row.key]: [] }));
      }
      return next();
    };
    await Promise.all([next(), next()]);
  }

  /** Only now is anything queued: every ticked show's, in the background; the Dashboard shows how far it is. */
  async function runBulk() {
    const kind = bulkKind;
    if (!kind || !bulkShows) return;
    const allPlans = Object.values(bulkPlans).flatMap((plans) => plans ?? []);
    const keys = kind === 'upgrade'
      ? bulkShows.filter((row) => (bulkPlans[row.key] ?? []).some((plan) => bulkPlanPicked.has(plan.info_hash))).map((row) => row.key)
      : bulkShows.filter((row) => bulkPicked.has(row.key)).map((row) => row.key);
    if (!keys.length) return;
    setBulkKind(null);
    setBulkRunning(kind);
    const shows = keys.length + ' show' + (keys.length === 1 ? '' : 's');
    toast.info((kind === 'upgrade' ? 'Upgrading ' : 'Downloading the available episodes of ') + shows + ' in the background…');
    try {
      const result = await api.runAnimeBulk(kind, keys, kind === 'upgrade' ? approvedOf(allPlans, bulkPlanPicked) : undefined);
      const failed = result.failed.length ? ' · failed for ' + result.failed.join(', ') : '';
      if (kind === 'upgrade') {
        const message = 'Queued ' + result.queued + ' HEVC replacement' + (result.queued === 1 ? '' : 's')
          + ' in ' + (result.downloads ?? 0) + ' download' + (result.downloads === 1 ? '' : 's')
          + (result.no_replacement ? ' · ' + result.no_replacement + ' without a replacement now' : '')
          + (result.changed ? ' · ' + result.changed + ' left out, their files changed' : '') + failed;
        if (result.queued) toast.success(message);
        else toast.info(message);
      } else {
        toast.success(
          'Queued ' + result.queued + ' download' + (result.queued === 1 ? '' : 's')
          + ' for ' + (result.episodes ?? 0) + ' episode' + (result.episodes === 1 ? '' : 's')
          + (result.duplicates ? ' · ' + result.duplicates + ' already queued' : '') + failed,
        );
      }
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBulkRunning(null);
    }
  }

  useEffect(() => { void load(); }, []);
  // The list follows the typing a moment behind, so the search field never waits for it.
  const needle = useDeferredValue(query.toLocaleLowerCase());
  const visible = useMemo(() => {
    const matched = shows.filter((show) =>
      (!onlyBlacklisted || show.blacklisted)
      && (!onlyUnmatched || isUnmatched(show))
      && show.title.toLocaleLowerCase().includes(needle));
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
  }, [shows, needle, sort, onlyBlacklisted, onlyUnmatched]);
  const blacklistedCount = useMemo(() => shows.filter((show) => show.blacklisted).length, [shows]);
  // What the bulk buttons offer: the shows' "Upgrade for N" and "N available", blacklisted ones left out.
  const bulkCounts = useMemo(() => shows.reduce((acc, show) => (show.blacklisted || isUnmatched(show) ? acc : {
    upgrade: acc.upgrade + (show.upgrade_available_count ?? 0),
    missing: acc.missing + (show.missing_available_count ?? 0),
  }), { upgrade: 0, missing: 0 }), [shows]);
  const bulkTotal = bulkKind === 'upgrade'
    ? Object.values(bulkPlans).flatMap((plans) => plans ?? []).filter((plan) => bulkPlanPicked.has(plan.info_hash)).reduce((sum, plan) => sum + plan.replaces.length, 0)
    : (bulkShows ?? []).filter((row) => bulkPicked.has(row.key)).reduce((sum, row) => sum + row.episodes.length, 0);
  const bulkPickedShows = bulkKind === 'upgrade'
    ? (bulkShows ?? []).filter((row) => (bulkPlans[row.key] ?? []).some((plan) => bulkPlanPicked.has(plan.info_hash))).length
    : bulkPicked.size;
  const unmatchedCount = useMemo(() => shows.filter((show) => isUnmatched(show)).length, [shows]);
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

  // The open show lives in the address (?show=<key>), so Back closes it and a
  // show can be linked to. Opened from the list it was pushed, and closing it
  // goes back there; opened by a link, closing replaces it with the list.
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const wide = useWide();
  const split = view === 'split' && wide;
  const showKey = params.get('show');

  function goShow(key: string) {
    if (key === showKey) return;
    const next = new URLSearchParams(params);
    next.set('show', key);
    // Clicking through shows in the split view is not a trail to walk back.
    setParams(next, { replace: split && Boolean(showKey), state: { fromList: true } });
    void openShow(key, true);
  }

  function closeShow() {
    if ((location.state as { fromList?: boolean } | null)?.fromList) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(params);
    next.delete('show');
    setParams(next, { replace: true });
  }

  // The show open in the split view is opened again on coming back from another tab.
  useRememberedOpen('anime', showKey, split, (key) => {
    const next = new URLSearchParams(params);
    next.set('show', key);
    setParams(next, { replace: true });
  });

  useEffect(() => {
    if (showKey) {
      if (showKey !== selected) void openShow(showKey);
    } else if (selected) {
      setSelected(null);
      setSelectedShow(null);
      setTab(null);
      clearChecked();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showKey]);

  const active = selectedShow;
  const seasons = active ? Array.from(new Set(active.episodes.map((episode) => episode.season_number))).sort((a, b) => (a ?? -1) - (b ?? -1)) : [];
  // Another card opens on its first tab; a refreshed one stays where it was, while that tab is still there.
  const openTab = tab && tab.key === active?.key && seasons.map(String).includes(tab.value) ? tab.value : String(seasons[0]);
  // The open card's ticked files, across its tabs.
  const activeChecked = checkedRows(active);
  const checkedPaths = new Set(activeChecked.map((entry) => entry.path));
  // Read absolutely, a season tab is one of the user's own arc folders: named after it.
  const seasonLabel = (season: number | null) => {
    if (season === null) return 'Other files';
    // An AniList card's tabs are its entries or its season folders, each named.
    if (active?.numbering === 'anilist') {
      const label = active.episodes.find((episode) => episode.season_number === season)?.season;
      if (label) return label;
    }
    return season === 0 ? 'Specials' : 'Season ' + season;
  };

  // One show: its own page (?show=<key>), or the right half of the split view.
  const showDetail = (
    <>
      {/* Loading another show: its shape. Reloading the open one keeps it on screen. */}
      {detailLoading && !active && <DrawerSkeleton />}
      {active && (
        <>
          <div className='flex flex-col gap-2'>
            <div className='flex items-start gap-4'>
              <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
              <div className='flex min-w-0 flex-col gap-2'>
                <h2 className='font-heading text-[22px] leading-[1.1] font-light tracking-[-0.02em] text-balance text-foreground'>
                  {active.title}{active.year ? ' (' + active.year + ')' : ''}
                  {(active.anilist_id || active.collection_id != null) && (
                    // The AniList link and the collection's Rename, kept together when the title wraps.
                    <span className='ml-2 inline-flex items-center gap-0.5 whitespace-nowrap align-[0.1em]'>
                      {active.anilist_id && (
                        <a
                          href={anilistUrl(active.anilist_id)}
                          target='_blank'
                          rel='noreferrer'
                          className='inline-flex size-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-ring'
                          aria-label='Open on AniList'
                          title={(active.anilist_ids?.length ?? 0) > 1 ? 'On AniList: the first of its ' + active.anilist_ids!.length + ' entries; each season links its own' : 'On AniList'}
                        >
                          <ExternalLink className='size-4' />
                        </a>
                      )}
                      {active.collection_id != null && (
                        <button
                          type='button'
                          onClick={() => openRename(active)}
                          className='inline-flex size-6 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-ring'
                          aria-label='Rename collection'
                          title={active.collection_name_custom
                            ? 'Renamed by hand: rename it again, or give it back AniList\'s name'
                            : 'Give the collection a name of your own, for its folder and in Jellyfin'}
                        >
                          <Pencil className='size-4' />
                        </button>
                      )}
                    </span>
                  )}
                </h2>
                {active.source_title && active.source_title !== active.title && (
                  <p className='font-mono text-[0.7rem] leading-tight text-muted-foreground' title='The name Erai-raws publishes this show under'>
                    {active.source_title}
                  </p>
                )}
                <p className='text-xs text-muted-foreground'>{active.downloaded_count}/{active.total_count} episodes · {active.season_count} seasons · {formatSize(active.size)}</p>
                <div className='flex flex-wrap items-center gap-2'>
                  {(active.collection_id != null || active.anilist_id != null) && (
                    <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => setGraphFor(active.collection_id != null ? { collection: active.collection_id } : { entry: active.anilist_id! })}
                      title='How this collection&apos;s entries relate on AniList: split, merge, pull entries in'
                    >
                      <Network data-icon='inline-start' /> Relations
                    </Button>
                  )}
                  {(active.collection_entries?.length ?? 0) > 1 && active.collection_id != null && (
                    <Select<string>
                      value={String(active.anilist_id ?? '')}
                      onValueChange={(value) => { if (value !== null) void setTitle(active, Number(value)); }}
                      disabled={titling}
                      items={active.collection_entries!.map((entry) => ({
                        value: String(entry.anilist_id),
                        label: 'Shown as: ' + entry.title + (entry.season ? ' · ' + entry.season : '') + (entry.owned ? '' : ' (not downloaded)'),
                      }))}
                    >
                      <SelectTrigger
                        size='sm'
                        className='w-72'
                        aria-label='Which entry the collection is shown as'
                        title={active.collection_name_custom
                          ? 'The entry this collection is pictured after; its name is your own'
                          : 'The entry this collection is named and pictured after, here and in Jellyfin'}
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {active.collection_entries!.map((entry) => (
                            <SelectItem key={entry.anilist_id} value={String(entry.anilist_id)}>
                              Shown as: {entry.title}{entry.season ? ' · ' + entry.season : ''}{entry.owned ? '' : ' (not downloaded)'}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  )}
                  {active.collection_id != null && (
                    <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => setArtworkFor(active)}
                      title='Poster, backdrop, thumb, logo, banner and season posters in Jellyfin: from AniList and TMDB, or any picture'
                    >
                      <Images data-icon='inline-start' /> Artwork
                    </Button>
                  )}
                  {active.collection_id != null && (
                    <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => setArcsFor(active)}
                      title='Split a long season into named arcs, a season each: Bleach as its 16 arcs'
                    >
                      <Split data-icon='inline-start' /> Seasons
                    </Button>
                  )}
                  {isUnmatched(active) && (
                    <Button
                      size='sm'
                      onClick={() => setPickFor({ mode: 'match', show: active })}
                      title='Pick the AniList entry these files are: each becomes its episode by the number in its name, and is moved into place'
                    >
                      <Link2 data-icon='inline-start' /> Match on AniList
                    </Button>
                  )}
                  {/* Only once the background search found one: the search itself
                      is not a button but a pass on the dashboard. */}
                  {(active.upgrade_available_count ?? 0) > 0 && (
                    <Button
                      size='sm'
                      variant='secondary'
                      disabled={!active.anilist_ids?.length || upgrading === active.key}
                      onClick={() => void upgradeToHevc(active)}
                      title='See the HEVC versions found (Erai-raws, with German subtitles) and the AVC files they would replace; nothing is downloaded until you approve'
                    >
                      <FileVideo data-icon='inline-start' />
                      {upgrading === active.key ? 'Queueing…' : `Upgrade ${active.upgrade_available_count} to HEVC`}
                    </Button>
                  )}
                  {/* Likewise only once found; it opens the list, it downloads nothing. */}
                  {(active.missing_available_count ?? 0) > 0 && (
                    <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => void openMissing(active)}
                      title='Releases with German subtitles were found for missing episodes: choose which to download'
                    >
                      <Download data-icon='inline-start' />
                      Download {active.missing_available_count} remaining episode{active.missing_available_count === 1 ? '' : 's'}
                    </Button>
                  )}
                  {/* Straight on the header, not in a menu: a dialog opened from a
                      popover item was dismissed by the popover closing, so
                      "Remove and blacklist" never asked and never removed. */}
                  <Button
                    size='sm'
                    variant='secondary'
                    onClick={() => setRemoveTarget({ show: active, blacklist: false })}
                    title='Delete this show from disk and stop following it, without blacklisting: search can still find new releases'
                  >
                    <Trash2 data-icon='inline-start' /> Remove
                  </Button>
                  <Button
                    size='sm'
                    variant='destructive'
                    onClick={() => setRemoveTarget({ show: active, blacklist: true })}
                    title='Delete this show from disk and never download it again'
                  >
                    <Trash2 data-icon='inline-start' /> Remove and blacklist
                  </Button>
                </div>
              </div>
            </div>
          </div>
          {/* The episode check runs in the background; only what it found wrong is said. */}
          {!isUnmatched(active) && (active.verify_wrong ?? 0) > 0 && (
            <p className='text-xs text-destructive'>
              {active.verify_wrong} file{active.verify_wrong === 1 ? ' is' : 's are'} another episode than {active.verify_wrong === 1 ? 'it is' : 'they are'} filed as: marked below.
            </p>
          )}
          <Tabs value={openTab} onValueChange={(value) => setTab({ key: active.key, value: String(value) })} className='gap-3'>
            <TabsList className='h-auto max-w-full flex-wrap'>
              {seasons.map((season) => <TabsTab key={String(season)} value={String(season)}>{seasonLabel(season)}</TabsTab>)}
            </TabsList>
            {seasons.map((season) => (
              <TabsPanel key={String(season)} value={String(season)} className='flex flex-col gap-3'>
                {(() => {
                  // The season's AniList entries, each with its pencil (another entry for its files);
                  // a card of one entry whose tabs name none has that entry.
                  const named = season !== null ? active.season_anilist?.[String(season)] ?? [] : [];
                  const single = season !== null && !named.length ? tabEntry(active, String(season)) : null;
                  const entries = named.length ? named : single ? [single] : [];
                  if (!entries.length) return null;
                  // A collection's seasons can be named by hand (not its specials, nor an entry split into arcs).
                  const opener = entries[0];
                  const nameable = active.collection_id != null && seasonLabel(season) !== 'Specials';
                  return (
                    <p className='flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground'>
                      <span>AniList:</span>
                      {entries.map((entry) => (
                        <span key={entry.anilist_id} className='inline-flex items-center gap-0.5'>
                          <a
                            href={anilistUrl(entry.anilist_id)}
                            target='_blank'
                            rel='noreferrer'
                            className='inline-flex items-center gap-1 text-foreground underline-offset-4 hover:underline'
                          >
                            {entry.title} <ExternalLink className='size-3' />
                          </a>
                          {!isUnmatched(active) && (
                            <Button
                              size='icon-sm'
                              variant='ghost'
                              className='size-6 text-muted-foreground'
                              aria-label={'Change the AniList entry of ' + entry.title}
                              title='Pick another AniList entry for these files; they move with it'
                              onClick={() => setPickFor({ mode: 'rematch', show: active, from: entry })}
                            >
                              <Pencil className='size-3' />
                            </Button>
                          )}
                        </span>
                      ))}
                      {nameable && (
                        <Button
                          size='sm'
                          variant='ghost'
                          className='h-6 px-2 text-xs text-muted-foreground'
                          onClick={() => openSeasonRename(active, season!, opener)}
                          title={'Name this season yourself: its tab here and its season in Jellyfin' + (active.season_names?.[String(opener.anilist_id)] ? ' (it has a name of your own now)' : '')}
                        >
                          <TextCursorInput data-icon='inline-start' /> Rename season
                        </Button>
                      )}
                    </p>
                  );
                })()}
                <Table variant='plain' dense containerProps={{ className: 'shrink-0' }}>
                    <TableHeader><TableRow><TableHead className='w-7 pr-2'>{(() => {
                      // "Select all in this tab": every row of it that is a file.
                      const files = active.episodes.filter((entry) => entry.season_number === season && hasFile(entry)).map((entry) => entry.path);
                      const ticked = files.filter((path) => checkedPaths.has(path)).length;
                      return (
                        <Checkbox
                          // A real button: the drawer's swipe-to-close leaves buttons alone, but takes
                          // the pointer from Base UI's span checkbox, so its click never lands.
                          nativeButton
                          render={<button type='button' />}
                          className='align-middle'
                          aria-label='Select every file in this tab'
                          title='Select every file in this tab'
                          disabled={files.length === 0}
                          checked={files.length > 0 && ticked === files.length}
                          indeterminate={ticked > 0 && ticked < files.length}
                          onCheckedChange={(on) => toggleChecked(active, files, on)}
                        />
                      );
                    })()}</TableHead><TableHead className='pr-3'>Ep</TableHead><TableHead className='w-full'>Episode / File</TableHead><TableHead>Encode · German</TableHead><TableHead numeric>Size</TableHead><TableHead numeric>State</TableHead></TableRow></TableHeader>
                    <TableBody>{(() => {
                      // A season made of several AniList entries: each entry's episodes under a row naming it.
                      const tabRows = active.episodes.filter((entry) => entry.season_number === season);
                      const parts = active.season_anilist?.[String(season)] ?? [];
                      const grouped = parts.length > 1;
                      return tabRows.map((entry, index) => {
                        const starts = grouped && entry.anilist_id != null && entry.anilist_id !== tabRows[index - 1]?.anilist_id;
                        const own = starts ? tabRows.filter((row) => row.anilist_id === entry.anilist_id) : [];
                        const part = starts ? parts.find((item) => item.anilist_id === entry.anilist_id) : undefined;
                        const numbers = own.map((row) => row.episode ?? 0).filter(Boolean);
                        return (
                      <Fragment key={entry.path || String(entry.season_number) + ':' + entry.episode}>
                      {starts && (
                        <TableRow className='bg-control-surface/60 hover:bg-control-surface/60'>
                          <TableCell colSpan={6} className='py-1.5'>
                            <div className='flex flex-wrap items-center gap-x-2 gap-y-1 text-xs'>
                              <span className='font-medium text-foreground'>{part?.title ?? 'AniList ' + entry.anilist_id}</span>
                              {numbers.length > 0 && (
                                <span className='font-mono text-muted-foreground'>
                                  ep {Math.min(...numbers)}{numbers.length > 1 ? '–' + Math.max(...numbers) : ''}
                                </span>
                              )}
                              <span className='text-muted-foreground'>
                                {own.filter((row) => !row.missing).length}/{own.length} on disk
                              </span>
                              <a
                                href={anilistUrl(entry.anilist_id!)}
                                target='_blank'
                                rel='noreferrer'
                                className='inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground'
                                title='This entry on AniList'
                              >
                                <ExternalLink className='size-3' /> AniList
                              </a>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                      <TableRow data-selected={hasFile(entry) && checkedPaths.has(entry.path) ? '' : undefined}>
                        <TableCell className='w-7 pr-2'>
                          {hasFile(entry) && (
                            <Checkbox
                              nativeButton
                              render={<button type='button' />}
                              className='align-middle'
                              aria-label={'Select ' + entry.name}
                              checked={checkedPaths.has(entry.path)}
                              onCheckedChange={(on) => toggleChecked(active, [entry.path], on)}
                            />
                          )}
                        </TableCell>
                        <TableCell className='pr-3 font-mono text-xs tabular-nums text-foreground'>{entry.episode ?? '—'}</TableCell>
                        <TableCell className='max-w-0 text-xs text-muted-foreground'>{entry.episode_title && <p className='truncate text-sm text-foreground' title={entry.episode_title}>{entry.episode_title}</p>}{!entry.missing && <p className='truncate font-mono text-[0.68rem]' title={entry.name}>{entry.name}</p>}{entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}{!entry.missing && <VerifyNote show={active} episode={entry} busy={assigning} onRefile={refile} onDelete={() => setEpisodeTarget({ show: active, episode: entry })} onConfirm={confirmEpisode} />}</TableCell>
                        <TableCell>
                          <div className='flex flex-wrap items-center gap-1.5'>
                            {entry.codec ? <StatusTag variant={entry.codec === 'hevc' ? 'success' : 'warning'}>{entry.codec.toUpperCase()}</StatusTag> : <span className='text-xs text-muted-foreground'>—</span>}
                            {entry.german_dub && <StatusTag variant='transfer' title='Carries a German dub — never replaced by an HEVC upgrade'>GER dub</StatusTag>}
                            {!entry.missing && entry.german_subs === true && <StatusTag variant='muted' title='Carries German subtitles'>GER subs</StatusTag>}
                            {!entry.missing && entry.german_subs === false && !entry.german_dub && <StatusTag variant='warning' title='Neither German subtitles nor a German dub'>No German</StatusTag>}
                          </div>
                        </TableCell>
                        <TableCell numeric className='text-xs text-foreground'>{entry.missing ? '—' : formatSize(entry.size)}</TableCell>
                        <TableCell className='text-right'>{entry.missing ? <Button size='icon-sm' variant='secondary' disabled={entryOf(active, entry) == null || entry.tba} aria-label='Search for a release' title={entry.tba ? 'Not aired yet' : 'Search for a release of this AniList episode'} onClick={() => void searchMissing(active, entry)}><Search /></Button> : (
                          <div className='flex items-center justify-end gap-1.5'>
                            {entry.staged ? (
                              <Button size='sm' variant='secondary' onClick={() => void transfer(entry)} disabled={transferring === entry.path || entry.transfer_status === 'transferring'}><ArrowRight data-icon='inline-start' /> {entry.transfer_status === 'transferring' ? 'Transferring' : 'Transfer'}</Button>
                            ) : <StatusTag variant='success'>In library</StatusTag>}
                            <VerifyTag verify={entry.verify} />
                            <Button
                              size='icon-sm'
                              variant='ghost'
                              className='size-7 text-muted-foreground hover:text-destructive'
                              disabled={entry.transfer_status === 'transferring'}
                              aria-label={'Delete ' + entry.name}
                              title='Delete this episode from disk'
                              onClick={() => setEpisodeTarget({ show: active, episode: entry })}
                            >
                              <Trash2 />
                            </Button>
                          </div>
                        )}</TableCell>
                      </TableRow>
                      </Fragment>
                        );
                      });
                    })()}</TableBody>
                </Table>
              </TabsPanel>
            ))}
            {/* Under the rows and kept in sight at the bottom, so ticking a file never moves them. */}
            {activeChecked.length > 0 && (
              <div className='sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0'>
                <span className='text-xs text-muted-foreground'>
                  <span className='font-mono tabular-nums text-foreground'>{activeChecked.length}</span> file{activeChecked.length === 1 ? '' : 's'} selected
                </span>
                <Button
                  size='sm'
                  disabled={assigning}
                  onClick={() => openAssign(active, activeChecked)}
                  title='Make the selected files episodes of one AniList entry, numbered as you set; they are moved into place'
                >
                  <Link2 data-icon='inline-start' /> Match to AniList entry…
                </Button>
                {!isUnmatched(active) && (
                  <Button
                    size='sm'
                    variant='secondary'
                    disabled={assigning}
                    onClick={() => setMagnetFor(activeChecked.map((entry) => entry.path))}
                    title='Replace the selected episodes with the torrent of a magnet link'
                  >
                    <Magnet data-icon='inline-start' /> Replace with magnet link…
                  </Button>
                )}
                {!isUnmatched(active) && (
                  <Button
                    size='sm'
                    variant='secondary'
                    disabled={assigning}
                    onClick={() => void unmatch(active, activeChecked)}
                    title='Take the selected files off their AniList entry: they move to an Unmatched card'
                  >
                    <Unlink data-icon='inline-start' /> {assigning ? 'Working…' : 'Unmatch'}
                  </Button>
                )}
                <Button size='sm' variant='ghost' onClick={clearChecked} className='ml-auto'>
                  <X data-icon='inline-start' /> Clear
                </Button>
              </div>
            )}
          </Tabs>
        </>
      )}
    </>
  );

  const showTable = (
      // Plain, across the page's whole width (its rows' padding lines up with the gutter), and
      // fitted to the room it has: as it narrows -- the split view's divider dragged -- the lesser
      // columns glide one by one into the series cell, seasons first, onto the line under the
      // title, after the year. Its scrolling body paints the ground, as the app's scrolling area:
      // opaque, it scrolls on the compositor (App.tsx).
      <Table fit variant='plain' className='page-ground' containerProps={{ className: split ? 'h-full' : '-mx-(--page-gutter) w-auto min-h-0 flex-1' }}>
          <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
            <TableRow>
              {/* Fixed widths: the columns never shift with what the rows hold, and measuring is cheap. */}
              <TableHead width='auto'><span className='sr-only'>Poster</span></TableHead>
              <SortHeader label='Series' column='title' sort={sort} onSort={toggleSort} titleColumn />
              {/* Seasons to the right, beside Size: a number or two left-aligned under its label sat
                  140px from the size. 5rem: the widest size, "999.99 GiB". */}
              <SortHeader label='Seasons' column='seasons' sort={sort} onSort={toggleSort} align='right' inline priority={1} width='5.5rem' />
              <SortHeader label='Episodes' column='episodes' sort={sort} onSort={toggleSort} inline priority={4} width='11rem' />
              <SortHeader label='Size' column='size' sort={sort} onSort={toggleSort} align='right' inline priority={2} width='5rem' />
              {/* 13rem: the widest state ("Complete · 5 without German") on one line; more wraps. */}
              <SortHeader label='State' column='state' sort={sort} onSort={toggleSort} inline priority={3} width='13rem' />
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((show) => (
              <TableRow
                key={show.key}
                tabIndex={0}
                onClick={() => goShow(show.key)}
                aria-current={split && show.key === selected ? 'true' : undefined}
                onPointerEnter={() => prefetchShow(show.key)}
                onPointerLeave={stopPrefetch}
                onFocus={() => prefetchShow(show.key)}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); goShow(show.key); } }}
                className='data-row cursor-pointer aria-current:bg-control-surface focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-control-focus'
              >
                <TableCell><AnimePoster url={show.poster_url} title={show.title} className='w-8 shrink-0 rounded' /></TableCell>
                <TableCell className='[&>[data-slot=table-title-text]]:min-w-0 [&>[data-slot=table-title-text]]:flex-1'>
                  {/* The title takes the room its column has and is cut off only where that is too
                      little; the whole title on hover. To the table it needs about 24 letters
                      (`contain`, `min-w`): once its column would be narrower, the next column
                      glides in under it. The year below, where the columns that step out go. */}
                  <span className='inline-block w-full min-w-[24ch] truncate align-top font-medium text-foreground [contain:inline-size]' title={show.title}>{show.title}</span>
                  {show.year ? (
                    <>
                      <br />
                      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.year}</span>
                    </>
                  ) : null}
                </TableCell>
                {/* Moved in, a cell says what it is: "2 seasons". */}
                <TableCell className='font-mono text-xs tabular-nums text-muted-foreground in-data-[mode=inline]:text-[0.68rem]'>
                  {show.season_count}<span className='hidden in-data-[mode=inline]:inline'>&nbsp;season{show.season_count === 1 ? '' : 's'}</span>
                </TableCell>
                <TableCell><Progress show={show} /></TableCell>
                <TableCell numeric className='text-xs text-foreground in-data-[mode=inline]:font-mono in-data-[mode=inline]:text-[0.68rem]'>{formatSize(show.size)}</TableCell>
                <TableCell><StateTags show={show} /></TableCell>
              </TableRow>
            ))}
          </TableBody>
      </Table>
  );

  // Every dialog, whichever way the page is laid out.
  const dialogs = (
    <>
      <Dialog open={Boolean(removeTarget)} onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}>
        <DialogContent size='sm'>
          <DialogHeader>
            <DialogTitle>Remove {removeTarget?.show.title}?</DialogTitle>
            <DialogDescription>
              This deletes {removeTarget?.show.downloaded_count ?? 0} episode{removeTarget?.show.downloaded_count === 1 ? '' : 's'}
              {removeTarget ? ' (' + formatSize(removeTarget.show.size) + ')' : ''} from disk and removes its torrents.
              {removeTarget?.blacklist
                ? ' It also blacklists the show so no season of it is downloaded again. It moves to the Blacklist tab, where you can restore it.'
                : ' bankai stops following it, but it is not blacklisted: new releases can still be found by search and downloaded.'}
              {' '}The deleted files cannot be recovered.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setRemoveTarget(null)}>Cancel</Button>
            <Button variant='destructive' onClick={() => void removeShow()}>
              <Trash2 data-icon='inline-start' /> {removeTarget?.blacklist ? 'Delete and blacklist' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(episodeTarget)} onOpenChange={(open) => { if (!open && !deletingEpisode) setEpisodeTarget(null); }}>
        <DialogContent size='sm'>
          <DialogHeader>
            <DialogTitle>Delete episode {episodeTarget?.episode.episode ?? ''}?</DialogTitle>
            <DialogDescription>
              This deletes <span className='break-all font-mono text-xs text-foreground'>{episodeTarget?.episode.name}</span>
              {episodeTarget ? ' (' + formatSize(episodeTarget.episode.size) + ')' : ''} and its subtitle files from disk.
              The show is not blacklisted; the episode shows as missing afterwards. The file cannot be recovered.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setEpisodeTarget(null)} disabled={deletingEpisode}>Cancel</Button>
            <Button variant='destructive' onClick={() => void deleteEpisode()} disabled={deletingEpisode}>
              <Trash2 data-icon='inline-start' /> {deletingEpisode ? 'Deleting…' : 'Delete episode'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={Boolean(searchTarget)} onOpenChange={(open) => { if (!open) setSearchTarget(null); }}>
        <DialogContent className='h-[90dvh] max-h-none w-[92vw] max-w-none shrink-0'>
          <DialogHeader>
            <DialogTitle>Search {searchTarget ? seasonLabel(searchTarget.episode.season_number) : ''} — episode {searchTarget?.episode.episode ?? ''}</DialogTitle>
            <DialogDescription>Choose a release of this AniList episode. Every result is checked to be this entry's episode.</DialogDescription>
          </DialogHeader>
          <form className='flex gap-2' onSubmit={(event) => { event.preventDefault(); if (searchTarget) void searchMissing(searchTarget.show, searchTarget.episode, searchQuery); }}>
            <Input aria-label='Episode release search' value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} className='flex-1' />
            <Button type='submit' disabled={searching}><Search data-icon='inline-start' /> Search</Button>
          </form>
          <div className='flex flex-wrap items-center gap-2'>
            <Toggle size='sm' pressed={only1080} onPressedChange={setOnly1080}>1080p</Toggle>
            <Toggle size='sm' pressed={onlyHevc} onPressedChange={setOnlyHevc}>HEVC</Toggle>
            {!searching && shownResults.length > 0 && results.length > shownResults.length && (
              <span className='text-xs text-muted-foreground'>
                {results.length - shownResults.length} of {results.length} hidden by these filters
              </span>
            )}
          </div>
          <DialogBody>
            {searching ? <div className='flex h-40 items-center justify-center'><Spinner label='Searching' /></div> : shownResults.length ? (
              <Table dense>
                <TableHeader>
                  <TableRow>
                    <TableHead className='w-full'>Release</TableHead>
                    <TableHead>Quality</TableHead>
                    <TableHead>Encode</TableHead>
                    <TableHead numeric>Size</TableHead>
                    <TableHead numeric>Seeds</TableHead>
                    <TableHead>Published</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {shownResults.map((entry) => (
                    <TableRow key={entry.info_hash}>
                      <TableCell>
                        <p className='min-w-64 font-mono text-xs break-words text-foreground'>{entry.title}</p>
                        {entry.publisher && <p className='text-[0.68rem] text-muted-foreground'>{entry.publisher}</p>}
                      </TableCell>
                      <TableCell className='font-mono text-xs whitespace-nowrap'>{entry.quality ?? '—'}</TableCell>
                      <TableCell>{isHevc(entry.title) ? <StatusTag variant='success'>HEVC</StatusTag> : <StatusTag variant='warning'>AVC</StatusTag>}</TableCell>
                      <TableCell numeric>{entry.size}</TableCell>
                      <TableCell numeric>{entry.seeders}</TableCell>
                      <TableCell className='font-mono text-xs whitespace-nowrap text-muted-foreground'>{publishedDay(entry.published_at)}</TableCell>
                      <TableCell>
                        <div className='flex items-center justify-end gap-1.5'>
                          <Button size='icon-sm' variant='ghost' aria-label={'Description of ' + entry.title} nativeButton={false} render={<a href={entry.detail_url} target='_blank' rel='noreferrer' />}><ExternalLink /></Button>
                          <Button size='sm' variant='secondary' disabled={Boolean(downloading)} onClick={() => void downloadMissing(entry)}><Download data-icon='inline-start' /> {downloading === entry.info_hash ? 'Queuing…' : 'Download'}</Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Empty>
                <EmptyMedia><Search /></EmptyMedia>
                <EmptyTitle>{results.length ? 'Nothing passes these filters' : 'No matching releases found'}</EmptyTitle>
                <EmptyDescription>{results.length ? 'Switch off 1080p or HEVC above to see the other ' + results.length + ' release' + (results.length === 1 ? '' : 's') + '.' : 'Try another title above, the way Erai-raws names the show.'}</EmptyDescription>
              </Empty>
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>
      {/* What the background search found for the open show's missing episodes;
          nothing is downloaded until "Download N" is clicked. */}
      <Dialog open={Boolean(missingFor)} onOpenChange={(open) => { if (!open && !missingQueuing) setMissingFor(null); }}>
        <DialogContent size='xl' className='max-h-[90dvh]'>
          <DialogHeader>
            <DialogTitle>Download remaining episodes of {missingFor?.title}</DialogTitle>
            <DialogDescription>
              Releases with German subtitles found for episodes that have aired and are not on disk. Each is checked to be
              this show&apos;s AniList episode; a batch is one download for every episode it covers. They go straight into the library.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {missingReleases === null ? <div className='flex h-40 items-center justify-center'><Spinner label='Loading the releases' /></div> : missingReleases.length ? (
              <Table dense>
                <TableHeader>
                  <TableRow>
                    <TableHead className='w-7 pr-2'>
                      <Checkbox
                        nativeButton
                        render={<button type='button' />}
                        className='align-middle'
                        aria-label='Select every release'
                        checked={missingPicked.size === missingReleases.length}
                        indeterminate={missingPicked.size > 0 && missingPicked.size < missingReleases.length}
                        onCheckedChange={(on) => setMissingPicked(new Set(on ? missingReleases.map((item) => item.info_hash) : []))}
                      />
                    </TableHead>
                    <TableHead className='w-full'>Release</TableHead>
                    <TableHead>Episodes</TableHead>
                    <TableHead>Quality</TableHead>
                    <TableHead>Encode</TableHead>
                    <TableHead numeric>Size</TableHead>
                    <TableHead numeric>Seeds</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {missingReleases.map((item) => (
                    <TableRow key={item.info_hash} data-selected={missingPicked.has(item.info_hash) ? '' : undefined}>
                      <TableCell className='w-7 pr-2'>
                        <Checkbox
                          nativeButton
                          render={<button type='button' />}
                          className='align-middle'
                          aria-label={'Download ' + item.title}
                          checked={missingPicked.has(item.info_hash)}
                          onCheckedChange={(on) => setMissingPicked((current) => {
                            const next = new Set(current);
                            if (on) next.add(item.info_hash);
                            else next.delete(item.info_hash);
                            return next;
                          })}
                        />
                      </TableCell>
                      <TableCell>
                        <a href={item.detail_url} target='_blank' rel='noreferrer' className='block min-w-64 font-mono text-xs break-words text-foreground underline-offset-4 hover:underline' title='Its page on Nyaa'>{item.title}</a>
                      </TableCell>
                      <TableCell className='text-xs whitespace-nowrap'>
                        {coveredEpisodes(item).map(([tab, numbers]) => (
                          <p key={tab}><span className='text-muted-foreground'>{tab}</span> <span className='font-mono tabular-nums text-foreground'>{numbers}</span></p>
                        ))}
                        {item.batch && <p className='text-[0.68rem] text-muted-foreground'>Batch</p>}
                      </TableCell>
                      <TableCell className='font-mono text-xs whitespace-nowrap'>{item.quality ?? '—'}</TableCell>
                      <TableCell>{item.hevc ? <StatusTag variant='success'>HEVC</StatusTag> : <StatusTag variant='warning'>AVC</StatusTag>}</TableCell>
                      <TableCell numeric className='text-xs whitespace-nowrap'>{item.size_bytes ? formatSize(item.size_bytes) : '—'}</TableCell>
                      <TableCell numeric className='text-xs'>{item.seeders}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Empty>
                <EmptyMedia><Download /></EmptyMedia>
                <EmptyTitle>Nothing left to download</EmptyTitle>
                <EmptyDescription>The episodes found are already on their way or in the library.</EmptyDescription>
              </Empty>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setMissingFor(null)} disabled={missingQueuing}>Cancel</Button>
            <Button onClick={() => void downloadMissingReleases()} disabled={missingQueuing || missingPicked.size === 0}>
              <Download data-icon='inline-start' /> {missingQueuing ? 'Queuing…' : 'Download ' + missingPicked.size}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <SeasonArcsDialog
        collectionId={arcsFor?.collection_id ?? null}
        title={arcsFor?.title ?? ''}
        onClose={() => setArcsFor(null)}
        onChanged={() => { void load(); if (selected) void openShow(selected); }}
      />
      <MagnetReplaceDialog
        open={magnetFor !== null}
        count={magnetFor?.length ?? 0}
        what='episode'
        paths={magnetFor ?? []}
        scope='anime'
        onClose={() => setMagnetFor(null)}
        onSubmit={async (magnet, force) => {
          try {
            const result = await api.animeReplaceMagnet(magnetFor ?? [], magnet, force);
            toast.success(`${result.anilist_title}: ${result.episodes.length} episode${result.episodes.length === 1 ? '' : 's'} to be replaced by ${result.queued}`);
            clearChecked();
            return { ok: true };
          } catch (error: any) {
            if (!force && /confirm/i.test(error.message)) return { ok: false, confirm: error.message };
            toast.error(error.message);
            return { ok: false };
          }
        }}
      />
      <ArtworkDialog
        collectionId={artworkFor?.collection_id ?? null}
        title={artworkFor?.title ?? ''}
        onClose={() => setArtworkFor(null)}
        onChanged={() => void load()}
      />
      {/* An upgrade replaces files: what each release is taken for and every file it replaces, approved first. */}
      <Dialog open={upgradeFor !== null} onOpenChange={(open) => { if (!open && !upgrading) setUpgradeFor(null); }}>
        <DialogContent size='xl' className='max-h-[90dvh]'>
          <DialogHeader>
            <DialogTitle>Upgrade {upgradeFor?.show.title ?? ''} to HEVC</DialogTitle>
            <DialogDescription>
              Each release found (Erai-raws, with German subtitles), what it is taken for, and every file it would
              replace. Only the ticked releases are downloaded; the AVC files stay until their replacement lands.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {upgradeFor?.plans == null ? (
              <div className='flex h-40 items-center justify-center'><Spinner label='Looking for HEVC releases' /></div>
            ) : upgradeFor.plans.length ? (
              <HevcUpgradePlans plans={upgradeFor.plans} picked={upgradePicked} onPickedChange={setUpgradePicked} />
            ) : (
              <Empty>
                <EmptyMedia><FileVideo /></EmptyMedia>
                <EmptyTitle>Nothing to upgrade</EmptyTitle>
                <EmptyDescription>{upgradeFor.note}</EmptyDescription>
              </Empty>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setUpgradeFor(null)} disabled={upgrading !== null}>Cancel</Button>
            {(() => {
              const plans = (upgradeFor?.plans ?? []).filter((plan) => upgradePicked.has(plan.info_hash));
              const files = plans.reduce((sum, plan) => sum + plan.replaces.length, 0);
              return (
                <Button onClick={() => void approveUpgrade()} disabled={!plans.length || upgrading !== null}>
                  <FileVideo data-icon='inline-start' />
                  {upgrading ? 'Queueing…' : 'Replace ' + files + ' file' + (files === 1 ? '' : 's') + ' with ' + plans.length + ' download' + (plans.length === 1 ? '' : 's')}
                </Button>
              );
            })()}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Every show's upgrades or available episodes at once; nothing is queued until confirmed. */}
      <Dialog open={bulkKind !== null} onOpenChange={(open) => { if (!open) setBulkKind(null); }}>
        <DialogContent size='xl' className='max-h-[90dvh]'>
          <DialogHeader>
            <DialogTitle>{bulkKind === 'upgrade' ? 'Upgrade all to HEVC' : 'Download all available episodes'}</DialogTitle>
            <DialogDescription>
              {bulkKind === 'upgrade'
                ? 'Each HEVC release (Erai-raws, with German subtitles), what it is taken for, and every file it would replace. Only the ticked releases are queued, and only while their files are still these; the AVC files stay until their replacement lands. German dubs are never replaced.'
                : 'Aired episodes not on disk that a release with German subtitles was found for. They go straight into the library; a batch is one download for every episode it covers.'}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {bulkShows === null ? <div className='flex h-40 items-center justify-center'><Spinner label='Loading the shows' /></div> : bulkKind === 'upgrade' && bulkShows.length ? (
              <div className='flex flex-col gap-6'>
                {bulkShows.map((row) => {
                  const plans = bulkPlans[row.key];
                  return (
                    <section key={row.key} className='flex flex-col gap-2'>
                      <div className='flex items-center gap-2.5'>
                        <AnimePoster url={row.poster_url} title={row.title} className='w-7 shrink-0 rounded' />
                        <span className='truncate font-medium text-foreground' title={row.title}>{row.title}</span>
                      </div>
                      {plans === undefined || plans === null ? (
                        <p className='flex items-center gap-2 text-xs text-muted-foreground'><Spinner size='sm' /> Looking up what it would replace…</p>
                      ) : plans.length ? (
                        <HevcUpgradePlans plans={plans} picked={bulkPlanPicked} onPickedChange={setBulkPlanPicked} />
                      ) : (
                        <p className='text-xs text-muted-foreground'>No release with German subtitles found any more.</p>
                      )}
                    </section>
                  );
                })}
              </div>
            ) : bulkShows.length ? (
              <Table dense>
                <TableHeader>
                  <TableRow>
                    <TableHead className='w-7 pr-2'>
                      <Checkbox
                        nativeButton
                        render={<button type='button' />}
                        className='align-middle'
                        aria-label='Select every show'
                        checked={bulkPicked.size === bulkShows.length}
                        indeterminate={bulkPicked.size > 0 && bulkPicked.size < bulkShows.length}
                        onCheckedChange={(on) => setBulkPicked(new Set(on ? bulkShows.map((row) => row.key) : []))}
                      />
                    </TableHead>
                    <TableHead className='w-full'>Show</TableHead>
                    <TableHead>Episodes</TableHead>
                    <TableHead numeric>Count</TableHead>
                    {bulkKind === 'missing' && <TableHead numeric>Size</TableHead>}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {bulkShows.map((row) => (
                    <TableRow key={row.key} data-selected={bulkPicked.has(row.key) ? '' : undefined}>
                      <TableCell className='w-7 pr-2'>
                        <Checkbox
                          nativeButton
                          render={<button type='button' />}
                          className='align-middle'
                          aria-label={'Include ' + row.title}
                          checked={bulkPicked.has(row.key)}
                          onCheckedChange={(on) => setBulkPicked((current) => {
                            const next = new Set(current);
                            if (on) next.add(row.key);
                            else next.delete(row.key);
                            return next;
                          })}
                        />
                      </TableCell>
                      <TableCell className='max-w-0'>
                        <div className='flex items-center gap-2.5'>
                          <AnimePoster url={row.poster_url} title={row.title} className='w-7 shrink-0 rounded' />
                          <span className='truncate font-medium text-foreground' title={row.title}>{row.title}</span>
                        </div>
                      </TableCell>
                      <TableCell className='text-xs whitespace-nowrap'>
                        {/* The tab's short name: "S1 · <entry title>" repeats the show beside it. */}
                        {episodeRuns(row.episodes).map(([tab, numbers]) => (
                          <p key={tab}><span className='text-muted-foreground' title={tab}>{tab.split(' · ')[0]}</span> <span className='font-mono tabular-nums text-foreground'>{numbers}</span></p>
                        ))}
                      </TableCell>
                      <TableCell numeric className='text-xs whitespace-nowrap'>
                        {row.episodes.length}
                        {bulkKind === 'missing' && row.downloads !== row.episodes.length && (
                          <p className='text-[0.68rem] text-muted-foreground'>{row.downloads} download{row.downloads === 1 ? '' : 's'}</p>
                        )}
                      </TableCell>
                      {bulkKind === 'missing' && <TableCell numeric className='text-xs whitespace-nowrap'>{row.size_bytes ? formatSize(row.size_bytes) : '—'}</TableCell>}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <Empty>
                <EmptyMedia>{bulkKind === 'upgrade' ? <CircleArrowUp /> : <Download />}</EmptyMedia>
                <EmptyTitle>Nothing to {bulkKind === 'upgrade' ? 'upgrade' : 'download'}</EmptyTitle>
                <EmptyDescription>What was found is already on its way or in the library.</EmptyDescription>
              </Empty>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setBulkKind(null)}>Cancel</Button>
            <Button onClick={() => void runBulk()} disabled={!bulkTotal}>
              {bulkKind === 'upgrade' ? <CircleArrowUp data-icon='inline-start' /> : <Download data-icon='inline-start' />}
              {(bulkKind === 'upgrade' ? 'Upgrade ' : 'Download ') + bulkTotal + ' episode' + (bulkTotal === 1 ? '' : 's')
                + ' of ' + bulkPickedShows + ' show' + (bulkPickedShows === 1 ? '' : 's')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={assignFor !== null} onOpenChange={(open) => { if (!open && !assigning) setAssignFor(null); }}>
        <DialogContent size='lg' className='max-h-[90dvh]'>
          <DialogHeader>
            <DialogTitle>Match {assignFor?.rows.length ?? 0} file{assignFor?.rows.length === 1 ? '' : 's'} to an AniList entry</DialogTitle>
            <DialogDescription>
              Each file becomes the episode of the entry you set below and is renamed into place; the entry joins a
              collection if it is in none.
            </DialogDescription>
          </DialogHeader>
          {assignFor && (() => {
            const known = knownEntries(assignFor.show);
            const options = assignEntry && !known.some((entry) => entry.anilist_id === assignEntry.anilist_id) ? [assignEntry, ...known] : known;
            return (
              <DialogBody className='flex flex-col gap-4'>
                <section className='flex flex-col gap-2'>
                  <span className='eyebrow'>Entry</span>
                  {options.length > 0 && (
                    <ul className='flex flex-col gap-1' role='radiogroup' aria-label='AniList entry'>
                      {options.map((entry) => {
                        const on = assignEntry?.anilist_id === entry.anilist_id;
                        return (
                          <li key={entry.anilist_id}>
                            <button
                              type='button'
                              role='radio'
                              aria-checked={on}
                              onClick={() => setAssignEntry(entry)}
                              className={cn(
                                'flex w-full cursor-pointer items-center gap-2.5 rounded-control border px-3 py-2 text-left text-sm transition-colors focus-ring',
                                on ? 'border-foreground/60 bg-control-hover text-foreground' : 'border-field-edge text-foreground-soft hover:bg-control-hover/60',
                              )}
                            >
                              <span className={cn('size-3 shrink-0 rounded-full border', on ? 'border-foreground bg-foreground' : 'border-muted-foreground')} aria-hidden='true' />
                              <span className='min-w-0 flex-1 break-words'>{entry.title}</span>
                              <span className='shrink-0 font-mono text-[0.65rem] text-muted-foreground'>id {entry.anilist_id}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <Button
                    size='sm'
                    variant='secondary'
                    className='self-start'
                    onClick={() => setPickFor({ mode: 'assign', query: assignFor.show.source_title || assignFor.show.title })}
                  >
                    <Search data-icon='inline-start' /> Search AniList…
                  </Button>
                </section>
                <section className='flex flex-col gap-2'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <span className='eyebrow'>Episodes</span>
                    <label className='flex items-center gap-2 text-xs text-muted-foreground'>
                      First episode
                      <Input
                        type='number'
                        min={0}
                        inputMode='numeric'
                        aria-label='First episode'
                        value={assignFirst}
                        onChange={(event) => changeAssignFirst(event.target.value)}
                        size='sm'
                        className='w-20 font-mono tabular-nums'
                      />
                    </label>
                  </div>
                  <p className='text-xs text-muted-foreground'>The files in their order on the card; changing the first episode numbers them all on from it.</p>
                  <ul className='flex flex-col divide-y divide-border/60 rounded-control border border-field-edge'>
                    {assignFor.rows.map((row, index) => (
                      <li key={row.path} className='flex items-center gap-3 px-3 py-2'>
                        <div className='min-w-0 flex-1'>
                          <p className='break-all font-mono text-[0.7rem] text-foreground'>{row.name}</p>
                          {row.anilist_id != null && (
                            <p className='text-[0.65rem] text-muted-foreground'>
                              Now: {known.find((entry) => entry.anilist_id === row.anilist_id)?.title ?? 'AniList ' + row.anilist_id}
                              {row.anilist_episode != null ? ' · episode ' + row.anilist_episode : ''}
                            </p>
                          )}
                        </div>
                        <Input
                          type='number'
                          min={0}
                          inputMode='numeric'
                          aria-label={'Episode of ' + row.name}
                          value={assignEpisodes[index] ?? ''}
                          onChange={(event) => {
                            const value = event.target.value;
                            setAssignEpisodes((current) => current.map((old, at) => (at === index ? value : old)));
                          }}
                          size='sm'
                          className='w-20 shrink-0 font-mono tabular-nums'
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              </DialogBody>
            );
          })()}
          <DialogFooter>
            <Button variant='secondary' onClick={() => setAssignFor(null)} disabled={assigning}>Cancel</Button>
            <Button onClick={() => void assign()} disabled={assigning || !assignEntry}>
              <Link2 data-icon='inline-start' /> {assigning ? 'Matching…' : assignEntry ? 'Match to ' + assignEntry.title : 'Pick an entry'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AniListPickDialog
        query={pickFor ? (pickFor.mode === 'match' ? pickFor.show.source_title || pickFor.show.title : pickFor.mode === 'assign' ? pickFor.query : pickFor.from.title) : null}
        onClose={() => setPickFor(null)}
        onPick={pickEntry}
        title={pickFor?.mode === 'rematch' ? 'Change the AniList entry of ' + pickFor.from.title : pickFor?.mode === 'assign' ? 'Choose the AniList entry' : 'Match on AniList'}
        description={pickFor?.mode === 'rematch'
          ? 'Its files become the picked entry, with the same episode numbers, and are moved into place.'
          : pickFor?.mode === 'assign'
            ? 'The selected files are matched to it once you confirm their episode numbers.'
            : "This card's files become the picked entry's episodes, by the number in each file name, and are moved into place."}
        action={pickFor?.mode === 'rematch' ? 'Change' : pickFor?.mode === 'assign' ? 'Choose' : 'Match'}
      />
      <Dialog open={seasonRename !== null} onOpenChange={(open) => { if (!open && !renaming) setSeasonRename(null); }}>
        <DialogContent size='sm'>
          <DialogHeader>
            <DialogTitle>Rename season</DialogTitle>
            <DialogDescription>
              The name its tab here and its season in Jellyfin show. It stays with the season while
              {' '}{seasonRename?.entry.title ?? 'its first entry'} opens it.
            </DialogDescription>
          </DialogHeader>
          <form
            id='rename-season'
            onSubmit={(event) => {
              event.preventDefault();
              const name = seasonName.trim();
              if (name) void renameSeason(name);
            }}
          >
            <Input aria-label='Season name' value={seasonName} onChange={(event) => setSeasonName(event.target.value)} autoFocus />
          </form>
          <DialogFooter className='sm:justify-between'>
            <Button
              variant='secondary'
              disabled={renaming || !seasonRename?.show.season_names?.[String(seasonRename.entry.anilist_id)]}
              onClick={() => void renameSeason(null)}
              title='Drop your name: the season is named after its entry again'
            >
              <RotateCcw data-icon='inline-start' /> Automatic name
            </Button>
            <div className='flex gap-2'>
              <Button variant='secondary' onClick={() => setSeasonRename(null)} disabled={renaming}>Cancel</Button>
              <Button type='submit' form='rename-season' disabled={renaming || !seasonName.trim() || seasonName.trim() === seasonRename?.label}>
                {renaming ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={renameFor !== null} onOpenChange={(open) => { if (!open && !renaming) setRenameFor(null); }}>
        <DialogContent size='sm'>
          <DialogHeader>
            <DialogTitle>Rename collection</DialogTitle>
            <DialogDescription>
              The name its folder, its episode files and Jellyfin show it under.
              {renameFor?.collection_name_custom ? ' It carries a name of your own now.' : " It carries AniList's name now."}
            </DialogDescription>
          </DialogHeader>
          <form
            id='rename-collection'
            onSubmit={(event) => {
              event.preventDefault();
              const name = renameValue.trim();
              if (name) void renameCollection(name);
            }}
          >
            <Input aria-label='Collection name' value={renameValue} onChange={(event) => setRenameValue(event.target.value)} autoFocus />
          </form>
          <DialogFooter className='sm:justify-between'>
            <Button
              variant='secondary'
              disabled={renaming || !renameFor?.collection_name_custom}
              onClick={() => void renameCollection(null)}
              title={renameFor?.collection_name_custom ? 'Drop your name: the collection is named after its title entry on AniList again' : 'It already carries AniList\'s name'}
            >
              <RotateCcw data-icon='inline-start' /> Use AniList&apos;s name
            </Button>
            <div className='flex gap-2'>
              <Button variant='secondary' onClick={() => setRenameFor(null)} disabled={renaming}>Cancel</Button>
              <Button type='submit' form='rename-collection' disabled={renaming || !renameValue.trim() || renameValue.trim() === renameFor?.title}>
                {renaming ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <CollectionGraph
        target={graphFor}
        onClose={() => setGraphFor(null)}
        onChanged={() => { void load(); if (selected) void openShow(selected); }}
        onOpenCollection={(id) => {
          const card = shows.find((show) => show.collection_id === id);
          if (!card) {
            toast.info('That collection has no card in the library: none of its entries is on disk');
            return;
          }
          setGraphFor(null);
          goShow(card.key);
        }}
      />
    </>
  );

  // A show opened outside the split view is a page of its own.
  if (showKey && !split) {
    return (
      <div className='flex min-h-0 flex-1 flex-col gap-6'>
        <div>
          <Button variant='ghost' size='sm' onClick={closeShow}><ArrowLeft data-icon='inline-start' /> Library</Button>
        </div>
        {showDetail}
        {dialogs}
      </div>
    );
  }

  return (
    // As tall as the window: the grid, the table and the split view's panes scroll inside it.
    <div className='flex h-full min-h-0 flex-col gap-5'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Library</h1>
          <p className='break-all font-mono text-xs text-muted-foreground'>{root || 'Dedicated anime destination'}</p>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <InputGroup className='w-60'>
            <InputGroupAddon align='inline-start'><Search /></InputGroupAddon>
            <InputGroupInput aria-label='Search Anime library' placeholder='Search your Anime…' value={query} onChange={(event) => setQuery(event.target.value)} />
          </InputGroup>
          {(unmatchedCount > 0 || onlyUnmatched) && (
            <Button
              variant={onlyUnmatched ? 'primary' : 'secondary'}
              aria-pressed={onlyUnmatched}
              onClick={() => setOnlyUnmatched((current) => !current)}
              title='Files no AniList entry is known for; match them on AniList from their details'
            >
              <Link2 data-icon='inline-start' /> Unmatched ({unmatchedCount})
            </Button>
          )}
          {(bulkCounts.upgrade > 0 || bulkRunning === 'upgrade') && (
            <Button
              variant='secondary'
              onClick={() => void openBulk('upgrade')}
              disabled={bulkRunning === 'upgrade'}
              title='Replace every AVC episode an HEVC release with German subtitles was found for. Shows the list first.'
            >
              <CircleArrowUp data-icon='inline-start' /> {bulkRunning === 'upgrade' ? 'Upgrading…' : 'Upgrade all (' + bulkCounts.upgrade + ')'}
            </Button>
          )}
          {(bulkCounts.missing > 0 || bulkRunning === 'missing') && (
            <Button
              variant='secondary'
              onClick={() => void openBulk('missing')}
              disabled={bulkRunning === 'missing'}
              title='Download every missing episode a German-subtitled release was found for. Shows the list first.'
            >
              <Download data-icon='inline-start' /> {bulkRunning === 'missing' ? 'Queuing…' : 'Download available (' + bulkCounts.missing + ')'}
            </Button>
          )}
          {blacklistedCount > 0 && (
            <Button
              variant='secondary'
              aria-pressed={onlyBlacklisted}
              onClick={() => setOnlyBlacklisted((current) => !current)}
              title='Only the shows you blacklisted whose files are still in the library. Nothing new is downloaded for them; open the show to delete them.'
            >
              <Ban data-icon='inline-start' /> Blacklisted ({blacklistedCount})
            </Button>
          )}
          <ToggleGroup
            aria-label='Library view'
            value={[view]}
            onValueChange={(values) => { if (values[0]) setView(values[0] as View); }}
          >
            {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table'], ...(wide ? [['split', Columns2, 'Split']] as const : [])] as const).map(([value, Icon, label]) => (
              <ToggleGroupItem key={value} value={value} icon={<Icon />}>{label}</ToggleGroupItem>
            ))}
          </ToggleGroup>
          <Button variant='secondary' onClick={() => void load(true)} disabled={loading}><RefreshCw data-icon='inline-start' className={loading ? 'animate-spin' : ''} /> Rescan</Button>
        </div>
      </div>

      {loading ? <div className='flex min-h-40 items-center justify-center'><Spinner /></div> : visible.length === 0 ? (
        <Empty>
          <EmptyMedia><HardDrive /></EmptyMedia>
          <EmptyTitle>{shows.length ? 'No matching Anime' : 'No Anime in your library yet'}</EmptyTitle>
          <EmptyDescription>Completed and staged episodes are grouped into collections of AniList entries.</EmptyDescription>
        </Empty>
      ) : view === 'grid' ? (
        // The ground painted in, as the app's scrolling area: opaque, it scrolls on the compositor (App.tsx).
        <div className='page-ground min-h-0 flex-1 overflow-y-auto'>
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {visible.map((show) => {
            const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
            return (
              <button key={show.key} type='button' onClick={() => goShow(show.key)} onPointerEnter={() => prefetchShow(show.key)} onPointerLeave={stopPrefetch} onFocus={() => prefetchShow(show.key)} aria-label={'View ' + show.title} className='block w-full cursor-pointer rounded-card text-left focus-ring'>
                <Card className='poster-card h-full gap-0 overflow-hidden [--card-padding:0]'>
                  <div className='relative block w-full'>
                    <AnimePoster url={show.poster_url} title={show.title} />
                    {show.blacklisted && <StatusTag variant='destructive' className='absolute left-2 top-2 bg-background/85 backdrop-blur-sm'>Blacklisted</StatusTag>}
                    {isUnmatched(show) && (
                      <span className='absolute right-2 top-2'>
                        <StatusTag variant='warning' className='bg-background/85 backdrop-blur-sm' title='No AniList entry is known for these files: match it from its details'>Unmatched</StatusTag>
                      </span>
                    )}
                    <span className='absolute inset-x-0 bottom-0 bg-linear-to-t from-black via-black/75 to-transparent px-3 pb-2.5 pt-10 text-left text-sm font-medium leading-tight text-white'>{show.title}</span>
                  </div>
                  {/* The completion colour, as the rule between the cover and
                      what is written under it. */}
                  <span className={cn('block h-0.5 w-full', tone.className)} title={tone.label} aria-hidden='true' />
                  <div className='flex flex-col gap-2 p-3'>
                    <div className='flex items-center gap-2'>
                      <Progress show={show} fill />
                      <span className='shrink-0 whitespace-nowrap font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(show.size)}</span>
                    </div>
                  </div>
                </Card>
              </button>
            );
          })}
        </div>
        </div>
      ) : split ? (
        // The page's whole width, the open show beside the table; the divider is dragged.
        <LibrarySplit
          id='anime'
          list={showTable}
          open={Boolean(selected)}
          onClose={closeShow}
          empty={{ title: 'Pick a show', description: 'Its seasons and episodes open here, beside the list.' }}
        >
          {showDetail}
        </LibrarySplit>
      ) : (
        showTable
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

      {/* Deleting a whole show is not undoable, so it is never one click. */}
      {dialogs}
    </div>
  );
}
