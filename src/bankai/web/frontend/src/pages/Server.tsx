import { useEffect, useState } from "react";
import { Server as ServerIcon, RefreshCw, Film, Tv, Loader2, Plus, Trash2, FolderPlus } from "lucide-react";
import { toast } from "sonner";
import { api, type ServerTitle } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty";

function Column({
  title,
  icon: Icon,
  items,
  filter,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  items: ServerTitle[];
  filter: string;
}) {
  const filtered = items.filter((i) => i.name.toLowerCase().includes(filter.toLowerCase()));
  const present = items.filter((i) => i.present).length;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Icon className="h-4 w-4" /> {title}
        </CardTitle>
        <Badge variant="muted">
          {present}/{items.length} on server
        </Badge>
      </CardHeader>
      <CardContent className="max-h-[60vh] space-y-1.5 overflow-auto">
        {filtered.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No titles.</p>
        ) : (
          filtered.map((it) => (
            <div
              key={it.name}
              className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5 hover:bg-secondary/40"
            >
              <span className="truncate text-sm" title={it.location || undefined}>
                {it.name}
              </span>
              {it.present ? (
                <Badge variant="success">On server</Badge>
              ) : (
                <Badge variant="muted">Missing</Badge>
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
  const [filter, setFilter] = useState("");

  const [movieDirs, setMovieDirs] = useState<string[]>([]);
  const [showDirs, setShowDirs] = useState<string[]>([]);
  const [newMovieDir, setNewMovieDir] = useState("");
  const [newShowDir, setNewShowDir] = useState("");
  const [dirBusy, setDirBusy] = useState(false);

  async function load(rescan = false) {
    rescan ? setRescanning(true) : setLoading(true);
    setError(null);
    try {
      const r = await api.serverContents(rescan);
      setMovies(r.movies);
      setShows(r.shows);
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

  async function addDir(kind: "movie" | "show", path: string) {
    if (!path.trim()) return;
    setDirBusy(true);
    try {
      const r = await api.addServerDir(kind, path.trim());
      kind === "movie" ? setMovieDirs(r.dirs) : setShowDirs(r.dirs);
      kind === "movie" ? setNewMovieDir("") : setNewShowDir("");
      toast.success("Directory added");
      await load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDirBusy(false);
    }
  }

  async function removeDir(kind: "movie" | "show", path: string) {
    setDirBusy(true);
    try {
      const r = await api.removeServerDir(kind, path);
      kind === "movie" ? setMovieDirs(r.dirs) : setShowDirs(r.dirs);
      toast.success("Directory removed");
      await load(true);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDirBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Server</h1>
          <p className="text-sm text-muted-foreground">What already lives on the media server.</p>
        </div>
        <div className="flex gap-2">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            className="w-48"
          />
          <Button variant="secondary" onClick={() => load(true)} disabled={rescanning}>
            {rescanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Rescan
          </Button>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FolderPlus className="h-4 w-4" /> Scan directories
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <DirManager
            title="Movie directories"
            icon={Film}
            dirs={movieDirs}
            value={newMovieDir}
            onChange={setNewMovieDir}
            onAdd={() => addDir("movie", newMovieDir)}
            onRemove={(p) => removeDir("movie", p)}
            busy={dirBusy}
          />
          <DirManager
            title="Show directories"
            icon={Tv}
            dirs={showDirs}
            value={newShowDir}
            onChange={setNewShowDir}
            onAdd={() => addDir("show", newShowDir)}
            onRemove={(p) => removeDir("show", p)}
            busy={dirBusy}
          />
        </CardContent>
      </Card>

      {error ? (
        <EmptyState icon={ServerIcon} title="Could not read server" description={error} />
      ) : loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <Column title="Movies" icon={Film} items={movies} filter={filter} />
          <Column title="Shows" icon={Tv} items={shows} filter={filter} />
        </div>
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
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Icon className="h-4 w-4" /> {title}
      </div>
      <div className="space-y-1.5">
        {dirs.length === 0 ? (
          <p className="text-xs text-muted-foreground">No directories configured.</p>
        ) : (
          dirs.map((d) => (
            <div
              key={d}
              className="flex items-center justify-between gap-2 rounded-md bg-secondary/40 px-2 py-1.5"
            >
              <code className="truncate text-xs">{d}</code>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => onRemove(d)}
                disabled={busy}
                title="Remove"
              >
                <Trash2 className="h-4 w-4 text-red-400" />
              </Button>
            </div>
          ))
        )}
      </div>
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAdd()}
          placeholder="/mnt/media12/movies"
          className="flex-1"
        />
        <Button size="icon" variant="outline" onClick={onAdd} disabled={busy || !value.trim()}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
