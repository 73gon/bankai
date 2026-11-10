import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRightToLine, Ban, BookOpen, Download, ExternalLink, FolderInput, FolderSearch, FolderTree, Link2, ListOrdered, Merge, Network, PanelRightOpen, Pencil, Plus, RotateCcw, Scissors, Search, Star, StarOff, Trash2, X } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import {
  api, pagePaths, recall,
  type MangaBreakdown, type MangaChapterRun, type MangaCollection, type MangaDetail, type MangaEntry, type MangaFolder, type MangaGraphNode,
  type MangaLayoutPlan, type MangaLibrary as Library, type MangaRelease, type MangaSeries,
} from '@/lib/api';
import { anilistMangaUrl, anilistName, anilistUrl, formatLabel } from '@/lib/anilist';
import { formatBytes, timeAgo } from '@/lib/utils';
import { AnimePoster } from '@/components/AnimePoster';
import { MangaGraph } from '@/components/MangaGraph';
import { MangaPickDialog, mangaFacts } from '@/components/MangaPickDialog';
import { MoveMangaFiles } from '@/components/MoveMangaFiles';
import { SegmentMeter, rampParts } from '@/components/segment-meter';
import { SortHeader, nextSort, type SortDir, type SortState } from '@/components/sort-header';
import { StatusTag, type StatusVariant } from '@/components/status-tag';
import { TruncatedText } from '@/components/TruncatedText';
import { releaseSpan } from '@/pages/MangaReview';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Card } from '@zonsh/ui';
import { Checkbox } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Progress } from '@zonsh/ui';
import { LibrarySplit } from '@/components/library-split';
import { CORNER_TAG, FooterStat, LibraryFooter, PosterCard, PosterGrid, TitlePage, ViewSwitch, formatSize, useLibraryAddress, useLibraryView } from '@/components/library/library-page';
import { Skeleton } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@zonsh/ui';

type Tab = 'library' | 'followed' | 'downloads' | 'match';
// 'split': the table beside the open series, to try out against the series' own page.
type SortKey = 'title' | 'volumes' | 'chapters' | 'size' | 'state';

const FIRST_DIRECTION: Record<SortKey, SortDir> = { title: 'asc', volumes: 'desc', chapters: 'desc', size: 'desc', state: 'asc' };
// The table's lesser columns, by the table's own width.
const COLUMN_CHAPTERS = 'hidden @3xl:table-cell';
const COLUMN_SIZE = 'hidden @xl:table-cell';
const COLUMN_STATE = 'hidden @lg:table-cell';

const STATUS: Record<string, { label: string; variant: StatusVariant }> = {
  review: { label: 'To review', variant: 'review' },
  queued: { label: 'Waiting for qBittorrent', variant: 'queue' },
  downloading: { label: 'Downloading', variant: 'torrent' },
  publishing: { label: 'Filing', variant: 'transfer' },
  done: { label: 'In the library', variant: 'success' },
  failed: { label: 'Failed', variant: 'destructive' },
  discarded: { label: 'Discarded', variant: 'muted' },
  owned: { label: 'Had it all', variant: 'muted' },
};

/** What a download put into the library and what it left out, each file with why on hover. */
function FiledNote({ release }: { release: MangaRelease }) {
  const copied = release.copied ?? [];
  const left = release.left ?? [];
  const reasons = [...new Set(left.map(([, why]) => why))];
  return (
    <span
      className='font-mono text-[0.66rem] text-muted-foreground'
      title={[...copied.map((name) => '+ ' + name), ...left.map(([name, why]) => `− ${name}: ${why}`)].join('\n')}
    >
      {copied.length} file{copied.length === 1 ? '' : 's'} filed
      {left.length > 0 && ` · ${left.length} left out (${reasons.join(', ')})`}
    </span>
  );
}

export function ReleaseStatus({ release }: { release: MangaRelease }) {
  const status = STATUS[release.status] ?? { label: release.status, variant: 'default' as StatusVariant };
  return <StatusTag variant={status.variant} title={release.error || release.reason || undefined}>{status.label}</StatusTag>;
}




type Holding = { label: string; variant: StatusVariant; order: number; title: string };

/** How much of a series the library has: the drawer's badge, the table's State and its sort. */
function holding(have: number, total: number, files: number, finished: boolean, noun: string): Holding {
  if (have === 0) {
    return files > 0
      ? { label: 'Unknown', variant: 'muted', order: 3, title: `Its files do not say which ${noun} they hold` }
      : { label: 'Not in library', variant: 'muted', order: 4, title: 'Nothing of it in your library yet' };
  }
  if (have >= total) {
    return finished
      ? { label: 'Complete', variant: 'success', order: 0, title: `All ${total} ${noun} are in your library` }
      : { label: 'Up to date', variant: 'info', order: 1, title: `All ${total} ${noun} out so far are in your library` };
  }
  return { label: 'Partial', variant: 'warning', order: 2, title: `${have} of ${total} ${noun} in your library, ${total - have} missing` };
}

function seriesHolding(series: MangaSeries): Holding {
  const count = series.count ?? { unit: 'chapter', have: 0, total: 0 };
  return holding(count.have, count.total, series.books ?? 0, series.status === 'FINISHED', count.unit === 'volume' ? 'volumes' : 'chapters');
}

const SORTERS: Record<SortKey, (series: MangaSeries) => number | string> = {
  title: (series) => anilistName(series).toLocaleLowerCase(),
  volumes: (series) => series.owned_volumes ?? 0,
  chapters: (series) => (series.count?.unit === 'chapter' ? series.count.have : -1),
  size: (series) => series.size ?? 0,
  state: (series) => seriesHolding(series).order,
};

/** "12/24" and a meter of it; "?" where nothing says how many there are. */
function CountMeter({ have, total, noun }: { have: number; total: number; noun: string }) {
  if (!have && !total) return <span className='font-mono text-[0.68rem] text-muted-foreground'>—</span>;
  return (
    <div className='flex items-center gap-2'>
      <SegmentMeter className='w-[74px]' total={total || have} title={`${have} of ${total || '?'} ${noun}`} parts={rampParts(have, total || have)} />
      <span className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>{have}/{total || '?'}</span>
    </div>
  );
}

// -- The drawer: a series' chapters, relations and releases ----------------------------

function span(from: number, to: number) {
  return from === to ? String(from) : from + '–' + to;
}

function runVolume(run: MangaChapterRun) {
  if (run.volume == null) return '—';
  return run.volume_to != null && run.volume_to !== run.volume ? `Vol ${run.volume}–${run.volume_to}` : `Vol ${run.volume}`;
}

const RUN_DOT: Record<MangaChapterRun['kind'], string> = {
  volume: 'bg-success',
  chapter: 'bg-info',
  missing: 'bg-destructive',
  other: 'bg-muted-foreground',
};

/** A checkbox inside a table: a real button, so the click lands. */
function Tick({ checked, indeterminate, label, onChange }: { checked: boolean; indeterminate?: boolean; label: string; onChange: (on: boolean) => void }) {
  return (
    <Checkbox
      nativeButton
      render={<button type='button' />}
      className='align-middle'
      aria-label={label}
      checked={checked}
      indeterminate={indeterminate}
      onCheckedChange={(on) => onChange(on)}
    />
  );
}

/** Every file of a row: "Chapter 0001.cbz to Chapter 0007.cbz, 7 files", for labels. */
function rowFiles(names: string[]) {
  return names.length > 1 ? `${names.length} files, ${names[0]} to ${names[names.length - 1]}` : names[0];
}

/** Every chapter and how the library has it, and every file of the folder in a row: a row moves to
 *  another series by its own button, ticked rows together by the drawer's bar. */
function ChapterBreakdown({ breakdown, sizes, waiting, picked, onPick, onReview, onMove, onRenumber }: {
  breakdown: MangaBreakdown;
  sizes: Map<string, number>;
  waiting: number;
  picked: Set<string>;
  onPick: (names: string[], on: boolean) => void;
  onReview: () => void;
  onMove: (names: string[]) => void;
  onRenumber: (names: string[]) => void;
}) {
  const byVolume = breakdown.unit === 'volume';
  const files = useMemo(() => [...new Set(breakdown.rows.flatMap((run) => run.names))], [breakdown]);
  const ticked = files.filter((name) => picked.has(name)).length;
  return (
    <div className='flex flex-col gap-4'>
      {breakdown.missing > 0 && waiting > 0 && (
        <div className='flex flex-wrap items-center gap-2'>
          <Button size='sm' variant='secondary' onClick={onReview}>{waiting} release{waiting === 1 ? '' : 's'} to review</Button>
        </div>
      )}
      {breakdown.rows.length === 0 && <p className='text-sm text-muted-foreground'>Nothing of it in your library yet.</p>}
      {breakdown.rows.length > 0 && (
        <Table variant='plain'>
          <TableHeader>
            <TableRow>
              <TableHead className='w-7 pr-2'>
                {files.length > 0 && (
                  <Tick label='Tick every file' checked={ticked === files.length} indeterminate={ticked > 0 && ticked < files.length} onChange={(on) => onPick(files, on)} />
                )}
              </TableHead>
              <TableHead>{byVolume ? 'Volumes' : 'Chapters'}</TableHead>
              {!byVolume && <TableHead>Volume</TableHead>}
              <TableHead className='w-full'>In your library</TableHead>
              <TableHead numeric>Size</TableHead>
              <TableHead className='w-0'><span className='sr-only'>Actions</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {breakdown.rows.map((run) => {
              const on = run.names.length > 0 && run.names.every((name) => picked.has(name));
              const size = run.names.reduce((sum, name) => sum + (sizes.get(name) ?? 0), 0);
              return (
                <TableRow
                  key={(run.names[0] ?? 'missing') + '|' + run.from}
                  data-selected={on ? '' : undefined}
                  className={cn('group/row', run.kind === 'missing' && 'bg-destructive/5')}
                >
                  <TableCell className='w-7 pr-2'>
                    {run.names.length > 0 && (
                      <Tick
                        label={'Tick ' + rowFiles(run.names)}
                        checked={on}
                        onChange={(next) => onPick(run.names, next)}
                      />
                    )}
                  </TableCell>
                  <TableCell className='whitespace-nowrap font-mono text-xs tabular-nums'>{run.from != null && run.to != null ? span(run.from, run.to) : '—'}</TableCell>
                  {!byVolume && (
                    <TableCell
                      className='whitespace-nowrap text-xs text-muted-foreground'
                      title={run.kind === 'chapter' && run.volume != null
                        ? `The names of the pages inside ${run.names.length > 1 ? 'these files' : 'this file'} say volume ${run.volume}`
                        : undefined}
                    >
                      {runVolume(run)}
                    </TableCell>
                  )}
                  <TableCell className='max-w-0 text-sm'>
                    {run.kind === 'missing' ? (
                      <span className='flex items-center gap-2 text-destructive'><span className={cn('size-1.5 shrink-0 rounded-full', RUN_DOT.missing)} />Missing</span>
                    ) : (
                      <div className='flex flex-wrap items-center gap-x-2'>
                        <span className={cn('size-1.5 shrink-0 rounded-full', RUN_DOT[run.kind])} />
                        <span className='min-w-0 truncate' title={run.names.join('\n')}>
                          {run.names.length > 1 ? `${run.names[0]} – ${run.names[run.names.length - 1]}` : run.via}
                        </span>
                        {run.names.length > 1 && <span className='text-xs text-muted-foreground'>{run.names.length} files</span>}
                        {run.guessed && (
                          <span className='text-xs text-muted-foreground' title='The pages of this volume do not name their chapters: these are worked out from the volumes around it'>
                            (worked out, the pages do not say)
                          </span>
                        )}
                        {run.note && <span className='text-xs text-muted-foreground'>{run.note}</span>}
                      </div>
                    )}
                  </TableCell>
                  <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground'>{size ? formatBytes(size) : ''}</TableCell>
                  <TableCell className='w-0 py-0 pl-0'>
                    {/* Shown on the row's hover or focus; always where there is no hover. */}
                    {run.names.length > 0 && (
                      <div className='flex opacity-0 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100 pointer-coarse:opacity-100'>
                        <Button
                          size='sm'
                          variant='ghost'
                          aria-label={`Renumber ${rowFiles(run.names)}`}
                          title={`Give ${run.names.length > 1 ? 'these ' + run.names.length + ' files' : 'this file'} another volume or chapter number in this series`}
                          onClick={() => onRenumber(run.names)}
                        >
                          <ListOrdered data-icon='inline-start' /> Renumber
                        </Button>
                        <Button
                          size='sm'
                          variant='ghost'
                          aria-label={`Move ${rowFiles(run.names)} to another series`}
                          title={`Move ${run.names.length > 1 ? 'these ' + run.names.length + ' files' : 'this file'} into another series (a prequel, a spin-off)`}
                          onClick={() => onMove(run.names)}
                        >
                          <FolderInput data-icon='inline-start' /> Move
                        </Button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

/** What the picked graph entries can do to the collection: the anime library's actions, for manga. */
function CollectionActions({ nodes, root, collection, armed, busy, onArm, onAct }: {
  nodes: MangaGraphNode[];
  root: MangaDetail;
  collection: MangaDetail['collection'];
  armed: number | null;
  busy: boolean;
  onArm: (id: number | null) => void;
  onAct: (work: () => Promise<MangaLayoutPlan>, done: string) => Promise<void>;
}) {
  const note = (text: string) => <p className='text-xs text-muted-foreground'>{text}</p>;
  const members = nodes.filter((node) => node.in_collection || (!collection && node.anilist_id === root.anilist_id));
  const related = nodes.filter((node) => !members.includes(node));
  if (members.length > 0 && related.length > 0) return note('Pick entries of the collection or related ones, not both at once.');
  if (related.some((node) => (node.format === 'NOVEL') !== (root.kind === 'novel'))) {
    return note('Light novels and manga live in different libraries: they never share a collection.');
  }
  if (collection && members.length > 0) {
    if (members.length >= collection.members.length) return note('Pick fewer than all its entries to split some off.');
    return (
      <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => void onAct(
        () => api.splitMangaCollection(members.map((node) => node.anilist_id)),
        members.length === 1 ? members[0].title + ' split off' : members.length + ' entries split off into a new collection',
      )}>
        <Scissors data-icon='inline-start' /> {members.length === 1 ? 'Split off on its own' : `Split these ${members.length} off into a new collection`}
      </Button>
    );
  }
  if (!collection && members.length > 0) return note('Pick a related manga to put this one together with it.');
  if (related.length !== 1) return note('Pull related entries in one at a time.');
  const node = related[0];
  if (!collection) {
    return (
      <div className='flex flex-col gap-2'>
        {node.collection_id != null ? (
          <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => void onAct(
            () => api.includeInMangaCollection(root.anilist_id, { collection: node.collection_id! }),
            anilistName(root) + ' moved into ' + node.collection_name,
          )}>
            <ArrowRightToLine data-icon='inline-start' /> Move it into {node.collection_name}
          </Button>
        ) : (
          <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => void onAct(
            () => api.includeInMangaCollection(node.anilist_id, { with: root.anilist_id }),
            node.title + ' and ' + anilistName(root) + ' are a collection now',
          )}>
            <Plus data-icon='inline-start' /> Put the two together in a collection
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className='flex flex-col gap-2'>
      <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => void onAct(
        () => api.includeInMangaCollection(node.anilist_id, { collection: collection.id }),
        node.title + ' pulled into ' + collection.name,
      )}>
        <Plus data-icon='inline-start' /> Pull this entry into the collection
      </Button>
      {node.collection_id != null && node.collection_id !== collection.id && (
        armed === node.collection_id ? (
          <Button size='sm' variant='destructive' className='justify-start' disabled={busy} onClick={() => void onAct(
            () => api.mergeMangaCollection(collection.id, node.collection_id!),
            'Merged ' + node.collection_name + ' into ' + collection.name,
          )}>
            <Merge data-icon='inline-start' /> Yes, merge all of {node.collection_name}
          </Button>
        ) : (
          <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => onArm(node.collection_id ?? null)}>
            <Merge data-icon='inline-start' /> Merge its whole collection in: {node.collection_name}
          </Button>
        )
      )}
    </div>
  );
}

/** A collection's entry as its tab says it: its name past the collection's -- "Part 3: Stardust
 *  Crusaders" -- or its whole name where that leaves too little ("Tokyo Ghoul:re", not "re"). */
function memberLabel(member: string, collection: string) {
  const plain = (text: string) => text.replace(/[’‘]/g, "'").toLocaleLowerCase();
  if (!plain(member).startsWith(plain(collection))) return member;
  const rest = member.slice(collection.length).replace(/^[\s:.,\-–—]+/, '');
  return rest.length >= 3 ? rest : member;
}

/** One series: its own page (?series=<id>), or the right half of the split view. */
// Series answers kept while the page is open: a series opened again, or another entry of the
// collection, shows at once while its fresh answer comes (a click between entries waited 1-2 s).
const seen = new Map<number, MangaDetail>();

function SeriesView({ id, onOpen, onChanged }: { id: number | null; onOpen: (id: number) => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<MangaDetail | null>(null);
  const [releasesOpen, setReleasesOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Books ticked in the Chapters tab, and those being moved to another series.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [moving, setMoving] = useState<string[] | null>(null);
  // Moved to another series, or renumbered in this one.
  const [renumbering, setRenumbering] = useState(false);
  // The relations graph's dialog, and the question before the series is removed.
  const [graphOpen, setGraphOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // The collection's name being edited (null: not), and a collection change under way.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [regrouping, setRegrouping] = useState(false);
  // The graph's picked manga (Shift+click adds), and a merge waiting for its "yes".
  const [graphPicked, setGraphPicked] = useState<number[]>([]);
  const [armed, setArmed] = useState<number | null>(null);
  const loads = useRef(0);
  const navigate = useNavigate();
  // Why the open series could not be loaded: shown with Retry instead of a skeleton that never ends.
  const [failed, setFailed] = useState<string | null>(null);

  async function load(which: number) {
    const mine = ++loads.current;
    setFailed(null);
    try {
      const answer = await api.mangaSeries(which);
      seen.set(which, answer);
      if (mine === loads.current) setDetail(answer);
      // The collection's other entries fetched behind it, one after another, so a click on one is instant.
      for (const member of answer.collection?.members ?? []) {
        if (mine !== loads.current) break;
        if (member.anilist_id === which || seen.has(member.anilist_id)) continue;
        try {
          seen.set(member.anilist_id, await api.mangaSeries(member.anilist_id));
        } catch {
          // A failed look-ahead costs nothing: the click loads it as before.
        }
      }
    } catch (error: any) {
      if (mine === loads.current) setFailed(error.message || 'The series could not be loaded');
    }
  }

  useEffect(() => {
    setDetail(id !== null ? seen.get(id) ?? null : null);
    setReleasesOpen(false);
    setPicked(new Set());
    // A move left open (Back while its dialog was up) must not open on the next series with these names.
    setMoving(null);
    setGraphOpen(false);
    setConfirmRemove(false);
    setRenaming(null);
    setGraphPicked([]);
    setArmed(null);
    if (id !== null) void load(id);
  }, [id]);

  async function act(work: () => Promise<unknown>, done: string) {
    if (id === null) return;
    setBusy(true);
    try {
      await work();
      toast.success(done);
      await load(id);
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  /** A collection changed by hand; its folders follow on disk (journaled), which the toast says. */
  async function regroup(work: () => Promise<MangaLayoutPlan>, done: string) {
    if (id === null) return;
    setRegrouping(true);
    try {
      const result = await work();
      const moved = result.folders.length;
      toast.success(done + (moved ? ` — ${moved} folder${moved === 1 ? '' : 's'} moved` : ''));
      setGraphPicked([]);
      setArmed(null);
      await load(id);
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRegrouping(false);
    }
  }

  async function removeSeries() {
    if (id === null) return;
    setConfirmRemove(false);
    setBusy(true);
    try {
      const result = await api.removeMangaSeries(id);
      toast.success(`${name} removed and blacklisted — deleted ${result.deleted_files} file${result.deleted_files === 1 ? '' : 's'}`);
      await load(id);
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  function pick(names: string[], on: boolean) {
    setPicked((current) => {
      const next = new Set(current);
      for (const file of names) {
        if (on) next.add(file);
        else next.delete(file);
      }
      return next;
    });
  }

  const name = detail ? anilistName(detail) : '';
  const collection = detail?.collection ?? null;
  const graphNodes = useMemo(() => new Map((detail?.graph.nodes ?? []).map((node) => [node.anilist_id, node])), [detail]);
  const pickedNodes = graphPicked.map((pick) => graphNodes.get(pick)).filter((node): node is MangaGraphNode => Boolean(node));
  const blacklisted = detail?.policy?.mode === 'blacklisted';
  const byHand = detail?.policy?.mode === 'followed';
  const sizes = useMemo(() => new Map((detail?.files ?? []).map((file) => [file.name, file.size])), [detail]);
  // The ticked books still in the folder: the bar's count and what it moves.
  const ticked = useMemo(
    () => [...new Set((detail?.breakdown.rows ?? []).flatMap((run) => run.names))].filter((file) => picked.has(file)),
    [detail, picked],
  );
  const breakdown = detail?.breakdown;
  const noun = breakdown?.unit === 'volume' ? 'volumes' : 'chapters';
  const total = breakdown?.last ? breakdown.last - breakdown.first + 1 : 0;
  const state = holding(breakdown?.have ?? 0, total, detail?.files.length ?? 0, detail?.status === 'FINISHED', noun);
  return (
    <>
        {detail === null && failed !== null ? (
          <Empty>
            <EmptyTitle>This series could not be loaded</EmptyTitle>
            <EmptyDescription>{failed}</EmptyDescription>
            {id !== null && <Button variant='secondary' onClick={() => void load(id)}>Try again</Button>}
          </Empty>
        ) : detail === null ? (
          <div className='flex flex-col gap-4'>
            <Skeleton className='h-24 w-full' />
            <Skeleton className='h-96 w-full' />
          </div>
        ) : (
          <>
            <div className='flex flex-col gap-2'>
              <div className='flex items-start gap-4'>
                <AnimePoster url={detail.cover} title={name} className='w-16 shrink-0' />
                <div className='flex min-w-0 flex-col gap-2'>
                  <div className='flex flex-wrap items-center gap-x-3 gap-y-1'>
                    <h2 className='font-heading text-[22px] leading-[1.1] font-light tracking-[-0.02em] text-balance text-foreground'>
                      {collection ? collection.name : <>{name}{detail.year ? ' (' + detail.year + ')' : ''}</>}
                      {/* Beside the title, as the anime library's: the entry on AniList, and the collection's rename. */}
                      <span className='ml-2 inline-flex items-center gap-0.5 whitespace-nowrap align-[0.1em]'>
                        <a
                          href={anilistMangaUrl(detail.anilist_id)}
                          target='_blank'
                          rel='noreferrer'
                          className='inline-flex size-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-ring'
                          aria-label='Open on AniList'
                          title={collection ? 'This entry on AniList' : 'On AniList'}
                        >
                          <ExternalLink className='size-4' />
                        </a>
                        {collection && (
                          <button
                            type='button'
                            onClick={() => setRenaming(collection.custom ?? collection.name)}
                            className='inline-flex size-6 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-ring'
                            aria-label='Rename collection'
                            title={collection.custom ? 'Renamed by hand: rename it again, or give it back the name its entries share' : 'Give the collection a name of your own, for its folder too'}
                          >
                            <Pencil className='size-4' />
                          </button>
                        )}
                      </span>
                    </h2>
                    <StatusTag variant={state.variant} title={state.title}>{state.label}</StatusTag>
                  </div>
                  {collection && <p className='text-sm text-foreground'>{name}{detail.year ? ' (' + detail.year + ')' : ''}</p>}
                  <p className='font-mono text-[0.7rem] text-muted-foreground'>
                    {[mangaFacts(detail), detail.status?.toLowerCase().replace('_', ' '), detail.authors.join(', ')].filter(Boolean).join(' · ')}
                  </p>
                  <div className='flex flex-wrap gap-1'>
                    {blacklisted && <StatusTag variant='destructive'>Blacklisted</StatusTag>}
                    {detail.genres.map((genre) => <StatusTag key={genre} variant='outline'>{genre}</StatusTag>)}
                  </div>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => setGraphOpen(true)}
                      title='How this series relates on AniList: its anime, sequels, spin-offs and other versions'
                    >
                      <Network data-icon='inline-start' /> Relations
                    </Button>
                    {/* A finished series the library has whole is not looked for: no releases to see, unless some still wait. */}
                    {(state.label !== 'Complete' || (detail.waiting ?? 0) > 0) && <Button
                      size='sm'
                      variant='secondary'
                      onClick={() => setReleasesOpen(true)}
                      title='Every release bankai found on nyaa for this series, and what became of it'
                    >
                      <Download data-icon='inline-start' /> Releases
                      <span className='font-mono text-[0.68rem] text-muted-foreground'>{detail.releases.length}</span>
                    </Button>}
                    {blacklisted ? (
                      <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(() => api.restoreMangaSeries(detail.anilist_id), name + ' restored')}>
                        <RotateCcw data-icon='inline-start' /> Restore
                      </Button>
                    ) : (
                      <>
                        {byHand ? (
                          <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(() => api.unfollowMangaSeries(detail.anilist_id), 'No longer following ' + name)}>
                            <StarOff data-icon='inline-start' /> Unfollow
                          </Button>
                        ) : detail.why.length === 0 && (
                          <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(() => api.followMangaSeries(detail.anilist_id), 'Following ' + name)}>
                            <Star data-icon='inline-start' /> Follow
                          </Button>
                        )}
                        {/* With books on disk it takes them too; asked first. */}
                        {detail.files.length > 0 ? (
                          <Button size='sm' variant='destructive' disabled={busy} title='Delete this series from disk and never look for it again' onClick={() => setConfirmRemove(true)}>
                            <Trash2 data-icon='inline-start' /> Remove and blacklist
                          </Button>
                        ) : (
                          <Button size='sm' variant='destructive' disabled={busy} title='Never look for this series again' onClick={() => void act(() => api.blacklistMangaSeries(detail.anilist_id), name + ' blacklisted')}>
                            <Ban data-icon='inline-start' /> Blacklist
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </div>
            </div>
            {/* The collection's entries, where the Chapters / Releases tabs were: the chapters are the
                page itself, the releases a dialog beside Relations. */}
            {collection && (
              <Tabs value={String(detail.anilist_id)} onValueChange={(value) => onOpen(Number(value))}>
                <TabsList className='h-auto max-w-full flex-wrap'>
                  {collection.members.map((member) => (
                    <TabsTab
                      key={member.anilist_id}
                      value={String(member.anilist_id)}
                      className={cn(!member.on_disk && 'text-muted-foreground')}
                      title={member.name + (member.year ? ' (' + member.year + ')' : '') + (member.on_disk ? '' : ': not in your library')}
                    >
                      {memberLabel(member.name, collection.name)}
                    </TabsTab>
                  ))}
                </TabsList>
              </Tabs>
            )}
            <ChapterBreakdown
              breakdown={detail.breakdown}
              sizes={sizes}
              waiting={detail.waiting ?? 0}
              picked={picked}
              onPick={pick}
              onReview={() => navigate('/m/review')}
              onMove={(names) => { setRenumbering(false); setMoving(names); }}
              onRenumber={(names) => { setRenumbering(true); setMoving(names); }}
            />
            <Dialog open={releasesOpen} onOpenChange={setReleasesOpen}>
              <DialogContent size='lg' className='max-h-[90vh] max-w-4xl overflow-y-auto'>
                <DialogHeader>
                  <DialogTitle>Releases of {name}</DialogTitle>
                  <DialogDescription>Every release found on nyaa for this series: waiting for review, downloading, filed (and what went into the library), discarded or already had.</DialogDescription>
                </DialogHeader>
                <DialogBody>
                {detail.releases.length === 0 ? (
                  <p className='text-sm text-muted-foreground'>No release of it found on nyaa yet.</p>
                ) : (
                  <Table variant='plain'>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Release</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className='text-right'>Size</TableHead>
                        <TableHead>Found</TableHead>
                        <TableHead className='text-right' />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.releases.map((release) => (
                        <TableRow key={release.info_hash}>
                          <TableCell className='max-w-xl'>
                            <TruncatedText text={release.title} className='line-clamp-2 break-words text-sm' />
                            {releaseSpan(release) && <span className='font-mono text-[0.66rem] text-muted-foreground'>{releaseSpan(release)}</span>}
                            {release.status === 'done' && release.copied && <span className='block'><FiledNote release={release} /></span>}
                          </TableCell>
                          <TableCell><ReleaseStatus release={release} /></TableCell>
                          <TableCell className='whitespace-nowrap text-right font-mono text-xs tabular-nums'>{formatBytes(release.size_bytes)}</TableCell>
                          <TableCell className='whitespace-nowrap text-xs text-muted-foreground'>{timeAgo(release.found_at)}</TableCell>
                          <TableCell>
                            <div className='flex justify-end gap-1'>
                              {release.status === 'review' && (
                                <>
                                  <Button size='sm' variant='primary' disabled={busy} onClick={() => void act(() => api.downloadMangaRelease(release.info_hash), 'Downloading ' + release.title)}>
                                    <Download data-icon='inline-start' /> Download
                                  </Button>
                                  <Button size='icon-sm' variant='ghost' title='Discard: never offered again' aria-label={'Discard ' + release.title} disabled={busy} onClick={() => void act(() => api.discardMangaRelease(release.info_hash), 'Discarded')}>
                                    <Trash2 />
                                  </Button>
                                </>
                              )}
                              <Button
                                size='icon-sm'
                                variant='ghost'
                                className='text-muted-foreground'
                                nativeButton={false}
                                render={<a href={release.detail_url} target='_blank' rel='noreferrer' title='The release on nyaa' aria-label={release.title + ' on nyaa'} />}
                              >
                                <ExternalLink />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
                </DialogBody>
              </DialogContent>
            </Dialog>
            {/* Under the rows and kept in sight at the bottom, as the anime library's: ticking never moves them. */}
            {ticked.length > 0 && (
              <div className='sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0'>
                <span className='text-xs text-muted-foreground'>
                  <span className='font-mono tabular-nums text-foreground'>{ticked.length}</span> file{ticked.length === 1 ? '' : 's'} ticked
                </span>
                <Button size='sm' onClick={() => { setRenumbering(false); setMoving(ticked); }} title='Files that are another manga (a prequel, a spin-off): move them into that series'>
                  <FolderInput data-icon='inline-start' /> Move to another series
                </Button>
                <Button size='sm' variant='secondary' onClick={() => { setRenumbering(true); setMoving(ticked); }} title='Give the ticked files other volume or chapter numbers in this series'>
                  <ListOrdered data-icon='inline-start' /> Renumber
                </Button>
                <Button size='sm' variant='ghost' className='ml-auto' onClick={() => setPicked(new Set())}>
                  <X data-icon='inline-start' /> Clear
                </Button>
              </div>
            )}
            {/* The graph is the dialog's whole body, edge to edge under the title, as the anime library's. */}
            <Dialog open={graphOpen} onOpenChange={(open) => { if (!open) { setGraphOpen(false); setGraphPicked([]); setArmed(null); } }}>
              <DialogContent className='h-[98dvh] max-h-none w-[98vw] max-w-none shrink-0 gap-0 p-0 sm:p-0'>
                <DialogHeader className='px-4 pb-3 pt-4 sm:px-5 sm:pt-5'>
                  <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {collection ? collection.name : name}</DialogTitle>
                  <DialogDescription>
                    {collection
                      ? 'Its collection in the middle row; what relates to it in bands, anime above, other manga and light novels below. Pick entries to split them off, or related ones to pull in or merge their collection.'
                      : 'In no collection. What relates to it in bands, anime above, other manga and light novels below: pick a related manga to put the two together, or to move this one into its collection.'}
                  </DialogDescription>
                </DialogHeader>
                <div className='relative min-h-0 flex-1 overflow-hidden border-t border-border'>
                  {graphOpen && (
                    <MangaGraph
                      graph={detail.graph}
                      selected={graphPicked}
                      onPick={(node, add) => {
                        setArmed(null);
                        setGraphPicked((current) => add
                          ? (current.includes(node.anilist_id) ? current.filter((other) => other !== node.anilist_id) : [...current, node.anilist_id])
                          : (current.length === 1 && current[0] === node.anilist_id ? [] : [node.anilist_id]));
                      }}
                      onOpen={(node) => window.open(anilistUrl(node.anilist_id), '_blank', 'noreferrer')}
                    />
                  )}
                  {graphOpen && graphPicked.length === 0 && (
                    <p className='pointer-events-none absolute right-3 top-3 rounded-button material-menu px-2.5 py-1 text-xs text-muted-foreground'>
                      Click a manga to change the collection · Shift+click to pick more · an anime opens on AniList
                    </p>
                  )}
                  {/* What can be done with the picked entries, over the graph, as the anime library's. */}
                  {pickedNodes.length > 0 && (
                    <div className='absolute right-3 top-3 z-20 flex max-h-[calc(100%-24px)] w-72 flex-col gap-3 overflow-y-auto rounded-control material-menu p-3 text-sm shadow-overlay'>
                      <ul className='flex flex-col gap-1.5'>
                        {pickedNodes.map((node) => (
                          <li key={node.anilist_id} className='min-w-0'>
                            <span className='block break-words text-foreground'>{node.title}</span>
                            <span className='block truncate font-mono text-[0.65rem] text-muted-foreground'>
                              {[formatLabel(node.format), node.year, node.on_disk ? null : 'not in your library'].filter(Boolean).join(' · ')}
                            </span>
                            {!node.in_collection && node.collection_name && <span className='block break-words text-[0.65rem] text-cyan-300'>In {node.collection_name}</span>}
                          </li>
                        ))}
                      </ul>
                      <CollectionActions
                        nodes={pickedNodes}
                        root={detail}
                        collection={collection}
                        armed={armed}
                        busy={regrouping}
                        onArm={setArmed}
                        onAct={regroup}
                      />
                      {pickedNodes.length === 1 && pickedNodes[0].anilist_id !== detail.anilist_id && (
                        <Button size='sm' variant='ghost' className='justify-start' onClick={() => { setGraphOpen(false); onOpen(pickedNodes[0].anilist_id); }}>
                          <PanelRightOpen data-icon='inline-start' /> Open this series
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              </DialogContent>
            </Dialog>
            <Dialog open={renaming !== null} onOpenChange={(open) => { if (!open) setRenaming(null); }}>
              <DialogContent size='sm'>
                <DialogHeader>
                  <DialogTitle>Rename the collection</DialogTitle>
                  <DialogDescription>
                    Its name in the library and its folder's on disk, where its entries' folders move along. Empty gives it back the name
                    its entries share.
                  </DialogDescription>
                </DialogHeader>
                <form
                  className='flex flex-col gap-4'
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (!collection || renaming === null) return;
                    const next = renaming.trim();
                    setRenaming(null);
                    void regroup(() => api.renameMangaCollection(collection.id, next), next ? 'Renamed to ' + next : 'The shared name is back');
                  }}
                >
                  <Input autoFocus value={renaming ?? ''} onChange={(event) => setRenaming(event.target.value)} placeholder={collection?.name} />
                  <DialogFooter>
                    <Button type='button' variant='secondary' onClick={() => setRenaming(null)}>Cancel</Button>
                    <Button type='submit'>Rename</Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
            <Dialog open={confirmRemove} onOpenChange={(open) => { if (!open) setConfirmRemove(false); }}>
              <DialogContent size='sm'>
                <DialogHeader>
                  <DialogTitle>Remove {name}?</DialogTitle>
                  <DialogDescription>
                    This deletes its folder with {detail.files.length} book{detail.files.length === 1 ? '' : 's'}
                    {' (' + formatBytes(detail.files.reduce((sum, file) => sum + file.size, 0)) + ')'} from disk and stops its downloads.
                    It also blacklists the series, so it is never looked for again; it moves to the Blacklist, where you can restore it.
                    The deleted files cannot be recovered.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant='secondary' onClick={() => setConfirmRemove(false)}>Cancel</Button>
                  <Button variant='destructive' onClick={() => void removeSeries()}>
                    <Trash2 data-icon='inline-start' /> Delete and blacklist
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <MoveMangaFiles
              from={{ id: detail.anilist_id, name }}
              files={moving}
              renumber={renumbering}
              onClose={() => setMoving(null)}
              onMoved={() => {
                // Only what moved leaves the ticked: another row ticked meanwhile stays.
                pick(moving ?? [], false);
                void load(detail.anilist_id);
                onChanged();
              }}
            />
          </>
        )}
    </>
  );
}

// -- The series, as posters or as a table ----------------------------------------------

/** A row of the lists: a series, or a collection standing in its first entry's place. */
type Row = MangaSeries & { collection?: { id: number; name: string; members: MangaSeries[] } };

/**
 * A list's series with each collection's (two or more of them in the list) in one row, as the
 * anime library's: its first entry's poster and place, the collection's name, every entry's books
 * counted. It opens on its first entry, the others a tab away.
 */
function grouped(items: MangaSeries[], collections: MangaCollection[]): Row[] {
  const owner = new Map<number, MangaCollection>();
  for (const collection of collections) for (const member of collection.members) owner.set(member, collection);
  const inList = new Map<number, MangaSeries[]>();
  for (const series of items) {
    const collection = owner.get(series.anilist_id);
    if (collection) inList.set(collection.id, [...(inList.get(collection.id) ?? []), series]);
  }
  const rows: Row[] = [];
  const placed = new Set<number>();
  for (const series of items) {
    const collection = owner.get(series.anilist_id);
    const members = collection ? inList.get(collection.id) ?? [] : [];
    if (!collection || members.length < 2) {
      rows.push(series);
      continue;
    }
    if (placed.has(collection.id)) continue;
    placed.add(collection.id);
    members.sort((a, b) => collection.members.indexOf(a.anilist_id) - collection.members.indexOf(b.anilist_id));
    const sum = (pick: (member: MangaSeries) => number) => members.reduce((total, member) => total + pick(member), 0);
    const first = members[0];
    rows.push({
      ...first,
      name: collection.name,
      english: collection.name,
      romaji: collection.name,
      owned: '',
      volumes: members.every((member) => member.volumes != null) ? sum((member) => member.volumes ?? 0) : null,
      owned_volumes: sum((member) => member.owned_volumes ?? 0),
      owned_chapters: sum((member) => member.owned_chapters ?? 0),
      last_volume: null,
      books: sum((member) => member.books ?? 0),
      size: sum((member) => member.size ?? 0),
      count: first.count && { unit: first.count.unit, have: sum((member) => member.count?.have ?? 0), total: sum((member) => member.count?.total ?? 0) },
      complete: members.every((member) => member.complete),
      waiting: sum((member) => member.waiting ?? 0),
      collection: { id: collection.id, name: collection.name, members },
    });
  }
  return rows;
}

/** "1986–2011": the years a collection's entries began in. */
function yearSpan(members: MangaSeries[]) {
  const years = members.map((member) => member.year).filter((year): year is number => Boolean(year));
  if (!years.length) return '';
  const [first, last] = [Math.min(...years), Math.max(...years)];
  return first === last ? String(first) : first + '–' + last;
}

/** Whether a row is the open series: a collection's row when it is any of its entries. */
function isOpen(row: Row, current: number | null) {
  return current !== null && (row.anilist_id === current || Boolean(row.collection?.members.some((member) => member.anilist_id === current)));
}

function SeriesGrid({ items, onOpen }: { items: Row[]; onOpen: (id: number) => void }) {
  return (
    <PosterGrid>
      {items.map((series) => (
        <PosterCard
          key={series.anilist_id}
          cover={series.cover}
          title={anilistName(series)}
          onOpen={() => onOpen(series.anilist_id)}
          topLeft={series.kind === 'novel' ? <StatusTag variant='outline' className={CORNER_TAG}>Light novel</StatusTag> : null}
          topRight={(series.waiting ?? 0) > 0 ? <StatusTag variant='warning' className={CORNER_TAG}>{series.waiting} to review</StatusTag> : null}
          bottomLeft={series.collection ? <StatusTag variant='outline' className={CORNER_TAG}>{series.collection.members.length} entries</StatusTag> : null}
        >
          <span className='truncate font-mono text-[0.68rem] text-muted-foreground'>
            {series.collection
              ? `${series.owned_volumes} vols · ${formatSize(series.size ?? 0)}`
              : series.owned || [series.year, series.volumes ? series.volumes + ' vols' : null].filter(Boolean).join(' · ') || ' '}
          </span>
        </PosterCard>
      ))}
    </PosterGrid>
  );
}

function SeriesTable({ items, sort, onSort, onOpen, split = false, current = null }: { items: Row[]; sort: SortState<SortKey> | null; onSort: (key: SortKey) => void; onOpen: (id: number) => void; split?: boolean; current?: number | null }) {
  return (
    // In its own container, as the anime library's; the header stays put as it scrolls. Plain,
    // across the page's whole width: through its gutter, the cells' padding lining up with it.
    <Table variant='plain' containerProps={{ className: split ? 'page-ground @container h-full overflow-auto' : 'page-ground @container -mx-(--page-gutter) w-auto min-h-0 flex-1 overflow-auto' }}>
      <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
        <TableRow>
          <SortHeader label='Series' column='title' sort={sort} onSort={onSort} className='w-full' />
          <SortHeader label='Volumes' column='volumes' sort={sort} onSort={onSort} />
          <SortHeader label='Chapters' column='chapters' sort={sort} onSort={onSort} className={COLUMN_CHAPTERS} />
          <SortHeader label='Size' column='size' sort={sort} onSort={onSort} align='right' className={COLUMN_SIZE} />
          <SortHeader label='State' column='state' sort={sort} onSort={onSort} className={COLUMN_STATE} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((series) => {
          const name = anilistName(series);
          const state = seriesHolding(series);
          return (
            <TableRow
              key={series.anilist_id}
              tabIndex={0}
              onClick={() => onOpen(series.anilist_id)}
              aria-current={split && isOpen(series, current) ? 'true' : undefined}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(series.anilist_id); } }}
              className='data-row cursor-pointer aria-current:bg-control-surface focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-control-focus'
            >
              <TableCell className='max-w-0'>
                <div className='flex items-center gap-2.5'>
                  <AnimePoster url={series.cover} title={name} className='w-8 shrink-0 rounded' />
                  <div className='min-w-0'>
                    <p className='truncate font-medium text-foreground' title={name}>{name}</p>
                    <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                      {series.collection
                        ? [`${series.collection.members.length} entries`, yearSpan(series.collection.members)].filter(Boolean).join(' · ')
                        : [series.year, formatLabel(series.format), series.status?.toLowerCase().replace(/_/g, ' ')].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                <CountMeter have={series.owned_volumes ?? 0} total={series.volumes ?? Math.max(series.owned_volumes ?? 0, series.last_volume ?? 0)} noun='volumes' />
              </TableCell>
              <TableCell className={COLUMN_CHAPTERS}>
                {series.count?.unit === 'chapter'
                  ? <CountMeter have={series.count.have} total={series.count.total} noun='chapters' />
                  : <span className='font-mono text-[0.68rem] text-muted-foreground'>—</span>}
              </TableCell>
              <TableCell numeric className={cn(COLUMN_SIZE, 'whitespace-nowrap text-xs text-foreground')}>{formatSize(series.size ?? 0)}</TableCell>
              <TableCell className={COLUMN_STATE}>
                <div className='flex flex-wrap items-center gap-1.5 @5xl:flex-nowrap @5xl:whitespace-nowrap'>
                  <StatusTag variant={state.variant} title={state.title}>{state.label}</StatusTag>
                  {(series.waiting ?? 0) > 0 && <StatusTag variant='warning'>{series.waiting} to review</StatusTag>}
                  {series.kind === 'novel' && <StatusTag variant='muted'>Light novel</StatusTag>}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export default function MangaLibrary() {
  const [data, setData] = useState<Library | null>(() => recall<Library>(pagePaths.mangaLibrary) ?? null);
  const [tab, setTab] = useState<Tab>('library');
  const { view, setView, wide, split } = useLibraryView('manga');
  // The split view holds the Library and Followed lists; from another tab a series opens as its page.
  const splitHere = split && (tab === 'library' || tab === 'followed');
  // The open series lives in the address (?series=<id>): see useLibraryAddress.
  const address = useLibraryAddress('manga', ['series'], { split: splitHere });
  const [sort, setSort] = useState<SortState<SortKey> | null>(null);
  const [query, setQuery] = useState('');
  const [matchFolder, setMatchFolder] = useState<MangaFolder | null>(null);
  // What laying the manga out by collection would move (nothing moves until asked), and its dialog.
  const [layout, setLayout] = useState<MangaLayoutPlan | null>(null);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [laying, setLaying] = useState(false);
  const selected = address.current ? Number(address.current.key) : null;

  async function load() {
    try {
      setData(await api.mangaLibrary());
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  // What laying out would move: on opening and after a change, not with every refresh (it reads every
  // folder). Not there before the server knows it: no banner then.
  function loadLayout() {
    api.mangaLayout().then(setLayout, () => setLayout(null));
  }

  async function refresh() {
    loadLayout();
    await load();
  }

  async function layOut() {
    setLaying(true);
    try {
      const result = await api.applyMangaLayout();
      toast.success(`Laid out: ${result.folders.length} folder${result.folders.length === 1 ? '' : 's'} moved, ${result.files.length} book${result.files.length === 1 ? '' : 's'} renamed`);
      setLayoutOpen(false);
      await refresh();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLaying(false);
    }
  }
  const pending = layout ? layout.folders.length + layout.files.length : 0;
  const gathering = layout?.collections.filter((collection) => collection.folder) ?? [];

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);


  function open(id: number) {
    if (id !== selected) address.open('series', String(id));
  }
  const close = address.close;

  // The list follows the typing a moment behind, so the search field never waits for it.
  const needle = useDeferredValue(query.trim().toLocaleLowerCase());
  const shown = useMemo(() => {
    const pick = (items: MangaSeries[]): Row[] => {
      const kept = grouped(items.filter((series) =>
        !needle || [series.name, series.romaji, series.english, series.folder].some((text) => (text ?? '').toLocaleLowerCase().includes(needle))), data?.collections ?? []);
      if (!sort) return kept;
      const key = SORTERS[sort.key];
      return [...kept].sort((a, b) => {
        const left = key(a);
        const right = key(b);
        const order = typeof left === 'string' ? left.localeCompare(String(right)) : left - (right as number);
        return sort.dir === 'asc' ? order : -order;
      });
    };
    return { series: pick(data?.series ?? []), followed: pick(data?.followed ?? []) };
  }, [data, needle, sort]);
  const active = data?.downloads.filter((release) => ['queued', 'downloading', 'publishing', 'failed'].includes(release.status)).length ?? 0;
  const totals = useMemo(() => ({
    volumes: (data?.series ?? []).reduce((sum, series) => sum + (series.owned_volumes ?? 0), 0),
    chapters: (data?.series ?? []).reduce((sum, series) => sum + (series.owned_chapters ?? 0), 0),
    size: (data?.series ?? []).reduce((sum, series) => sum + (series.size ?? 0), 0),
  }), [data]);

  const onSort = (key: SortKey) => setSort((current) => nextSort(current, key, FIRST_DIRECTION));
  const listing = (items: Row[]) => view === 'grid'
    ? <SeriesGrid items={items} onOpen={open} />
    : !split
      ? <SeriesTable items={items} sort={sort} onSort={onSort} onOpen={open} />
      : (
        // The page's whole width, the open series beside the table; the divider is dragged.
        <LibrarySplit
          id='manga'
          list={<SeriesTable items={items} sort={sort} onSort={onSort} onOpen={open} split current={selected} />}
          open={selected !== null}
          onClose={close}
          empty={{ title: 'Pick a series', description: 'Its chapters, relations and releases open here, beside the list.', icon: <BookOpen /> }}
        >
          {selected !== null && <SeriesView id={selected} onOpen={open} onChanged={() => void refresh()} />}
        </LibrarySplit>
      );

  // Every dialog, whichever way the page is laid out.
  const dialogs = (
    <>
    <Dialog open={layoutOpen} onOpenChange={(open) => { if (!open && !laying) setLayoutOpen(false); }}>
      <DialogContent size='xl'>
        <DialogHeader>
          <DialogTitle>Lay the manga out by collection</DialogTitle>
          <DialogDescription>
            Each collection with two or more entries on disk gets a folder of its own, the entries' folders inside it, named so OpenPanel
            reads their AniList entry; books get the library's names. OpenPanel keeps each entry a series of its own and finds the moved
            books by their pages. Every move is journaled and can be put back.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-4 text-sm'>
          {layout && layout.folders.length > 0 && (
            <div className='flex flex-col gap-1'>
              <p className='font-medium text-foreground'>{layout.folders.length} folder{layout.folders.length === 1 ? '' : 's'} move</p>
              {layout.folders.map((move) => (
                <p key={move.anilist_id} className='font-mono text-xs text-muted-foreground'>{move.from} <span className='text-foreground'>→</span> {move.to}</p>
              ))}
            </div>
          )}
          {layout && layout.files.length > 0 && (
            <div className='flex flex-col gap-1'>
              <p className='font-medium text-foreground'>{layout.files.length} book{layout.files.length === 1 ? '' : 's'} renamed</p>
              {layout.files.map((rename) => (
                <p key={rename.folder + '/' + rename.from} className='font-mono text-xs text-muted-foreground'>{rename.folder}: {rename.from} <span className='text-foreground'>→</span> {rename.to}</p>
              ))}
            </div>
          )}
          {layout && layout.skipped.length > 0 && (
            <div className='flex flex-col gap-1'>
              <p className='font-medium text-foreground'>{layout.skipped.length} left as they are</p>
              {layout.skipped.map((skip) => (
                <p key={skip.from + skip.to} className='font-mono text-xs text-muted-foreground'>{skip.from} → {skip.to}: {skip.why}</p>
              ))}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant='secondary' disabled={laying} onClick={() => setLayoutOpen(false)}>Cancel</Button>
          <Button disabled={laying || pending === 0} onClick={() => void layOut()}>
            <FolderTree data-icon='inline-start' /> {laying ? 'Laying out…' : 'Lay out'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <MangaPickDialog
      query={matchFolder ? matchFolder.title : null}
      kind={matchFolder?.kind ?? null}
      title={matchFolder ? 'Which series is “' + matchFolder.folder + '”?' : ''}
      action='Match'
      onClose={() => setMatchFolder(null)}
      onPick={async (entry: MangaEntry) => {
        const folder = matchFolder;
        if (!folder) return;
        setMatchFolder(null);
        void inBackground(api.matchMangaFolder(folder.kind, folder.folder, entry.anilist_id), {
          loading: 'Matching ' + folder.folder + '…',
          success: folder.folder + ' is ' + anilistName(entry),
        }).then(() => load(), () => undefined);
      }}
    />
    </>
  );

  // A series opened outside the split view is a page of its own.
  if (selected !== null && !splitHere) {
    return (
      <TitlePage onBack={close}>
        <SeriesView id={selected} onOpen={open} onChanged={() => void refresh()} />
        {dialogs}
      </TitlePage>
    );
  }

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>Manga library</h1>
        <p className='text-sm text-muted-foreground'>
          Your manga and light novels, in the folders OpenPanel reads: a folder per series, "Vol 01.cbz" inside. Followed are the series
          bankai looks for: AniList's links to the anime in your library, what is in your folders, and what you follow from Search.
        </p>
      </div>
      {pending > 0 && (
        // Nothing moves on its own: the collections' folders wait for a word.
        <div className='flex flex-wrap items-center gap-3 rounded-control surface-container px-4 py-3 text-sm'>
          <FolderTree className='size-4 shrink-0 text-muted-foreground' />
          <span className='min-w-0 flex-1'>
            {gathering.length > 0
              ? `${gathering.map((collection) => collection.name).join(', ')} can get a folder of their own on disk: `
              : ''}
            {[
              layout!.folders.length ? `${layout!.folders.length} folder${layout!.folders.length === 1 ? '' : 's'} move` : null,
              layout!.files.length ? `${layout!.files.length} book${layout!.files.length === 1 ? '' : 's'} get the library's names` : null,
            ].filter(Boolean).join(', ')}.
          </span>
          <Button size='sm' onClick={() => setLayoutOpen(true)}>See what moves</Button>
        </div>
      )}
      {data === null ? <Skeleton className='h-96 w-full' /> : (
        <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)} className='gap-4'>
          <div className='flex flex-wrap items-center justify-between gap-2'>
            <TabsList>
              <TabsTab value='library'>Library <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{data.series.length}</span></TabsTab>
              <TabsTab value='followed'>Followed <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{data.followed.length}</span></TabsTab>
              <TabsTab value='downloads'>Downloads <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{active}</span></TabsTab>
              {data.unmatched.length > 0 && (
                <TabsTab value='match'>To match <span className='ml-1.5 font-mono text-[0.68rem] text-warning'>{data.unmatched.length}</span></TabsTab>
              )}
            </TabsList>
            <div className='flex flex-wrap items-center gap-2'>
              <InputGroup className='w-64'>
                <InputGroupAddon><Search /></InputGroupAddon>
                <InputGroupInput placeholder='Filter series' value={query} onChange={(event) => setQuery(event.target.value)} />
              </InputGroup>
              <ViewSwitch view={view} onView={setView} wide={wide} />
            </div>
          </div>
          <TabsPanel value='library' className='flex flex-col gap-3'>
            {data.series.length === 0 ? (
              <Empty>
                <EmptyMedia><BookOpen /></EmptyMedia>
                <EmptyTitle>No series yet</EmptyTitle>
                <EmptyDescription>The folders in your manga and light novel libraries show here once bankai knows which series each is.</EmptyDescription>
              </Empty>
            ) : (
              <>
                {listing(shown.series)}
                <LibraryFooter>
                  <FooterStat value={shown.series.length}>series{shown.series.length !== data.series.length ? ` of ${data.series.length}` : ''}</FooterStat>
                  <FooterStat value={totals.volumes.toLocaleString()}>volumes</FooterStat>
                  <FooterStat value={totals.chapters.toLocaleString()}>chapter files</FooterStat>
                  <FooterStat value={formatSize(totals.size)}>stored</FooterStat>
                </LibraryFooter>
              </>
            )}
          </TabsPanel>
          <TabsPanel value='followed'>
            {data.followed.length === 0 ? (
              <Empty>
                <EmptyMedia><Star /></EmptyMedia>
                <EmptyTitle>Nothing followed beyond your library</EmptyTitle>
                <EmptyDescription>The manga and light novels of your anime show here as AniList's links are learned, a few batches every five minutes.</EmptyDescription>
              </Empty>
            ) : listing(shown.followed)}
          </TabsPanel>
          <TabsPanel value='downloads'>
            {data.downloads.length === 0 ? (
              <Empty>
                <EmptyMedia><Download /></EmptyMedia>
                <EmptyTitle>No downloads</EmptyTitle>
                <EmptyDescription>Releases you download from the review show here until they are in your library, and for a week after.</EmptyDescription>
              </Empty>
            ) : (
              <Table variant='plain' containerProps={{ className: '-mx-(--page-gutter) w-auto' }}>
                <TableHeader>
                  <TableRow>
                    <TableHead>Series</TableHead>
                    <TableHead>Release</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className='text-right'>Size</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.downloads.map((release) => (
                    <TableRow key={release.info_hash} className='cursor-pointer' onClick={() => release.anilist_id && open(release.anilist_id)}>
                      <TableCell className='max-w-56'><TruncatedText text={release.name || ''} className='line-clamp-2 text-sm' /></TableCell>
                      <TableCell className='max-w-xl'>
                        <TruncatedText text={release.title} className='line-clamp-2 break-words text-sm' />
                        {release.status === 'done' && release.copied && <FiledNote release={release} />}
                        {release.status === 'failed' && (
                          <span className='mt-1 flex flex-wrap items-center gap-2'>
                            {release.error && <span className='text-xs text-destructive'>{release.error}</span>}
                            <Button
                              size='sm'
                              variant='secondary'
                              onClick={(event) => {
                                event.stopPropagation();
                                void api.downloadMangaRelease(release.info_hash)
                                  .then(() => { toast.success('Trying ' + release.title + ' again'); void load(); })
                                  .catch((error: any) => toast.error(error.message));
                              }}
                            >
                              <RotateCcw data-icon='inline-start' /> Try again
                            </Button>
                          </span>
                        )}
                      </TableCell>
                      <TableCell className='w-44'>
                        <div className='flex flex-col gap-1'>
                          <ReleaseStatus release={release} />
                          {release.status === 'downloading' && release.progress != null && <Progress value={Math.round(release.progress * 100)} />}
                        </div>
                      </TableCell>
                      <TableCell className='whitespace-nowrap text-right font-mono text-xs tabular-nums'>{formatBytes(release.size_bytes)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </TabsPanel>
          <TabsPanel value='match'>
            <div className='flex flex-col gap-2'>
              <p className='text-sm text-muted-foreground'>
                Folders bankai could not tell the series of: no AniList id in the name, none in OpenPanel, and no title on AniList that is the
                folder's name exactly. Matched, new volumes of the series go into the folder.
              </p>
              <Card className='divide-y divide-border p-0'>
                {data.unmatched.map((folder) => (
                  <div key={folder.kind + '/' + folder.folder} className='flex items-center gap-3 px-4 py-2.5'>
                    <FolderSearch className='size-4 shrink-0 text-muted-foreground' />
                    <span className='min-w-0 flex-1 truncate text-sm'>{folder.folder}</span>
                    <StatusTag variant='muted'>{folder.kind === 'novel' ? 'Light novels' : 'Manga'}</StatusTag>
                    <Button size='sm' variant='secondary' onClick={() => setMatchFolder(folder)}>
                      <Link2 data-icon='inline-start' /> Match
                    </Button>
                  </div>
                ))}
              </Card>
            </div>
          </TabsPanel>
        </Tabs>
      )}
      {dialogs}
    </div>
  );
}
