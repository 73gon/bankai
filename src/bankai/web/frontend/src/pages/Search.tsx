import { useState } from "react";
import { Search as SearchIcon, Loader2, Plus } from "lucide-react";
import { toast } from "sonner";
import { api, type SearchResult, type EpisodeItem } from "@/lib/api";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty";
import { Card, CardContent } from "@/components/ui/card";

export default function Search() {
  const [kind, setKind] = useState("movie");
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // show episode picker
  const [picked, setPicked] = useState<SearchResult | null>(null);
  const [season, setSeason] = useState("1");
  const [episodes, setEpisodes] = useState<EpisodeItem[]>([]);
  const [selectedEps, setSelectedEps] = useState<Set<number>>(new Set());
  const [range, setRange] = useState("");
  const [loadingEps, setLoadingEps] = useState(false);

  async function runSearch() {
    if (!q.trim()) return;
    setLoading(true);
    setSearched(true);
    setPicked(null);
    try {
      const r = await api.search(q.trim(), kind);
      setResults(r.results);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function queueMovie(r: SearchResult) {
    setBusy(r.url);
    try {
      await api.queueMovie({ title: r.title, url: r.url, site: r.site });
      toast.success(`Queued ${r.title}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function loadEpisodes(r: SearchResult, s: string) {
    setPicked(r);
    setLoadingEps(true);
    setSelectedEps(new Set());
    try {
      const res = await api.episodes(r.title, Number(s) || 1, r.site);
      setEpisodes(res.episodes);
    } catch (e: any) {
      toast.error(e.message);
      setEpisodes([]);
    } finally {
      setLoadingEps(false);
    }
  }

  function applyRange() {
    const next = new Set(selectedEps);
    range.split(",").forEach((p) => {
      const m = p.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) for (let i = +m[1]; i <= +m[2]; i++) next.add(i);
      else if (p.trim()) next.add(Number(p.trim()));
    });
    setSelectedEps(next);
  }

  async function queueShow() {
    if (!picked) return;
    setBusy(picked.url);
    try {
      await api.queueShow({
        show: picked.title,
        season: Number(season) || 1,
        episodes: selectedEps.size ? Array.from(selectedEps).sort((a, b) => a - b) : undefined,
        site: picked.site,
      });
      toast.success(`Queued ${picked.title} S${season}`);
      setPicked(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Search</h1>
        <p className="text-sm text-muted-foreground">Find a title across supported sites.</p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={kind} onValueChange={(v) => { setKind(v); setResults([]); setSearched(false); }}>
          <TabsList>
            <TabsTrigger value="movie">Movie</TabsTrigger>
            <TabsTrigger value="show">Show</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex flex-1 gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder={kind === "movie" ? "Inception…" : "Arcane…"}
          />
          <Button onClick={runSearch} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchIcon className="h-4 w-4" />}
            Search
          </Button>
        </div>
      </div>

      {searched && !loading && results.length === 0 && (
        <EmptyState icon={SearchIcon} title="No results" description="Try a different title or site." />
      )}

      <div className="space-y-2">
        {results.map((r) => (
          <Card key={`${r.site}-${r.url}`}>
            <CardContent className="flex items-center justify-between gap-4 p-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{r.title}</span>
                  {r.year && <span className="text-xs text-muted-foreground">{r.year}</span>}
                </div>
                <Badge variant="muted" className="mt-1">{r.site}</Badge>
              </div>
              {kind === "movie" ? (
                <Button size="sm" onClick={() => queueMovie(r)} disabled={busy === r.url}>
                  {busy === r.url ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                  Queue
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => loadEpisodes(r, season)}>
                  Episodes
                </Button>
              )}
            </CardContent>

            {picked?.url === r.url && (
              <CardContent className="border-t border-border/50 pt-4">
                <div className="mb-3 flex flex-wrap items-end gap-3">
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">Season</label>
                    <Input
                      type="number"
                      min={1}
                      value={season}
                      onChange={(e) => setSeason(e.target.value)}
                      onBlur={() => loadEpisodes(r, season)}
                      className="w-24"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-muted-foreground">Range (e.g. 1-9)</label>
                    <div className="flex gap-2">
                      <Input value={range} onChange={(e) => setRange(e.target.value)} className="w-28" />
                      <Button size="sm" variant="outline" onClick={applyRange}>Add</Button>
                    </div>
                  </div>
                  <Button size="sm" onClick={queueShow} disabled={busy === r.url}>
                    {busy === r.url ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                    Queue {selectedEps.size ? `(${selectedEps.size})` : "all"}
                  </Button>
                </div>

                {loadingEps ? (
                  <div className="text-sm text-muted-foreground">Loading episodes…</div>
                ) : episodes.length === 0 ? (
                  <div className="text-sm text-muted-foreground">No episodes found for this season.</div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {episodes.map((ep) => {
                      const on = selectedEps.has(ep.episode);
                      return (
                        <button
                          key={ep.episode}
                          onClick={() => {
                            const next = new Set(selectedEps);
                            on ? next.delete(ep.episode) : next.add(ep.episode);
                            setSelectedEps(next);
                          }}
                          className={
                            "rounded-md px-2.5 py-1 text-xs font-medium ring-1 transition-colors " +
                            (on
                              ? "bg-primary/20 text-primary-foreground ring-primary/40"
                              : "bg-secondary/40 text-muted-foreground ring-border/40 hover:text-foreground")
                          }
                          title={ep.title || undefined}
                        >
                          E{String(ep.episode).padStart(2, "0")}
                        </button>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
