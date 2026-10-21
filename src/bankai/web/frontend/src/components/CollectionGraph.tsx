import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  ConnectionMode,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  useNodesState,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import {
  closestCenter,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  rectSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  ArrowRightToLine,
  ChevronDown,
  Download,
  ExternalLink,
  Info,
  Layers,
  ListOrdered,
  Maximize,
  Merge,
  MoreHorizontal,
  Network,
  PanelRightOpen,
  Plus,
  RotateCcw,
  Save,
  Scissors,
  SplitSquareHorizontal,
  Star,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type CollectionGraph as Graph, type CollectionNode } from '@/lib/api';
import { anilistUrl } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@/zon/ui/empty';
import { Popover, PopoverContent, PopoverTrigger } from '@/zon/ui/popover';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Spinner } from '@/zon/ui/spinner';

// One entry's card: a poster with its title under it.
const CARD_W = 208;
const CARD_H = 400;
const GAP_X = 80;
const GAP_Y = 64;
// Extra room between the main line and the rows above and below it, for their arrows.
const SIDE_GAP = 48;
const FRAME_PAD = 22;
const FRAME_HEAD = 32;

/** What kind of entry it is, by AniList's format: its colour throughout. */
const TYPES: Record<string, { label: string; stripe: string; chip: string }> = {
  TV: { label: 'TV series', stripe: 'bg-sky-400', chip: 'border-sky-400/40 bg-sky-400/10 text-sky-300' },
  MOVIE: { label: 'Movie', stripe: 'bg-rose-400', chip: 'border-rose-400/40 bg-rose-400/10 text-rose-300' },
  OVA: { label: 'OVA', stripe: 'bg-violet-400', chip: 'border-violet-400/40 bg-violet-400/10 text-violet-300' },
  ONA: { label: 'ONA', stripe: 'bg-emerald-400', chip: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300' },
  SPECIAL: { label: 'Special', stripe: 'bg-orange-400', chip: 'border-orange-400/40 bg-orange-400/10 text-orange-300' },
  MUSIC: { label: 'Music', stripe: 'bg-lime-400', chip: 'border-lime-400/40 bg-lime-400/10 text-lime-300' },
};
const OTHER_TYPE = { label: 'Other', stripe: 'bg-zinc-500', chip: 'border-zinc-500/40 bg-zinc-500/10 text-zinc-300' };

function typeOf(node: CollectionNode) {
  const format = (node.type ?? '').toUpperCase();
  // A TV short is a TV series as far as the collection goes.
  return TYPES[format === 'TV_SHORT' ? 'TV' : format] ?? OTHER_TYPE;
}

type EntryData = { node: CollectionNode; selected: boolean; /** Where the unsaved arrangement files it, when that differs. */ draft?: string | null };
type FrameData = { name: string; width: number; height: number; tone: string; own: boolean };
type FlowNode = Node<EntryData, 'entry'> | Node<FrameData, 'frame'>;
type Point = { x: number; y: number };

/** Air date, else year, else last: the order entries came out in. */
function aired(node: CollectionNode) {
  return node.first_aired ?? (node.year ? String(node.year) : '9999');
}

function meta(node: CollectionNode) {
  return [node.year ? String(node.year) : null, node.episodes ? `${node.episodes} eps` : null].filter(Boolean).join(' · ');
}

/** A collection's seasons as edited here: AniList ids per season, in order, and the specials. */
type Arrangement = { seasons: number[][]; specials: number[] };

function sameArrangement(a: Arrangement, b: Arrangement) {
  return JSON.stringify(a.seasons) === JSON.stringify(b.seasons) && JSON.stringify(a.specials) === JSON.stringify(b.specials);
}

/** Every edit works on a copy with the entry taken out (empty seasons kept, so indexes hold), then tidies. */
function edit(arrangement: Arrangement, change: (draft: Arrangement) => void): Arrangement {
  const draft = { seasons: arrangement.seasons.map((season) => [...season]), specials: [...arrangement.specials] };
  change(draft);
  return { seasons: draft.seasons.filter((season) => season.length > 0), specials: draft.specials };
}

function takeOut(draft: Arrangement, id: number) {
  draft.seasons = draft.seasons.map((season) => season.filter((other) => other !== id));
  draft.specials = draft.specials.filter((other) => other !== id);
}

/** The entry to the end of season ``index`` (a new last season when past the end). */
function appendTo(arrangement: Arrangement, id: number, index: number) {
  return edit(arrangement, (draft) => {
    takeOut(draft, id);
    if (index >= draft.seasons.length) draft.seasons.push([id]);
    else draft.seasons[index].push(id);
  });
}

function toSpecials(arrangement: Arrangement, id: number) {
  return edit(arrangement, (draft) => {
    takeOut(draft, id);
    draft.specials.push(id);
  });
}

/** The entry out of its season into a new one straight after it. */
function ownSeason(arrangement: Arrangement, id: number) {
  return edit(arrangement, (draft) => {
    const at = draft.seasons.findIndex((season) => season.includes(id));
    if (at < 0) return;
    draft.seasons[at] = draft.seasons[at].filter((other) => other !== id);
    draft.seasons.splice(at + 1, 0, [id]);
  });
}

/** Where each entry is filed under an arrangement: "Season 2", "Specials". */
function labels(arrangement: Arrangement) {
  const out = new Map<number, string>();
  arrangement.seasons.forEach((season, index) => season.forEach((id) => out.set(id, 'Season ' + (index + 1))));
  arrangement.specials.forEach((id) => out.set(id, 'Specials'));
  return out;
}

/** Entries in sequel order, prequel first, with the row each goes in; what the links do not order goes by air date. */
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
  // A loop in AniList's links leaves entries waiting: after the rest, by date.
  for (const id of [...ids].sort(byDate)) {
    if (!layer.has(id)) layer.set(id, Math.max(0, ...layer.values()) + 1);
    if (!done.includes(id)) done.push(id);
  }
  return { order: done, layer };
}

type Layout = { positions: Map<number, Point>; spine: Set<number>; frames: { id: number; name: string; own: boolean }[] };

/**
 * Where each entry goes.
 *
 * The collection's main line -- the sequel chain its title entry is on --
 * runs left to right, a timeline. A collection that continues it, a sequel
 * kept as a collection of its own, carries the line on to the right in a
 * frame; one that comes before it, to the left. Side stories, recaps and
 * movies stand above and below the entry they belong to: those in the
 * collection on the rows next to the line, those in none on the rows past
 * them, so the collection's frame holds its entries and nothing else. A
 * collection related in any other way stands in a frame under it all.
 */
function layout(graph: Graph): Layout {
  const byId = new Map(graph.nodes.map((node) => [node.anilist_id, node]));
  const sequels = new Map<number, number[]>();
  const linked = new Map<number, number[]>();
  for (const node of graph.nodes) {
    sequels.set(node.anilist_id, []);
    linked.set(node.anilist_id, []);
  }
  for (const edge of graph.edges) {
    linked.get(edge.from)?.push(edge.to);
    linked.get(edge.to)?.push(edge.from);
    if (edge.label === 'sequel') sequels.get(edge.from)?.push(edge.to);
  }
  const byDate = (a: number, b: number) => aired(byId.get(a)!).localeCompare(aired(byId.get(b)!)) || a - b;
  const elsewhere = (node: CollectionNode) => !node.in_collection && node.collection_id !== null && node.collection_id !== graph.collection_id;
  const own = graph.nodes.filter((node) => node.in_collection).map((node) => node.anilist_id);
  const ownSet = new Set(own);

  // The main line: the title entry's sequel chain, within the collection.
  const start = graph.title_entry && ownSet.has(graph.title_entry) ? graph.title_entry : own[0];
  const spine = new Set<number>();
  const walk = start !== undefined ? [start] : [];
  while (walk.length) {
    const id = walk.pop()!;
    if (spine.has(id) || !ownSet.has(id)) continue;
    spine.add(id);
    for (const edge of graph.edges) {
      if (edge.label !== 'sequel') continue;
      if (edge.from === id) walk.push(edge.to);
      if (edge.to === id) walk.push(edge.from);
    }
  }
  const step = CARD_H + GAP_Y;
  const lane = CARD_W + GAP_X;
  // Row 0 is the main line; rows above are negative.
  const yOf = (row: number) => row * step + Math.sign(row) * SIDE_GAP;
  const positions = new Map<number, Point>();
  const rowOf = new Map<number, number>();

  // Rows as lists of the widths taken in them, so cards never overlap.
  const taken = new Map<number, [number, number][]>();
  const free = (row: number, x: number) => (taken.get(row) ?? []).every(([left, right]) => x + CARD_W + GAP_X / 2 <= left || x >= right + GAP_X / 2);
  const place = (id: number, row: number, x: number) => {
    taken.set(row, [...(taken.get(row) ?? []), [x, x + CARD_W]]);
    rowOf.set(id, row);
    positions.set(id, { x, y: yOf(row) });
  };
  /**
   * The free spot nearest ``x`` from ``row`` outwards (``sign``): along the row, by half a
   * column, up to four columns either way before a row further out (the canvas is wide);
   * three rows full, wider rows.
   */
  const spot = (row: number, sign: number, x: number) => {
    for (let width = 8; ; width += 8) {
      for (let out = 0; out < (3 * width) / 8; out += 1) {
        for (let shift = 0; shift <= width; shift += 1) {
          for (const offset of shift ? [(shift * lane) / 2, (-shift * lane) / 2] : [0]) {
            if (free(row + sign * out, x + offset)) return { row: row + sign * out, x: x + offset };
          }
        }
      }
    }
  };

  // Entries the sequel links put level -- two sequels of one entry -- stack under each other.
  const { order: spineOrder, layer } = sequelOrder([...spine], sequels, byId);
  const stacked = new Map<number, number>();
  for (const id of spineOrder) {
    const column = layer.get(id)!;
    const k = stacked.get(column) ?? 0;
    stacked.set(column, k + 1);
    place(id, k, column * lane);
  }

  // What hangs off what: each entry's nearest laid-out neighbour, outwards from the main line.
  const parent = new Map<number, number>();
  const depth = new Map<number, number>(spineOrder.map((id) => [id, 0]));
  const queue = [...spineOrder];
  while (queue.length) {
    const id = queue.shift()!;
    for (const other of [...linked.get(id)!].sort(byDate)) {
      if (depth.has(other) || elsewhere(byId.get(other)!)) continue;
      parent.set(other, id);
      depth.set(other, depth.get(id)! + 1);
      queue.push(other);
    }
  }

  // Off the main line, below and above in turn; what hangs off a side entry goes on its side, a row further out.
  const sideOf = new Map<number, number>();
  const counts = new Map<number, { above: number; below: number }>();
  const hang = (id: number, past: Record<number, number>) => {
    let from = parent.get(id)!;
    while (!positions.has(from)) from = parent.get(from)!;
    let sign: number;
    if (spine.has(from)) {
      const count = counts.get(from) ?? { above: 0, below: 0 };
      sign = count.below <= count.above ? 1 : -1;
      if (sign > 0) count.below += 1;
      else count.above += 1;
      counts.set(from, count);
    } else {
      sign = sideOf.get(from)!;
    }
    const first = Math.max(spine.has(from) ? 1 : Math.abs(rowOf.get(from)!) + 1, past[sign] + 1);
    const at = spot(sign * first, sign, positions.get(from)!.x);
    place(id, at.row, at.x);
    sideOf.set(id, sign);
  };
  const side = [...depth.keys()].filter((id) => !spine.has(id)).sort((a, b) => depth.get(a)! - depth.get(b)! || byDate(a, b));
  // The collection's own entries first, on the rows next to the line ...
  for (const id of side) if (ownSet.has(id)) hang(id, { 1: 0, [-1]: 0 });
  // ... with any related to nothing laid out after them ...
  const loose = graph.nodes.filter((node) => !depth.has(node.anilist_id) && !elsewhere(node)).map((node) => node.anilist_id).sort(byDate);
  for (const id of loose) {
    if (!ownSet.has(id)) continue;
    const at = spot(1, 1, 0);
    place(id, at.row, at.x);
    sideOf.set(id, 1);
  }
  // ... and the rest on the rows past them, outside the collection's frame.
  const reach = (sign: number) => Math.max(0, ...[...rowOf.values()].map((row) => row * sign));
  const past = { 1: reach(1), [-1]: reach(-1) };
  for (const id of side) if (!ownSet.has(id)) hang(id, past);
  const lastRow = () => Math.max(0, ...rowOf.values());
  const looseRow = lastRow() + 1;
  loose.filter((id) => !ownSet.has(id)).forEach((id, index) => place(id, looseRow, index * lane));

  // Other collections, each laid out along its own sequels.
  const groups = new Map<number, number[]>();
  for (const node of graph.nodes) {
    if (elsewhere(node)) groups.set(node.collection_id!, [...(groups.get(node.collection_id!) ?? []), node.anilist_id]);
  }
  const frames: Layout['frames'] = own.length && graph.collection_id !== null ? [{ id: graph.collection_id, name: graph.name, own: true }] : [];
  const xs = [...positions.values()].map((point) => point.x);
  let after = Math.max(0, ...xs) + CARD_W + GAP_X + FRAME_PAD * 2;
  let before = Math.min(0, ...xs) - GAP_X - FRAME_PAD * 2;
  const under = yOf(lastRow()) + CARD_H + GAP_Y + FRAME_HEAD + FRAME_PAD * 2;
  let underX = -Infinity;
  for (const [collection, ids] of [...groups.entries()].sort((a, b) => a[0] - b[0])) {
    const inside = new Set(ids);
    const { order, layer: columns } = sequelOrder(ids, sequels, byId);
    const height = new Map<number, number>();
    const local = new Map<number, Point>();
    for (const id of order) {
      const column = columns.get(id)!;
      const k = height.get(column) ?? 0;
      height.set(column, k + 1);
      local.set(id, { x: column * lane, y: k * step });
    }
    const span = (Math.max(...order.map((id) => columns.get(id)!)) + 1) * lane - GAP_X;
    const continues = graph.edges.some((edge) => edge.label === 'sequel' && spine.has(edge.from) && inside.has(edge.to));
    const precedes = graph.edges.some((edge) => edge.label === 'sequel' && inside.has(edge.from) && spine.has(edge.to));
    let origin: Point;
    if (continues) {
      // A sequel of the main line: the line goes on to the right, in its frame.
      origin = { x: after, y: 0 };
      after += span + GAP_X + FRAME_PAD * 2;
    } else if (precedes) {
      origin = { x: before - span, y: 0 };
      before = origin.x - GAP_X - FRAME_PAD * 2;
    } else {
      const neighbours = ids.flatMap((id) => linked.get(id)!).filter((other) => positions.has(other) && !inside.has(other));
      const left = neighbours.length ? Math.min(...neighbours.map((other) => positions.get(other)!.x)) : 0;
      origin = { x: Math.max(left, underX), y: under };
      underX = origin.x + span + GAP_X + FRAME_PAD * 2;
    }
    for (const id of order) {
      const point = local.get(id)!;
      positions.set(id, { x: origin.x + point.x, y: origin.y + point.y });
    }
    frames.push({ id: collection, name: byId.get(ids[0])!.collection_name ?? 'Another collection', own: false });
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

/** What an entry is not, as its icon struck through: not downloaded, in no collection. */
function Slashed({ icon: Icon, label }: { icon: typeof Star; label: string }) {
  return (
    <span
      role='img'
      aria-label={label}
      title={label}
      className='relative inline-flex size-6 items-center justify-center rounded-md border border-border/70 bg-background/60 text-muted-foreground'
    >
      <Icon className='size-3.5' />
      <svg viewBox='0 0 24 24' className='absolute inset-0 size-full' aria-hidden='true'>
        <line x1='5' y1='5' x2='19' y2='19' stroke='currentColor' strokeWidth='1.75' strokeLinecap='round' />
      </svg>
    </span>
  );
}

function EntryCard({ data }: NodeProps<Node<EntryData, 'entry'>>) {
  const { node, selected } = data;
  const kind = typeOf(node);
  return (
    <div
      className={cn(
        'relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-card p-2.5 pt-3.5 shadow-lg',
        node.in_collection ? 'border-border' : node.collection_id === null ? 'border-dashed border-muted-foreground/50 bg-card/70' : 'border-cyan-400/30',
        // The collection's title entry: the golden card.
        node.title_entry && 'border-amber-300 bg-linear-to-b from-amber-300/30 via-amber-400/15 to-amber-500/10 shadow-amber-500/20',
        selected && 'ring-2 ring-info ring-offset-2 ring-offset-background',
      )}
      style={{ width: CARD_W, height: CARD_H }}
      title={node.main_title && node.main_title !== node.title ? node.main_title : node.title}
    >
      <span className={cn('absolute inset-x-0 top-0 h-1.5', kind.stripe)} aria-hidden='true' />
      {SIDES.map(([id, position]) => (
        <Handle key={id} id={id} type='source' position={position} isConnectable={false} className={hidden} />
      ))}
      <div className='relative'>
        <AnimePoster url={node.poster_url} title={node.title} className={cn('w-full rounded-lg', !node.in_collection && node.collection_id === null && 'opacity-70')} />
        {/* Where a member is filed on disk: Season 2, Specials. */}
        {node.in_collection && (data.draft || node.season) && (
          <span
            className={cn(
              'absolute left-1.5 top-1.5 rounded border bg-background/85 px-1.5 py-px text-[0.62rem] font-medium backdrop-blur-sm',
              data.draft ? 'border-info/70 text-info' : 'border-white/15 text-foreground',
            )}
            title={data.draft ? 'Not saved yet: now ' + (node.season ?? 'unfiled') : undefined}
          >
            {data.draft ?? node.season}
          </span>
        )}
      </div>
      <div className='flex min-h-0 flex-1 flex-col gap-1.5'>
        <span className={cn('line-clamp-2 text-[0.86rem] font-medium leading-tight', node.title_entry ? 'text-amber-100' : 'text-foreground')}>
          {node.title_entry && <Star className='mr-1 inline size-3.5 -translate-y-px fill-amber-300 text-amber-300' aria-label='Title entry' />}
          {node.title}
        </span>
        <span className='flex items-center gap-1.5'>
          <span className={cn('rounded border px-1 py-px text-[0.6rem] font-medium uppercase tracking-wide', kind.chip)}>{kind.label}</span>
          <span className='truncate font-mono text-[0.64rem] text-muted-foreground'>{meta(node)}</span>
        </span>
        <span className='mt-auto flex flex-wrap gap-1'>
          {!node.owned && <Slashed icon={Download} label='Not downloaded' />}
          {!node.in_collection && node.collection_id === null && (
            <Slashed icon={Layers} label={node.alone ? 'In no collection' : 'Not in a collection'} />
          )}
        </span>
      </div>
    </div>
  );
}

/** A collection's shade behind its cards: this one's in the accent, each other one in a colour of its own. */
const OWN_TONE = 'var(--info)';
const TONES = ['#22d3ee', '#c084fc', '#34d399', '#f472b6', '#fb923c', '#a3e635'];

function CollectionFrame({ data }: NodeProps<Node<FrameData, 'frame'>>) {
  return (
    <div
      className='pointer-events-none rounded-2xl border'
      style={{
        width: data.width,
        height: data.height,
        borderColor: `color-mix(in oklab, ${data.tone} 40%, transparent)`,
        background: `color-mix(in oklab, ${data.tone} 12%, transparent)`,
      }}
    >
      <span className='block truncate px-4 pt-2 text-xs font-medium' style={{ color: data.tone }}>
        {data.own ? 'This collection' : 'Collection'}: {data.name}
      </span>
    </div>
  );
}

const nodeTypes = { entry: EntryCard, frame: CollectionFrame };

/** Which sides of two cards a link joins: the facing sides along a row, else bottom to top between rows. */
function joins(from: Point, to: Point) {
  if (Math.abs(from.y - to.y) < CARD_H / 2) return from.x < to.x ? ['right', 'left'] : ['left', 'right'];
  return from.y < to.y ? ['bottom', 'top'] : ['top', 'bottom'];
}

/** Zoom and fit, as zon buttons rather than React Flow's own. */
function GraphControls() {
  const flow = useReactFlow();
  // The canvas settles its size after the first fit (the dialog opening, the Seasons panel folding): fit it again.
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  useEffect(() => {
    if (width && height) void flow.fitView({ padding: 0.08, maxZoom: 1 });
  }, [width, height]);
  return (
    <Panel position='bottom-left' className='!m-3 flex gap-1 rounded-button material-menu p-1'>
      <Button size='icon-sm' variant='ghost' aria-label='Zoom in' title='Zoom in' onClick={() => void flow.zoomIn({ duration: 160 })}><ZoomIn /></Button>
      <Button size='icon-sm' variant='ghost' aria-label='Zoom out' title='Zoom out' onClick={() => void flow.zoomOut({ duration: 160 })}><ZoomOut /></Button>
      <Button size='icon-sm' variant='ghost' aria-label='Fit the graph' title='Fit the graph' onClick={() => void flow.fitView({ padding: 0.08, duration: 200 })}><Maximize /></Button>
    </Panel>
  );
}

/** The graph itself: laid out once per loaded graph, then free to pan, zoom and drag. */
function Canvas({
  graph,
  selected,
  drafts,
  onPick,
  onClear,
}: {
  graph: Graph;
  selected: number[];
  /** Entries the unsaved arrangement files elsewhere, and where. */
  drafts: Map<number, string>;
  onPick: (id: number, add: boolean) => void;
  onClear: () => void;
}) {
  const { positions, spine, frames } = useMemo(() => layout(graph), [graph]);
  const [nodes, , onNodesChange] = useNodesState<Node<EntryData, 'entry'>>(
    graph.nodes.map((node) => ({
      id: String(node.anilist_id),
      type: 'entry' as const,
      position: positions.get(node.anilist_id) ?? { x: 0, y: 0 },
      data: { node, selected: false },
    })),
  );
  // Each frame drawn round wherever its cards are now, so it follows them when dragged.
  const shown = useMemo<FlowNode[]>(() => {
    const boxes = frames.flatMap((frame, index) => {
      const members = nodes.filter((item) => (frame.own ? item.data.node.in_collection : !item.data.node.in_collection && item.data.node.collection_id === frame.id));
      if (!members.length) return [];
      const left = Math.min(...members.map((item) => item.position.x)) - FRAME_PAD;
      const top = Math.min(...members.map((item) => item.position.y)) - FRAME_PAD - FRAME_HEAD;
      const right = Math.max(...members.map((item) => item.position.x + CARD_W)) + FRAME_PAD;
      const bottom = Math.max(...members.map((item) => item.position.y + CARD_H)) + FRAME_PAD;
      return [{
        id: 'frame-' + frame.id,
        type: 'frame' as const,
        position: { x: left, y: top },
        // Sized here: React Flow keeps a node hidden until it knows its size, and frames are never measured.
        width: right - left,
        height: bottom - top,
        data: {
          name: frame.name,
          width: right - left,
          height: bottom - top,
          own: frame.own,
          tone: frame.own ? OWN_TONE : TONES[(index - (frames[0]?.own ? 1 : 0)) % TONES.length],
        },
        draggable: false,
        selectable: false,
        zIndex: -1,
      }];
    });
    return [...boxes, ...nodes.map((item) => ({
      ...item,
      data: { ...item.data, selected: selected.includes(Number(item.id)), draft: drafts.get(Number(item.id)) ?? null },
    }))];
  }, [frames, nodes, selected, drafts]);
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
      // Straight when the cards line up, along a row or down a column; round the corner otherwise.
      type: Math.abs(from.y - to.y) < 1 || Math.abs(from.x - to.x) < 1 ? 'straight' : 'smoothstep',
      label: main ? undefined : item.label,
      markerEnd: sequel || item.label === 'side story' || item.label === 'summary'
        ? { type: MarkerType.ArrowClosed, width: 18, height: 18, color }
        : undefined,
      style: main
        ? { stroke: color, strokeWidth: 3.5 }
        : sequel
          ? { stroke: color, strokeWidth: 2.5 }
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
      onNodesChange={(changes) => onNodesChange(
        // Frames are drawn round their cards, not moved on their own.
        changes.filter((change) => !('id' in change) || !String(change.id).startsWith('frame-')) as NodeChange<Node<EntryData, 'entry'>>[],
      )}
      onNodeClick={(event, node) => { if (node.type === 'entry') onPick(Number(node.id), event.shiftKey); }}
      onPaneClick={onClear}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      elementsSelectable={false}
      selectionKeyCode={null}
      multiSelectionKeyCode={null}
      colorMode='dark'
      fitView
      fitViewOptions={{ padding: 0.08, maxZoom: 1 }}
      minZoom={0.1}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
      <GraphControls />
    </ReactFlow>
  );
}

/** An action in the floating panel: the button wraps a long collection name rather than spill out of itself. */
function Action({ icon: Icon, children, ...props }: React.ComponentProps<typeof Button> & { icon: typeof Star }) {
  return (
    <Button size='sm' variant='secondary' className='justify-start whitespace-normal py-1.5 text-left leading-snug' {...props}>
      <Icon data-icon='inline-start' className='shrink-0' />
      <span className='min-w-0 break-words'>{children}</span>
    </Button>
  );
}

/** "Append to season…": an entry to the end of a season, a new season, or the specials. */
function SeasonPicker({
  arrangement,
  id,
  disabled,
  onChange,
  label = 'Append to season…',
}: {
  arrangement: Arrangement;
  id: number;
  disabled?: boolean;
  onChange: (next: Arrangement) => void;
  label?: string;
}) {
  const special = arrangement.specials.includes(id);
  return (
    <Select<string>
      value={null}
      disabled={disabled}
      onValueChange={(value) => {
        if (value === null) return;
        if (value === 'specials') onChange(toSpecials(arrangement, id));
        else if (value === 'new') onChange(appendTo(arrangement, id, arrangement.seasons.length));
        else onChange(appendTo(arrangement, id, Number(value)));
      }}
    >
      <SelectTrigger size='sm' className='w-full min-w-0' aria-label={label}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {arrangement.seasons.map((season, index) => (
            <SelectItem key={index} value={String(index)}>
              Append to Season {index + 1}{season.includes(id) && season[season.length - 1] === id ? ' (already last)' : ''}
            </SelectItem>
          ))}
          <SelectItem value='new'>Make it a new last season</SelectItem>
          {!special && <SelectItem value='specials'>To specials</SelectItem>}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

/** What the Seasons panel shows while it is edited: each season with a key that holds still while it moves. */
type Board = { seasons: { key: string; ids: number[] }[]; specials: number[] };

const SPECIALS = 'specials';
const NEW_SEASON = 'new-season';

function toBoard(arrangement: Arrangement): Board {
  return {
    seasons: arrangement.seasons.map((ids) => ({ key: 'season-' + ids.join('-'), ids: [...ids] })),
    specials: [...arrangement.specials],
  };
}

function fromBoard(board: Board): Arrangement {
  return {
    seasons: board.seasons.map((season) => season.ids).filter((ids) => ids.length > 0),
    specials: board.specials,
  };
}

/** The container an entry is in: a season's key, or the specials. */
function containerOf(board: Board, id: number): string | null {
  if (board.specials.includes(id)) return SPECIALS;
  return board.seasons.find((season) => season.ids.includes(id))?.key ?? null;
}

function idsOf(board: Board, container: string): number[] {
  return container === SPECIALS ? board.specials : board.seasons.find((season) => season.key === container)?.ids ?? [];
}

/** A board with ``ids`` as the contents of ``container``. */
function withIds(board: Board, container: string, ids: number[]): Board {
  if (container === SPECIALS) return { ...board, specials: ids };
  return { ...board, seasons: board.seasons.map((season) => (season.key === container ? { ...season, ids } : season)) };
}

type DragKind = { type: 'entry'; id: number } | { type: 'season'; key: string };

const entryId = (id: number) => 'entry-' + id;
const mergeId = (key: string) => 'merge-' + key;
/** A season's entry area: not the season's own id, which its sortable box has. */
const areaId = (key: string) => 'area-' + key;

/** An entry's face: type colour, title, episodes. Shared by the box in its place and the one floating under the cursor. */
function ChipFace({ id, node, floating, menu }: { id: number; node: CollectionNode | undefined; floating?: boolean; menu?: React.ReactNode }) {
  const kind = node ? typeOf(node) : OTHER_TYPE;
  const title = node?.title ?? 'AniList ' + id;
  return (
    <span
      title={title + ' · ' + kind.label}
      className={cn(
        'inline-flex max-w-full select-none items-center gap-1 rounded-md border py-0.5 pl-1.5 pr-0.5 text-xs',
        kind.chip,
        node && !node.owned && !floating && 'opacity-70',
        floating ? 'cursor-grabbing bg-control-menu shadow-overlay ring-1 ring-info/60' : 'cursor-grab',
      )}
    >
      <span className='min-w-0 truncate text-foreground'>{title}</span>
      <span className='shrink-0 font-mono text-[0.62rem] opacity-80'>{node?.episodes ? node.episodes + ' eps' : '? eps'}</span>
      {menu ?? <span className='size-5 shrink-0' />}
    </span>
  );
}

/** An entry in its place: sortable within and across seasons and the specials. */
function SortableChip({ id, node, disabled, children }: { id: number; node: CollectionNode | undefined; disabled: boolean; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: entryId(id),
    data: { type: 'entry', id },
    disabled,
  });
  const title = node?.title ?? 'AniList ' + id;
  return (
    <span
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn('inline-flex max-w-full touch-none', isDragging && 'opacity-30')}
      {...attributes}
      {...listeners}
      // The season box around it is draggable too: this press is the entry's.
      onPointerDown={(event) => {
        event.stopPropagation();
        listeners?.onPointerDown?.(event);
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        listeners?.onKeyDown?.(event);
      }}
    >
      <ChipFace
        id={id}
        node={node}
        menu={
          <Popover>
            <PopoverTrigger
              render={
                <Button
                  size='icon-sm'
                  variant='ghost'
                  className='size-5 shrink-0'
                  aria-label={'More for ' + title}
                  onPointerDown={(event) => event.stopPropagation()}
                />
              }
            >
              <MoreHorizontal />
            </PopoverTrigger>
            <PopoverContent align='start' className='w-64 gap-2 p-3'>
              <p className='break-words text-xs font-medium text-foreground'>{title}</p>
              {children}
            </PopoverContent>
          </Popover>
        }
      />
    </span>
  );
}

/** Where entries can be dropped when the container itself is empty or the pointer is past its last entry. */
function EntryArea({ id, children, className }: { id: string; children: React.ReactNode; className?: string }) {
  const { setNodeRef, isOver } = useDroppable({ id, data: { type: 'container', container: id } });
  return (
    <div ref={setNodeRef} className={cn('flex min-h-7 min-w-0 flex-1 flex-wrap items-center gap-1 rounded-md p-0.5', isOver && 'bg-info/10', className)}>
      {children}
    </div>
  );
}

/** A drop zone on a season, shown while another season is dragged: dropped here, the two are merged. */
function MergeZone({ seasonKey, number }: { seasonKey: string; number: number }) {
  const { setNodeRef, isOver } = useDroppable({ id: mergeId(seasonKey), data: { type: 'merge', key: seasonKey } });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        // Over the box's right end rather than beside it: appearing, it must not move anything.
        'absolute inset-y-1 right-1 flex items-center justify-center rounded-md border border-dashed px-3 text-[0.7rem] backdrop-blur-sm transition-colors',
        isOver ? 'border-info bg-info/20 text-info' : 'border-border bg-background/80 text-muted-foreground',
      )}
    >
      <Merge className='mr-1 size-3.5' /> Merge into Season {number}
    </div>
  );
}

/** A season's box: sortable among the seasons as a whole, holding its entries. */
function SeasonBox({
  season,
  number,
  disabled,
  seasonDragged,
  children,
}: {
  season: { key: string; ids: number[] };
  number: number;
  disabled: boolean;
  /** Which season is being dragged, if any: every other one then offers to merge. */
  seasonDragged: string | null;
  children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: season.key,
    data: { type: 'season', key: season.key },
    disabled,
  });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        'relative flex touch-none flex-col gap-1 rounded-md border border-border/60 bg-background/60 px-1.5 py-1 sm:flex-row sm:items-center',
        disabled ? 'cursor-default' : 'cursor-grab active:cursor-grabbing',
        isDragging && 'opacity-30',
      )}
      {...attributes}
      {...listeners}
    >
      <span className='shrink-0 select-none text-xs font-medium text-foreground sm:w-16'>Season {number}</span>
      {children}
      {seasonDragged && seasonDragged !== season.key && <MergeZone seasonKey={season.key} number={number} />}
    </div>
  );
}

/** A dragged season floating under the cursor. */
function SeasonFace({ number, ids, byId }: { number: number; ids: number[]; byId: Map<number, CollectionNode> }) {
  return (
    <div className='flex cursor-grabbing flex-wrap items-center gap-1 rounded-button border border-info/60 bg-control-menu px-1.5 py-1 shadow-overlay'>
      <span className='text-xs font-medium text-foreground sm:w-16'>Season {number}</span>
      {ids.map((id) => (
        <ChipFace key={id} id={id} node={byId.get(id)} />
      ))}
    </div>
  );
}

/** The "new season" target after the last season: always there, so a drag starting never moves the list. */
function NewSeasonZone({ number, active }: { number: number; active: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: NEW_SEASON, data: { type: 'container', container: NEW_SEASON } });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'flex min-h-7 items-center justify-center rounded-md border border-dashed text-xs transition-colors',
        isOver ? 'border-info bg-info/10 text-info' : active ? 'border-border text-muted-foreground' : 'border-border/40 text-muted-foreground/50',
      )}
    >
      <Plus className='mr-1 size-3.5' /> New season {number}
    </div>
  );
}

/**
 * The collection's seasons, edited here before they are saved: which
 * entries make up each season and in what order (an entry after the first
 * continues the season's numbering), and which are specials. Everything is
 * dragged: an entry to any place in a season, onto the specials or a new
 * season; a season to another place in the order, or onto another season's
 * merge zone. The dragged box floats under the cursor, the others make room.
 */
function SeasonsPanel({
  arrangement,
  byId,
  open,
  onToggle,
  dirty,
  custom,
  busy,
  onChange,
  onDiscard,
  onSave,
  onReset,
  onDragging,
}: {
  arrangement: Arrangement;
  byId: Map<number, CollectionNode>;
  open: boolean;
  onToggle: () => void;
  dirty: boolean;
  custom: boolean;
  busy: boolean;
  onChange: (next: Arrangement) => void;
  onDiscard: () => void;
  onSave: () => void;
  onReset: () => void;
  onDragging?: (dragging: boolean) => void;
}) {
  // While something is dragged the board moves on its own; it becomes the arrangement on drop.
  const [live, setLive] = useState<Board | null>(null);
  const [active, setActive] = useState<DragKind | null>(null);
  const board = live ?? toBoard(arrangement);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  // An entry is placed by what is under the pointer; a season by the nearest place in the list or a merge zone.
  const collide: CollisionDetection = (args) => {
    const kind = args.active.data.current?.type;
    const pool = args.droppableContainers.filter((container) => {
      const type = container.data.current?.type;
      return kind === 'season' ? type === 'season' || type === 'merge' : type === 'entry' || type === 'container';
    });
    const scoped = { ...args, droppableContainers: pool };
    if (kind === 'season') {
      const merge = pointerWithin(scoped).filter((hit) => String(hit.id).startsWith('merge-'));
      return merge.length ? merge : closestCenter({ ...scoped, droppableContainers: pool.filter((c) => c.data.current?.type === 'season') });
    }
    const within = pointerWithin(scoped);
    if (within.length) {
      // On an entry rather than the area around it, when both are under the pointer.
      const entry = within.find((hit) => String(hit.id).startsWith('entry-'));
      return entry ? [entry] : within;
    }
    return closestCenter(scoped);
  };

  const containerFromOver = (overId: string, current: Board): string | null => {
    if (overId === NEW_SEASON) return NEW_SEASON;
    if (overId === SPECIALS) return SPECIALS;
    if (overId.startsWith('entry-')) return containerOf(current, Number(overId.slice('entry-'.length)));
    if (overId.startsWith('area-')) {
      const key = overId.slice('area-'.length);
      return current.seasons.some((season) => season.key === key) ? key : null;
    }
    return null;
  };

  const onDragStart = (event: DragStartEvent) => {
    const data = event.active.data.current;
    setLive(toBoard(arrangement));
    setActive(data?.type === 'season' ? { type: 'season', key: data.key } : { type: 'entry', id: data?.id });
    onDragging?.(true);
  };

  // An entry moving into another container joins it at once, so the boxes there make room for it.
  const onDragOver = (event: DragOverEvent) => {
    if (!live || active?.type !== 'entry' || !event.over) return;
    const from = containerOf(live, active.id);
    const to = containerFromOver(String(event.over.id), live);
    if (!from || !to || to === NEW_SEASON || from === to) return;
    const target = idsOf(live, to).filter((id) => id !== active.id);
    const overId = String(event.over.id);
    const at = overId.startsWith('entry-') ? target.indexOf(Number(overId.slice('entry-'.length))) : -1;
    if (at < 0) target.push(active.id);
    else target.splice(at, 0, active.id);
    setLive(withIds(withIds(live, from, idsOf(live, from).filter((id) => id !== active.id)), to, target));
  };

  const finish = (next: Board | null) => {
    if (next) onChange(fromBoard(next));
    setLive(null);
    setActive(null);
    // After the key event that ended it has reached the dialog.
    setTimeout(() => onDragging?.(false), 0);
  };

  const onDragEnd = (event: DragEndEvent) => {
    const current = live;
    const over = event.over ? String(event.over.id) : null;
    if (!current || !active || !over) return finish(null);
    if (active.type === 'season') {
      if (over.startsWith('merge-')) {
        const into = over.slice('merge-'.length);
        const moving = current.seasons.find((season) => season.key === active.key);
        if (!moving) return finish(null);
        return finish({
          ...current,
          seasons: current.seasons
            .map((season) => (season.key === into ? { ...season, ids: [...season.ids, ...moving.ids] } : season))
            .filter((season) => season.key !== active.key),
        });
      }
      const from = current.seasons.findIndex((season) => season.key === active.key);
      const to = current.seasons.findIndex((season) => season.key === over);
      return finish(from < 0 || to < 0 ? null : { ...current, seasons: arrayMove(current.seasons, from, to) });
    }
    const container = containerOf(current, active.id);
    if (!container) return finish(null);
    if (over === NEW_SEASON) {
      const without = withIds(current, container, idsOf(current, container).filter((id) => id !== active.id));
      return finish({ ...without, seasons: [...without.seasons, { key: 'season-new-' + active.id, ids: [active.id] }] });
    }
    if (over.startsWith('entry-')) {
      const ids = idsOf(current, container);
      const from = ids.indexOf(active.id);
      const to = ids.indexOf(Number(over.slice('entry-'.length)));
      if (from >= 0 && to >= 0 && from !== to) return finish(withIds(current, container, arrayMove(ids, from, to)));
    }
    return finish(current);
  };

  const seasonNumber = (key: string) => board.seasons.findIndex((season) => season.key === key) + 1;
  const dragged = active?.type === 'season' ? board.seasons.find((season) => season.key === active.key) : null;
  const menu = (id: number, inSeason: { ids: number[] } | null) =>
    inSeason ? (
      <>
        <Button size='sm' variant='secondary' className='justify-start' disabled={busy || inSeason.ids.length < 2} onClick={() => onChange(ownSeason(arrangement, id))}>
          <SplitSquareHorizontal data-icon='inline-start' /> Own season
        </Button>
        <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => onChange(toSpecials(arrangement, id))}>
          <Star data-icon='inline-start' /> To specials
        </Button>
        <SeasonPicker arrangement={arrangement} id={id} disabled={busy} onChange={onChange} />
      </>
    ) : (
      <>
        <SeasonPicker arrangement={arrangement} id={id} disabled={busy} onChange={onChange} />
        <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => onChange(appendTo(arrangement, id, arrangement.seasons.length))}>
          <Plus data-icon='inline-start' /> Make season
        </Button>
      </>
    );

  return (
    <section className='flex max-h-[40%] shrink-0 flex-col rounded-control surface-container'>
      <header className='flex flex-wrap items-center gap-x-3 gap-y-1 px-2.5 py-1.5'>
        <button
          type='button'
          onClick={onToggle}
          aria-expanded={open}
          className='flex cursor-pointer items-center gap-1.5 rounded-item text-sm font-medium text-foreground focus-ring'
        >
          <ChevronDown className={cn('size-4 transition-transform', !open && '-rotate-90')} />
          <ListOrdered className='size-4' /> Seasons
        </button>
        <span className='text-xs text-muted-foreground'>
          {custom ? 'Arranged by hand' : 'Automatic: the sequel chain by air date'}
          {dirty && <span className='text-info'> · unsaved changes</span>}
        </span>
        {/* The how-to, on hover rather than taking a line of the panel. */}
        <span
          className='inline-flex text-muted-foreground'
          aria-label='How to arrange'
          title={'Drag an entry to any place in a season, onto Specials or onto "New season". Drag a season to another place in the order, or onto another season\'s "Merge" zone. Saving renames the files on disk into their new seasons and episode numbers; the moves are journaled and can be undone.'}
        >
          <Info className='size-3.5' />
        </span>
        <div className='ml-auto flex flex-wrap gap-2'>
          {dirty && <Button size='sm' variant='ghost' disabled={busy} onClick={onDiscard}>Discard</Button>}
          <Button
            size='sm'
            variant='secondary'
            disabled={busy || !custom}
            onClick={onReset}
            title={custom ? 'Forget the hand arrangement: seasons follow the sequel chain by air date again' : 'Already arranged automatically'}
          >
            <RotateCcw data-icon='inline-start' /> Reset to automatic
          </Button>
          <Button size='sm' disabled={busy || !dirty} onClick={onSave}>
            <Save data-icon='inline-start' /> Save arrangement
          </Button>
        </div>
      </header>
      {open && (
        <div className='flex min-h-0 flex-col gap-1 overflow-y-auto border-t border-border px-2 py-1.5'>
          <DndContext
            sensors={sensors}
            collisionDetection={collide}
            // Merge zones and the boxes making room appear and move during a drag: measure them as they are.
            measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDragEnd={onDragEnd}
            onDragCancel={() => finish(null)}
          >
            <SortableContext items={board.seasons.map((season) => season.key)} strategy={verticalListSortingStrategy}>
              <div className='flex flex-col gap-1'>
                {board.seasons.map((season, index) => (
                  <SeasonBox key={season.key} season={season} number={index + 1} disabled={busy} seasonDragged={active?.type === 'season' ? active.key : null}>
                    <SortableContext items={season.ids.map(entryId)} strategy={rectSortingStrategy}>
                      <EntryArea id={areaId(season.key)}>
                        {season.ids.map((id) => (
                          <SortableChip key={id} id={id} node={byId.get(id)} disabled={busy}>
                            {menu(id, season)}
                          </SortableChip>
                        ))}
                      </EntryArea>
                    </SortableContext>
                  </SeasonBox>
                ))}
              </div>
            </SortableContext>
            <NewSeasonZone number={board.seasons.filter((season) => season.ids.length).length + 1} active={active?.type === 'entry'} />
            <div className='flex flex-col gap-1 rounded-md border border-border/40 px-1.5 py-1 sm:flex-row sm:items-center'>
              <span className='shrink-0 text-xs font-medium text-foreground sm:w-16'>Specials</span>
              <SortableContext items={board.specials.map(entryId)} strategy={rectSortingStrategy}>
                <EntryArea id={SPECIALS}>
                  {board.specials.length === 0 && <span className='text-xs text-muted-foreground'>None — drop entries here</span>}
                  {board.specials.map((id) => (
                    <SortableChip key={id} id={id} node={byId.get(id)} disabled={busy}>
                      {menu(id, null)}
                    </SortableChip>
                  ))}
                </EntryArea>
              </SortableContext>
            </div>
            <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.2, 0, 0, 1)' }}>
              {active?.type === 'entry' ? (
                <ChipFace id={active.id} node={byId.get(active.id)} floating />
              ) : dragged ? (
                <SeasonFace number={seasonNumber(dragged.key)} ids={dragged.ids} byId={byId} />
              ) : null}
            </DragOverlay>
          </DndContext>
        </div>
      )}
    </section>
  );
}

/** What to show: a collection, or one entry that is in none. */
export type GraphTarget = { collection: number } | { entry: number } | null;

/**
 * A collection's relation graph: its entries, and the entries AniList relates
 * to them. Splitting, merging and pulling entries in change bankai's own
 * collections, which the library lays out on disk and Jellyfin shows; for an
 * entry not downloaded yet the choice is kept until it is.
 */
export function CollectionGraph({
  target,
  onClose,
  onChanged,
  onOpenCollection,
}: {
  target: GraphTarget;
  onClose: () => void;
  onChanged: () => void;
  /** Open another collection's drawer in the library. */
  onOpenCollection?: (collectionId: number) => void;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  // A merge moves a whole collection: the button asks once more before it does.
  const [armed, setArmed] = useState<number | null>(null);
  const collectionId = target && 'collection' in target ? target.collection : null;
  const rootAid = target && 'entry' in target ? target.entry : null;
  // The season arrangement as edited here; saved only with "Save arrangement".
  const [draft, setDraft] = useState<Arrangement | null>(null);
  const [seasonsOpen, setSeasonsOpen] = useState(true);
  // While a box is dragged in the Seasons panel, Escape cancels the drag, not the dialog.
  const [seasonDrag, setSeasonDrag] = useState(false);
  const saved = useMemo<Arrangement | null>(
    () => (graph?.arrangement ? { seasons: graph.arrangement.seasons, specials: graph.arrangement.specials } : null),
    [graph],
  );
  useEffect(() => setDraft(saved), [saved]);
  const dirty = Boolean(draft && saved && !sameArrangement(draft, saved));
  // On the cards: where the unsaved arrangement files an entry, where that differs from the saved one.
  const drafts = useMemo(() => {
    const out = new Map<number, string>();
    if (!draft || !saved || !dirty) return out;
    const before = labels(saved);
    for (const [id, label] of labels(draft)) if (before.get(id) !== label) out.set(id, label);
    return out;
  }, [draft, saved, dirty]);

  async function load() {
    if (!target) return;
    setLoading(true);
    try {
      setGraph(await ('collection' in target ? api.collectionGraph(target.collection) : api.entryGraph(target.entry)));
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
  }, [collectionId, rootAid]);

  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((node) => [node.anilist_id, node])), [graph]);
  const picked = selected.map((id) => byId.get(id)).filter((node): node is CollectionNode => Boolean(node));
  const members = picked.filter((node) => node.in_collection);
  const suggestions = picked.filter((node) => !node.in_collection);
  const memberCount = graph?.nodes.filter((node) => node.in_collection).length ?? 0;

  /** A click picks one entry; with Shift it adds to (or takes from) what is picked. */
  function pick(id: number, add: boolean) {
    setArmed(null);
    setSelected((current) => {
      if (add) return current.includes(id) ? current.filter((other) => other !== id) : [...current, id];
      return current.length === 1 && current[0] === id ? [] : [id];
    });
  }

  function clear() {
    setArmed(null);
    setSelected([]);
  }

  async function act(label: string, work: () => Promise<unknown>, { close = false } = {}) {
    setBusy(true);
    try {
      await work();
      toast.success(label);
      clear();
      onChanged();
      if (close) onClose();
      else await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  /** The arrangement on disk: the edited one, or back to the automatic one. */
  async function saveArrangement(reset: boolean) {
    if (collectionId === null || (!reset && !draft)) return;
    setBusy(true);
    try {
      const result = await api.arrangeCollection(collectionId, reset ? { reset: true } : { seasons: draft!.seasons, specials: draft!.specials });
      toast.success(
        (reset ? 'Seasons arranged automatically again' : 'Arrangement saved')
        + ' — ' + result.moved + ' file' + (result.moved === 1 ? '' : 's') + ' moved',
      );
      onChanged();
      await load();
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
          <Action icon={ArrowRightToLine} disabled={busy} onClick={() => void act(
            (root?.title ?? 'The entry') + ' moved into ' + into.collection_name,
            () => api.entryMove(rootAid, into.collection_id!),
            { close: true },
          )}>
            Move it into {into.collection_name}
          </Action>
        );
      }
      return <p className='text-xs text-muted-foreground'>Pick an entry of a related collection to move this one into it.</p>;
    }
    if (members.length > 0) {
      return (
        <div className='flex flex-col gap-2'>
          {members.length === 1 && !members[0].title_entry && (
            <Action icon={Star} disabled={busy} onClick={() => void act('Collection now named after ' + members[0].title, () => api.collectionTitle(collectionId!, members[0].anilist_id))}>
              Name the collection after it
            </Action>
          )}
          {members.length < memberCount && (
            <Action icon={Scissors} disabled={busy} onClick={() => void act(
              members.length === 1 ? members[0].title + ' split off' : members.length + ' entries split off into a new collection',
              () => (members.length === 1 ? api.collectionExclude(collectionId!, members[0].anilist_id) : api.collectionSplit(collectionId!, members.map((node) => node.anilist_id))),
            )}>
              {members.length === 1 ? 'Split off into its own collection' : `Split these ${members.length} off into a new collection`}
            </Action>
          )}
          {members.length === 1 && draft && (
            <SeasonPicker
              arrangement={draft}
              id={members[0].anilist_id}
              disabled={busy}
              onChange={setDraft}
            />
          )}
          {members.length === 1 && !members[0].owned && !members[0].title_entry && (
            <p className='text-xs text-muted-foreground'>Not downloaded: named after it, the collection takes its name (unless renamed by hand) and its cover, on disk and in Jellyfin.</p>
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
          <Action icon={Plus} disabled={busy} onClick={() => void act(node.title + ' pulled in', () => api.collectionInclude(collectionId!, node.anilist_id))}>
            Pull this entry into the collection
          </Action>
          {node.collection_id !== null && node.collection_id !== collectionId && (
            armed === node.collection_id ? (
              <Action icon={Merge} variant='destructive' disabled={busy} onClick={() => void act('Merged ' + node.collection_name, () => api.collectionMerge(collectionId!, node.collection_id!))}>
                Yes, merge all of {node.collection_name}
              </Action>
            ) : (
              <Action icon={Merge} disabled={busy} onClick={() => setArmed(node.collection_id)}>
                Merge its whole collection in: {node.collection_name}
              </Action>
            )
          )}
        </div>
      );
    }
    return <p className='text-xs text-muted-foreground'>Pull related entries in one at a time.</p>;
  }

  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open, details) => {
        if (open) return;
        if (seasonDrag && details.reason === 'escape-key') {
          details.cancel();
          return;
        }
        onClose();
      }}
    >
      <DialogContent className='h-[98dvh] max-h-none w-[98vw] max-w-none shrink-0 gap-3 p-4 sm:p-5'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            {rootAid !== null
              ? 'This entry is in no collection. Its related entries are around it; move it into one of their collections.'
              : 'The main line runs left to right, sequel collections continuing it; side stories, recaps and movies stand above and below the entry they belong to. Each collection is shaded.'}
          </DialogDescription>
        </DialogHeader>
        <div className='flex min-h-0 flex-1 flex-col gap-3'>
          <div data-slot='graph' className='relative min-h-0 flex-1 overflow-hidden rounded-control border border-field-edge bg-black/30'>
            {loading && !graph ? (
              <div className='flex h-full items-center justify-center'><Spinner label='Loading the graph' /></div>
            ) : !graph ? (
              <Empty>
                <EmptyMedia><Network /></EmptyMedia>
                <EmptyTitle>No graph</EmptyTitle>
                <EmptyDescription>The relations of this collection could not be read.</EmptyDescription>
              </Empty>
            ) : (
              <Canvas key={version} graph={graph} selected={selected} drafts={drafts} onPick={pick} onClear={clear} />
            )}
            {graph && picked.length === 0 && (
              <p className='pointer-events-none absolute left-3 top-3 rounded-button material-menu px-2.5 py-1 text-xs text-muted-foreground'>
                Click an entry to change the collection · Shift+click to pick more
              </p>
            )}
            {/* What can be done with the picked entries, over the graph rather than beside it. */}
            {picked.length > 0 && (
              <div className='absolute right-3 top-3 z-10 flex max-h-[calc(100%-1.5rem)] w-72 flex-col gap-3 overflow-y-auto rounded-control material-menu p-3 text-sm shadow-overlay'>
                <ul className='flex flex-col gap-1.5'>
                  {picked.map((node) => (
                    <li key={node.anilist_id} className='flex items-start justify-between gap-2'>
                      <span className='min-w-0'>
                        <span className='block break-words text-foreground'>{node.title}</span>
                        <span className='block truncate font-mono text-[0.65rem] text-muted-foreground'>{[typeOf(node).label, node.in_collection ? node.season : null, meta(node), node.owned ? null : 'not downloaded'].filter(Boolean).join(' · ')}</span>
                        {!node.in_collection && node.collection_name && <span className='block break-words text-[0.65rem] text-cyan-300'>In {node.collection_name}</span>}
                      </span>
                      <a href={anilistUrl(node.anilist_id)} target='_blank' rel='noreferrer' className='shrink-0 text-muted-foreground hover:text-foreground' title='Open on AniList' aria-label={'Open ' + node.title + ' on AniList'}>
                        <ExternalLink className='size-3.5' />
                      </a>
                    </li>
                  ))}
                </ul>
                {actions()}
                {onOpenCollection && picked.length === 1 && !picked[0].in_collection && picked[0].collection_id !== null && picked[0].collection_id !== collectionId && (
                  <Action icon={PanelRightOpen} variant='ghost' onClick={() => onOpenCollection(picked[0].collection_id!)}>
                    Open {picked[0].collection_name ?? 'its collection'}
                  </Action>
                )}
              </div>
            )}
            {busy && (
              <div className='absolute bottom-3 right-3 flex items-center gap-2 rounded-button material-menu px-3 py-1.5 text-xs text-muted-foreground'>
                <Spinner label='Saving' /> Saving…
              </div>
            )}
          </div>
          {collectionId !== null && draft && (
            <SeasonsPanel
              arrangement={draft}
              byId={byId}
              open={seasonsOpen}
              onToggle={() => setSeasonsOpen((value) => !value)}
              dirty={dirty}
              custom={Boolean(graph?.arrangement?.custom)}
              busy={busy}
              onChange={setDraft}
              onDiscard={() => setDraft(saved)}
              onSave={() => void saveArrangement(false)}
              onReset={() => void saveArrangement(true)}
              onDragging={setSeasonDrag}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
