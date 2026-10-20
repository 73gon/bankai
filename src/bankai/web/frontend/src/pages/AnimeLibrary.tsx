import { useEffect, useMemo, useState, useRef } from 'react';
import { HardDrive, RefreshCw, ArrowRight, ExternalLink, Search, Download, FileVideo, LayoutGrid, Rows3, Trash2, Ban, Link2, Network, Pencil, RotateCcw, Unlink, X } from 'lucide-react';
import { toast } from 'sonner';
import { api, pagePaths, recall, type AniListAnime, type AnimeLibraryEntry, type AnimeLibraryShow, type AnimeLibraryEpisode, type AnimeEntry } from '@/lib/api';
import { anilistName, anilistUrl } from '@/lib/anilist';
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
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { CollectionGraph, type GraphTarget } from '@/components/CollectionGraph';
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
        await api.matchLibraryFolders(folders, anime.anilist_id);
        toast.success(target.show.title + ' matched to ' + name);
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
      await api.animeDownload(entry, anilistId, 'show', searchTarget.episode.episode);
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
          <p className='text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground'>Anime</p>
          <h1 className='font-serif text-3xl font-semibold'>Library</h1>
          <p className='break-all font-mono text-xs text-muted-foreground'>{root || 'Dedicated anime destination'}</p>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          <div className='relative'>
            <Search className='pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground' />
            <Input aria-label='Search Anime library' placeholder='Search your Anime…' value={query} onChange={(event) => setQuery(event.target.value)} className='w-60 pl-9' />
          </div>
          {(unmatchedCount > 0 || onlyUnmatched) && (
            <Button
              variant={onlyUnmatched ? 'default' : 'secondary'}
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
        <EmptyState icon={HardDrive} title={shows.length ? 'No matching Anime' : 'No Anime in your library yet'} description='Completed and staged episodes are grouped into collections of AniList entries.' />
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
                    {isUnmatched(show) && <Badge variant='warning' className='absolute right-2 top-2 bg-background/85 backdrop-blur-sm' title='No AniList entry is known for these files: match it from its details'>Unmatched</Badge>}
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
                        {isUnmatched(show) && <Badge variant='warning' title='No AniList entry is known for these files: match it from its details'>Unmatched</Badge>}
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

      <Drawer open={Boolean(selected)} onOpenChange={(open) => { if (!open) { setSelected(null); setSelectedShow(null); setTab(null); clearChecked(); } }}>
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
                      {/* First, always: every matched card is an AniList entry, so the
                          tabs' own links do not stand in for it. */}
                      {active.anilist_id && <Button asChild variant='secondary' size='sm'><a href={anilistUrl(active.anilist_id)} target='_blank' rel='noreferrer' title={(active.anilist_ids?.length ?? 0) > 1 ? 'The first of its ' + active.anilist_ids!.length + ' AniList entries; each tab links its own' : 'This show on AniList'}><ExternalLink data-icon='inline-start' /> AniList</a></Button>}
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
                        <Select
                          value={String(active.anilist_id ?? '')}
                          onValueChange={(value) => void setTitle(active, Number(value))}
                          disabled={titling}
                        >
                          <SelectTrigger
                            data-size='sm'
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
                      {Boolean(active.avc_count) && (
                        <Button
                          size='sm'
                          disabled={!active.anilist_ids?.length || upgrading === active.key}
                          onClick={() => void upgradeToHevc(active)}
                          title='Download HEVC versions of this show&apos;s AVC episodes and replace them'
                        >
                          <FileVideo data-icon='inline-start' />
                          {upgrading === active.key ? 'Queueing…' : `Upgrade ${active.avc_count} to HEVC`}
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
              <Tabs value={openTab} onValueChange={(value) => setTab({ key: active.key, value })} className='flex min-h-0 flex-1 flex-col'>
                <TabsList className='flex h-auto flex-wrap justify-start px-5 pt-3'>
                  {seasons.map((season) => <TabsTrigger key={String(season)} value={String(season)}>{seasonLabel(season)}</TabsTrigger>)}
                </TabsList>
                {activeChecked.length > 0 && (
                  <div className='mx-5 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card/60 px-3 py-2'>
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
                  <TabsContent key={String(season)} value={String(season)} className='min-h-0 flex-1 overflow-y-auto px-5 pb-4'>
                    {season !== null && (active.season_anilist?.[String(season)]?.length ?? 0) > 0 && (
                      <p className='flex flex-wrap items-center gap-x-3 gap-y-1 pt-3 text-xs text-muted-foreground'>
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
                                size='icon'
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
                    <div className='overflow-x-auto'>
                      <table className='w-full border-collapse text-sm'>
                        <thead><tr className='border-b border-border text-left text-[0.7rem] uppercase tracking-wide text-muted-foreground'><th className='w-7 py-2.5 pr-2 font-medium'>{(() => {
                          // "Select all in this tab": every row of it that is a file.
                          const files = active.episodes.filter((entry) => entry.season_number === season && hasFile(entry)).map((entry) => entry.path);
                          const ticked = files.filter((path) => checkedPaths.has(path)).length;
                          return (
                            <input
                              type='checkbox'
                              className='size-3.5 cursor-pointer accent-foreground align-middle disabled:cursor-not-allowed'
                              aria-label='Select every file in this tab'
                              title='Select every file in this tab'
                              disabled={files.length === 0}
                              checked={files.length > 0 && ticked === files.length}
                              ref={(element) => { if (element) element.indeterminate = ticked > 0 && ticked < files.length; }}
                              onChange={(event) => toggleChecked(active, files, event.target.checked)}
                            />
                          );
                        })()}</th><th className='py-2.5 pr-3 font-medium'>Ep</th><th className='px-3 py-2.5 font-medium'>Episode / File</th><th className='px-3 py-2.5 font-medium'>Encode</th><th className='px-3 py-2.5 text-right font-medium'>Size</th><th className='py-2.5 pl-3 text-right font-medium'>State</th></tr></thead>
                        <tbody>{active.episodes.filter((entry) => entry.season_number === season).map((entry) => (
                          <tr key={entry.path || String(entry.season_number) + ':' + entry.episode} className={cn('border-b border-border/70 last:border-0', hasFile(entry) && checkedPaths.has(entry.path) && 'bg-accent/30')}>
                            <td className='w-7 py-2.5 pr-2'>
                              {hasFile(entry) && (
                                <input
                                  type='checkbox'
                                  className='size-3.5 cursor-pointer accent-foreground align-middle'
                                  aria-label={'Select ' + entry.name}
                                  checked={checkedPaths.has(entry.path)}
                                  onChange={(event) => toggleChecked(active, [entry.path], event.target.checked)}
                                />
                              )}
                            </td>
                            <td className='py-2.5 pr-3 font-mono text-xs tabular-nums'>{entry.episode ?? '—'}</td>
                            <td className='px-3 py-2.5 text-xs text-muted-foreground'>{entry.episode_title && <p className='text-sm text-foreground'>{entry.episode_title}</p>}{!entry.missing && <p className='font-mono text-[0.68rem]'>{entry.name}</p>}{entry.missing && <p>{entry.tba ? 'TBA' : 'Missing'}{entry.aired ? ' · ' + entry.aired : ''}</p>}</td>
                            <td className='px-3 py-2.5'>
                              <div className='flex flex-wrap items-center gap-1.5'>
                                {entry.codec ? <Badge variant={entry.codec === 'hevc' ? 'success' : 'warning'}>{entry.codec.toUpperCase()}</Badge> : <span className='text-xs text-muted-foreground'>—</span>}
                                {entry.german_dub && <Badge variant='transfer' title='Carries a German dub — never replaced by an HEVC upgrade'>GER dub</Badge>}
                              </div>
                            </td>
                            <td className='px-3 py-2.5 text-right font-mono text-xs tabular-nums'>{entry.missing ? '—' : formatSize(entry.size)}</td>
                            <td className='py-2.5 pl-3 text-right'>{entry.missing ? <Button size='icon' variant='secondary' disabled={entryOf(active, entry) == null || entry.tba} aria-label='Search for a release' title={entry.tba ? 'Not aired yet' : 'Search for a release of this AniList episode'} onClick={() => void searchMissing(active, entry)}><Search /></Button> : (
                              <div className='flex items-center justify-end gap-1.5'>
                                {entry.staged ? (
                                  <Button size='sm' variant='secondary' onClick={() => void transfer(entry)} disabled={transferring === entry.path || entry.transfer_status === 'transferring'}><ArrowRight data-icon='inline-start' /> {entry.transfer_status === 'transferring' ? 'Transferring' : 'Transfer'}</Button>
                                ) : <Badge variant='success'>In library</Badge>}
                                <Button
                                  size='icon'
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
                            )}</td>
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
        <DialogContent className='max-w-md'>
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
        <DialogContent className='flex h-[90dvh] w-[92vw] max-w-none flex-col overflow-hidden'>
          <DialogHeader>
            <DialogTitle>Search {searchTarget ? seasonLabel(searchTarget.episode.season_number) : ''} — episode {searchTarget?.episode.episode ?? ''}</DialogTitle>
            <DialogDescription>Choose a release of this AniList episode. Every result is checked to be this entry's episode.</DialogDescription>
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
            </div> : <EmptyState icon={Search} title='No matching releases found' description='Try another title above, the way Erai-raws names the show.' />}
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={assignFor !== null} onOpenChange={(open) => { if (!open && !assigning) setAssignFor(null); }}>
        <DialogContent className='flex max-h-[90dvh] w-[min(44rem,calc(100vw-2rem))] max-w-2xl flex-col'>
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
              <div className='flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1'>
                <section className='flex flex-col gap-2'>
                  <span className='text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground'>Entry</span>
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
                                'flex w-full items-center gap-2.5 rounded-md border px-3 py-2 text-left text-sm transition-colors',
                                on ? 'border-foreground/60 bg-accent/50 text-foreground' : 'border-border text-foreground-soft hover:bg-accent/30',
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
                    <span className='text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground'>Episodes</span>
                    <label className='flex items-center gap-2 text-xs text-muted-foreground'>
                      First episode
                      <Input
                        type='number'
                        min={0}
                        inputMode='numeric'
                        aria-label='First episode'
                        value={assignFirst}
                        onChange={(event) => changeAssignFirst(event.target.value)}
                        className='w-20 font-mono tabular-nums'
                      />
                    </label>
                  </div>
                  <p className='text-xs text-muted-foreground'>The files in their order on the card; changing the first episode numbers them all on from it.</p>
                  <ul className='flex flex-col divide-y divide-border/70 rounded-md border border-border'>
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
                          className='w-20 shrink-0 font-mono tabular-nums'
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
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
        <DialogContent className='max-w-md'>
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
          <DialogFooter className='gap-2 sm:justify-between sm:space-x-0'>
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
      />
    </div>
  );
}
