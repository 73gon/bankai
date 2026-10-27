import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Ban, BookOpen, Download, ExternalLink, FolderInput, FolderSearch, LayoutGrid, Link2, RotateCcw, Rows3, Search, Star, StarOff, Trash2, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import {
  api, pagePaths, recall,
  type MangaBreakdown, type MangaChapterRun, type MangaDetail, type MangaEntry, type MangaFolder,
  type MangaLibrary as Library, type MangaRelease, type MangaSeries,
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
import { releaseSpan, whyLabels } from '@/pages/MangaReview';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/zon/ui/drawer';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Progress } from '@/zon/ui/progress';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';

type Tab = 'library' | 'followed' | 'downloads' | 'match';
type View = 'grid' | 'table';
type SortKey = 'title' | 'volumes' | 'chapters' | 'size' | 'state';

const VIEW_KEY = 'bankai.manga.library.view';
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

function storedView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'grid';
  } catch {
    return 'grid';
  }
}

function formatSize(bytes: number) {
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? gib.toFixed(2) + ' GiB' : bytes > 0 ? (bytes / 1024 ** 2).toFixed(0) + ' MiB' : '—';
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
function ChapterBreakdown({ breakdown, sizes, waiting, picked, onPick, onReview, onMove }: {
  breakdown: MangaBreakdown;
  sizes: Map<string, number>;
  waiting: number;
  picked: Set<string>;
  onPick: (names: string[], on: boolean) => void;
  onReview: () => void;
  onMove: (names: string[]) => void;
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
                      <Button
                        size='sm'
                        variant='ghost'
                        className='opacity-0 transition-opacity group-hover/row:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100'
                        aria-label={`Move ${rowFiles(run.names)} to another series`}
                        title={`Move ${run.names.length > 1 ? 'these ' + run.names.length + ' files' : 'this file'} into another series (a prequel, a spin-off)`}
                        onClick={() => onMove(run.names)}
                      >
                        <FolderInput data-icon='inline-start' /> Move
                      </Button>
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

function SeriesDrawer({ id, onClose, onOpen, onChanged }: { id: number | null; onClose: () => void; onOpen: (id: number) => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<MangaDetail | null>(null);
  const [tab, setTab] = useState('chapters');
  const [busy, setBusy] = useState(false);
  // Books ticked in the Chapters tab, and those being moved to another series.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [moving, setMoving] = useState<string[] | null>(null);
  const loads = useRef(0);
  const navigate = useNavigate();

  async function load(which: number) {
    const mine = ++loads.current;
    try {
      const answer = await api.mangaSeries(which);
      if (mine === loads.current) setDetail(answer);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    setDetail(null);
    setTab('chapters');
    setPicked(new Set());
    // A move left open (Back while its dialog was up) must not open on the next series with these names.
    setMoving(null);
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
    <Drawer side='right' open={id !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DrawerContent className='w-full md:w-[85%]'>
        {detail === null ? (
          <div className='flex flex-col gap-4 p-6'>
            <Skeleton className='h-24 w-full' />
            <Skeleton className='h-96 w-full' />
          </div>
        ) : (
          <>
            <DrawerHeader>
              <div className='flex items-start gap-4'>
                <AnimePoster url={detail.cover} title={name} className='w-16 shrink-0' />
                <div className='flex min-w-0 flex-col gap-2'>
                  <div className='flex flex-wrap items-center gap-x-3 gap-y-1'>
                    <DrawerTitle className='text-[22px]'>{name}{detail.year ? ' (' + detail.year + ')' : ''}</DrawerTitle>
                    <StatusTag variant={state.variant} title={state.title}>{state.label}</StatusTag>
                  </div>
                  <p className='font-mono text-[0.7rem] text-muted-foreground'>
                    {[mangaFacts(detail), detail.status?.toLowerCase().replace('_', ' '), detail.authors.join(', ')].filter(Boolean).join(' · ')}
                  </p>
                  <div className='flex flex-wrap gap-1'>
                    {whyLabels(detail).map((label) => <StatusTag key={label} variant='muted'>{label}</StatusTag>)}
                    {blacklisted && <StatusTag variant='destructive'>Blacklisted</StatusTag>}
                    {detail.genres.map((genre) => <StatusTag key={genre} variant='outline'>{genre}</StatusTag>)}
                  </div>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Button
                      size='sm'
                      variant='secondary'
                      nativeButton={false}
                      render={<a href={anilistMangaUrl(detail.anilist_id)} target='_blank' rel='noreferrer' title='This series on AniList' />}
                    >
                      <ExternalLink data-icon='inline-start' /> AniList
                    </Button>
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
                        <Button size='sm' variant='destructive' disabled={busy} title='Never look for this series again' onClick={() => void act(() => api.blacklistMangaSeries(detail.anilist_id), name + ' blacklisted')}>
                          <Ban data-icon='inline-start' /> Blacklist
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            </DrawerHeader>
            {/* While the bar floats over the bottom, a row reached by Tab scrolls clear of it. */}
            <Tabs
              value={tab}
              onValueChange={(value) => setTab(String(value))}
              className={cn('min-h-0 flex-1 gap-4 overflow-y-auto px-6 pb-6', tab === 'chapters' && ticked.length > 0 && 'scroll-pb-24')}
            >
              <TabsList>
                <TabsTab value='chapters'>
                  {detail.breakdown.unit === 'volume' ? 'Volumes' : 'Chapters'}{' '}
                  <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{detail.breakdown.have}/{detail.breakdown.last ? detail.breakdown.last - detail.breakdown.first + 1 : 0}</span>
                </TabsTab>
                <TabsTab value='relations'>Relations <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{detail.graph.nodes.length - 1}</span></TabsTab>
                <TabsTab value='releases'>Releases <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{detail.releases.length}</span></TabsTab>
              </TabsList>
              <TabsPanel value='chapters' className={ticked.length > 0 ? 'pb-16' : undefined}>
                <ChapterBreakdown
                  breakdown={detail.breakdown}
                  sizes={sizes}
                  waiting={detail.waiting ?? 0}
                  picked={picked}
                  onPick={pick}
                  onReview={() => navigate('/m/review')}
                  onMove={setMoving}
                />
              </TabsPanel>
              <TabsPanel value='relations'>
                {/* The drawer is swiped shut by a drag on it: a drag in the graph pans the graph, not the drawer. */}
                <div
                  className='h-[65dvh] overflow-hidden rounded-xl border border-border'
                  onPointerDown={(event) => event.stopPropagation()}
                  onTouchStart={(event) => event.stopPropagation()}
                >
                  {tab === 'relations' && (
                    <MangaGraph
                      graph={detail.graph}
                      onOpen={(node) => {
                        if (node.anilist_id === detail.anilist_id) return;
                        if (node.type === 'MANGA') onOpen(node.anilist_id);
                        else window.open(anilistUrl(node.anilist_id), '_blank', 'noreferrer');
                      }}
                    />
                  )}
                </div>
                <p className='mt-2 text-xs text-muted-foreground'>
                  Anime on the left, other manga and light novels on the right. Solid lines are what bankai follows a series by (its source,
                  adaptations, alternative versions); a manga card opens that series.
                </p>
              </TabsPanel>
              <TabsPanel value='releases'>
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
              </TabsPanel>
            </Tabs>
            {/* Floats over the drawer's bottom edge, as the anime library's: ticking never moves the rows. */}
            {tab === 'chapters' && ticked.length > 0 && (
              <div className='absolute inset-x-6 bottom-6 z-10 flex flex-wrap items-center gap-2 rounded-control surface-container px-3 py-2 shadow-overlay transition-[opacity,translate] duration-150 ease-out starting:translate-y-2 starting:opacity-0 sm:inset-x-8'>
                <span className='text-xs text-muted-foreground'>
                  <span className='font-mono tabular-nums text-foreground'>{ticked.length}</span> file{ticked.length === 1 ? '' : 's'} ticked
                </span>
                <Button size='sm' onClick={() => setMoving(ticked)} title='Files that are another manga (a prequel, a spin-off): move them into that series'>
                  <FolderInput data-icon='inline-start' /> Move to another series
                </Button>
                <Button size='sm' variant='ghost' className='ml-auto' onClick={() => setPicked(new Set())}>
                  <X data-icon='inline-start' /> Clear
                </Button>
              </div>
            )}
            <MoveMangaFiles
              from={{ id: detail.anilist_id, name }}
              files={moving}
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
      </DrawerContent>
    </Drawer>
  );
}

// -- The series, as posters or as a table ----------------------------------------------

function SeriesGrid({ items, onOpen }: { items: MangaSeries[]; onOpen: (id: number) => void }) {
  return (
    <div className='grid grid-cols-[repeat(auto-fill,minmax(min(9.5rem,calc(50%_-_0.5rem)),1fr))] gap-x-4 gap-y-6'>
      {items.map((series) => {
        const name = anilistName(series);
        return (
          <button key={series.anilist_id} type='button' className='group flex flex-col gap-2 text-left focus-visible:outline-none' onClick={() => onOpen(series.anilist_id)}>
            <div className='relative'>
              <AnimePoster url={series.cover} title={name} className='w-full rounded-lg ring-offset-2 ring-offset-background transition group-hover:ring-2 group-hover:ring-foreground/30 group-focus-visible:ring-2 group-focus-visible:ring-info' />
              {series.kind === 'novel' && (
                <span className='absolute left-1.5 top-1.5 rounded border border-violet-400/50 bg-background/85 px-1.5 py-px text-[0.6rem] font-medium text-violet-200 backdrop-blur-sm'>Light novel</span>
              )}
              {(series.waiting ?? 0) > 0 && (
                <span className='absolute right-1.5 top-1.5 rounded border border-warning/50 bg-background/85 px-1.5 py-px text-[0.6rem] font-medium text-warning backdrop-blur-sm'>{series.waiting} to review</span>
              )}
            </div>
            <div className='flex min-w-0 flex-col gap-0.5'>
              <span className='line-clamp-2 text-sm font-medium leading-tight'>{name}</span>
              <span className='truncate font-mono text-[0.66rem] text-muted-foreground'>
                {series.owned || [series.year, series.volumes ? series.volumes + ' vols' : null].filter(Boolean).join(' · ') || ' '}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function SeriesTable({ items, sort, onSort, onOpen }: { items: MangaSeries[]; sort: SortState<SortKey> | null; onSort: (key: SortKey) => void; onOpen: (id: number) => void }) {
  return (
    // In its own container, as the anime library's; the header stays put as it scrolls. Plain,
    // across the page's whole width: through its gutter, the cells' padding lining up with it.
    <Table variant='plain' containerProps={{ className: '@container -mx-(--page-gutter) w-auto min-h-0 flex-1 overflow-auto' }}>
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
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(series.anilist_id); } }}
              className='data-row cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-control-focus'
            >
              <TableCell className='max-w-0'>
                <div className='flex items-center gap-2.5'>
                  <AnimePoster url={series.cover} title={name} className='w-8 shrink-0 rounded' />
                  <div className='min-w-0'>
                    <p className='truncate font-medium text-foreground' title={name}>{name}</p>
                    <p className='font-mono text-[0.68rem] tabular-nums text-muted-foreground'>
                      {[series.year, formatLabel(series.format), series.status?.toLowerCase().replace(/_/g, ' ')].filter(Boolean).join(' · ')}
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
  const [view, setView] = useState<View>(storedView);
  const [sort, setSort] = useState<SortState<SortKey> | null>(null);
  const [query, setQuery] = useState('');
  const [params, setParams] = useSearchParams();
  const [matchFolder, setMatchFolder] = useState<MangaFolder | null>(null);
  const selected = params.get('series') ? Number(params.get('series')) : null;

  async function load() {
    try {
      setData(await api.mangaLibrary());
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      /* the view is simply not remembered */
    }
  }, [view]);

  function open(id: number | null) {
    const next = new URLSearchParams(params);
    if (id === null) next.delete('series');
    else next.set('series', String(id));
    setParams(next, { replace: id === null });
  }

  const needle = query.trim().toLocaleLowerCase();
  const shown = useMemo(() => {
    const pick = (items: MangaSeries[]) => {
      const kept = items.filter((series) =>
        !needle || [series.name, series.romaji, series.english, series.folder].some((text) => (text ?? '').toLocaleLowerCase().includes(needle)));
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

  const listing = (items: MangaSeries[]) => view === 'grid'
    ? <SeriesGrid items={items} onOpen={open} />
    : <SeriesTable items={items} sort={sort} onSort={(key) => setSort((current) => nextSort(current, key, FIRST_DIRECTION))} onOpen={open} />;

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>Manga library</h1>
        <p className='text-sm text-muted-foreground'>
          Your manga and light novels, in the folders OpenPanel reads: a folder per series, "Vol 01.cbz" inside. Followed are the series
          bankai looks for: AniList's links to the anime in your library, what is in your folders, and what you follow from Search.
        </p>
      </div>
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
              <ToggleGroup aria-label='Library view' value={[view]} onValueChange={(values) => { if (values[0]) setView(values[0] as View); }}>
                {([['grid', LayoutGrid, 'Grid'], ['table', Rows3, 'Table']] as const).map(([value, Icon, label]) => (
                  <ToggleGroupItem key={value} value={value} icon={<Icon />}>{label}</ToggleGroupItem>
                ))}
              </ToggleGroup>
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
                <footer className='flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-t border-border pt-2.5 text-xs text-muted-foreground'>
                  <span><span className='font-mono tabular-nums text-foreground'>{shown.series.length}</span> series{shown.series.length !== data.series.length ? ` of ${data.series.length}` : ''}</span>
                  <span><span className='font-mono tabular-nums text-foreground'>{totals.volumes.toLocaleString()}</span> volumes</span>
                  <span><span className='font-mono tabular-nums text-foreground'>{totals.chapters.toLocaleString()}</span> chapter files</span>
                  <span><span className='font-mono tabular-nums text-foreground'>{formatSize(totals.size)}</span> stored</span>
                </footer>
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
      <SeriesDrawer id={selected} onClose={() => open(null)} onOpen={open} onChanged={() => void load()} />
      <MangaPickDialog
        query={matchFolder ? matchFolder.title : null}
        kind={matchFolder?.kind ?? null}
        title={matchFolder ? 'Which series is “' + matchFolder.folder + '”?' : ''}
        action='Match'
        onClose={() => setMatchFolder(null)}
        onPick={async (entry: MangaEntry) => {
          if (!matchFolder) return;
          try {
            await api.matchMangaFolder(matchFolder.kind, matchFolder.folder, entry.anilist_id);
            toast.success(matchFolder.folder + ' is ' + anilistName(entry));
            setMatchFolder(null);
            await load();
          } catch (error: any) {
            toast.error(error.message);
          }
        }}
      />
    </div>
  );
}
