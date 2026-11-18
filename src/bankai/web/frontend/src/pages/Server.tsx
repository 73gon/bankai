import { useEffect, useMemo, useState, useRef } from 'react';
import { Server as ServerIcon, RefreshCw, Film, Tv, Sparkles, BookOpen, Loader2, Plus, Trash2, ChevronDown, ChevronRight, Pencil, GripVertical, TriangleAlert, MoveRight } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type LibraryMove, type LibraryMovePlan, type ServerTitle, type ServerSeason, type ServerDirKind } from '@/lib/api';
import { formatBytes } from '@/lib/utils';
import { Button } from '@zonsh/ui';
import { Badge } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Card, cn } from '@zonsh/ui';
import { Skeleton } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Tooltip, TooltipContent, TooltipTrigger } from '@zonsh/ui';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { AlertDialog, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@zonsh/ui';

// Anime is listed here too: this page is where anything already on the server
// is added and renamed, whichever library it belongs to.
type LibraryKind = ServerDirKind | 'manga';
const KINDS: { kind: LibraryKind; title: string; icon: typeof Film; hint: string }[] = [
  { kind: 'movie', title: 'Movies', icon: Film, hint: '/mnt/g/media/movies' },
  { kind: 'show', title: 'Shows', icon: Tv, hint: '/mnt/g/media/shows' },
  { kind: 'anime', title: 'Anime', icon: Sparkles, hint: '/mnt/g/media/shows_anime' },
  // Manga's folders are its settings' (Manga -> Settings): shown and dropped onto, not edited here.
  { kind: 'manga', title: 'Manga', icon: BookOpen, hint: '' },
];

// The title being dragged, kept here as well as in the drag's data: dragover cannot read the data.
let dragging: { location: string; directory: string; name: string } | null = null;
const DRAG_TYPE = 'application/x-bankai-title';

type RenameTarget = {
  kind: 'movie' | 'episode';
  name: string;
  path: string;
};

function PathText({ text, tooltip, className }: { text: string; tooltip: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={className} />}>{text}</TooltipTrigger>
      <TooltipContent className='font-mono'>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

function fmtSize(bytes: number): string {
  if (!bytes) return '';
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

function ShowRow({ it, onRename }: { it: ServerTitle; onRename: (target: RenameTarget) => void }) {
  const [open, setOpen] = useState(false);
  const [seasons, setSeasons] = useState<ServerSeason[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [openSeasons, setOpenSeasons] = useState<Record<string, boolean>>({});
  const canExpand = it.present && !!it.location;

  async function toggle() {
    if (!canExpand) return;
    const next = !open;
    setOpen(next);
    if (next && seasons === null) {
      setLoading(true);
      try {
        const r = await api.serverShow(it.location!);
        setSeasons(r.seasons);
      } catch (e: any) {
        toast.error(e.message);
        setSeasons([]);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className='rounded-item'>
      <button
        onClick={toggle}
        disabled={!canExpand}
        className='flex w-full items-center justify-between gap-3 rounded-item material-row px-2 py-1.5 text-left focus-ring disabled:cursor-default'
      >
        <span className='flex min-w-0 items-center gap-1.5'>
          {canExpand ? (
            open ? (
              <ChevronDown className='h-4 w-4 shrink-0 text-muted-foreground' />
            ) : (
              <ChevronRight className='h-4 w-4 shrink-0 text-muted-foreground' />
            )
          ) : (
            <span className='w-4 shrink-0' />
          )}
          {it.location ? <PathText text={it.name} tooltip={it.location} className='truncate text-sm' /> : <span className='truncate text-sm'>{it.name}</span>}
        </span>
      </button>
      {open && (
        <div className='ml-6 mt-1 space-y-2 border-l border-border/60 pl-3'>
          {loading ? (
            <p className='py-2 text-xs text-muted-foreground'>Scanning…</p>
          ) : !seasons || seasons.length === 0 ? (
            <p className='py-2 text-xs text-muted-foreground'>No episodes found.</p>
          ) : (
            seasons.map((se) => {
              const sOpen = openSeasons[se.name] ?? false;
              return (
                <div key={se.name}>
                  <button
                    onClick={() => setOpenSeasons((s) => ({ ...s, [se.name]: !sOpen }))}
                    className='flex w-full items-center gap-1.5 rounded-item material-row px-1 py-1 text-left text-xs font-medium text-muted-foreground focus-ring'
                  >
                    {sOpen ? <ChevronDown className='h-3.5 w-3.5 shrink-0' /> : <ChevronRight className='h-3.5 w-3.5 shrink-0' />}
                    {se.name}
                    <Badge variant='secondary' size='sm'>{se.episodes.length}</Badge>
                  </button>
                  {sOpen &&
                    (se.episodes.length === 0 ? (
                      <p className='pl-6 text-xs text-muted-foreground/70'>No files.</p>
                    ) : (
                      se.episodes.map((ep) => (
                        <div key={ep.path} className='flex items-center justify-between gap-3 rounded-item px-2 py-1 pl-6 hover:bg-control-hover/50'>
                          <div className='min-w-0'>
                            <Tooltip>
                              <TooltipTrigger render={<span className='truncate text-xs' />}>{ep.name}</TooltipTrigger>
                              <TooltipContent className='font-mono'>{ep.path}</TooltipContent>
                            </Tooltip>
                          </div>
                          <div className='ml-auto flex shrink-0 items-center gap-1'>
                            <span className='font-mono text-[11px] tabular-nums text-muted-foreground'>{fmtSize(ep.size)}</span>
                            <Tooltip>
                              <TooltipTrigger
                                render={
                                  <Button
                                    size='icon-sm'
                                    variant='ghost'
                                    onClick={() => onRename({ kind: 'episode', name: ep.name, path: ep.path })}
                                    aria-label='Rename this episode file'
                                  />
                                }
                              >
                                <Pencil />
                              </TooltipTrigger>
                              <TooltipContent>Rename this episode file</TooltipContent>
                            </Tooltip>
                          </div>
                        </div>
                      ))
                    ))}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/** A title that can be picked up and dropped on another library folder. */
function DragRow({ it, moving, children }: { it: ServerTitle; moving: boolean; children: React.ReactNode }) {
  const can = it.present && !!it.location && !!it.directory && !moving;
  return (
    <div
      draggable={can}
      onDragStart={(event) => {
        if (!can) return;
        dragging = { location: it.location!, directory: it.directory!, name: it.name };
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData(DRAG_TYPE, it.location!);
        event.dataTransfer.setData('text/plain', it.name);
      }}
      onDragEnd={() => {
        dragging = null;
      }}
      className={cn('group/drag relative', can && 'cursor-grab active:cursor-grabbing', moving && 'opacity-50')}
      title={moving ? 'Being moved' : undefined}
    >
      {can && (
        <GripVertical aria-hidden='true' className='pointer-events-none absolute top-2 -left-0.5 size-3.5 text-muted-foreground/0 transition-colors group-hover/drag:text-muted-foreground/70' />
      )}
      {children}
    </div>
  );
}

/** Where a title can be dropped: a folder other than its own. The groups below and the folder
 *  rows at the top both are (scrolling down to a far folder was tedious). */
function useDropTarget(directory: string, enabled: boolean, onDrop: (location: string, targetRoot: string) => void) {
  const [over, setOver] = useState(false);
  const accepts = () => enabled && dragging !== null && dragging.directory.toLocaleLowerCase() !== directory.toLocaleLowerCase();
  const handlers = {
    onDragOver: (event: React.DragEvent) => {
      if (!accepts()) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      if (!over) setOver(true);
    },
    onDragLeave: (event: React.DragEvent) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false);
    },
    onDrop: (event: React.DragEvent) => {
      setOver(false);
      const location = event.dataTransfer.getData(DRAG_TYPE) || dragging?.location;
      if (!accepts() || !location) return;
      event.preventDefault();
      onDrop(location, directory);
    },
  };
  return { over, handlers };
}

/** One library folder's titles; a title of another folder dropped on it is moved there. */
function DirectoryGroup({
  directory,
  items,
  dropTarget,
  expandable,
  renamable,
  revision,
  moving,
  onRename,
  onDrop,
}: {
  directory: string;
  renamable?: boolean;
  items: ServerTitle[];
  dropTarget: boolean;
  expandable?: boolean;
  revision: number;
  moving: Set<string>;
  onRename: (target: RenameTarget) => void;
  onDrop: (location: string, targetRoot: string) => void;
}) {
  const { over, handlers } = useDropTarget(directory, dropTarget, onDrop);
  return (
    <section
      {...handlers}
      className={cn('border-b border-border/60 transition-colors last:border-b-0', over && 'bg-primary/10 outline-2 -outline-offset-2 outline-primary/60')}
    >
      <div className='sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border/60 bg-control-surface px-3 py-1.5'>
        <PathText text={directory} tooltip={directory} className='truncate font-mono text-[11px] text-foreground' />
        <span className='shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground'>{over ? 'Drop to move here' : items.length}</span>
      </div>
      <div className='flex flex-col gap-0.5 px-1 py-1'>
        {items.length === 0 ? (
          <p className='px-2 py-3 text-center text-xs text-muted-foreground'>{over ? 'Drop to move here' : 'No matching titles in this folder.'}</p>
        ) : expandable ? (
          items.map((it) => (
            <DragRow key={`${it.location}:${revision}`} it={it} moving={moving.has(it.location ?? '')}>
              <ShowRow it={it} onRename={onRename} />
            </DragRow>
          ))
        ) : (
          items.map((it) => (
            <DragRow key={it.location ?? it.name} it={it} moving={moving.has(it.location ?? '')}>
              <div className='flex items-center justify-between gap-3 rounded-item px-2 py-1.5 hover:bg-control-hover/50'>
                <Tooltip>
                  <TooltipTrigger render={<span className='min-w-0 truncate text-sm' />}>{it.name}</TooltipTrigger>
                  <TooltipContent className='font-mono'>{it.location || it.name}</TooltipContent>
                </Tooltip>
                {renamable && it.present && it.location && (
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          size='icon-sm'
                          variant='ghost'
                          onClick={() => onRename({ kind: 'movie', name: it.name, path: it.location! })}
                          aria-label='Rename the movie folder and matching file'
                        />
                      }
                    >
                      <Pencil />
                    </TooltipTrigger>
                    <TooltipContent>Rename the movie folder and matching file</TooltipContent>
                  </Tooltip>
                )}
              </div>
            </DragRow>
          ))
        )}
      </div>
    </section>
  );
}

function Column({
  title,
  icon: Icon,
  items,
  filter,
  expandable,
  renamable,
  directories,
  revision,
  moving,
  onRename,
  onDrop,
}: {
  title: string;
  renamable?: boolean;
  icon: React.ComponentType<{ className?: string }>;
  items: ServerTitle[];
  filter: string;
  expandable?: boolean;
  directories: string[];
  revision: number;
  moving: Set<string>;
  onRename: (target: RenameTarget) => void;
  onDrop: (location: string, targetRoot: string) => void;
}) {
  const needle = filter.toLowerCase();
  const filtered = items.filter((i) => i.name.toLowerCase().includes(needle));
  const groups = directories.map((directory) => ({
    directory,
    target: true,
    items: filtered.filter((item) => item.directory?.toLocaleLowerCase() === directory.toLocaleLowerCase()),
  }));
  const ungrouped = filtered.filter(
    (item) => !directories.some((directory) => item.directory?.toLocaleLowerCase() === directory.toLocaleLowerCase()),
  );
  if (ungrouped.length > 0) groups.push({ directory: 'Other / unavailable', target: false, items: ungrouped });
  return (
    <div className='flex min-h-0 min-w-0 flex-col max-md:h-[32rem]'>
      <div className='flex items-center justify-between gap-2 border-b border-border px-3 py-2.5'>
        <h2 className='flex items-center gap-2 font-heading text-lg font-light'>
          <Icon className='size-4 text-muted-foreground' /> {title}
        </h2>
        <span className='font-mono text-[11px] tabular-nums text-muted-foreground'>{items.length} titles</span>
      </div>
      <div className='min-h-0 flex-1 overflow-y-auto'>
        {groups.length === 0 ? (
          <p className='py-6 text-center text-sm text-muted-foreground'>No folders.</p>
        ) : (
          groups.map((group) => (
            <DirectoryGroup
              key={group.directory}
              directory={group.directory}
              items={group.items}
              dropTarget={group.target}
              expandable={expandable}
              renamable={renamable}
              revision={revision}
              moving={moving}
              onRename={onRename}
              onDrop={onDrop}
            />
          ))
        )}
      </div>
    </div>
  );
}

const KIND_NAME: Record<LibraryKind, string> = { movie: 'movie', show: 'show', anime: 'anime', manga: 'manga' };

export default function Server() {
  const [contents, setContents] = useState<Record<LibraryKind, ServerTitle[]>>({ movie: [], show: [], anime: [], manga: [] });
  const [loading, setLoading] = useState(true);
  const [rescanning, setRescanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [revision, setRevision] = useState(0);
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [dirs, setDirs] = useState<Record<LibraryKind, string[]>>({ movie: [], show: [], anime: [], manga: [] });
  const [draft, setDraft] = useState<Record<ServerDirKind, string>>({ movie: '', show: '', anime: '' });
  const [dirBusy, setDirBusy] = useState(false);
  // A drop, checked by the server, waiting for the user's word.
  const [proposal, setProposal] = useState<LibraryMovePlan | null>(null);
  const [moveBusy, setMoveBusy] = useState(false);
  const [moves, setMoves] = useState<LibraryMove[]>([]);

  // The last answers: the same one again (coming back to the tab) changes nothing on the page.
  const lastContents = useRef<unknown>(null);
  const lastDirs = useRef<unknown>(null);

  async function load(rescan = false) {
    // The spinner only while there is nothing to show yet.
    if (rescan) setRescanning(true);
    else if (lastContents.current === null) setLoading(true);
    setError(null);
    try {
      const r = await api.serverContents(rescan);
      if (r === lastContents.current) return;
      lastContents.current = r;
      setContents({ movie: r.movies, show: r.shows, anime: r.anime ?? [], manga: r.manga ?? [] });
      setRevision((value) => value + 1);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
      setRescanning(false);
    }
  }

  async function loadDirs() {
    try {
      const r = await api.serverDirs();
      if (r === lastDirs.current) return;
      lastDirs.current = r;
      setDirs({ movie: r.movie_dirs, show: r.show_dirs, anime: r.anime_dirs ?? [], manga: r.manga_dirs ?? [] });
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    void load();
    void loadDirs();
  }, []);

  // Moves under way: the rows they take dim, and a finished one reads the folders again.
  const active = moves.some((move) => move.status === 'queued' || move.status === 'running');
  useEffect(() => {
    let alive = true;
    let timer = 0;
    let wasActive = false;
    async function poll() {
      try {
        const { moves: listed } = await api.libraryMoves();
        if (!alive) return;
        setMoves(listed);
        const now = listed.some((move) => move.status === 'queued' || move.status === 'running');
        if (wasActive && !now) void load();
        wasActive = now;
      } catch {
        /* the next poll may reach the server */
      }
      if (alive) timer = window.setTimeout(() => void poll(), wasActive ? 2_000 : 15_000);
    }
    void poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [active]);
  const moving = useMemo(
    () => new Set(moves.filter((move) => move.status === 'queued' || move.status === 'running').map((move) => move.source)),
    [moves],
  );

  async function proposeMove(location: string, targetRoot: string) {
    try {
      setProposal(await api.planLibraryMove(location, targetRoot));
    } catch (e: any) {
      toast.error(`Cannot move: ${e.message}`);
    }
  }

  async function startMove() {
    if (!proposal) return;
    setMoveBusy(true);
    try {
      const started = await api.startLibraryMove(proposal.source, proposal.target.slice(0, proposal.target.lastIndexOf('/')));
      setMoves((current) => [...current, started]);
      toast.success(`Moving ${started.title}: the Dashboard's Workers card shows how far`);
      setProposal(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setMoveBusy(false);
    }
  }

  async function addDir(kind: ServerDirKind, path: string) {
    if (!path.trim()) return;
    setDirBusy(true);
    try {
      const r = await api.addServerDir(kind, path.trim());
      setDirs((current) => ({ ...current, [kind]: r.dirs }));
      setDraft((current) => ({ ...current, [kind]: '' }));
      toast.success('Directory added');
      await load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDirBusy(false);
    }
  }

  async function removeDir(kind: ServerDirKind, path: string) {
    setDirBusy(true);
    try {
      const r = await api.removeServerDir(kind, path);
      setDirs((current) => ({ ...current, [kind]: r.dirs }));
      toast.success('Directory removed');
      await load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDirBusy(false);
    }
  }

  function openRename(target: RenameTarget) {
    setRenameTarget(target);
    setRenameTitle(target.name);
  }

  async function renameServerItem() {
    if (!renameTarget) return;
    const title = renameTitle.trim();
    if (!title) {
      toast.error('Enter a title.');
      return;
    }
    setRenameBusy(true);
    try {
      const result = await api.renameServerItem(renameTarget.kind, renameTarget.path, title);
      toast.success(
        result.renamed
          ? renameTarget.kind === 'movie'
            ? 'Movie folder and matching file renamed.'
            : 'Episode file renamed.'
          : 'The title is already unchanged.',
      );
      setRenameTarget(null);
      setRenameTitle('');
      await load(true);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setRenameBusy(false);
    }
  }

  const otherKind = proposal && proposal.from_kind !== proposal.to_kind;

  // Edge to edge: the page's gutter given back, square panels that are part of the page, not cards on it.
  return (
    <div className='-mx-(--page-gutter) -mb-6 flex h-full min-h-0 flex-col md:overflow-hidden'>
      <header className='flex flex-wrap items-center justify-between gap-3 px-(--page-gutter) pb-4'>
        <div className='flex items-baseline gap-2'>
          <h1 className='font-heading text-3xl font-light'>Library</h1>
          <span className='text-sm text-muted-foreground'>— What lives on the media drives. Drag a title onto another folder to move it.</span>
        </div>
        <div className='flex gap-2'>
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Search titles…' className='w-64' />
          <Button variant='secondary' onClick={() => load(true)} disabled={rescanning}>
            {rescanning ? <Loader2 className='h-4 w-4 animate-spin' /> : <RefreshCw className='h-4 w-4' />}
            Rescan
          </Button>
        </div>
      </header>

      <Card variant='region' className='grid gap-0 divide-border/60 border-x-0 [--card-padding:0px] max-md:divide-y md:grid-cols-2 md:divide-x xl:grid-cols-4'>
        {KINDS.map(({ kind, title, icon, hint }) => (
          <DirManager
            key={kind}
            title={title}
            icon={icon}
            hint={hint}
            dirs={dirs[kind]}
            readOnly={kind === 'manga'}
            value={kind === 'manga' ? '' : draft[kind]}
            onChange={(value) => kind !== 'manga' && setDraft((current) => ({ ...current, [kind]: value }))}
            onAdd={() => kind !== 'manga' && addDir(kind, draft[kind])}
            onRemove={(p) => kind !== 'manga' && removeDir(kind, p)}
            onDrop={proposeMove}
            busy={dirBusy}
          />
        ))}
      </Card>

      {error ? (
        <Empty>
          <EmptyMedia><ServerIcon /></EmptyMedia>
          <EmptyTitle>Could not read server</EmptyTitle>
          <EmptyDescription>{error}</EmptyDescription>
        </Empty>
      ) : loading ? (
        <Skeleton shape='card' className='min-h-0 flex-1 rounded-none' />
      ) : (
        <Card
          variant='region'
          className='-mt-px grid min-h-0 flex-1 gap-0 divide-border/60 border-x-0 [--card-padding:0px] max-md:divide-y md:grid-cols-2 md:divide-x xl:grid-cols-4'
        >
          <Column title='Movies' icon={Film} items={contents.movie} directories={dirs.movie} filter={filter} renamable revision={revision} moving={moving} onRename={openRename} onDrop={proposeMove} />
          <Column title='Shows' icon={Tv} items={contents.show} directories={dirs.show} filter={filter} expandable revision={revision} moving={moving} onRename={openRename} onDrop={proposeMove} />
          <Column title='Anime' icon={Sparkles} items={contents.anime} directories={dirs.anime} filter={filter} expandable revision={revision} moving={moving} onRename={openRename} onDrop={proposeMove} />
          <Column title='Manga' icon={BookOpen} items={contents.manga} directories={dirs.manga} filter={filter} revision={revision} moving={moving} onRename={openRename} onDrop={proposeMove} />
        </Card>
      )}

      <AlertDialog open={!!proposal} onOpenChange={(open) => !open && !moveBusy && setProposal(null)}>
        <AlertDialogContent size='default'>
          <AlertDialogHeader>
            <AlertDialogTitle>{otherKind ? `Move a ${KIND_NAME[proposal!.from_kind]} into a ${KIND_NAME[proposal!.to_kind]} folder?` : 'Move this title?'}</AlertDialogTitle>
            <AlertDialogDescription>
              {proposal?.same_drive
                ? 'Both folders are on one drive: the title is renamed into place at once.'
                : 'The folders are on different drives: every file is copied, written out and checked, and the original is removed only once the copy is whole. If anything fails, the original stays as it is.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {proposal && (
            <div className='flex flex-col gap-3 text-sm'>
              <div className='flex flex-col gap-1 rounded-item border border-field-edge bg-field-surface px-3 py-2 font-mono text-xs'>
                <span className='truncate' title={proposal.source}>{proposal.source}</span>
                <span className='flex items-center gap-1.5 truncate text-foreground' title={proposal.target}>
                  <MoveRight className='size-3.5 shrink-0 text-muted-foreground' /> {proposal.target}
                </span>
              </div>
              <p className='text-muted-foreground'>
                {proposal.files} file{proposal.files === 1 ? '' : 's'}, {formatBytes(proposal.size)}
              </p>
              {proposal.warnings.map((warning) => (
                <p key={warning} className='flex items-start gap-2 text-warning'>
                  <TriangleAlert className='mt-0.5 size-4 shrink-0' /> {warning}
                </p>
              ))}
            </div>
          )}
          <AlertDialogFooter>
            <Button variant='secondary' onClick={() => setProposal(null)} disabled={moveBusy}>Cancel</Button>
            <Button variant={otherKind ? 'destructive' : 'primary'} onClick={() => void startMove()} disabled={moveBusy}>
              {moveBusy ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <MoveRight data-icon='inline-start' />}
              {otherKind ? 'Move anyway' : 'Move'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={!!renameTarget}
        onOpenChange={(open) => {
          if (!open && !renameBusy) {
            setRenameTarget(null);
            setRenameTitle('');
          }
        }}
      >
        <DialogContent className='max-w-xl'>
          <DialogHeader>
            <DialogTitle>Rename {renameTarget?.kind === 'movie' ? 'movie' : 'episode'}</DialogTitle>
            <DialogDescription>
              {renameTarget?.kind === 'movie'
                ? 'The media-server movie folder and files with the matching basename will be renamed.'
                : 'Only this episode file will be renamed; its show and season folders remain unchanged.'}
            </DialogDescription>
          </DialogHeader>
          <div className='flex flex-col gap-1.5'>
            <label htmlFor='server-rename-title' className='text-xs text-muted-foreground'>New title</label>
            <Input
              id='server-rename-title'
              value={renameTitle}
              onChange={(event) => setRenameTitle(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && void renameServerItem()}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setRenameTarget(null)} disabled={renameBusy}>Cancel</Button>
            <Button onClick={() => void renameServerItem()} disabled={renameBusy || !renameTitle.trim()}>
              {renameBusy ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Pencil data-icon='inline-start' />}
              Rename
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** A folder at the top: drop a title on it to move it there. */
function DirRow({ directory, readOnly, busy, onRemove, onDrop }: {
  directory: string;
  readOnly?: boolean;
  busy: boolean;
  onRemove: (p: string) => void;
  onDrop: (location: string, targetRoot: string) => void;
}) {
  const { over, handlers } = useDropTarget(directory, true, onDrop);
  return (
    <div
      {...handlers}
      className={cn('flex h-7 items-center justify-between gap-2 rounded-item bg-control-surface pl-2 transition-colors', over && 'bg-primary/15 outline-2 -outline-offset-2 outline-primary/60')}
    >
      <code className='truncate font-mono text-[11px]'>{over ? 'Drop to move here' : directory}</code>
      {!readOnly && (
        <Button size='icon-sm' variant='ghost' onClick={() => onRemove(directory)} disabled={busy} title='Remove' aria-label={`Remove ${directory}`}>
          <Trash2 className='size-3.5 text-destructive' />
        </Button>
      )}
    </div>
  );
}

function DirManager({
  title,
  icon: Icon,
  dirs,
  value,
  onChange,
  onAdd,
  onRemove,
  busy,
  hint,
  readOnly,
  onDrop,
}: {
  title: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  dirs: string[];
  value: string;
  onChange: (v: string) => void;
  onAdd: () => void;
  onRemove: (p: string) => void;
  busy: boolean;
  readOnly?: boolean;
  onDrop: (location: string, targetRoot: string) => void;
}) {
  return (
    <div className='flex flex-col gap-1.5 px-3 py-2.5'>
      <div className='flex items-center gap-2 text-xs font-medium text-muted-foreground'>
        <Icon className='size-3.5' /> {title} folders
        {readOnly && <span className='ml-auto text-[10px] font-normal'>set in Manga → Settings</span>}
      </div>
      {dirs.length === 0 ? (
        <p className='text-xs text-muted-foreground'>No folders configured.</p>
      ) : (
        dirs.map((d) => <DirRow key={d} directory={d} readOnly={readOnly} busy={busy} onRemove={onRemove} onDrop={onDrop} />)
      )}
      {!readOnly && (
        <div className='flex gap-1.5'>
          <Input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onAdd()}
            placeholder={hint}
            size='sm'
            className='flex-1 font-mono text-xs'
          />
          <Button size='icon-sm' variant='secondary' onClick={onAdd} disabled={busy || !value.trim()} aria-label='Add folder'>
            <Plus className='size-3.5' />
          </Button>
        </div>
      )}
    </div>
  );
}
