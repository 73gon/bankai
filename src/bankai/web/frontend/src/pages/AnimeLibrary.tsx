import { Fragment, useEffect, useMemo, useState, useRef } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, FileVideo, LayoutGrid, Rows3, Trash2, Ban, Link2, Network, Pencil, RotateCcw, Unlink, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, pagePaths, recall, type AniListAnime, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry } from '@/lib/api';
import { anilistName, anilistUrl } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { CollectionGraph, type GraphTarget } from '@/components/CollectionGraph';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/zon/ui/drawer';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Input } from '@/zon/ui/input';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Skeleton } from '@/zon/ui/skeleton';
import { Spinner } from '@/zon/ui/spinner';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';

function formatSize(bytes: number) {
  const tib = bytes / 1024 ** 4;
  if (tib >= 1) return tib.toFixed(2) + ' TiB';
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : (bytes / 1024 ** 2).toFixed(0) + ' MiB';
}

/** A segmented meter: discrete steps, each able to carry its own colour, so one
 *  meter can show a composition (the encode mix) as well as a ratio. */
const SEGMENTS = 12;

type MeterPart = { value: number; className: string };

function SegmentMeter({ parts, total, className, title }: { parts: MeterPart[]; total: number; className?: string; title?: string }) {
  const filled: string[] = [];
  if (total > 0) {
    // Largest-remainder allocation, so a part that exists at all keeps a
    // segment instead of rounding away to nothing.
    const exact = parts.map((part) => ({ ...part, exact: (part.value / total) * SEGMENTS }));
    const base = exact.map((part) => ({ ...part, whole: Math.floor(part.exact) }));
    let used = base.reduce((sum, part) => sum + part.whole, 0);
    const order = [...base]
      .map((part, index) => ({ index, rest: part.exact - part.whole, any: part.value > 0 }))
      .filter((row) => row.any)
      .sort((a, b) => b.rest - a.rest);
    for (const row of order) {
      if (used >= SEGMENTS) break;
      if (base[row.index].whole === 0 || row.rest > 0) {
        base[row.index].whole += 1;
        used += 1;
      }
    }
    for (const part of base) {
      for (let i = 0; i < part.whole; i += 1) filled.push(part.className);
    }
  }
  return (
    <div
      className={cn('flex h-[13px] items-center gap-[2px] overflow-hidden rounded-[3px] border border-field-edge bg-field-surface px-[2px] shadow-field', className)}
      title={title}
      role='img'
      aria-label={title}
    >
      {Array.from({ length: SEGMENTS }, (_, index) => (
        <span key={index} className={cn('h-[7px] min-w-px flex-1 rounded-[1px]', filled[index] ?? 'bg-control-surface')} />
      ))}
    </div>
  );
}

/** Threshold colours for a single ratio, ramping as the meter fills. */
function rampParts(value: number, total: number): MeterPart[] {
  if (total <= 0 || value <= 0) return [];
  const ratio = value / total;
  const className = ratio >= 0.85 ? 'bg-success' : ratio >= 0.5 ? 'bg-warning' : 'bg-destructive';
  return [{ value, className }];
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

// The library table's lesser columns, by the table's own width (not the window's).
// A drawer is asked for once the pointer has rested this long on its show,
// and a click takes that answer while it is younger than PREFETCH_MS.
const PREFETCH_DELAY_MS = 90;
const PREFETCH_MS = 15_000;

const COLUMN_SEASONS = 'hidden @5xl:table-cell';
const COLUMN_ENCODE = 'hidden @3xl:table-cell';
const COLUMN_SIZE = 'hidden @xl:table-cell';
const COLUMN_STATE = 'hidden @lg:table-cell';
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
      <SegmentMeter
        className='w-[74px]'
        total={total}
        title={title}
        parts={[
          { value: hevc, className: 'bg-success' },
          { value: avc, className: 'bg-warning' },
          { value: dubbed, className: 'bg-transfer' },
          { value: unknown, className: 'bg-muted-foreground/40' },
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
      <SegmentMeter
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
  const [query, setQuery] = useState('');
  // Blacklisted shows still on disk are hidden unless asked for.
  const [showBlacklisted, setShowBlacklisted] = useState(false);
  // Only the cards no AniList entry is known for.
  const [onlyUnmatched, setOnlyUnmatched] = useState(false);
  const [view, setView] = useState<View>(storedView);
  const [sort, setSort] = useState<SortState<LibrarySortKey> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedShow, setSelectedShow] = useState<AnimeLibraryShow | null>(null);
  // What the AniList picker is open for.
  const [pickFor, setPickFor] = useState<PickTarget | null>(null);
  // The open season tab of the drawer, and the card it was opened on.
  const [tab, setTab] = useState<{ key: string; value: string } | null>(null);
  // The collection being renamed, and the name typed for it.
  const [renameFor, setRenameFor] = useState<AnimeLibraryShow | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  // Only a spinner with nothing to show: otherwise the held answer stays up while it refreshes.
  const [loading, setLoading] = useState(() => recall(pagePaths.animeLibrary) === undefined);
  const [transferring, setTransferring] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [searchTarget, setSearchTarget] = useState<{ show: AnimeLibraryShow; episode: AnimeLibraryEpisode } | null>(null);
  const [results, setResults] = useState<AnimeEntry[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [downloading, setDownloading] = useState<string | null>(null);
  // The show to remove, and whether it is blacklisted too.
  const [removeTarget, setRemoveTarget] = useState<{ show: AnimeLibraryShow; blacklist: boolean } | null>(null);
  // The episode files ticked in the drawer, and the card they are of.
  const [checked, setChecked] = useState<{ key: string; paths: string[] }>({ key: '', paths: [] });
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
  // Shows being removed in the background: kept off the page until done.
  const removing = useRef<Set<string>>(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* remembering the choice is a convenience, not a requirement */
    }
  }, [view]);

  async function load(rescan = false): Promise<AnimeLibraryShow[] | null> {
    if (rescan) setLoading(true);
    try {
      const result = await api.animeLibrary(rescan);
      const fresh = result.shows.filter((show) => !removing.current.has(show.key));
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

  /** After a change that may rename the open card: open the card it became, or close the drawer.
   *  Several ways to find it are tried in order. */
  async function reopen(...finds: Array<(show: AnimeLibraryShow) => boolean>) {
    const fresh = await load();
    let card: AnimeLibraryShow | undefined;
    for (const find of finds) {
      card = fresh?.find(find);
      if (card) break;
    }
    if (card) {
      await openShow(card.key);
    } else {
      setSelected(null);
      setSelectedShow(null);
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
  // after a change the drawer is always asked for afresh.
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
      setSelectedShow(ready ? await ready : (await api.animeLibraryShow(key)).shows[0] ?? null);
    } catch (error: any) {
      toast.error(error.message);
      setSelectedShow(null);
    } finally {
      setDetailLoading(false);
    }
  }

  async function upgradeToHevc(show: AnimeLibraryShow) {
    if (!show.anilist_ids?.length) return;
    setUpgrading(show.key);
    try {
      const result = await api.upgradeShowToHevc(show.anilist_ids);
      if (result.queued === 0) {
        // Say what was found, not what is assumed: "already HEVC" only when it is.
        toast.info(result.no_replacement > 0
          ? 'No HEVC release with German subtitles was found for ' + result.no_replacement + ' AVC episode(s)'
          : result.not_probed
            ? result.not_probed + ' episode(s) have not been checked for their codec yet; try again later'
            : result.german_dub_kept
              ? 'Nothing to upgrade: the AVC episodes left carry a German dub'
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
    const { show: target, blacklist } = removeTarget;
    // Off the page at once; deleting runs in the background, where the
    // Dashboard shows it under "Your actions".
    removing.current.add(target.key);
    setShows((current) => current.filter((show) => show.key !== target.key));
    setRemoveTarget(null);
    setSelected(null);
    setSelectedShow(null);
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

  /** The AniList entry a row of the drawer is an episode of: its own, else its tab's first entry. */
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

  useEffect(() => { void load(); }, []);
  const visible = useMemo(() => {
    const matched = shows.filter((show) =>
      (showBlacklisted || !show.blacklisted)
      && (!onlyUnmatched || isUnmatched(show))
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
  }, [shows, query, sort, showBlacklisted, onlyUnmatched]);
  const blacklistedCount = useMemo(() => shows.filter((show) => show.blacklisted).length, [shows]);
  const unmatchedCount = useMemo(() => shows.filter((show) => isUnmatched(show) && (showBlacklisted || !show.blacklisted)).length, [shows, showBlacklisted]);
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
  // Another card opens on its first tab; a refreshed one stays where it was, while that tab is still there.
  const openTab = tab && tab.key === active?.key && seasons.map(String).includes(tab.value) ? tab.value : String(seasons[0]);
  const swappable = active && !isUnmatched(active) ? tabEntry(active, openTab) : null;
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

  return (
    <div className='flex min-h-0 flex-1 flex-col gap-5'>
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
          <ToggleGroup
            aria-label='Library view'
            value={[view]}
            onValueChange={(values) => { if (values[0]) setView(values[0] as View); }}
          >
            {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
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
        <div className='min-h-0 flex-1 overflow-y-auto'>
        <div className='grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7'>
          {visible.map((show) => {
            const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
            return (
              <button key={show.key} type='button' onClick={() => void openShow(show.key, true)} onPointerEnter={() => prefetchShow(show.key)} onPointerLeave={stopPrefetch} onFocus={() => prefetchShow(show.key)} aria-label={'View ' + show.title} className='block w-full cursor-pointer rounded-card text-left focus-ring'>
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
                    <Progress show={show} />
                    <div className='flex items-center justify-between gap-2'>
                      <CodecMix show={show} />
                      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{formatSize(show.size)}</span>
                    </div>
                  </div>
                </Card>
              </button>
            );
          })}
        </div>
        </div>
      ) : (
        <Table variant='plain' containerProps={{ className: '@container min-h-0 flex-1 overflow-auto' }}>
            <TableHeader className='sticky top-0 z-10 bg-(--secondary)'>
              <TableRow>
                {/* The series takes what the other columns leave and truncates its
                    title there, and as the table narrows the lesser columns go --
                    seasons, then the encode bar, then size and state: it never
                    runs wider than the page. */}
                <SortHeader label='Series' column='title' sort={sort} onSort={toggleSort} className='w-full' />
                <SortHeader label='Seasons' column='seasons' sort={sort} onSort={toggleSort} className={COLUMN_SEASONS} />
                <SortHeader label='Episodes' column='episodes' sort={sort} onSort={toggleSort} />
                <SortHeader label='Encode' column='encode' sort={sort} onSort={toggleSort} className={COLUMN_ENCODE} />
                <SortHeader label='Size' column='size' sort={sort} onSort={toggleSort} align='right' className={COLUMN_SIZE} />
                <SortHeader label='State' column='state' sort={sort} onSort={toggleSort} className={COLUMN_STATE} />
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((show) => {
                const tone = COMPLETION[show.completion_state] ?? COMPLETION.unknown;
                return (
                  <TableRow
                    key={show.key}
                    tabIndex={0}
                    onClick={() => void openShow(show.key, true)}
                    onPointerEnter={() => prefetchShow(show.key)}
                    onPointerLeave={stopPrefetch}
                    onFocus={() => prefetchShow(show.key)}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); void openShow(show.key, true); } }}
                    className='data-row cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-control-focus'
                  >
                    <TableCell className='max-w-0'>
                      <div className='flex items-center gap-2.5'>
                        <AnimePoster url={show.poster_url} title={show.title} className='w-8 shrink-0 rounded' />
                        <div className='min-w-0'>
                          <p className='truncate font-medium text-foreground' title={show.title}>{show.title}</p>
                          {show.year ? <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{show.year}</p> : null}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className={cn(COLUMN_SEASONS, 'font-mono text-xs tabular-nums text-muted-foreground')}>{show.season_count}</TableCell>
                    <TableCell><Progress show={show} /></TableCell>
                    <TableCell className={COLUMN_ENCODE}><CodecMix show={show} /></TableCell>
                    <TableCell numeric className={cn(COLUMN_SIZE, 'text-xs text-foreground')}>{formatSize(show.size)}</TableCell>
                    <TableCell className={COLUMN_STATE}>
                      <div className='flex flex-wrap items-center gap-1.5'>
                        <span className={cn('size-1.5 rounded-full', tone.className)} />
                        <span className='text-xs text-muted-foreground'>{tone.label}</span>
                        <UpgradeTag show={show} />
                        {(show.no_german_count ?? 0) > 0 && <StatusTag variant='warning' title='Episodes on disk with neither German subtitles nor a German dub'>{show.no_german_count} without German</StatusTag>}
                        {show.staged_count > 0 && <StatusTag variant='warning'>{show.staged_count} staged</StatusTag>}
                        {show.blacklisted && <StatusTag variant='destructive'>Blacklisted</StatusTag>}
                        {isUnmatched(show) && <StatusTag variant='warning' title='No AniList entry is known for these files: match it from its details'>Unmatched</StatusTag>}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
        </Table>
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

      <Drawer side='right' open={Boolean(selected)} onOpenChange={(open) => { if (!open) { setSelected(null); setSelectedShow(null); setTab(null); clearChecked(); } }}>
        <DrawerContent className='w-full md:w-[85%]'>
          {/* Loading another show: its shape. Reloading the open one keeps it on screen. */}
          {detailLoading && !active && <DrawerSkeleton />}
          {active && (
            <>
              <DrawerHeader>
                <div className='flex items-start gap-4'>
                  <AnimePoster url={active.poster_url} title={active.title} className='w-16 shrink-0' />
                  <div className='flex min-w-0 flex-col gap-2'>
                    <DrawerTitle className='text-[22px]'>{active.title}{active.year ? ' (' + active.year + ')' : ''}</DrawerTitle>
                    {active.source_title && active.source_title !== active.title && (
                      <p className='font-mono text-[0.7rem] leading-tight text-muted-foreground' title='The name Erai-raws publishes this show under'>
                        {active.source_title}
                      </p>
                    )}
                    <p className='text-xs text-muted-foreground'>{active.downloaded_count}/{active.total_count} episodes · {active.season_count} seasons · {formatSize(active.size)}</p>
                    <div className='flex flex-wrap items-center gap-2'>
                      {/* First, always: every matched card is an AniList entry, so the
                          tabs' own links do not stand in for it. */}
                      {active.anilist_id && (
                        <Button
                          variant='secondary'
                          size='sm'
                          nativeButton={false}
                          render={<a href={anilistUrl(active.anilist_id)} target='_blank' rel='noreferrer' title={(active.anilist_ids?.length ?? 0) > 1 ? 'The first of its ' + active.anilist_ids!.length + ' AniList entries; each tab links its own' : 'This show on AniList'} />}
                        >
                          <ExternalLink data-icon='inline-start' /> AniList
                        </Button>
                      )}
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
                          onClick={() => openRename(active)}
                          title={active.collection_name_custom
                            ? 'Renamed by hand: rename it again, or give it back AniList\'s name'
                            : 'Give the collection a name of your own, for its folder and in Jellyfin'}
                        >
                          <Pencil data-icon='inline-start' /> Rename
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
                          title='Download the HEVC versions found (Erai-raws, with German subtitles) and replace the AVC files'
                        >
                          <FileVideo data-icon='inline-start' />
                          {upgrading === active.key ? 'Queueing…' : `Upgrade ${active.upgrade_available_count} to HEVC`}
                        </Button>
                      )}
                      {/* Straight on the header, not in a menu: a dialog opened from a
                          popover item was dismissed by the popover closing, so
                          "Remove and blacklist" never asked and never removed. */}
                      {!isUnmatched(active) && active.anilist_id != null && (
                        <Button
                          size='sm'
                          variant='secondary'
                          onClick={() => swappable
                            ? setPickFor({ mode: 'rematch', show: active, from: swappable })
                            : toast.info('This tab holds several AniList entries: change one with the pencil beside its name')}
                          title={swappable
                            ? 'Pick another AniList entry for ' + swappable.title + '; its files move with it'
                            : 'This tab holds several entries: change one with the pencil beside its name'}
                        >
                          <Search data-icon='inline-start' /> Change AniList entry
                        </Button>
                      )}
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
              </DrawerHeader>
              <Tabs value={openTab} onValueChange={(value) => setTab({ key: active.key, value: String(value) })} className='min-h-0 flex-1 gap-3'>
                <TabsList className='h-auto max-w-full flex-wrap'>
                  {seasons.map((season) => <TabsTab key={String(season)} value={String(season)}>{seasonLabel(season)}</TabsTab>)}
                </TabsList>
                {/* Floats over the drawer's bottom edge instead of sitting above the table, so ticking a file never moves the rows. */}
                {activeChecked.length > 0 && (
                  <div className='absolute inset-x-6 bottom-6 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0 sm:inset-x-8'>
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
                {seasons.map((season) => (
                  <TabsPanel key={String(season)} value={String(season)} className={'flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto' + (activeChecked.length > 0 ? ' pb-16' : '')}>
                    {season !== null && (active.season_anilist?.[String(season)]?.length ?? 0) > 0 && (
                      <p className='flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground'>
                        <span>AniList:</span>
                        {active.season_anilist![String(season)].map((entry) => (
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
                      </p>
                    )}
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
                            <TableCell className='max-w-0 text-xs text-muted-foreground'>{entry.episode_title && <p className='truncate text-sm text-foreground' title={entry.episode_title}>{entry.episode_title}</p>}{!entry.missing && <p className='truncate font-mono text-[0.68rem]' title={entry.name}>{entry.name}</p>}{entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}</TableCell>
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
              </Tabs>
            </>
          )}
        </DrawerContent>
      </Drawer>
      {/* Deleting a whole show is not undoable, so it is never one click. */}
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
          <DialogBody>
            {searching ? <div className='flex h-40 items-center justify-center'><Spinner label='Searching' /></div> : results.length ? <div className='flex flex-col gap-2'>
              {results.map((entry) => <Card key={entry.info_hash} className='flex-row flex-wrap items-center justify-between gap-3 rounded-button [--card-padding:12px]'>
                <div className='flex min-w-0 flex-1 flex-col gap-1'><p className='break-words font-mono text-xs'>{entry.title}</p><p className='text-xs text-muted-foreground'>{entry.quality} · {entry.size} · {entry.seeders} seeds · {entry.publisher}</p></div>
                <Button size='sm' variant='secondary' nativeButton={false} render={<a href={entry.detail_url} target='_blank' rel='noreferrer' />}><ExternalLink data-icon='inline-start' /> Description</Button>
                <Button size='sm' variant='secondary' disabled={Boolean(downloading)} onClick={() => void downloadMissing(entry)}><Download data-icon='inline-start' /> {downloading === entry.info_hash ? 'Queuing…' : 'Download'}</Button>
              </Card>)}
            </div> : (
              <Empty>
                <EmptyMedia><Search /></EmptyMedia>
                <EmptyTitle>No matching releases found</EmptyTitle>
                <EmptyDescription>Try another title above, the way Erai-raws names the show.</EmptyDescription>
              </Empty>
            )}
          </DialogBody>
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
          void openShow(card.key);
        }}
      />
    </div>
  );
}
