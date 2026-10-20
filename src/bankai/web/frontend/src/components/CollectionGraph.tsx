import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Merge, Network, Plus, Scissors, Star } from 'lucide-react';
import { toast } from 'sonner';
import { api, type CollectionEdge, type CollectionGraph as Graph, type CollectionNode } from '@/lib/api';
import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { cn } from '@/lib/utils';

const NODE_W = 208;
const NODE_H = 92;
const GAP_X = 64;
const GAP_Y = 36;
const PAD = 24;
// Room between the collection's row and the suggestions under it, for their heading.
const BAND = 40;

type Placed = CollectionNode & { x: number; y: number };

/** Air date, else year, else last: the order a show's seasons came out in. */
function aired(node: CollectionNode) {
  return node.first_aired ?? (node.year ? String(node.year) : '9999');
}

/** The collection left to right by air date; each suggestion under the entry it hangs off. */
function layout(graph: Graph): { nodes: Placed[]; width: number; height: number } {
  const members = graph.nodes
    .filter((node) => node.in_collection)
    .sort((a, b) => aired(a).localeCompare(aired(b)) || a.anidb_id - b.anidb_id);
  const column = new Map(members.map((node, index) => [node.anidb_id, index]));
  const below = new Map<number, number>();
  const placed: Placed[] = members.map((node, index) => ({ ...node, x: PAD + index * (NODE_W + GAP_X), y: PAD }));
  const suggestions = graph.nodes
    .filter((node) => !node.in_collection)
    .sort((a, b) => aired(a).localeCompare(aired(b)) || a.anidb_id - b.anidb_id);
  for (const node of suggestions) {
    const anchors = graph.edges
      .map((edge) => (edge.from === node.anidb_id ? edge.to : edge.to === node.anidb_id ? edge.from : null))
      .filter((other): other is number => other !== null && column.has(other))
      .map((other) => column.get(other)!);
    const col = anchors.length ? Math.min(...anchors) : 0;
    const row = (below.get(col) ?? 0) + 1;
    below.set(col, row);
    placed.push({ ...node, x: PAD + col * (NODE_W + GAP_X), y: PAD + BAND + row * (NODE_H + GAP_Y) });
  }
  const rows = Math.max(0, ...below.values());
  return {
    nodes: placed,
    width: PAD * 2 + Math.max(1, members.length) * (NODE_W + GAP_X) - GAP_X,
    height: PAD * 2 + NODE_H + (rows ? BAND + rows * (NODE_H + GAP_Y) : 0),
  };
}

/** A link between two boxes: side to side within a row, else bottom to top. */
function path(from: Placed, to: Placed) {
  if (from.y === to.y) {
    const [left, right] = from.x < to.x ? [from, to] : [to, from];
    const y = left.y + NODE_H / 2;
    return { d: `M ${left.x + NODE_W} ${y} L ${right.x} ${y}`, mid: [(left.x + NODE_W + right.x) / 2, y] };
  }
  const [upper, lower] = from.y < to.y ? [from, to] : [to, from];
  const x1 = upper.x + NODE_W / 2;
  const y1 = upper.y + NODE_H;
  const x2 = lower.x + NODE_W / 2;
  const y2 = lower.y;
  const bend = (y2 - y1) / 2;
  return {
    d: `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`,
    mid: [(x1 + x2) / 2, (y1 + y2) / 2],
  };
}

function meta(node: CollectionNode) {
  const parts = [node.type, node.year ? String(node.year) : null];
  if (node.owned) parts.push(`${node.on_disk}/${node.episodes ?? '?'} on disk`);
  else parts.push(node.episodes ? `${node.episodes} eps, not downloaded` : 'not downloaded');
  return parts.filter(Boolean).join(' · ');
}

/**
 * A collection's relation graph: its entries, and the entries AniDB relates
 * to them. Splitting, merging and pulling entries in are changes to Shoko's
 * groups, which Jellyfin shows; for an entry not downloaded yet the choice is
 * kept and handed to Shoko once it is.
 */
export function CollectionGraph({
  groupId,
  onClose,
  onChanged,
}: {
  /** The Shoko group to show; null closes the dialog. */
  groupId: number | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  // A merge moves a whole collection: the button asks once more before it does.
  const [armed, setArmed] = useState<number | null>(null);

  async function load(id: number) {
    setLoading(true);
    try {
      setGraph(await api.collectionGraph(id));
    } catch (error: any) {
      toast.error(error.message);
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setSelected([]);
    setArmed(null);
    setGraph(null);
    if (groupId !== null) void load(groupId);
  }, [groupId]);

  const placed = useMemo(() => (graph ? layout(graph) : null), [graph]);
  const byId = useMemo(() => new Map((placed?.nodes ?? []).map((node) => [node.anidb_id, node])), [placed]);
  const picked = selected.map((id) => byId.get(id)).filter((node): node is Placed => Boolean(node));
  const members = picked.filter((node) => node.in_collection);
  const suggestions = picked.filter((node) => !node.in_collection);
  const memberCount = graph?.nodes.filter((node) => node.in_collection).length ?? 0;

  function toggle(id: number) {
    setArmed(null);
    setSelected((current) => (current.includes(id) ? current.filter((other) => other !== id) : [...current, id]));
  }

  async function act(label: string, work: () => Promise<unknown>) {
    if (groupId === null) return;
    setBusy(true);
    try {
      await work();
      toast.success(label);
      setSelected([]);
      setArmed(null);
      onChanged();
      await load(groupId);
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  function edge(item: CollectionEdge) {
    const from = byId.get(item.from);
    const to = byId.get(item.to);
    if (!from || !to) return null;
    const { d, mid } = path(from, to);
    return (
      <g key={`${item.from}-${item.to}-${item.label}`}>
        <path
          d={d}
          fill='none'
          className={item.main_line ? 'stroke-foreground/60' : 'stroke-muted-foreground/50'}
          strokeWidth={item.main_line ? 2 : 1.5}
          strokeDasharray={item.main_line ? undefined : '5 4'}
          markerEnd={item.label === 'sequel' || item.label === 'side story' || item.label === 'summary' ? 'url(#arrow)' : undefined}
        />
        {!item.main_line && (
          <text x={mid[0]} y={mid[1] - 4} textAnchor='middle' className='fill-muted-foreground text-[10px]'>
            {item.label}
          </text>
        )}
      </g>
    );
  }

  return (
    <Dialog open={groupId !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='flex h-[88dvh] w-[min(96vw,1400px)] max-w-none flex-col gap-3'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            The collection&apos;s entries left to right as they aired, sequels joined by solid arrows. Below: entries AniDB relates to
            them that are not in it. Changes go to Shoko&apos;s groups, so Jellyfin shows the same; a snapshot is kept before each.
          </DialogDescription>
        </DialogHeader>
        <div className='flex min-h-0 flex-1 gap-3'>
          <div className='relative min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-black/20'>
            {loading && !graph ? (
              <div className='flex h-full items-center justify-center'><Spinner /></div>
            ) : !placed ? (
              <EmptyState icon={Network} title='No graph' description='Shoko could not be read for this collection.' />
            ) : (
              <div className='relative' style={{ width: placed.width, height: placed.height }}>
                <svg className='pointer-events-none absolute inset-0' width={placed.width} height={placed.height} aria-hidden='true'>
                  <defs>
                    <marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto-start-reverse'>
                      <path d='M 0 0 L 10 5 L 0 10 z' className='fill-foreground/60' />
                    </marker>
                  </defs>
                  {graph!.edges.map(edge)}
                </svg>
                {placed.nodes.some((node) => !node.in_collection) && (
                  <p className='absolute text-[0.68rem] uppercase tracking-wide text-muted-foreground' style={{ left: PAD, top: PAD + NODE_H + 12 }}>
                    Related, not in this collection
                  </p>
                )}
                {placed.nodes.map((node) => (
                  <button
                    key={node.anidb_id}
                    type='button'
                    onClick={() => toggle(node.anidb_id)}
                    aria-pressed={selected.includes(node.anidb_id)}
                    title={node.main_title && node.main_title !== node.title ? node.main_title : node.title}
                    className={cn(
                      'absolute flex gap-2 rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      node.in_collection ? 'border-border bg-card' : 'border-dashed border-border/80 bg-card/60',
                      !node.owned && 'opacity-80',
                      node.title_entry && 'ring-1 ring-primary/70',
                      selected.includes(node.anidb_id) && 'ring-2 ring-ring',
                    )}
                    style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                  >
                    <AnimePoster url={node.poster_url} title={node.title} className='h-full w-auto shrink-0 rounded' />
                    <span className='flex min-w-0 flex-1 flex-col gap-1'>
                      <span className='line-clamp-2 text-xs font-medium leading-tight text-foreground'>
                        {node.title_entry && <Star className='mr-1 inline size-3 fill-current text-primary' aria-label='Title entry' />}
                        {node.title}
                      </span>
                      <span className='truncate font-mono text-[0.62rem] text-muted-foreground'>{meta(node)}</span>
                      {!node.in_collection && node.collection_name && (
                        <Badge variant='info' className='max-w-full truncate text-[0.6rem]'>In: {node.collection_name}</Badge>
                      )}
                      {node.alone && <Badge variant='muted' className='text-[0.6rem]'>In no collection</Badge>}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <aside className='flex w-80 shrink-0 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-card/40 p-3 text-sm'>
            {picked.length === 0 ? (
              <div className='flex flex-col gap-2 text-xs text-muted-foreground'>
                <p className='text-sm text-foreground'>Pick entries to change the collection.</p>
                <p>In the collection (top row): name it after one, or split entries off into a collection of their own.</p>
                <p>Related (below): pull one in, or merge the whole collection it is in.</p>
                <p>Not downloaded entries are kept where you put them and handed to Shoko once they are on disk. Cutting one out takes the entries reached only through it as well.</p>
              </div>
            ) : (
              <>
                <ul className='flex flex-col gap-1.5'>
                  {picked.map((node) => (
                    <li key={node.anidb_id} className='flex items-start justify-between gap-2'>
                      <span className='min-w-0'>
                        <span className='block truncate text-foreground'>{node.title}</span>
                        <span className='block truncate font-mono text-[0.65rem] text-muted-foreground'>{meta(node)}</span>
                      </span>
                      <a href={'https://anidb.net/anime/' + node.anidb_id} target='_blank' rel='noreferrer' className='text-muted-foreground hover:text-foreground' aria-label={'Open ' + node.title + ' on AniDB'}>
                        <ExternalLink className='size-3.5' />
                      </a>
                    </li>
                  ))}
                </ul>
                {members.length > 0 && suggestions.length > 0 ? (
                  <p className='text-xs text-muted-foreground'>Pick entries of the collection or related ones, not both at once.</p>
                ) : members.length > 0 ? (
                  <div className='flex flex-col gap-2'>
                    {members.length === 1 && members[0].owned && !members[0].title_entry && (
                      <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act('Collection now named after ' + members[0].title, () => api.collectionTitle(groupId!, members[0].anidb_id))}>
                        <Star data-icon='inline-start' /> Name the collection after it
                      </Button>
                    )}
                    {members.length < memberCount && (
                      <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(
                        members.length === 1 ? members[0].title + ' split off' : members.length + ' entries split off into a new collection',
                        () => (members.length === 1 ? api.collectionExclude(groupId!, members[0].anidb_id) : api.collectionSplit(groupId!, members.map((node) => node.anidb_id))),
                      )}>
                        <Scissors data-icon='inline-start' /> {members.length === 1 ? 'Split off into its own collection' : `Split ${members.length} off into a new collection`}
                      </Button>
                    )}
                    {members.every((node) => !node.owned) && (
                      <p className='text-xs text-muted-foreground'>Not downloaded: split off on its own, it leaves every collection, with the entries reached only through it.</p>
                    )}
                  </div>
                ) : suggestions.length === 1 ? (
                  <div className='flex flex-col gap-2'>
                    <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(suggestions[0].title + ' pulled in', () => api.collectionInclude(groupId!, suggestions[0].anidb_id))}>
                      <Plus data-icon='inline-start' /> Pull into this collection
                    </Button>
                    {suggestions[0].collection_id !== null && suggestions[0].collection_id !== groupId && (
                      armed === suggestions[0].collection_id ? (
                        <Button size='sm' variant='destructive' disabled={busy} onClick={() => void act('Merged ' + suggestions[0].collection_name, () => api.collectionMerge(groupId!, suggestions[0].collection_id!))}>
                          <Merge data-icon='inline-start' /> Yes, merge all of {suggestions[0].collection_name}
                        </Button>
                      ) : (
                        <Button size='sm' variant='secondary' disabled={busy} onClick={() => setArmed(suggestions[0].collection_id)}>
                          <Merge data-icon='inline-start' /> Merge its whole collection in
                        </Button>
                      )
                    )}
                  </div>
                ) : (
                  <p className='text-xs text-muted-foreground'>Pull related entries in one at a time.</p>
                )}
                {busy && <div className='flex items-center gap-2 text-xs text-muted-foreground'><Spinner /> Asking Shoko…</div>}
              </>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  );
}
