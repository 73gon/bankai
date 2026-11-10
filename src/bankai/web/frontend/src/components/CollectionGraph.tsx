import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
  type FitViewOptions,
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
  BookOpen,
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
import { api, type CollectionGraph as Graph, type CollectionNode, type LinkedManga } from '@/lib/api';
import { anilistUrl, formatLabel } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Empty, EmptyDescription, EmptyMedia, EmptyTitle } from '@zonsh/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';

// One entry's card: a poster with its title under it.
const CARD_W = 208;
const CARD_H = 400;
const GAP_X = 80;
const GAP_Y = 64;
// Extra room between the main line and the rows above and below it, for their arrows.
const SIDE_GAP = 48;
const FRAME_PAD = 22;
// The frame's name over its cards: large enough to read with the graph zoomed out to fit.
const FRAME_HEAD = 48;
// Rows off the main line, above (negative) and below it, with extra room for their arrows.
const rowY = (row: number) => row * (CARD_H + GAP_Y) + Math.sign(row) * SIDE_GAP;

/** What kind of entry it is, by AniList's format: the badge on its card and its chip. */
const TYPES: Record<string, { label: string; chip: string }> = {
  TV: { label: 'TV series', chip: 'border-sky-400/40 bg-sky-400/10 text-sky-300' },
  MOVIE: { label: 'Movie', chip: 'border-rose-400/40 bg-rose-400/10 text-rose-300' },
  OVA: { label: 'OVA', chip: 'border-violet-400/40 bg-violet-400/10 text-violet-300' },
  ONA: { label: 'ONA', chip: 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300' },
  SPECIAL: { label: 'Special', chip: 'border-orange-400/40 bg-orange-400/10 text-orange-300' },
  MUSIC: { label: 'Music', chip: 'border-lime-400/40 bg-lime-400/10 text-lime-300' },
};
const OTHER_TYPE = { label: 'Other', chip: 'border-zinc-500/40 bg-zinc-500/10 text-zinc-300' };

// Manga and light novels: a hue no format, collection or state in the graph uses.
const MANGA_TONE = '#e879f9';

function typeOf(node: CollectionNode) {
  const format = (node.type ?? '').toUpperCase();
  // A TV short is a TV series as far as the collection goes.
  return TYPES[format === 'TV_SHORT' ? 'TV' : format] ?? OTHER_TYPE;
}

type EntryData = {
  node: CollectionNode;
  selected: boolean;
  /** Where the unsaved arrangement files it, when that differs. */
  draft?: string | null;
  /** What it is to the entry it hangs off ("side story", "summary"): said on the card, not on the line. */
  relation?: string | null;
};
type FrameData = { name: string; width: number; height: number; tone: string; own: boolean; /** The whole heading, in place of "Collection: <name>". */ heading?: string };
type MangaData = { item: LinkedManga; /** The anime it is linked to, said on the card in place of a line to it. */ anime?: string };
type EntryNode = Node<EntryData, 'entry'>;
type MangaNode = Node<MangaData, 'manga'>;
/** What can be dragged on the canvas: entries and manga; frames follow their entries. */
type Movable = EntryNode | MangaNode;
type FlowNode = Movable | Node<FrameData, 'frame'>;
type Point = { x: number; y: number };
/** A manga or light novel an entry of the graph links to, and the earliest such entry. */
type GraphManga = { item: LinkedManga; anime: number };

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

type Layout = {
  positions: Map<number, Point>;
  spine: Set<number>;
  frames: { id: number; name: string; own: boolean }[];
  /** Each entry off the main line: the one it hangs off, and what it is to it. */
  parent: Map<number, number>;
  relation: Map<number, string>;
};

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
  const yOf = rowY;
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
  const relation = new Map<number, string>();
  for (const [child, from] of parent) {
    const link = graph.edges.find((edge) => (edge.from === from && edge.to === child) || (edge.from === child && edge.to === from));
    if (link) relation.set(child, link.label);
  }
  return { positions, spine, frames, parent, relation };
}

/**
 * Where the manga and light novels go: a column of their own left of everything, where a
 * source stands on the timeline, the first beside the main line and the rest above and below
 * it in turn; each nearest the entry it is linked to, in that entry's order.
 */
function mangaLayout(manga: GraphManga[], positions: Map<number, Point>): Map<number, Point> {
  const out = new Map<number, Point>();
  if (!manga.length) return out;
  const lane = CARD_W + GAP_X;
  const left = Math.min(0, ...[...positions.values()].map((point) => point.x)) - FRAME_PAD * 2 - lane;
  const rows = [0, 1, -1, 2, -2];
  const at = (id: number) => positions.get(id) ?? { x: 0, y: 0 };
  const ordered = [...manga].sort((a, b) => at(a.anime).x - at(b.anime).x || at(a.anime).y - at(b.anime).y || a.item.anilist_id - b.item.anilist_id);
  ordered.forEach((entry, index) => {
    // A sixth one and on: another column further left.
    out.set(entry.item.anilist_id, { x: left - Math.floor(index / rows.length) * lane, y: rowY(rows[index % rows.length]) });
  });
  return out;
}

/** The manga's id on the canvas, apart from the anime entries'. */
const mangaNodeId = (id: number) => 'manga-' + id;

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

function EntryCard({ data }: NodeProps<EntryNode>) {
  const { node, selected } = data;
  const kind = typeOf(node);
  return (
    <div
      className={cn(
        'relative flex flex-col gap-2 overflow-hidden rounded-xl border bg-card p-2.5 shadow-lg',
        node.in_collection ? 'border-border' : node.collection_id === null ? 'border-dashed border-muted-foreground/50 bg-card/70' : 'border-cyan-400/30',
        // The collection's title entry: the golden card.
        node.title_entry && 'border-amber-300 bg-linear-to-b from-amber-300/30 via-amber-400/15 to-amber-500/10 shadow-amber-500/20',
        selected && 'ring-2 ring-info ring-offset-2 ring-offset-background',
      )}
      style={{ width: CARD_W, height: CARD_H }}
      title={node.main_title && node.main_title !== node.title ? node.main_title : node.title}
    >
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
        {data.relation && <span className='truncate text-[0.7rem] text-foreground-soft'>{data.relation[0].toUpperCase() + data.relation.slice(1)}</span>}
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

/**
 * A collection's shade behind its cards: the open one neutral white, each other one a hue of
 * its own, from the categorical order validated against the dark surface (blue, orange, aqua,
 * yellow, magenta, violet), so neighbouring frames never read as two blues.
 */
const OWN_TONE = 'var(--foreground)';
const TONES = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9'];

function CollectionFrame({ data }: NodeProps<Node<FrameData, 'frame'>>) {
  return (
    <div
      className='pointer-events-none rounded-2xl border-2'
      style={{
        width: data.width,
        height: data.height,
        // The open collection in near-white, plain to see with the graph zoomed out to fit.
        borderColor: `color-mix(in oklab, ${data.tone} ${data.own ? 70 : 55}%, transparent)`,
        background: `color-mix(in oklab, ${data.tone} ${data.own ? 7 : 12}%, transparent)`,
      }}
    >
      <span className='flex items-center gap-2 truncate px-4 pt-2.5 text-[22px] font-semibold leading-7 text-foreground'>
        <span className='size-3 shrink-0 rounded-[4px]' style={{ background: data.tone }} aria-hidden='true' />
        {data.heading ?? `${data.own ? 'This collection' : 'Collection'}: ${data.name}`}
      </span>
    </div>
  );
}

/** What bankai does about a manga, as the manga library says it. */
function mangaState(item: LinkedManga): { label: string; tone: string } {
  if (item.blacklisted) return { label: 'Blacklisted', tone: 'text-destructive' };
  if (item.on_disk) return { label: item.owned ? 'You have ' + item.owned : 'In your library', tone: 'text-success' };
  if (item.in_review) return { label: 'Releases to review', tone: 'text-warning' };
  if (item.followed) return { label: 'Followed', tone: 'text-info' };
  return { label: 'Not followed', tone: 'text-muted-foreground' };
}

/** A manga or light novel the anime is linked to: a click opens it in the manga library. */
function MangaCard({ data }: NodeProps<MangaNode>) {
  const { item } = data;
  const state = mangaState(item);
  return (
    <div
      className='relative flex cursor-pointer flex-col gap-2 overflow-hidden rounded-xl border-2 bg-card p-2.5 shadow-lg transition-colors hover:bg-control-hover'
      style={{ width: CARD_W, height: CARD_H, borderColor: `color-mix(in oklab, ${MANGA_TONE} 70%, transparent)` }}
      title={'Open ' + item.title + ' in the manga library'}
    >
      {SIDES.map(([id, position]) => (
        <Handle key={id} id={id} type='source' position={position} isConnectable={false} className={hidden} />
      ))}
      <AnimePoster url={item.cover} title={item.title} className={cn('w-full rounded-lg', !item.on_disk && 'opacity-70')} />
      <div className='flex min-h-0 flex-1 flex-col gap-1.5'>
        <span className='line-clamp-2 text-[0.86rem] font-medium leading-tight text-foreground'>{item.title}</span>
        <span className='flex items-center gap-1.5'>
          <span className='rounded border border-fuchsia-400/40 bg-fuchsia-400/10 px-1 py-px text-[0.6rem] font-medium uppercase tracking-wide text-fuchsia-300'>
            {formatLabel(item.format) ?? 'Manga'}
          </span>
          {item.year && <span className='truncate font-mono text-[0.64rem] text-muted-foreground'>{item.year}</span>}
        </span>
        <span className={cn('truncate text-[0.7rem]', state.tone)}>{state.label}</span>
        {data.anime && <span className='line-clamp-2 text-[0.68rem] leading-snug text-fuchsia-200/80'>{data.item.relation.toLowerCase().replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())} of {data.anime}</span>}
        <span className='mt-auto flex items-center gap-1 text-[0.7rem] text-fuchsia-200'>
          <BookOpen className='size-3.5 shrink-0' aria-hidden='true' /> Open in the manga library
        </span>
      </div>
    </div>
  );
}

const nodeTypes = { entry: EntryCard, frame: CollectionFrame, manga: MangaCard };

/** Which sides of two cards a link joins: the facing sides along a row, else bottom to top between rows. */
function joins(from: Point, to: Point) {
  if (Math.abs(from.y - to.y) < CARD_H / 2) return from.x < to.x ? ['right', 'left'] : ['left', 'right'];
  return from.y < to.y ? ['bottom', 'top'] : ['top', 'bottom'];
}

/** Room around the fitted graph; at the bottom, what the floating Seasons panel covers. */
function fitPadding(reserve: number): NonNullable<FitViewOptions['padding']> {
  return { top: '8%', left: '6%', right: '6%', bottom: reserve ? (`${reserve + 16}px` as const) : '8%' };
}

/** Zoom and fit, as zon buttons rather than React Flow's own, above the floating Seasons panel. */
function GraphControls({ reserve }: { reserve: number }) {
  const flow = useReactFlow();
  // Fit again when the canvas settles its size (the dialog opening), when cards are measured
  // (the manga arrive after the graph), and when the Seasons panel opens or folds. A panel
  // growing by a row while it is edited leaves the view alone.
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  const measured = useStore((state) => {
    let count = 0;
    for (const node of state.nodeLookup.values()) if (node.measured?.width) count += 1;
    return count;
  });
  const fitted = useRef({ width: 0, height: 0, reserve: -1, measured: 0 });
  useEffect(() => {
    if (!width || !height) return;
    const last = fitted.current;
    // Only more cards measured than ever: zooming in leaves cards off screen unmeasured, and
    // fitting for that undid every zoom.
    if (width === last.width && height === last.height && measured <= last.measured && Math.abs(reserve - last.reserve) < 48) return;
    fitted.current = { width, height, reserve, measured: Math.max(measured, last.measured) };
    void flow.fitView({ padding: fitPadding(reserve), maxZoom: 1 });
  }, [width, height, reserve, measured]);
  return (
    <Panel position='bottom-left' className='!mx-3 !mt-3 flex gap-1 rounded-button material-menu p-1' style={{ marginBottom: reserve + 12 }}>
      <Button size='icon-sm' variant='ghost' aria-label='Zoom in' title='Zoom in' onClick={() => void flow.zoomIn({ duration: 160 })}><ZoomIn /></Button>
      <Button size='icon-sm' variant='ghost' aria-label='Zoom out' title='Zoom out' onClick={() => void flow.zoomOut({ duration: 160 })}><ZoomOut /></Button>
      <Button size='icon-sm' variant='ghost' aria-label='Fit the graph' title='Fit the graph' onClick={() => void flow.fitView({ padding: fitPadding(reserve), duration: 200 })}><Maximize /></Button>
    </Panel>
  );
}

/** The graph itself: laid out once per loaded graph, then free to pan, zoom and drag. */
function Canvas({
  graph,
  selected,
  drafts,
  manga,
  reserve,
  onPick,
  onClear,
  onOpenManga,
}: {
  graph: Graph;
  selected: number[];
  /** Entries the unsaved arrangement files elsewhere, and where. */
  drafts: Map<number, string>;
  /** The manga and light novels the entries link to, each joined to its entry. */
  manga: GraphManga[];
  /** How much of the canvas's bottom the floating Seasons panel covers, in pixels. */
  reserve: number;
  onPick: (id: number, add: boolean) => void;
  onClear: () => void;
  onOpenManga: (id: number) => void;
}) {
  const { positions, spine, frames, parent, relation } = useMemo(() => layout(graph), [graph]);
  const titleOf = useMemo(() => new Map(graph.nodes.map((node) => [node.anilist_id, node.title])), [graph]);
  const mangaAt = useMemo(() => mangaLayout(manga, positions), [manga, positions]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Movable>(
    graph.nodes.map((node) => ({
      id: String(node.anilist_id),
      type: 'entry' as const,
      position: positions.get(node.anilist_id) ?? { x: 0, y: 0 },
      data: { node, selected: false, relation: spine.has(node.anilist_id) ? null : relation.get(node.anilist_id) ?? null },
    })),
  );
  // The manga arrive after the graph: their cards join the canvas then.
  useEffect(() => {
    setNodes((current) => [
      ...current.filter((item) => item.type !== 'manga'),
      ...manga.map((entry): MangaNode => ({
        id: mangaNodeId(entry.item.anilist_id),
        type: 'manga',
        position: mangaAt.get(entry.item.anilist_id) ?? { x: 0, y: 0 },
        data: { item: entry.item, anime: titleOf.get(entry.anime) },
      })),
    ]);
  }, [manga, mangaAt, setNodes, titleOf]);
  // Each frame drawn round wherever its cards are now, so it follows them when dragged.
  const shown = useMemo<FlowNode[]>(() => {
    const entries = nodes.filter((item): item is EntryNode => item.type === 'entry');
    const boxes: Node<FrameData, 'frame'>[] = frames.flatMap((frame, index) => {
      const members = entries.filter((item) => (frame.own ? item.data.node.in_collection : !item.data.node.in_collection && item.data.node.collection_id === frame.id));
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
    // The manga and light novels framed together on the left, no line to each anime: those crossed
    // every row between (each card says its anime instead).
    const books = nodes.filter((item) => item.type === 'manga');
    if (books.length) {
      const left = Math.min(...books.map((item) => item.position.x)) - FRAME_PAD;
      const top = Math.min(...books.map((item) => item.position.y)) - FRAME_PAD - FRAME_HEAD;
      const right = Math.max(...books.map((item) => item.position.x + CARD_W)) + FRAME_PAD;
      const bottom = Math.max(...books.map((item) => item.position.y + CARD_H)) + FRAME_PAD;
      boxes.push({
        id: 'frame-manga',
        type: 'frame' as const,
        position: { x: left, y: top },
        width: right - left,
        height: bottom - top,
        data: { name: '', heading: 'Manga & light novels', width: right - left, height: bottom - top, own: false, tone: MANGA_TONE },
        draggable: false,
        selectable: false,
        zIndex: -1,
      });
    }
    return [...boxes, ...nodes.map((item): Movable => (item.type === 'entry' ? {
      ...item,
      data: { ...item.data, selected: selected.includes(Number(item.id)), draft: drafts.get(Number(item.id)) ?? null },
    } : item))];
  }, [frames, nodes, selected, drafts]);
  // Lines without labels -- stacked on one another they could not be read; a side entry says on its
  // card what it is. Links the layout does not hang anything by (between two side entries, across)
  // are faint.
  const edges = useMemo<Edge[]>(() => graph.edges.map((item) => {
    const from = positions.get(item.from) ?? { x: 0, y: 0 };
    const to = positions.get(item.to) ?? { x: 0, y: 0 };
    const [sourceHandle, targetHandle] = joins(from, to);
    const main = item.label === 'sequel' && spine.has(item.from) && spine.has(item.to);
    const sequel = item.label === 'sequel';
    const tree = main || parent.get(item.to) === item.from || parent.get(item.from) === item.to;
    const color = main ? 'var(--foreground)' : sequel ? 'var(--foreground-soft)' : 'var(--muted-foreground)';
    return {
      id: `${item.from}-${item.to}-${item.label}`,
      source: String(item.from),
      target: String(item.to),
      sourceHandle,
      targetHandle,
      // Straight when the cards line up, along a row or down a column; round the corner otherwise.
      type: Math.abs(from.y - to.y) < 1 || Math.abs(from.x - to.x) < 1 ? 'straight' : 'smoothstep',
      markerEnd: tree && (sequel || item.label === 'side story' || item.label === 'summary')
        ? { type: MarkerType.ArrowClosed, width: 18, height: 18, color }
        : undefined,
      style: main
        ? { stroke: color, strokeWidth: 3.5 }
        : !tree
          ? { stroke: color, strokeWidth: 1, strokeDasharray: '4 6', opacity: 0.35 }
          : sequel
            ? { stroke: color, strokeWidth: 2.5 }
            : { stroke: color, strokeWidth: 1.5, strokeDasharray: '6 5' },
      zIndex: main ? 1 : 0,
    };
  }), [graph, positions, spine, parent]);

  return (
    <ReactFlow
      nodes={shown}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={(changes) => onNodesChange(
        // Frames are drawn round their cards, not moved on their own.
        changes.filter((change) => !('id' in change) || !String(change.id).startsWith('frame-')) as NodeChange<Movable>[],
      )}
      onNodeClick={(event, node) => {
        if (node.type === 'entry') onPick(Number(node.id), event.shiftKey);
        else if (node.type === 'manga') onOpenManga((node as MangaNode).data.item.anilist_id);
      }}
      onPaneClick={onClear}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      elementsSelectable={false}
      selectionKeyCode={null}
      multiSelectionKeyCode={null}
      colorMode='dark'
      fitView
      fitViewOptions={{ padding: fitPadding(reserve), maxZoom: 1 }}
      minZoom={0.1}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
      // No ground of its own: the graph is the dialog's whole body, not a box in it.
      style={{ '--xy-background-color': 'transparent' } as React.CSSProperties}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
      <GraphControls reserve={reserve} />
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
  className,
  panelRef,
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
  /** Where and how it sits: floating over the graph. */
  className?: string;
  /** The panel, measured so the graph keeps clear of it. */
  panelRef?: React.Ref<HTMLElement>;
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
    <section ref={panelRef} className={cn('flex flex-col rounded-control', className ?? 'max-h-[40%] shrink-0 surface-container')}>
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

  // The manga and light novels the collection's entries (or the one entry) link to, each
  // joined to the earliest entry linking it (the answer names which entries link each one).
  const [manga, setManga] = useState<GraphManga[]>([]);
  useEffect(() => {
    setManga([]);
    if (!graph) return;
    let alive = true;
    const sources = graph.nodes
      .filter((node) => node.in_collection || node.anilist_id === graph.root_aid)
      .sort((a, b) => aired(a).localeCompare(aired(b)) || a.anilist_id - b.anilist_id)
      .slice(0, 24)
      .map((node) => node.anilist_id);
    if (!sources.length) return;
    api.mangaLinked(sources).then(
      (answer) => {
        if (alive) setManga(answer.items.map((item) => ({ item, anime: item.anime?.[0] ?? sources[0] })));
      },
      () => { /* no manga in the graph then */ },
    );
    return () => { alive = false; };
  }, [graph]);

  // How much of the graph the floating Seasons panel covers, so the fit and the controls keep clear of it.
  const [reserve, setReserve] = useState(0);
  const panelObserver = useRef<ResizeObserver | null>(null);
  const panelRef = useCallback((element: HTMLElement | null) => {
    panelObserver.current?.disconnect();
    panelObserver.current = null;
    if (!element) {
      setReserve(0);
      return;
    }
    const observer = new ResizeObserver(() => setReserve(element.offsetHeight + 12));
    observer.observe(element);
    panelObserver.current = observer;
  }, []);

  const navigate = useNavigate();
  function openManga(id: number) {
    onClose();
    navigate('/m/library?series=' + id);
  }

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
      {/* The graph is the dialog's whole body, edge to edge under the title; the Seasons panel floats over its foot. */}
      <DialogContent className='h-[98dvh] max-h-none w-[98vw] max-w-none shrink-0 gap-0 p-0 sm:p-0'>
        <DialogHeader className='px-4 pb-3 pt-4 sm:px-5 sm:pt-5'>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            {rootAid !== null
              ? 'This entry is in no collection. Its related entries are around it; move it into one of their collections.'
              : 'The main line runs left to right, sequel collections continuing it; side stories, recaps and movies stand above and below the entry they belong to. Each collection is shaded; a side entry says on its card what it is. The manga and light novels stand framed on the left, each saying its anime.'}
          </DialogDescription>
        </DialogHeader>
        <div className='flex min-h-0 flex-1 flex-col'>
          <div data-slot='graph' className='relative min-h-0 flex-1 overflow-hidden'>
            {loading && !graph ? (
              <div className='flex h-full items-center justify-center'><Spinner label='Loading the graph' /></div>
            ) : !graph ? (
              <Empty>
                <EmptyMedia><Network /></EmptyMedia>
                <EmptyTitle>No graph</EmptyTitle>
                <EmptyDescription>The relations of this collection could not be read.</EmptyDescription>
              </Empty>
            ) : (
              <Canvas
                key={version}
                graph={graph}
                selected={selected}
                drafts={drafts}
                manga={manga}
                reserve={reserve}
                onPick={pick}
                onClear={clear}
                onOpenManga={openManga}
              />
            )}
            {graph && picked.length === 0 && (
              <p className='pointer-events-none absolute left-3 top-3 rounded-button material-menu px-2.5 py-1 text-xs text-muted-foreground'>
                Click an entry to change the collection · Shift+click to pick more{manga.length > 0 ? ' · a manga opens in the manga library' : ''}
              </p>
            )}
            {/* What can be done with the picked entries, over the graph rather than beside it. */}
            {picked.length > 0 && (
              <div
                className='absolute right-3 top-3 z-20 flex w-72 flex-col gap-3 overflow-y-auto rounded-control material-menu p-3 text-sm shadow-overlay'
                style={{ maxHeight: `calc(100% - ${reserve + 24}px)` }}
              >
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
              <div className='absolute right-3 z-20 flex items-center gap-2 rounded-button material-menu px-3 py-1.5 text-xs text-muted-foreground' style={{ bottom: reserve + 12 }}>
                <Spinner label='Saving' /> Saving…
              </div>
            )}
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
                className='absolute inset-x-3 bottom-3 z-10 max-h-[45%] material-menu shadow-overlay'
                panelRef={panelRef}
              />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
