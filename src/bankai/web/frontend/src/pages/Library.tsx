import { useEffect, useMemo, useRef, useState } from "react";
import {
  Library as LibraryIcon,
  Trash2,
  Play,
  Loader2,
  Minus,
  Plus,
  RotateCcw,
  CheckCircle2,
  Send,
  Languages,
  AudioLines,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import { toast } from "sonner";
import { api, type LibraryEntry, type MediaInfo } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Card, CardContent } from "@/components/ui/card";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatBytes, timeAgo } from "@/lib/utils";

type SortKey = "name" | "date" | "size";

function stageBadge(stage: string) {
  switch (stage) {
    case "approved":
      return <Badge variant="success">Approved</Badge>;
    case "transferred":
      return <Badge variant="accent">Transferred</Badge>;
    case "review":
    default:
      return <Badge variant="warning">Review</Badge>;
  }
}

export default function Library() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("date");
  const [filter, setFilter] = useState("");
  const [openSeries, setOpenSeries] = useState<Record<string, boolean>>({});

  const [review, setReview] = useState<LibraryEntry | null>(null);
  const [del, setDel] = useState<LibraryEntry | null>(null);

  async function load() {
    setLoading(true);
    try {
      const r = await api.library();
      setEntries(r.entries);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    let list = entries.filter((e) => e.name.toLowerCase().includes(filter.toLowerCase()));
    list.sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "size") return b.size - a.size;
      return b.mtime - a.mtime;
    });
    return list;
  }, [entries, filter, sort]);

  const movies = filtered.filter((e) => e.kind === "movie");
  const shows = filtered.filter((e) => e.kind === "episode");
  const seriesGroups = useMemo(() => {
    const map = new Map<string, LibraryEntry[]>();
    for (const e of shows) {
      const k = e.series || "Unknown";
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(e);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [shows]);

  function Row({ e }: { e: LibraryEntry }) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-secondary/40">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{e.name}</div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{formatBytes(e.size)}</span>
            <span>· {timeAgo(e.mtime)}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {stageBadge(e.stage)}
          <Button size="sm" variant="secondary" onClick={() => setReview(e)}>
            <Play className="h-4 w-4" /> Review
          </Button>
          <Button size="icon" variant="ghost" onClick={() => setDel(e)} title="Delete">
            <Trash2 className="h-4 w-4 text-red-400" />
          </Button>
        </div>
      </div>
    );
  }

  async function doDelete() {
    if (!del) return;
    try {
      await api.deleteFile(del.path);
      toast.success("Deleted");
      setDel(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Library</h1>
          <p className="text-sm text-muted-foreground">Review, QC and approve merged titles.</p>
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            className="w-44"
          />
          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="date">Newest</SelectItem>
              <SelectItem value="name">Name</SelectItem>
              <SelectItem value="size">Size</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </header>

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={LibraryIcon}
          title="Library is empty"
          description="Merged titles will appear here for review."
        />
      ) : (
        <div className="space-y-6">
          {movies.length > 0 && (
            <Card>
              <CardContent className="p-3">
                <div className="mb-1 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Movies
                </div>
                {movies.map((e) => (
                  <Row key={e.path} e={e} />
                ))}
              </CardContent>
            </Card>
          )}

          {seriesGroups.map(([series, eps]) => {
            const open = openSeries[series] ?? true;
            return (
              <Card key={series}>
                <CardContent className="p-3">
                  <button
                    className="flex w-full items-center gap-2 px-2 py-1 text-left"
                    onClick={() => setOpenSeries((s) => ({ ...s, [series]: !open }))}
                  >
                    {open ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                    <span className="font-semibold">{series}</span>
                    <Badge variant="muted" className="ml-1">
                      {eps.length}
                    </Badge>
                  </button>
                  {open &&
                    eps
                      .sort((a, b) => a.name.localeCompare(b.name))
                      .map((e) => <Row key={e.path} e={e} />)}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Delete confirm */}
      <Dialog open={!!del} onOpenChange={(o) => !o && setDel(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete file?</DialogTitle>
            <DialogDescription>
              This permanently removes <span className="font-medium">{del?.name}</span> from the
              local library. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDel(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={doDelete}>
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Review studio */}
      {review && (
        <ReviewStudio
          entry={review}
          onClose={() => {
            setReview(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function ReviewStudio({ entry, onClose }: { entry: LibraryEntry; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [delay, setDelay] = useState(0);
  const [savedDelay, setSavedDelay] = useState(0);
  const [audioIdx, setAudioIdx] = useState(0);
  const [useTranscode, setUseTranscode] = useState(false);
  const [repacking, setRepacking] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function loadInfo() {
    setLoading(true);
    try {
      const m = await api.mediaInfo(entry.path);
      setInfo(m);
      setDelay(m.delay_ms);
      setSavedDelay(m.delay_ms);
      setUseTranscode(!m.browser_playable);
      const ger = m.audio_tracks.find((t) => t.is_german);
      setAudioIdx(ger ? ger.order : 0);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    loadInfo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry.path]);

  const src = useTranscode
    ? api.transcodeUrl(entry.path, audioIdx)
    : api.streamUrl(entry.path);

  // Apply live preview delay to the selected audio via playbackRate trick is not exact;
  // we use the audioTracks API when available, else just reload. For preview we offset
  // the audio element timing client-side is limited, so live preview = repack-less hint.
  const previewDelta = delay - savedDelay;

  // Keyboard shortcuts
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const v = videoRef.current;
      if (!v) return;
      if (ev.key === " ") {
        ev.preventDefault();
        v.paused ? v.play() : v.pause();
      } else if (ev.key === "ArrowLeft") {
        v.currentTime = Math.max(0, v.currentTime - 5);
      } else if (ev.key === "ArrowRight") {
        v.currentTime += 5;
      } else if (ev.key === "[") {
        setDelay((d) => d - 10);
      } else if (ev.key === "]") {
        setDelay((d) => d + 10);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function repack() {
    setRepacking(true);
    try {
      const r = await api.repack(entry.path, delay);
      toast.success(r.message || "Repacked");
      await loadInfo();
      if (videoRef.current) videoRef.current.load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setRepacking(false);
    }
  }

  async function persistDelay() {
    try {
      await api.setDelay(entry.path, delay);
      setSavedDelay(delay);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function approve() {
    setBusy("approve");
    try {
      await api.approve(entry.path);
      toast.success("Approved — ready to transfer");
      await loadInfo();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  async function transfer() {
    setBusy("transfer");
    try {
      await api.transfer(entry.path);
      toast.success("Transferred to server");
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {entry.name}
            {info && stageBadge(info.stage)}
          </DialogTitle>
          <DialogDescription>
            QC the German dub timing, then approve and transfer.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <Skeleton className="aspect-video w-full" />
        ) : (
          <>
            <video
              ref={videoRef}
              src={src}
              controls
              className="aspect-video w-full rounded-md bg-black"
            />

            <div className="grid gap-4 md:grid-cols-2">
              {/* Audio track + transcode */}
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <AudioLines className="h-4 w-4" /> Audio track
                </div>
                <Select
                  value={String(audioIdx)}
                  onValueChange={(v) => {
                    setAudioIdx(Number(v));
                    if (useTranscode && videoRef.current) {
                      const t = videoRef.current.currentTime;
                      requestAnimationFrame(() => {
                        if (videoRef.current) videoRef.current.currentTime = t;
                      });
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {info?.audio_tracks.map((t) => (
                      <SelectItem key={t.order} value={String(t.order)}>
                        {t.is_german ? "🇩🇪 " : ""}
                        {t.language || "und"} · {t.codec}
                        {t.title ? ` · ${t.title}` : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={useTranscode}
                    onChange={(e) => setUseTranscode(e.target.checked)}
                  />
                  Transcode (needed for 4K/HEVC)
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {info?.has_german ? (
                    <Badge variant="success" className="gap-1">
                      <Languages className="h-3 w-3" /> German present
                    </Badge>
                  ) : (
                    <Badge variant="destructive">No German track</Badge>
                  )}
                  {info?.video_codec && <Badge variant="muted">{info.video_codec}</Badge>}
                  {info?.width && (
                    <Badge variant="muted">
                      {info.width}×{info.height}
                    </Badge>
                  )}
                </div>
              </div>

              {/* Delay adjust */}
              <div className="space-y-3">
                <div className="flex items-center justify-between text-sm font-medium">
                  <span>German audio delay</span>
                  <span className="font-mono text-primary-foreground">
                    {delay > 0 ? "+" : ""}
                    {delay} ms
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Button size="icon" variant="outline" onClick={() => setDelay((d) => d - 100)}>
                    <Minus className="h-4 w-4" />
                  </Button>
                  <Slider
                    value={[delay]}
                    min={-2000}
                    max={2000}
                    step={10}
                    onValueChange={(v) => setDelay(v[0])}
                    onValueCommit={persistDelay}
                    className="flex-1"
                  />
                  <Button size="icon" variant="outline" onClick={() => setDelay((d) => d + 100)}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    type="number"
                    step={10}
                    value={delay}
                    onChange={(e) => setDelay(Number(e.target.value))}
                    onBlur={persistDelay}
                    className="w-28"
                  />
                  <Button size="sm" variant="ghost" onClick={() => setDelay(0)}>
                    <RotateCcw className="h-4 w-4" /> Reset
                  </Button>
                </div>
                {previewDelta !== 0 && (
                  <p className="text-xs text-amber-300">
                    Preview offset {previewDelta > 0 ? "+" : ""}
                    {previewDelta} ms — repack to bake it into the file.
                  </p>
                )}
                <Button onClick={repack} disabled={repacking} className="w-full">
                  {repacking ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <AudioLines className="h-4 w-4" />
                  )}
                  Repack with {delay > 0 ? "+" : ""}
                  {delay} ms
                </Button>
                <p className="text-[11px] text-muted-foreground">
                  Shortcuts: space = play/pause, ←/→ = seek 5s, [ / ] = ∓10 ms.
                </p>
              </div>
            </div>
          </>
        )}

        <DialogFooter className="border-t border-border/50 pt-4">
          {info?.stage === "approved" ? (
            <Button onClick={transfer} disabled={busy === "transfer"}>
              {busy === "transfer" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              Transfer to server
            </Button>
          ) : info?.stage === "transferred" ? (
            <Badge variant="accent">Already transferred</Badge>
          ) : (
            <Button onClick={approve} disabled={busy === "approve"} variant="default">
              {busy === "approve" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              Approve
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
