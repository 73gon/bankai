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
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  ArrowRightToLine,
  ChevronDown,
  ExternalLink,
  ListOrdered,
  Merge,
  MoreHorizontal,
  Network,
  Plus,
  RotateCcw,
  Save,
  Scissors,
  SplitSquareHorizontal,
  Star,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, type CollectionGraph as Graph, type CollectionNode } from '@/lib/api';
import { anilistUrl } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { EmptyState, Spinner } from '@/components/ui/empty';
import { cn } from '@/lib/utils';

// One entry's card: a poster with its title under it.
const CARD_W = 208;
const CARD_H = 400;
const GAP_X = 96;
const GAP_Y = 72;
// Extra room between the main line and the side entries, for their arrows.
const SIDE_GAP = 120;
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
type FrameData = { name: string; width: number; height: number };
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

/** Where an entry is dropped: into a season (before ``before``, else at its end), a new last season, or the specials. */
type DropTarget =
  | { where: 'season'; index: number; before?: number }
  | { where: 'new' }
  | { where: 'specials'; before?: number };

function moveEntry(arrangement: Arrangement, id: number, target: DropTarget) {
  return edit(arrangement, (draft) => {
    if ('before' in target && target.before === id) return;
    takeOut(draft, id);
    const insert = (list: number[]) => {
      const at = target.where !== 'new' && target.before !== undefined ? list.indexOf(target.before) : -1;
      if (at < 0) list.push(id);
      else list.splice(at, 0, id);
    };
    if (target.where === 'new') draft.seasons.push([id]);
    else if (target.where === 'specials') insert(draft.specials);
    else if (target.index < draft.seasons.length) insert(draft.seasons[target.index]);
    else draft.seasons.push([id]);
  });
}

/** A whole season into the gap before season ``gap`` (``gap`` = the number of seasons: last). */
function moveSeason(arrangement: Arrangement, from: number, gap: number) {
  return edit(arrangement, (draft) => {
    if (gap === from || gap === from + 1) return;
    const [season] = draft.seasons.splice(from, 1);
    draft.seasons.splice(gap > from ? gap - 1 : gap, 0, season);
  });
}

/** A whole season's entries onto the end of another season. */
function mergeSeason(arrangement: Arrangement, from: number, into: number) {
  return edit(arrangement, (draft) => {
    if (from === into) return;
    draft.seasons[into].push(...draft.seasons[from]);
    draft.seasons[from] = [];
  });
}

/** What is being dragged in the Seasons panel. */
type Dragging = { kind: 'entry'; id: number } | { kind: 'season'; index: number } | null;

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

type Layout = { positions: Map<number, Point>; spine: Set<number>; frames: { id: number; name: string }[] };

/**
 * Where each entry goes.
 *
 * The collection's main line -- the sequel chain its title entry is on --
 * runs top to bottom. A collection that continues it, a sequel kept as a
 * collection of its own, continues the line below it in a frame; one that
 * comes before it, above. Side stories, recaps and movies sit beside the
 * entry they belong to, one column each side, staggered by half a row so
 * their arrows do not run into each other; what hangs off them goes a column
 * further out. A collection related in any other way stands in a frame to
 * the right.
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
  const positions = new Map<number, Point>();
  const column = new Map<number, number>();
  const { order: spineOrder, layer } = sequelOrder([...spine], sequels, byId);
  const across = new Map<number, number>();
  for (const id of spineOrder) {
    const row = layer.get(id)!;
    const k = across.get(row) ?? 0;
    across.set(row, k + 1);
    column.set(id, k);
    positions.set(id, { x: k * lane, y: row * step });
  }
  const spineWidth = Math.max(1, ...across.values());
  const xOf = (col: number) => col * lane + (col >= spineWidth ? SIDE_GAP : col < 0 ? -SIDE_GAP : 0);

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

  // Columns as lists of the heights taken in them, so cards never overlap.
  const taken = new Map<number, [number, number][]>();
  const take = (col: number, y: number) => taken.set(col, [...(taken.get(col) ?? []), [y, y + CARD_H]]);
  const free = (col: number, y: number) => (taken.get(col) ?? []).every(([top, bottom]) => y + CARD_H + GAP_Y / 2 <= top || y >= bottom + GAP_Y / 2);
  for (const id of spineOrder) take(column.get(id)!, positions.get(id)!.y);
  const sideOf = new Map<number, number>();
  const counts = new Map<number, { right: number; left: number }>();
  const side = [...depth.keys()].filter((id) => !spine.has(id)).sort((a, b) => depth.get(a)! - depth.get(b)! || byDate(a, b));
  for (const id of side) {
    const from = parent.get(id)!;
    const origin = positions.get(from)!;
    let choices: { col: number; sign: number }[];
    if (spine.has(from)) {
      const count = counts.get(from) ?? { right: 0, left: 0 };
      const right = { col: spineWidth, sign: 1 };
      const left = { col: -1, sign: -1 };
      choices = count.right <= count.left ? [right, left] : [left, right];
    } else {
      const sign = sideOf.get(from)!;
      choices = [{ col: column.get(from)! + sign, sign }];
    }
    let spot: { col: number; sign: number; y: number } | null = null;
    for (let half = 0; spot === null; half += 1) {
      for (const choice of choices) {
        const y = origin.y + (half * step) / 2;
        if (free(choice.col, y)) {
          spot = { ...choice, y };
          break;
        }
      }
    }
    take(spot.col, spot.y);
    column.set(id, spot.col);
    sideOf.set(id, spot.sign);
    positions.set(id, { x: xOf(spot.col), y: spot.y });
    if (spine.has(from)) {
      const count = counts.get(from) ?? { right: 0, left: 0 };
      if (spot.sign > 0) count.right += 1;
      else count.left += 1;
      counts.set(from, count);
    }
  }

  // Entries related to nothing laid out: a row under everything.
  const lowest = () => Math.max(0, ...[...positions.values()].map((point) => point.y + CARD_H));
  const loose = graph.nodes.filter((node) => !positions.has(node.anilist_id) && !elsewhere(node)).map((node) => node.anilist_id).sort(byDate);
  const looseTop = lowest() + GAP_Y;
  loose.forEach((id, index) => positions.set(id, { x: xOf(index), y: looseTop }));

  // Other collections, each laid out down its own sequels.
  const groups = new Map<number, number[]>();
  for (const node of graph.nodes) {
    if (elsewhere(node)) groups.set(node.collection_id!, [...(groups.get(node.collection_id!) ?? []), node.anilist_id]);
  }
  const frames: Layout['frames'] = [];
  let below = lowest() + GAP_Y + FRAME_HEAD + FRAME_PAD;
  let above = -GAP_Y - FRAME_PAD;
  let right = Math.max(0, ...[...positions.values()].map((point) => point.x + CARD_W)) + GAP_X + SIDE_GAP + FRAME_PAD;
  for (const [collection, ids] of [...groups.entries()].sort((a, b) => a[0] - b[0])) {
    const inside = new Set(ids);
    const { order, layer: rows } = sequelOrder(ids, sequels, byId);
    const width = new Map<number, number>();
    const local = new Map<number, Point>();
    for (const id of order) {
      const row = rows.get(id)!;
      const k = width.get(row) ?? 0;
      width.set(row, k + 1);
      local.set(id, { x: k * lane, y: row * step });
    }
    const height = (Math.max(...order.map((id) => rows.get(id)!)) + 1) * step - GAP_Y;
    const span = Math.max(1, ...width.values()) * lane - GAP_X;
    const continues = graph.edges.some((edge) => edge.label === 'sequel' && spine.has(edge.from) && inside.has(edge.to));
    const precedes = graph.edges.some((edge) => edge.label === 'sequel' && inside.has(edge.from) && spine.has(edge.to));
    let origin: Point;
    if (continues) {
      // A sequel of the main line: the line goes on below, in its frame.
      origin = { x: 0, y: below };
      below += height + GAP_Y + FRAME_HEAD + FRAME_PAD * 2;
    } else if (precedes) {
      origin = { x: 0, y: above - height - FRAME_HEAD - FRAME_PAD };
      above = origin.y - FRAME_HEAD - FRAME_PAD - GAP_Y;
    } else {
      const neighbours = ids.flatMap((id) => linked.get(id)!).filter((other) => positions.has(other) && !inside.has(other));
      const top = neighbours.length ? Math.min(...neighbours.map((other) => positions.get(other)!.y)) : 0;
      origin = { x: right, y: top };
      right += span + GAP_X + FRAME_PAD * 2;
    }
    for (const id of order) {
      const point = local.get(id)!;
      positions.set(id, { x: origin.x + point.x, y: origin.y + point.y });
    }
    frames.push({ id: collection, name: byId.get(ids[0])!.collection_name ?? 'Another collection' });
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
        'relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-card p-2.5 pt-3.5 shadow-lg',
        node.in_collection ? 'border-border' : node.collection_id === null ? 'border-dashed border-muted-foreground/50 bg-card/70' : 'border-cyan-400/30',
        // The collection's title entry: the golden card.
        node.title_entry && 'border-amber-300 bg-gradient-to-b from-amber-300/30 via-amber-400/15 to-amber-500/10 shadow-amber-500/20',
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
          {!node.owned && <Badge variant='muted' className='text-[0.6rem]'>Not downloaded</Badge>}
          {!node.in_collection && node.collection_id === null && (
            <Badge variant='muted' className='text-[0.6rem]'>{node.alone ? 'In no collection' : 'Not in a collection'}</Badge>
          )}
        </span>
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
    const boxes = frames.flatMap((frame) => {
      const members = nodes.filter((item) => !item.data.node.in_collection && item.data.node.collection_id === frame.id);
      if (!members.length) return [];
      const left = Math.min(...members.map((item) => item.position.x)) - FRAME_PAD;
      const top = Math.min(...members.map((item) => item.position.y)) - FRAME_PAD - FRAME_HEAD;
      const right = Math.max(...members.map((item) => item.position.x + CARD_W)) + FRAME_PAD;
      const bottom = Math.max(...members.map((item) => item.position.y + CARD_H)) + FRAME_PAD;
      return [{
        id: 'frame-' + frame.id,
        type: 'frame' as const,
        position: { x: left, y: top },
        data: { name: frame.name, width: right - left, height: bottom - top },
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
      type: sourceHandle === 'bottom' || sourceHandle === 'top' ? 'straight' : 'smoothstep',
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
      <p className='text-sm text-foreground'>Click an entry to change the collection; Shift+click to pick more; click empty space to clear.</p>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>AniList format — the stripe on top</span>
        <span className='flex flex-wrap gap-1.5'>
          {[...Object.values(TYPES), OTHER_TYPE].map((kind) => (
            <span key={kind.label} className={cn('rounded border px-1.5 py-px text-[0.62rem] font-medium uppercase', kind.chip)}>{kind.label}</span>
          ))}
        </span>
      </div>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>Links</span>
        <span className='flex items-center gap-2'><span className='h-1 w-8 rounded bg-foreground' /> main line, top to bottom</span>
        <span className='flex items-center gap-2'><span className='h-0.5 w-8 rounded bg-foreground/60' /> sequels in another collection</span>
        <span className='flex items-center gap-2'><span className='w-8 border-t-2 border-dashed border-muted-foreground' /> side story, recap, other</span>
      </div>
      <div className='flex flex-col gap-1.5'>
        <span className='text-[0.65rem] uppercase tracking-wide'>Cards</span>
        <span className='flex items-center gap-2'><span className='h-3 w-5 rounded border border-amber-300 bg-amber-400/30' /> golden: names and pictures the collection</span>
        <span>Solid: in this collection, its season chip saying where it is filed. Dashed: in no collection.</span>
        <span className='text-cyan-300'>Inside a cyan frame: another collection; one that continues the main line sits above or below it.</span>
      </div>
      <p>Drag to move around, scroll or pinch to zoom, drag cards to untangle. Entries not downloaded yet keep the collection you put them in, and are filed into it once they are on disk.</p>
    </div>
  );
}

/** A side-panel action: the button wraps a long collection name rather than spill out of itself. */
function Action({ icon: Icon, children, ...props }: React.ComponentProps<typeof Button> & { icon: typeof Star }) {
  return (
    <Button size='sm' variant='secondary' className='h-auto min-h-8 justify-start whitespace-normal py-1.5 text-left leading-snug' {...props}>
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
    <Select
      value=''
      disabled={disabled}
      onValueChange={(value) => {
        if (value === 'specials') onChange(toSpecials(arrangement, id));
        else if (value === 'new') onChange(appendTo(arrangement, id, arrangement.seasons.length));
        else onChange(appendTo(arrangement, id, Number(value)));
      }}
    >
      <SelectTrigger data-size='sm' className='w-full' aria-label={label}>
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

/** One entry in the Seasons panel: its type colour, title and episodes. The whole box drags; ⋯ has the rest. */
function SeasonChip({
  id,
  node,
  dragging,
  over,
  onDragStart,
  onDragEnd,
  onDragOverChip,
  onDropChip,
  children,
}: {
  id: number;
  node: CollectionNode | undefined;
  /** Whether this chip is the one being dragged. */
  dragging: boolean;
  /** Whether a dragged entry would land just before this chip. */
  over: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOverChip: (event: React.DragEvent) => void;
  onDropChip: (event: React.DragEvent) => void;
  /** The chip's menu. */
  children: React.ReactNode;
}) {
  const kind = node ? typeOf(node) : OTHER_TYPE;
  const title = node?.title ?? 'AniList ' + id;
  return (
    <span
      draggable
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', String(id));
        // An entry, not the season box it sits in.
        event.stopPropagation();
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={onDragOverChip}
      onDrop={onDropChip}
      title={title + ' · ' + kind.label + ' — drag to move'}
      className={cn(
        'inline-flex max-w-full cursor-grab select-none items-center gap-1 rounded-md border py-1 pl-2 pr-0.5 text-xs active:cursor-grabbing',
        kind.chip,
        node && !node.owned && 'opacity-70',
        dragging && 'opacity-40',
        over && 'shadow-[-4px_0_0_0_var(--color-info)]',
      )}
    >
      <span className='min-w-0 truncate text-foreground'>{title}</span>
      <span className='shrink-0 font-mono text-[0.62rem] opacity-80'>{node?.episodes ? node.episodes + ' eps' : '? eps'}</span>
      <Popover>
        <PopoverTrigger asChild>
          <Button size='icon' variant='ghost' className='size-6 shrink-0' aria-label={'More for ' + title} draggable={false}>
            <MoreHorizontal />
          </Button>
        </PopoverTrigger>
        <PopoverContent align='start' className='flex w-64 flex-col gap-2 p-3'>
          <p className='break-words text-xs font-medium text-foreground'>{title}</p>
          {children}
        </PopoverContent>
      </Popover>
    </span>
  );
}

/**
 * The collection's seasons, edited here before they are saved: which
 * entries make up each season and in what order (an entry after the first
 * continues the season's numbering), and which are specials.
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
}) {
  const { seasons, specials } = arrangement;
  const [dragging, setDragging] = useState<Dragging>(null);
  // Where the drop would land, as a key: "season:2", "season:2:before:123", "new", "specials", "order:3".
  const [over, setOver] = useState<string | null>(null);
  const stop = () => {
    setDragging(null);
    setOver(null);
  };
  /** Handlers for a place an entry can be dropped on. */
  const entryTarget = (key: string, target: DropTarget) => ({
    onDragOver: (event: React.DragEvent) => {
      if (dragging?.kind !== 'entry' || busy) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'move';
      if (over !== key) setOver(key);
    },
    onDrop: (event: React.DragEvent) => {
      if (dragging?.kind !== 'entry' || busy) return;
      event.preventDefault();
      event.stopPropagation();
      onChange(moveEntry(arrangement, dragging.id, target));
      stop();
    },
  });
  /** Handlers for where a whole season can go: a gap between seasons (moved), or another season (merged into it). */
  const seasonTarget = (key: string, apply: (from: number) => Arrangement) => ({
    onDragOver: (event: React.DragEvent) => {
      if (dragging?.kind !== 'season' || busy) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'move';
      if (over !== key) setOver(key);
    },
    onDrop: (event: React.DragEvent) => {
      if (dragging?.kind !== 'season' || busy) return;
      event.preventDefault();
      event.stopPropagation();
      onChange(apply(dragging.index));
      stop();
    },
  });
  /** A gap between seasons a dragged season can be dropped into; shown only while one is dragged. */
  const gap = (at: number) =>
    dragging?.kind === 'season' && at !== dragging.index && at !== dragging.index + 1 ? (
      <div
        key={'gap' + at}
        {...seasonTarget('gap:' + at, (from) => moveSeason(arrangement, from, at))}
        className={cn(
          'flex h-6 shrink-0 items-center justify-center rounded border border-dashed border-transparent text-[0.68rem] text-muted-foreground',
          over === 'gap:' + at ? 'border-info bg-info/10 text-info' : 'border-border/60',
        )}
      >
        Move here
      </div>
    ) : null;
  const chipProps = (id: number, key: string, target: DropTarget) => {
    const drop = entryTarget(key, target);
    return {
      dragging: dragging?.kind === 'entry' && dragging.id === id,
      over: over === key,
      onDragStart: () => setDragging({ kind: 'entry', id }),
      onDragEnd: stop,
      onDragOverChip: drop.onDragOver,
      onDropChip: drop.onDrop,
    };
  };
  return (
    <section className='flex max-h-[45%] shrink-0 flex-col rounded-lg border border-border bg-card/40'>
      <header className='flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2'>
        <button
          type='button'
          onClick={onToggle}
          aria-expanded={open}
          className='flex items-center gap-1.5 text-sm font-medium text-foreground'
        >
          <ChevronDown className={cn('size-4 transition-transform', !open && '-rotate-90')} />
          <ListOrdered className='size-4' /> Seasons
        </button>
        <span className='text-xs text-muted-foreground'>
          {custom ? 'Arranged by hand' : 'Automatic: the sequel chain by air date'}
          {dirty && <span className='text-info'> · unsaved changes</span>}
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
        <div className='flex min-h-0 flex-col gap-2 overflow-y-auto border-t border-border px-3 py-2'>
          <p className='text-xs text-muted-foreground'>
            Drag an entry to any place in a season, onto Specials or onto "New season". Drag a season box between two others to
            move it, or onto another season to merge them.
            Saving renames the files on disk into their new seasons and episode numbers; the moves are journaled and can be undone.
          </p>
          {seasons.map((season, index) => [
            gap(index),
            <div
              key={index}
              draggable={!busy}
              onDragStart={(event) => {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', 'season ' + (index + 1));
                setDragging({ kind: 'season', index });
              }}
              onDragEnd={stop}
              {...(dragging?.kind === 'season'
                ? seasonTarget('merge:' + index, (from) => mergeSeason(arrangement, from, index))
                : {})}
              title='Drag between two seasons to move it, onto another season to merge them'
              className={cn(
                'relative flex cursor-grab select-none flex-col gap-1.5 rounded-md border border-border/60 bg-background/40 p-2 active:cursor-grabbing sm:flex-row sm:items-center',
                over === 'merge:' + index && 'ring-2 ring-info',
                dragging?.kind === 'season' && dragging.index === index && 'opacity-40',
              )}
            >
              <span className='shrink-0 text-xs font-medium text-foreground sm:w-20'>Season {index + 1}</span>
              <div
                {...entryTarget('season:' + index, { where: 'season', index })}
                className={cn(
                  'flex min-h-8 min-w-0 flex-1 flex-wrap items-center gap-1.5 rounded-md p-0.5',
                  over === 'season:' + index && 'ring-2 ring-info',
                )}
              >
                {season.map((id) => (
                  <SeasonChip
                    key={id}
                    id={id}
                    node={byId.get(id)}
                    {...chipProps(id, 'season:' + index + ':before:' + id, { where: 'season', index, before: id })}
                  >
                    <Button size='sm' variant='secondary' className='justify-start' disabled={busy || season.length < 2} onClick={() => onChange(ownSeason(arrangement, id))}>
                      <SplitSquareHorizontal data-icon='inline-start' /> Own season
                    </Button>
                    <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => onChange(toSpecials(arrangement, id))}>
                      <Star data-icon='inline-start' /> To specials
                    </Button>
                    <SeasonPicker arrangement={arrangement} id={id} disabled={busy} onChange={onChange} />
                  </SeasonChip>
                ))}
              </div>
              {over === 'merge:' + index && (
                <span className='pointer-events-none absolute right-2 top-1 text-[0.68rem] text-info'>Merge into Season {index + 1}</span>
              )}
            </div>,
          ])}
          {gap(seasons.length)}
          {dragging?.kind === 'entry' && (
            <div
              {...entryTarget('new', { where: 'new' })}
              className={cn(
                'flex min-h-9 items-center justify-center rounded-md border border-dashed border-border text-xs text-muted-foreground',
                over === 'new' && 'border-info text-info',
              )}
            >
              <Plus className='mr-1 size-3.5' /> New season {seasons.length + 1}
            </div>
          )}
          <div className='flex flex-col gap-1.5 sm:flex-row sm:items-start'>
            <span className='shrink-0 text-xs font-medium text-foreground sm:w-44'>Specials</span>
            <div
              {...entryTarget('specials', { where: 'specials' })}
              className={cn(
                'flex min-h-8 min-w-0 flex-1 flex-wrap items-center gap-1.5 rounded-md p-0.5',
                over === 'specials' && 'ring-2 ring-info',
              )}
            >
              {specials.length === 0 && <span className='text-xs text-muted-foreground'>None — drop here</span>}
              {specials.map((id) => (
                <SeasonChip
                  key={id}
                  id={id}
                  node={byId.get(id)}
                  {...chipProps(id, 'specials:before:' + id, { where: 'specials', before: id })}
                >
                  <SeasonPicker arrangement={arrangement} id={id} disabled={busy} onChange={onChange} />
                  <Button size='sm' variant='secondary' className='justify-start' disabled={busy} onClick={() => onChange(appendTo(arrangement, id, seasons.length))}>
                    <Plus data-icon='inline-start' /> Make season
                  </Button>
                </SeasonChip>
              ))}
            </div>
          </div>
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
  const collectionId = target && 'collection' in target ? target.collection : null;
  const rootAid = target && 'entry' in target ? target.entry : null;
  // The season arrangement as edited here; saved only with "Save arrangement".
  const [draft, setDraft] = useState<Arrangement | null>(null);
  const [seasonsOpen, setSeasonsOpen] = useState(true);
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
    <Dialog open={target !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='flex h-[98dvh] w-[98vw] max-w-none flex-col gap-3'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            {rootAid !== null
              ? 'This entry is in no collection. Its related entries are around it; move it into one of their collections.'
              : 'The main line runs top to bottom, sequel collections continuing it; side stories, recaps and movies sit beside the entry they belong to.'}
          </DialogDescription>
        </DialogHeader>
        <div className='flex min-h-0 flex-1 flex-col gap-3 md:flex-row'>
          <div className='flex min-h-0 min-w-0 flex-1 flex-col gap-3'>
            <div className='relative min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-black/30'>
              {loading && !graph ? (
                <div className='flex h-full items-center justify-center'><Spinner /></div>
              ) : !graph ? (
                <EmptyState icon={Network} title='No graph' description='The relations of this collection could not be read.' />
              ) : (
                <Canvas key={version} graph={graph} selected={selected} drafts={drafts} onPick={pick} onClear={clear} />
              )}
              {busy && (
                <div className='absolute right-3 top-3 flex items-center gap-2 rounded-md bg-card/90 px-3 py-1.5 text-xs text-muted-foreground'>
                  <Spinner /> Saving…
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
              />
            )}
          </div>
          <aside className='flex max-h-[35%] w-full shrink-0 flex-col md:max-h-none md:w-80 gap-3 overflow-y-auto rounded-lg border border-border bg-card/40 p-3 text-sm'>
            {picked.length === 0 ? (
              <Legend />
            ) : (
              <>
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
              </>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  );
}
