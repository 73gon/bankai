import { useEffect, useState } from 'react';
import { Server as ServerIcon, RefreshCw, Film, Tv, Loader2, Plus, Trash2, ChevronDown, ChevronRight, Pencil } from 'lucide-react';
import { toast } from 'sonner';
import { api, type ServerTitle, type ServerSeason } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type RenameTarget = {
  kind: 'movie' | 'episode';
  name: string;
  path: string;
};

function PathText({ text, tooltip, className }: { text: string; tooltip: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild><span className={className}>{text}</span></TooltipTrigger>
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
    <div className='rounded-md'>
      <button
        onClick={toggle}
        disabled={!canExpand}
        className='flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left hover:bg-secondary/40 disabled:cursor-default'
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
        {it.present ? <Badge variant='success'>On server</Badge> : <Badge variant='muted'>Missing</Badge>}
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
                    className='flex w-full items-center gap-1.5 rounded px-1 py-1 text-left text-xs font-medium text-muted-foreground hover:bg-secondary/30'
                  >
                    {sOpen ? <ChevronDown className='h-3.5 w-3.5 shrink-0' /> : <ChevronRight className='h-3.5 w-3.5 shrink-0' />}
                    {se.name}
                    <Badge variant='muted'>{se.episodes.length}</Badge>
                  </button>
                  {sOpen &&
                    (se.episodes.length === 0 ? (
                      <p className='pl-6 text-xs text-muted-foreground/70'>No files.</p>
                    ) : (
                      se.episodes.map((ep) => (
                        <div key={ep.path} className='flex items-center justify-between gap-3 rounded px-2 py-1 pl-6 hover:bg-secondary/30'>
                          <Tooltip>
                            <TooltipTrigger asChild><span className='truncate text-xs'>{ep.name}</span></TooltipTrigger>
                            <TooltipContent className='font-mono'>{ep.path}</TooltipContent>
                          </Tooltip>
                          <span className='ml-auto shrink-0 text-[11px] text-muted-foreground'>{fmtSize(ep.size)}</span>
                          <Button
                            size='icon'
                            variant='ghost'
                            onClick={() => onRename({ kind: 'episode', name: ep.name, path: ep.path })}
                            title='Rename this episode file'
                          >
                            <Pencil />
                          </Button>
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
  revision,
  onRename,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  items: ServerTitle[];
  filter: string;
  expandable?: boolean;
  revision: number;
  onRename: (target: RenameTarget) => void;
}) {
  const filtered = items.filter((i) => i.name.toLowerCase().includes(filter.toLowerCase()));
  const present = items.filter((i) => i.present).length;
  return (
    <Card className='flex h-full min-h-0 flex-col'>
      <CardHeader className='flex flex-row items-center justify-between'>
        <CardTitle className='flex items-center gap-2'>
          <Icon className='h-4 w-4' /> {title}
        </CardTitle>
        <Badge variant='muted'>
          {present}/{items.length} on server
        </Badge>
      </CardHeader>
      <CardContent className='min-h-0 flex-1 space-y-1.5 overflow-auto'>
        {filtered.length === 0 ? (
          <p className='py-6 text-center text-sm text-muted-foreground'>No titles.</p>
        ) : expandable ? (
          filtered.map((it) => <ShowRow key={`${it.name}:${revision}`} it={it} onRename={onRename} />)
        ) : (
          filtered.map((it) => (
            <div key={it.name} className='flex items-center justify-between gap-3 rounded-md px-2 py-1.5 hover:bg-secondary/40'>
              <Tooltip>
                <TooltipTrigger asChild><span className='truncate text-sm'>{it.name}</span></TooltipTrigger>
                <TooltipContent className='font-mono'>{it.location || it.name}</TooltipContent>
              </Tooltip>
              {it.present ? <Badge variant='success'>On server</Badge> : <Badge variant='muted'>Missing</Badge>}
              {it.present && it.location && (
                <Button
                  size='icon'
                  variant='ghost'
                  onClick={() => onRename({ kind: 'movie', name: it.name, path: it.location! })}
                  title='Rename the movie folder and matching file'
                >
                  <Pencil />
                </Button>
              )}
            </div>
          ))
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
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);

  const [movieDirs, setMovieDirs] = useState<string[]>([]);
  const [showDirs, setShowDirs] = useState<string[]>([]);
  const [newMovieDir, setNewMovieDir] = useState('');
  const [newShowDir, setNewShowDir] = useState('');
  const [dirBusy, setDirBusy] = useState(false);

  async function load(rescan = false) {
    rescan ? setRescanning(true) : setLoading(true);
    setError(null);
    try {
      const r = await api.serverContents(rescan);
      setMovies(r.movies);
      setShows(r.shows);
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
      setMovieDirs(r.movie_dirs);
      setShowDirs(r.show_dirs);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    load();
    loadDirs();
  }, []);

  async function addDir(kind: 'movie' | 'show', path: string) {
    if (!path.trim()) return;
    setDirBusy(true);
    try {
      const r = await api.addServerDir(kind, path.trim());
      kind === 'movie' ? setMovieDirs(r.dirs) : setShowDirs(r.dirs);
      kind === 'movie' ? setNewMovieDir('') : setNewShowDir('');
      toast.success('Directory added');
      await load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDirBusy(false);
    }
  }

  async function removeDir(kind: 'movie' | 'show', path: string) {
    setDirBusy(true);
    try {
      const r = await api.removeServerDir(kind, path);
      kind === 'movie' ? setMovieDirs(r.dirs) : setShowDirs(r.dirs);
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
          <h1 className='text-2xl font-semibold'>Library</h1>
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

      <Card>
        <CardContent className='grid gap-4 pt-6 md:grid-cols-2'>
          <DirManager
            title='Movie directories'
            icon={Film}
            dirs={movieDirs}
            value={newMovieDir}
            onChange={setNewMovieDir}
            onAdd={() => addDir('movie', newMovieDir)}
            onRemove={(p) => removeDir('movie', p)}
            busy={dirBusy}
          />
          <DirManager
            title='Show directories'
            icon={Tv}
            dirs={showDirs}
            value={newShowDir}
            onChange={setNewShowDir}
            onAdd={() => addDir('show', newShowDir)}
            onRemove={(p) => removeDir('show', p)}
            busy={dirBusy}
          />
        </CardContent>
      </Card>

      {error ? (
        <EmptyState icon={ServerIcon} title='Could not read server' description={error} />
      ) : loading ? (
        <div className='grid min-h-0 flex-1 gap-4 md:grid-cols-2'>
          <Skeleton className='h-96' />
          <Skeleton className='h-96' />
        </div>
      ) : (
        <div className='grid min-h-0 flex-1 gap-4 md:grid-cols-2'>
          <Column title='Movies' icon={Film} items={movies} filter={filter} revision={revision} onRename={openRename} />
          <Column title='Shows' icon={Tv} items={shows} filter={filter} expandable revision={revision} onRename={openRename} />
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
}: {
  title: string;
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
            <div key={d} className='flex items-center justify-between gap-2 rounded-md bg-secondary/40 px-2 py-1.5'>
              <code className='truncate text-xs'>{d}</code>
              <Button size='icon' variant='ghost' onClick={() => onRemove(d)} disabled={busy} title='Remove'>
                <Trash2 className='h-4 w-4 text-red-400' />
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
          placeholder='/mnt/media12/movies'
          className='flex-1'
        />
        <Button size='icon' variant='outline' onClick={onAdd} disabled={busy || !value.trim()}>
          <Plus className='h-4 w-4' />
        </Button>
      </div>
    </div>
  );
}
