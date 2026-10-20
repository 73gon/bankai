import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowRightToLine, ExternalLink, Merge, Network, Plus, Scissors, Star } from 'lucide-react';
import { toast } from 'sonner';
import { api, type CollectionGraph as Graph, type CollectionNode } from '@/lib/api';
import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { cn } from '@/lib/utils';

// One entry's card: a poster with its title under it.
const CARD_W = 176;
const CARD_H = 344;
const GAP_X = 88;
const GAP_Y = 64;
const FRAME_PAD = 20;
const FRAME_HEAD = 30;

/** What kind of entry it is, as anime-offline-database says: its colour throughout. */
const TYPES: Record<string, { label: string; stripe: string; chip: string }> = {
  TV: { label: 'TV series', stripe: 'bg-sky-400', chip: 'border-sky-400/40 bg-sky-400/10 text-sky-300' },
  MOVIE: { label: 'Movie', stripe: 'bg-rose-400', chip: 'border-rose-400/40 bg-rose-400/10 text-rose-300' },
  OVA: { label: 'OVA', stripe: 'bg-violet-400', chip: 'border-violet-400/40 bg-violet-400/10 text-violet-300' },
  ONA: { label: 'ONA', stripe: 'bg-emerald-400', chip: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300' },
  SPECIAL: { label: 'Special', stripe: 'bg-orange-400', chip: 'border-orange-400/40 bg-orange-400/10 text-orange-300' },
};
const OTHER_TYPE = { label: 'Other', stripe: 'bg-zinc-500', chip: 'border-zinc-500/40 bg-zinc-500/10 text-zinc-300' };

function typeOf(node: CollectionNode) {
  return TYPES[(node.type ?? '').toUpperCase()] ?? OTHER_TYPE;
}

type EntryData = { node: CollectionNode; selected: boolean; spine: boolean };
type FrameData = { name: string; width: number; height: number };
type FlowNode = Node<EntryData, 'entry'> | Node<FrameData, 'frame'>;
type Point = { x: number; y: number };

/** Air date, else year, else last: the order entries came out in. */
function aired(node: CollectionNode) {
  return node.first_aired ?? (node.year ? String(node.year) : '9999');
}

function meta(node: CollectionNode) {
  const parts = [node.year ? String(node.year) : null];
  if (node.owned) parts.push(`${node.on_disk}/${node.episodes ?? '?'} on disk`);
  else parts.push(node.episodes ? `${node.episodes} eps · not downloaded` : 'not downloaded');
  return parts.filter(Boolean).join(' · ');
}

/** Entries in sequel order, prequel first; what the links do not order goes by air date. */
function sequelOrder(ids: number[], sequels: Map<number, number[]>, byId: Map<number, CollectionNode>) {
  const members = new Set(ids);
  const waiting = new Map(ids.map((id) => [id, 0]));
  for (const id of ids) for (const next of sequels.get(id) ?? []) if (members.has(next)) waiting.set(next, (waiting.get(next) ?? 0) + 1);
  const byDate = (a: number, b: number) => aired(byId.get(a)!).localeCompare(aired(byId.get(b)!)) || a - b;
  const layer = new Map<number, number>();
  const ready = ids.filter((id) => waiting.get(id) === 0).sort(byDate);
  for (const id of ready) layer.set(id, 0);
  const done: number[] = [];
  while (ready.length) {
    const id = ready.shift()!;
    done.push(id);
    for (const next of sequels.get(id) ?? []) {
      if (!members.has(next)) continue;
      layer.set(next, Math.max(layer.get(next) ?? 0, (layer.get(id) ?? 0) + 1));
      waiting.set(next, waiting.get(next)! - 1);
      if (waiting.get(next) === 0) {
        ready.push(next);
        ready.sort(byDate);
      }
    }
  }
  // A loop in AniDB's links leaves entries waiting: after the rest, by date.
  for (const id of [...ids].sort(byDate)) {
    if (!layer.has(id)) layer.set(id, Math.max(0, ...layer.values()) + 1);
    if (!done.includes(id)) done.push(id);
  }
  return { order: done, layer };
}

type Layout = { positions: Map<number, Point>; spine: Set<number>; frames: { id: number; name: string; x: number; y: number; width: number; height: number }[] };

/**
 * Where each entry goes. The collection's main line -- the sequel chain its
 * title entry is on -- runs top to bottom down the middle. Its side stories,
 * recaps, movies hang to the sides of the entry they belong to, right then
 * left. Another collection something is related to stands in a frame of its
 * own beside it, its own sequels top to bottom.
 */
function layout(graph: Graph): Layout {
  const byId = new Map(graph.nodes.map((node) => [node.anidb_id, node]));
  const sequels = new Map<number, number[]>();
  const linked = new Map<number, number[]>();
  for (const node of graph.nodes) {
    sequels.set(node.anidb_id, []);
    linked.set(node.anidb_id, []);
  }
  for (const edge of graph.edges) {
    linked.get(edge.from)?.push(edge.to);
    linked.get(edge.to)?.push(edge.from);
    if (edge.label === 'sequel') sequels.get(edge.from)?.push(edge.to);
  }
  const own = graph.nodes.filter((node) => node.in_collection).map((node) => node.anidb_id);
  const ownSet = new Set(own);
  // The main line: the title entry's sequel chain, within the collection.
  const start = graph.title_entry && ownSet.has(graph.title_entry) ? graph.title_entry : own[0];
  const spine = new Set<number>();
  const walk = start !== undefined ? [start] : [];
  while (walk.length) {
    const id = walk.pop()!;
    if (spine.has(id) || !ownSet.has(id)) continue;
    spine.add(id);
    for (const other of graph.edges) {
      if (other.label !== 'sequel') continue;
      if (other.from === id) walk.push(other.to);
      if (other.to === id) walk.push(other.from);
    }
  }
  const step = CARD_H + GAP_Y;
  const lane = CARD_W + GAP_X;
  const positions = new Map<number, Point>();
  const { order: spineOrder, layer } = sequelOrder([...spine], sequels, byId);
  const across = new Map<number, number>(); // row -> parallel sequels placed in it
  for (const id of spineOrder) {
    const row = layer.get(id)!;
    const k = across.get(row) ?? 0;
    across.set(row, k + 1);
    positions.set(id, { x: k * lane, y: row * step });
  }
  const spineWidth = Math.max(1, ...across.values());

  // Each remaining entry hangs off the spine entry it is reached from first.
  const anchor = new Map<number, number>();
  const depth = new Map<number, number>();
  const queue = [...spineOrder];
  for (const id of spineOrder) {
    anchor.set(id, id);
    depth.set(id, 0);
  }
  const byDate = (a: number, b: number) => aired(byId.get(a)!).localeCompare(aired(byId.get(b)!)) || a - b;
  while (queue.length) {
    const id = queue.shift()!;
    for (const other of [...linked.get(id)!].sort(byDate)) {
      if (anchor.has(other)) continue;
      // Another collection is laid out on its own; only this one's and loose ones hang here.
      const elsewhere = !byId.get(other)!.in_collection && byId.get(other)!.collection_id !== null && byId.get(other)!.collection_id !== graph.collection_id;
      anchor.set(other, anchor.get(id)!);
      depth.set(other, depth.get(id)! + 1);
      if (!elsewhere) queue.push(other);
    }
  }
  const others = new Map<number, number[]>(); // collection -> its entries
  // Side entries pack into a band beside the main line, at most two columns
  // each side, and run downwards: in one row, eleven movies of one show made
  // a strip too wide to read.
  const SIDE_COLUMNS = 2;
  const taken = new Set<string>();
  const cell = (col: number, row: number) => `${col}:${row}`;
  for (const point of positions.values()) taken.add(cell(Math.round(point.x / lane), Math.round(point.y / step)));
  let rightmost = spineWidth - 1;
  const loose: number[] = [];
  for (const node of [...graph.nodes].sort((a, b) => (depth.get(a.anidb_id) ?? 99) - (depth.get(b.anidb_id) ?? 99) || byDate(a.anidb_id, b.anidb_id))) {
    const id = node.anidb_id;
    if (positions.has(id)) continue;
    if (!node.in_collection && node.collection_id !== null && node.collection_id !== graph.collection_id) {
      others.set(node.collection_id, [...(others.get(node.collection_id) ?? []), id]);
      continue;
    }
    const root = anchor.get(id);
    if (root === undefined) {
      loose.push(id);
      continue;
    }
    // The nearest free place beside its entry: right, left, one further out,
    // then a row further down.
    const row = Math.round(positions.get(root)!.y / step);
    let spot: Point | null = null;
    for (let down = 0; spot === null; down += 1) {
      for (let out = 1; out <= SIDE_COLUMNS && spot === null; out += 1) {
        for (const column of [spineWidth - 1 + out, -out]) {
          if (taken.has(cell(column, row + down))) continue;
          taken.add(cell(column, row + down));
          rightmost = Math.max(rightmost, column);
          spot = { x: column * lane, y: (row + down) * step };
          break;
        }
      }
    }
    positions.set(id, spot);
  }
  // Loose ends -- related to nothing laid out -- in a row under everything.
  const bottom = Math.max(0, ...[...positions.values()].map((point) => point.y / step)) + 1;
  loose.forEach((id, index) => positions.set(id, { x: index * lane, y: bottom * step }));

  // Other collections: each a frame of its own to the right, its main line downwards.
  const frames: Layout['frames'] = [];
  let column = rightmost + 1;
  for (const [collection, ids] of [...others.entries()].sort((a, b) => a[0] - b[0])) {
    const anchorRows = ids.map((id) => anchor.get(id)).filter((root): root is number => root !== undefined && positions.has(root)).map((root) => positions.get(root)!.y / step);
    const top = anchorRows.length ? Math.min(...anchorRows) : 0;
    const { order, layer: rows } = sequelOrder(ids, sequels, byId);
    const width = new Map<number, number>();
    for (const id of order) {
      const row = rows.get(id)!;
      const k = width.get(row) ?? 0;
      width.set(row, k + 1);
      positions.set(id, { x: (column + k) * lane, y: (top + row) * step });
    }
    const span = Math.max(1, ...width.values());
    const lastRow = Math.max(...order.map((id) => rows.get(id)!));
    frames.push({
      id: collection,
      name: byId.get(ids[0])!.collection_name ?? 'Another collection',
      x: column * lane - FRAME_PAD,
      y: top * step - FRAME_PAD - FRAME_HEAD,
      width: span * lane - GAP_X + FRAME_PAD * 2,
      height: (lastRow + 1) * step - GAP_Y + FRAME_PAD * 2 + FRAME_HEAD,
    });
    column += span + 1;
  }
  return { positions, spine, frames };
}

const hidden = '!h-2 !w-2 !min-h-0 !min-w-0 !border-0 !bg-transparent';
const SIDES = [
  ['left', Position.Left],
  ['right', Position.Right],
  ['top', Position.Top],
  ['bottom', Position.Bottom],
] as const;

function EntryCard({ data }: NodeProps<Node<EntryData, 'entry'>>) {
  const { node, selected } = data;
  const kind = typeOf(node);
  return (
    <div
      className={cn(
        'relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-card p-2 pt-3 shadow-lg',
        node.in_collection ? 'border-border' : node.collection_id === null ? 'border-dashed border-muted-foreground/50 bg-card/70' : 'border-cyan-400/30',
        node.title_entry && 'border-amber-400 ring-2 ring-amber-400/70',
        selected && 'ring-2 ring-info',
      )}
      style={{ width: CARD_W, height: CARD_H }}
      title={node.main_title && node.main_title !== node.title ? node.main_title : node.title}
    >
      <span className={cn('absolute inset-x-0 top-0 h-1.5', kind.stripe)} aria-hidden='true' />
      {SIDES.map(([id, position]) => (
        <Handle key={id} id={id} type='source' position={position} isConnectable={false} className={hidden} />
      ))}
      <AnimePoster url={node.poster_url} title={node.title} className={cn('w-full rounded-lg', !node.in_collection && node.collection_id === null && 'opacity-70')} />
      <div className='flex min-h-0 flex-1 flex-col gap-1'>
        <span className='line-clamp-2 text-[0.8rem] font-medium leading-tight text-foreground'>
          {node.title_entry && <Star className='mr-1 inline size-3.5 -translate-y-px fill-amber-400 text-amber-400' aria-label='Title entry' />}
          {node.title}
        </span>
        <span className='flex items-center gap-1.5'>
          <span className={cn('rounded border px-1 py-px text-[0.58rem] font-medium uppercase tracking-wide', kind.chip)}>{kind.label}</span>
          <span className='truncate font-mono text-[0.6rem] text-muted-foreground'>{meta(node)}</span>
        </span>
        {!node.in_collection && node.collection_id === null && (
          <span className='mt-auto'>
            <Badge variant='muted' className='text-[0.6rem]'>{node.alone ? 'In no collection' : 'Not in a collection'}</Badge>
          </span>
        )}
      </div>
    </div>
  );
}

function CollectionFrame({ data }: NodeProps<Node<FrameData, 'frame'>>) {
  return (
    <div className='pointer-events-none rounded-2xl border-2 border-dashed border-cyan-400/40 bg-cyan-400/[0.04]' style={{ width: data.width, height: data.height }}>
      <span className='block truncate px-4 pt-2 text-xs font-medium text-cyan-300'>Collection: {data.name}</span>
    </div>
  );
}

const nodeTypes = { entry: EntryCard, frame: CollectionFrame };

/** Which sides of two cards a link joins: top and bottom down a column, else the facing sides. */
function joins(from: Point, to: Point) {
  if (Math.abs(from.x - to.x) < CARD_W / 2) return from.y < to.y ? ['bottom', 'top'] : ['top', 'bottom'];
  return from.x < to.x ? ['right', 'left'] : ['left', 'right'];
}

/** The graph itself: laid out once per loaded graph, then free to pan, zoom and drag. */
function Canvas({ graph, selected, onToggle }: { graph: Graph; selected: number[]; onToggle: (id: number) => void }) {
  const { positions, spine, frames } = useMemo(() => layout(graph), [graph]);
  const [nodes, , onNodesChange] = useNodesState<FlowNode>([
    ...frames.map((frame) => ({
      id: 'frame-' + frame.id,
      type: 'frame' as const,
      position: { x: frame.x, y: frame.y },
      data: { name: frame.name, width: frame.width, height: frame.height },
      draggable: false,
      selectable: false,
      zIndex: -1,
    })),
    ...graph.nodes.map((node) => ({
      id: String(node.anidb_id),
      type: 'entry' as const,
      position: positions.get(node.anidb_id) ?? { x: 0, y: 0 },
      data: { node, selected: false, spine: spine.has(node.anidb_id) },
    })),
  ]);
  const shown = useMemo(
    () => nodes.map((item) => (item.type === 'entry'
      ? { ...item, data: { ...item.data, selected: selected.includes(Number(item.id)) } }
      : item)),
    [nodes, selected],
  );
  const edges = useMemo<Edge[]>(() => graph.edges.map((item) => {
    const from = positions.get(item.from) ?? { x: 0, y: 0 };
    const to = positions.get(item.to) ?? { x: 0, y: 0 };
    const [sourceHandle, targetHandle] = joins(from, to);
    const main = item.label === 'sequel' && spine.has(item.from) && spine.has(item.to);
    const sequel = item.label === 'sequel';
    const color = main ? 'var(--foreground)' : sequel ? 'var(--foreground-soft)' : 'var(--muted-foreground)';
    return {
      id: `${item.from}-${item.to}-${item.label}`,
      source: String(item.from),
      target: String(item.to),
      sourceHandle,
      targetHandle,
      type: main ? 'straight' : 'smoothstep',
      label: main ? undefined : item.label,
      markerEnd: sequel || item.label === 'side story' || item.label === 'summary'
        ? { type: MarkerType.ArrowClosed, width: 18, height: 18, color }
        : undefined,
      style: main
        ? { stroke: color, strokeWidth: 3.5 }
        : sequel
          ? { stroke: color, strokeWidth: 2 }
          : { stroke: color, strokeWidth: 1.5, strokeDasharray: '6 5' },
      labelStyle: { fill: 'var(--foreground-soft)', fontSize: 11 },
      labelBgStyle: { fill: 'var(--card)' },
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 6,
      zIndex: main ? 1 : 0,
    };
  }), [graph, positions, spine]);

  return (
    <ReactFlow
      nodes={shown}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_event, node) => { if (node.type === 'entry') onToggle(Number(node.id)); }}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      elementsSelectable={false}
      colorMode='dark'
      fitView
      fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
      minZoom={0.1}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
      style={{ background: 'transparent' }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color='var(--muted-foreground)' style={{ opacity: 0.25 }} />
      <Controls showInteractive={false} position='bottom-left' />
    </ReactFlow>
  );
}

function Legend() {
  return (
    <div className='flex flex-col gap-3 text-xs text-muted-foreground'>
      <p className='text-sm text-foreground'>Click entries to change the collection.</p>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>Type — the stripe on top</span>
        <span className='flex flex-wrap gap-1.5'>
          {[...Object.values(TYPES), OTHER_TYPE].map((kind) => (
            <span key={kind.label} className={cn('rounded border px-1.5 py-px text-[0.62rem] font-medium uppercase', kind.chip)}>{kind.label}</span>
          ))}
        </span>
      </div>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>Links</span>
        <span className='flex items-center gap-2'><span className='h-1 w-8 rounded bg-foreground' /> main line, top to bottom</span>
        <span className='flex items-center gap-2'><span className='h-0.5 w-8 rounded bg-foreground/60' /> another sequel chain</span>
        <span className='flex items-center gap-2'><span className='w-8 border-t-2 border-dashed border-muted-foreground' /> side story, recap, other</span>
      </div>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>Cards</span>
        <span className='flex items-center gap-2'><Star className='size-3.5 fill-amber-400 text-amber-400' /> names and pictures the collection</span>
        <span>Solid: in this collection. Dashed: in no collection.</span>
        <span className='text-cyan-300'>Inside a cyan frame: in another collection.</span>
      </div>
      <p>Drag to move around, scroll or pinch to zoom, drag cards to untangle. Not downloaded entries are kept where you put them and handed to Shoko once they are on disk.</p>
    </div>
  );
}

/** What to show: a collection, or one entry that is in none. */
export type GraphTarget = { group: number } | { entry: number } | null;

/**
 * A collection's relation graph: its entries, and the entries AniDB relates
 * to them. Splitting, merging and pulling entries in are changes to Shoko's
 * groups, which Jellyfin shows; for an entry not downloaded yet the choice is
 * kept and handed to Shoko once it is.
 */
export function CollectionGraph({
  target,
  onClose,
  onChanged,
}: {
  target: GraphTarget;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  // A merge moves a whole collection: the button asks once more before it does.
  const [armed, setArmed] = useState<number | null>(null);
  const groupId = target && 'group' in target ? target.group : null;
  const rootAid = target && 'entry' in target ? target.entry : null;

  async function load() {
    if (!target) return;
    setLoading(true);
    try {
      setGraph(await ('group' in target ? api.collectionGraph(target.group) : api.entryGraph(target.entry)));
      setVersion((value) => value + 1);
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
    void load();
  }, [groupId, rootAid]);

  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((node) => [node.anidb_id, node])), [graph]);
  const picked = selected.map((id) => byId.get(id)).filter((node): node is CollectionNode => Boolean(node));
  const members = picked.filter((node) => node.in_collection);
  const suggestions = picked.filter((node) => !node.in_collection);
  const memberCount = graph?.nodes.filter((node) => node.in_collection).length ?? 0;

  function toggle(id: number) {
    setArmed(null);
    setSelected((current) => (current.includes(id) ? current.filter((other) => other !== id) : [...current, id]));
  }

  async function act(label: string, work: () => Promise<unknown>, { close = false } = {}) {
    setBusy(true);
    try {
      await work();
      toast.success(label);
      setSelected([]);
      setArmed(null);
      onChanged();
      if (close) onClose();
      else await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  const root = rootAid !== null ? byId.get(rootAid) : undefined;

  function actions() {
    if (members.length > 0 && suggestions.length > 0) {
      return <p className='text-xs text-muted-foreground'>Pick entries of the collection or related ones, not both at once.</p>;
    }
    // One entry in no collection: it can only go into one.
    if (rootAid !== null) {
      const into = suggestions.length === 1 ? suggestions[0] : null;
      if (into?.collection_id) {
        return (
          <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(
            (root?.title ?? 'The entry') + ' moved into ' + into.collection_name,
            () => api.entryMove(rootAid, into.collection_id!),
            { close: true },
          )}>
            <ArrowRightToLine data-icon='inline-start' /> Move {root?.title ?? 'it'} into {into.collection_name}
          </Button>
        );
      }
      return <p className='text-xs text-muted-foreground'>Pick an entry of a related collection to move this one into it.</p>;
    }
    if (members.length > 0) {
      return (
        <div className='flex flex-col gap-2'>
          {members.length === 1 && !members[0].title_entry && (
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
          {members.length === 1 && !members[0].owned && !members[0].title_entry && (
            <p className='text-xs text-muted-foreground'>Not downloaded: named after it, the collection takes its name in Shoko and Jellyfin, and its cover here.</p>
          )}
          {members.every((node) => !node.owned) && (
            <p className='text-xs text-muted-foreground'>Split off on its own, an entry not downloaded leaves every collection, with the entries reached only through it; from its own graph it can then be moved into another.</p>
          )}
        </div>
      );
    }
    if (suggestions.length === 1) {
      const node = suggestions[0];
      return (
        <div className='flex flex-col gap-2'>
          <Button size='sm' variant='secondary' disabled={busy} onClick={() => void act(node.title + ' pulled in', () => api.collectionInclude(groupId!, node.anidb_id))}>
            <Plus data-icon='inline-start' /> Pull into this collection
          </Button>
          {node.collection_id !== null && node.collection_id !== groupId && (
            armed === node.collection_id ? (
              <Button size='sm' variant='destructive' disabled={busy} onClick={() => void act('Merged ' + node.collection_name, () => api.collectionMerge(groupId!, node.collection_id!))}>
                <Merge data-icon='inline-start' /> Yes, merge all of {node.collection_name}
              </Button>
            ) : (
              <Button size='sm' variant='secondary' disabled={busy} onClick={() => setArmed(node.collection_id)}>
                <Merge data-icon='inline-start' /> Merge all of {node.collection_name} in
              </Button>
            )
          )}
        </div>
      );
    }
    return <p className='text-xs text-muted-foreground'>Pull related entries in one at a time.</p>;
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='flex h-[92dvh] w-[min(98vw,1700px)] max-w-none flex-col gap-3'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            {rootAid !== null
              ? 'This entry is in no collection. Its related entries are around it; move it into one of their collections.'
              : 'The main line runs top to bottom; side stories, recaps and movies hang beside the entry they belong to. Related collections stand in frames of their own.'}
          </DialogDescription>
        </DialogHeader>
        <div className='flex min-h-0 flex-1 gap-3'>
          <div className='relative min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-black/30'>
            {loading && !graph ? (
              <div className='flex h-full items-center justify-center'><Spinner /></div>
            ) : !graph ? (
              <EmptyState icon={Network} title='No graph' description='Shoko could not be read for this collection.' />
            ) : (
              <Canvas key={version} graph={graph} selected={selected} onToggle={toggle} />
            )}
            {busy && (
              <div className='absolute right-3 top-3 flex items-center gap-2 rounded-md bg-card/90 px-3 py-1.5 text-xs text-muted-foreground'>
                <Spinner /> Asking Shoko…
              </div>
            )}
          </div>
          <aside className='flex w-80 shrink-0 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-card/40 p-3 text-sm'>
            {picked.length === 0 ? (
              <Legend />
            ) : (
              <>
                <ul className='flex flex-col gap-1.5'>
                  {picked.map((node) => (
                    <li key={node.anidb_id} className='flex items-start justify-between gap-2'>
                      <span className='min-w-0'>
                        <span className='block truncate text-foreground'>{node.title}</span>
                        <span className='block truncate font-mono text-[0.65rem] text-muted-foreground'>{typeOf(node).label} · {meta(node)}</span>
                        {!node.in_collection && node.collection_name && <span className='block truncate text-[0.65rem] text-cyan-300'>In {node.collection_name}</span>}
                      </span>
                      <a href={'https://anidb.net/anime/' + node.anidb_id} target='_blank' rel='noreferrer' className='text-muted-foreground hover:text-foreground' aria-label={'Open ' + node.title + ' on AniDB'}>
                        <ExternalLink className='size-3.5' />
                      </a>
                    </li>
                  ))}
                </ul>
                {actions()}
              </>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  );
}
