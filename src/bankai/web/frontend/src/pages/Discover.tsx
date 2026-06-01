import { useEffect, useState } from "react";
import { Compass, Loader2, Plus, Film, Tv } from "lucide-react";
import { toast } from "sonner";
import { api, type DiscoverItem } from "@/lib/api";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

function Poster({ item, onClick }: { item: DiscoverItem; onClick: () => void }) {
  const [err, setErr] = useState(false);
  return (
    <button
      onClick={onClick}
      className="group relative aspect-[2/3] overflow-hidden rounded-lg bg-secondary/40 text-left ring-1 ring-border/40 transition-transform hover:-translate-y-1 hover:ring-primary/50"
    >
      {item.poster_url && !err ? (
        <img
          src={api.posterUrl(item.poster_url)}
          onError={() => setErr(true)}
          className="h-full w-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-muted-foreground">
          {item.kind === "movie" ? <Film className="h-8 w-8" /> : <Tv className="h-8 w-8" />}
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent p-2.5 pt-8">
        <div className="line-clamp-2 text-xs font-medium">{item.name}</div>
        {item.year && <div className="text-[10px] text-muted-foreground">{item.year}</div>}
      </div>
      {item.is_new && (
        <span className="absolute left-2 top-2 rounded bg-primary px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary-foreground shadow">
          New
        </span>
      )}
      <div className="absolute inset-0 flex items-center justify-center bg-primary/20 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
        <Plus className="h-7 w-7" />
      </div>
    </button>
  );
}

export default function Discover() {
  const [kind, setKind] = useState("movie");
  const [items, setItems] = useState<DiscoverItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(true);
  const [selected, setSelected] = useState<DiscoverItem | null>(null);
  const [season, setSeason] = useState("1");
  const [episodes, setEpisodes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLoading(true);
    api
      .discoverTrending(kind)
      .then((r) => {
        setConfigured(r.configured);
        setItems(r.items);
      })
      .catch((e) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [kind]);

  async function enqueue() {
    if (!selected) return;
    setBusy(true);
    try {
      if (selected.kind === "movie") {
        await api.queueMovie({ title: selected.name });
      } else {
        const eps = episodes
          .split(",")
          .flatMap((p) => {
            const m = p.trim().match(/^(\d+)\s*-\s*(\d+)$/);
            if (m) {
              const out = [];
              for (let i = +m[1]; i <= +m[2]; i++) out.push(i);
              return out;
            }
            return p.trim() ? [Number(p.trim())] : [];
          })
          .filter((n) => !Number.isNaN(n));
        await api.queueShow({
          show: selected.name,
          season: Number(season) || 1,
          episodes: eps.length ? eps : undefined,
        });
      }
      toast.success(`Queued ${selected.name}`);
      setSelected(null);
      setEpisodes("");
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Discover</h1>
          <p className="text-sm text-muted-foreground">Trending titles from TheTVDB.</p>
        </div>
      </header>

      <Tabs value={kind} onValueChange={setKind}>
        <TabsList>
          <TabsTrigger value="movie">Movies</TabsTrigger>
          <TabsTrigger value="show">Shows</TabsTrigger>
        </TabsList>

        <TabsContent value={kind}>
          {!configured ? (
            <EmptyState
              icon={Compass}
              title="TheTVDB key not configured"
              description="Add your TVDB API key in Settings to browse trending titles."
            />
          ) : loading ? (
            <div className="grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6">
              {Array.from({ length: 12 }).map((_, i) => (
                <Skeleton key={i} className="aspect-[2/3] rounded-lg" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <EmptyState icon={Compass} title="Nothing to show" />
          ) : (
            <div className="grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6">
              {items.map((it) => (
                <Poster key={`${it.kind}-${it.tvdb_id}-${it.name}`} item={it} onClick={() => setSelected(it)} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{selected?.name}</DialogTitle>
            <DialogDescription>
              {selected?.kind === "movie"
                ? "Queue this movie for download and German dubbing."
                : "Pick a season and optional episode list to queue."}
            </DialogDescription>
          </DialogHeader>

          {selected?.kind === "show" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Season</label>
                <Input value={season} onChange={(e) => setSeason(e.target.value)} type="number" min={1} />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Episodes (e.g. 1-9 or blank=all)</label>
                <Input value={episodes} onChange={(e) => setEpisodes(e.target.value)} placeholder="all" />
              </div>
            </div>
          )}

          <DialogFooter>
            <Button onClick={enqueue} disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Queue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
