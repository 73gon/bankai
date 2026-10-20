import { useEffect, useState } from 'react';
import { Server as ServerIcon, RefreshCw, Film, Tv, Sparkles, Loader2, Plus, Trash2, ChevronDown, ChevronRight, Pencil } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type ServerTitle, type ServerSeason, type ServerDirKind } from '@/lib/api';
import { Button } from '@/zon/ui/button';
import { Badge } from '@/zon/ui/badge';
import { Input } from '@/zon/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/zon/ui/card';
import { Skeleton } from '@/zon/ui/skeleton';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/zon/ui/tooltip';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';

// Anime is listed here too: this page is where anything already on the server
// is added and renamed, whichever library it belongs to.
const KINDS: { kind: ServerDirKind; title: string; icon: typeof Film; hint: string }[] = [
  { kind: 'movie', title: 'Movie directories', icon: Film, hint: 'G:\media\movies' },
  { kind: 'show', title: 'Show directories', icon: Tv, hint: 'G:\media\shows' },
  { kind: 'anime', title: 'Anime directories', icon: Sparkles, hint: 'G:\media\shows_anime' },
];

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

function Column({
  title,
  icon: Icon,
  items,
  filter,
  expandable,
  directories,
  revision,
  onRename,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  items: ServerTitle[];
  filter: string;
  expandable?: boolean;
  directories: string[];
  revision: number;
  onRename: (target: RenameTarget) => void;
}) {
  const filtered = items.filter((i) => i.name.toLowerCase().includes(filter.toLowerCase()));
  const groups = directories.map((directory) => ({
    directory,
    items: filtered.filter((item) => item.directory?.toLocaleLowerCase() === directory.toLocaleLowerCase()),
  }));
  const ungrouped = filtered.filter(
    (item) => !directories.some((directory) => item.directory?.toLocaleLowerCase() === directory.toLocaleLowerCase()),
  );
  if (ungrouped.length > 0) groups.push({ directory: 'Other / unavailable', items: ungrouped });
  return (
    <Card className='flex h-full min-h-0 flex-col gap-4 [--card-padding:20px]'>
      <CardHeader className='flex flex-row items-center justify-between'>
        <CardTitle className='flex items-center gap-2 text-xl sm:text-xl'>
          <Icon className='h-4 w-4' /> {title}
        </CardTitle>
        <span className='font-mono text-xs tabular-nums text-muted-foreground'>{items.length} titles</span>
      </CardHeader>
      <CardContent className='min-h-0 flex-1 overflow-auto'>
        {groups.length === 0 ? (
          <p className='py-6 text-center text-sm text-muted-foreground'>No titles.</p>
        ) : (
          <div className='flex flex-col gap-4'>
            {groups.map((group) => (
              <section key={group.directory} className='overflow-hidden rounded-control border border-field-edge'>
                <div className='flex items-center justify-between gap-3 border-b border-border/60 bg-control-surface px-3 py-2'>
                  <PathText text={group.directory} tooltip={group.directory} className='truncate font-mono text-xs text-foreground' />
                  <span className='shrink-0 font-mono text-xs tabular-nums text-muted-foreground'>{group.items.length}</span>
                </div>
                <div className='flex flex-col gap-0.5 p-1'>
                  {group.items.length === 0 ? (
                    <p className='px-2 py-4 text-center text-xs text-muted-foreground'>No matching titles in this directory.</p>
                  ) : expandable ? (
                    group.items.map((it) => <ShowRow key={`${it.name}:${revision}`} it={it} onRename={onRename} />)
                  ) : (
                    group.items.map((it) => (
                      <div key={it.name} className='flex items-center justify-between gap-3 rounded-item px-2 py-1.5 hover:bg-control-hover/50'>
                        <Tooltip>
                          <TooltipTrigger render={<span className='min-w-0 truncate text-sm' />}>{it.name}</TooltipTrigger>
                          <TooltipContent className='font-mono'>{it.location || it.name}</TooltipContent>
                        </Tooltip>
                        {it.present && it.location && (
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
                    ))
                  )}
                </div>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function Server() {
  const [movies, setMovies] = useState<ServerTitle[]>([]);
  const [shows, setShows] = useState<ServerTitle[]>([]);
  const [loading, setLoading] = useState(true);
  const [rescanning, setRescanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [revision, setRevision] = useState(0);
  const [anime, setAnime] = useState<ServerTitle[]>([]);
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);

  // Three kinds now, so they are held by kind rather than as one pair of
  // state variables per list.
  const [dirs, setDirs] = useState<Record<ServerDirKind, string[]>>({ movie: [], show: [], anime: [] });
  const [draft, setDraft] = useState<Record<ServerDirKind, string>>({ movie: '', show: '', anime: '' });
  const [dirBusy, setDirBusy] = useState(false);

  async function load(rescan = false) {
    rescan ? setRescanning(true) : setLoading(true);
    setError(null);
    try {
      const r = await api.serverContents(rescan);
      setMovies(r.movies);
      setShows(r.shows);
      setAnime(r.anime ?? []);
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
      setDirs({ movie: r.movie_dirs, show: r.show_dirs, anime: r.anime_dirs ?? [] });
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    load();
    loadDirs();
  }, []);

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

  return (
    <div className='flex h-full min-h-0 flex-col gap-6 md:overflow-hidden'>
      <header className='flex flex-wrap items-center justify-between gap-3'>
        <div className='flex items-baseline gap-2'>
          <h1 className='font-heading text-3xl font-light'>Library</h1>
          <span className='text-sm text-muted-foreground'>— What already lives on the media server.</span>
        </div>
        <div className='flex gap-2'>
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder='Search titles…' className='w-64' />
          <Button variant='secondary' onClick={() => load(true)} disabled={rescanning}>
            {rescanning ? <Loader2 className='h-4 w-4 animate-spin' /> : <RefreshCw className='h-4 w-4' />}
            Rescan
          </Button>
        </div>
      </header>

      <Card className='[--card-padding:20px]'>
        <CardContent className='grid gap-4 md:grid-cols-2 xl:grid-cols-3'>
          {KINDS.map(({ kind, title, icon, hint }) => (
            <DirManager
              key={kind}
              title={title}
              icon={icon}
              hint={hint}
              dirs={dirs[kind]}
              value={draft[kind]}
              onChange={(value) => setDraft((current) => ({ ...current, [kind]: value }))}
              onAdd={() => addDir(kind, draft[kind])}
              onRemove={(p) => removeDir(kind, p)}
              busy={dirBusy}
            />
          ))}
        </CardContent>
      </Card>

      {error ? (
        <Empty>
          <EmptyMedia><ServerIcon /></EmptyMedia>
          <EmptyTitle>Could not read server</EmptyTitle>
          <EmptyDescription>{error}</EmptyDescription>
        </Empty>
      ) : loading ? (
        <div className='grid min-h-0 flex-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>
          <Skeleton shape='card' className='h-96' />
          <Skeleton shape='card' className='h-96' />
          <Skeleton shape='card' className='h-96' />
        </div>
      ) : (
        <div className='grid min-h-0 flex-1 gap-4 md:grid-cols-2 xl:grid-cols-3'>
          <Column title='Movies' icon={Film} items={movies} directories={dirs.movie} filter={filter} revision={revision} onRename={openRename} />
          <Column title='Shows' icon={Tv} items={shows} directories={dirs.show} filter={filter} expandable revision={revision} onRename={openRename} />
          <Column title='Anime' icon={Sparkles} items={anime} directories={dirs.anime} filter={filter} expandable revision={revision} onRename={openRename} />
        </div>
      )}

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
}) {
  return (
    <div className='space-y-2'>
      <div className='flex items-center gap-2 text-sm font-medium text-muted-foreground'>
        <Icon className='h-4 w-4' /> {title}
      </div>
      <div className='space-y-1.5'>
        {dirs.length === 0 ? (
          <p className='text-xs text-muted-foreground'>No directories configured.</p>
        ) : (
          dirs.map((d) => (
            <div key={d} className='flex items-center justify-between gap-2 rounded-item border border-field-edge bg-control-surface px-2 py-1.5'>
              <code className='truncate font-mono text-xs'>{d}</code>
              <Button size='icon-sm' variant='ghost' onClick={() => onRemove(d)} disabled={busy} title='Remove' aria-label='Remove'>
                <Trash2 className='h-4 w-4 text-destructive' />
              </Button>
            </div>
          ))
        )}
      </div>
      <div className='flex gap-2'>
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onAdd()}
          placeholder={hint}
          className='flex-1'
        />
        <Button size='icon' variant='secondary' onClick={onAdd} disabled={busy || !value.trim()} aria-label='Add directory'>
          <Plus className='h-4 w-4' />
        </Button>
      </div>
    </div>
  );
}
