import { useMemo } from 'react';
import {
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import { Check } from 'lucide-react';
import type { MangaDetail, MangaGraphNode } from '@/lib/api';
import { formatLabel } from '@/lib/anilist';
import { AnimePoster } from '@/components/AnimePoster';
import { cn } from '@zonsh/ui';

const CARD_W = 150;
const CARD_H = 296;
// Between the collection's own entries along their row.
const GAP_X = 90;
// A band of related entries: cards side by side, at most this many a row.
const BAND_COLS = 6;
const BAND_GAP = 18;
const BAND_PAD = 16;
const BAND_HEAD = 34;
// Between bands beside each other, and between the row and the bands above and below it.
const BANDS_APART = 40;
const ROW_TO_BANDS = 90;

/** AniList's relation types, as the edges say them. */
const RELATIONS: Record<string, string> = {
  ADAPTATION: 'Adaptation',
  SOURCE: 'Source',
  ALTERNATIVE: 'Alternative',
  SEQUEL: 'Sequel',
  PREQUEL: 'Prequel',
  SIDE_STORY: 'Side story',
  SPIN_OFF: 'Spin-off',
  PARENT: 'Parent story',
  SUMMARY: 'Summary',
  CHARACTER: 'Same characters',
  COMPILATION: 'Compilation',
  CONTAINS: 'Contains',
  OTHER: 'Other',
};
// What bankai follows a series by: drawn solid, the rest dashed.
const FOLLOWED_BY = new Set(['ADAPTATION', 'SOURCE', 'ALTERNATIVE']);

type CardData = { node: MangaGraphNode; root: boolean; selected: boolean };
type BandData = { label: string; count: string; width: number; height: number; tone: string; faint: boolean };

function stripe(node: MangaGraphNode): string {
  if (node.type === 'ANIME') return 'bg-cyan-400';
  return node.format === 'NOVEL' ? 'bg-violet-400' : 'bg-orange-400';
}

/** Whether the library has it: the manga library for manga, the anime library for anime. */
function has(node: MangaGraphNode): boolean {
  return node.on_disk;
}

function GraphCard({ data }: NodeProps<Node<CardData, 'card'>>) {
  const { node, root, selected } = data;
  const have = has(node);
  return (
    <div
      className={cn(
        'relative flex cursor-pointer flex-col gap-2 overflow-hidden rounded-xl p-2.5 pt-3.5 shadow-lg transition-colors',
        have ? 'border-2 border-success/80 bg-card' : 'border border-dashed border-muted-foreground/45 bg-card/45',
        node.blacklisted && 'border-destructive/70',
        root && 'ring-2 ring-amber-300 ring-offset-2 ring-offset-background',
        // Its collection framed, as the anime library's graph shades each.
        node.in_collection && !root && 'shadow-[0_0_0_3px_var(--color-cyan-400)]',
        selected && 'ring-2 ring-info ring-offset-2 ring-offset-background',
      )}
      style={{ width: CARD_W, height: CARD_H }}
      title={node.title}
    >
      <span className={cn('absolute inset-x-0 top-0 h-1.5', stripe(node), !have && 'opacity-40')} aria-hidden='true' />
      {/* A line may leave or reach either side: the collection's own links run between cards on one side. */}
      <Handle id='left-in' type='target' position={Position.Left} isConnectable={false} className='opacity-0' />
      <Handle id='left-out' type='source' position={Position.Left} isConnectable={false} className='opacity-0' />
      <Handle id='right-in' type='target' position={Position.Right} isConnectable={false} className='opacity-0' />
      <Handle id='right-out' type='source' position={Position.Right} isConnectable={false} className='opacity-0' />
      <Handle id='top-out' type='source' position={Position.Top} isConnectable={false} className='opacity-0' />
      <Handle id='bottom-out' type='source' position={Position.Bottom} isConnectable={false} className='opacity-0' />
      <div className='relative'>
        <AnimePoster url={node.cover} title={node.title} className={cn('w-full rounded-lg', !have && 'opacity-45 grayscale')} />
        <span
          className={cn(
            'absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.62rem] font-semibold shadow',
            node.blacklisted ? 'bg-destructive text-white'
              : have ? 'bg-success text-black'
              : 'border border-white/15 bg-background/85 text-muted-foreground backdrop-blur-sm',
          )}
        >
          {node.blacklisted ? 'Blacklisted' : have ? <><Check className='size-3' /> {node.type === 'ANIME' ? 'Anime library' : 'In your library'}</> : 'Not in your library'}
        </span>
      </div>
      <div className='flex min-h-0 flex-1 flex-col gap-1'>
        <span className={cn('line-clamp-2 text-[0.86rem] font-medium leading-tight', root ? 'text-amber-100' : have ? 'text-foreground' : 'text-muted-foreground')}>{node.title}</span>
        <span className='truncate font-mono text-[0.64rem] text-muted-foreground'>
          {[node.type === 'ANIME' ? 'Anime · ' + (formatLabel(node.format) ?? '') : formatLabel(node.format), node.year].filter(Boolean).join(' · ')}
        </span>
        {node.owned && <span className='truncate font-mono text-[0.64rem] text-success'>{node.owned}</span>}
        {!have && node.followed && !node.blacklisted && <span className='mt-auto text-[0.62rem] font-medium text-info'>Followed: bankai looks for it</span>}
        {node.type === 'MANGA' && !node.in_collection && node.collection_name && <span className='mt-auto truncate text-[0.62rem] text-cyan-300'>In {node.collection_name}</span>}
      </div>
    </div>
  );
}

/** A band: the entries one relation joins to one of the collection's, under its name, joined to that
 *  entry by one line -- not a line and a label per card, which crossed each other and the cards. */
function GraphBand({ data }: NodeProps<Node<BandData, 'band'>>) {
  return (
    <div
      className={cn('relative rounded-2xl border bg-card/30', data.faint ? 'border-dashed border-muted-foreground/35' : 'border-border')}
      style={{ width: data.width, height: data.height }}
    >
      <Handle id='top-in' type='target' position={Position.Top} isConnectable={false} className='opacity-0' />
      <Handle id='bottom-in' type='target' position={Position.Bottom} isConnectable={false} className='opacity-0' />
      <div className='flex items-baseline gap-2 px-4 pt-2.5'>
        <span className='text-[0.8rem] font-medium' style={{ color: data.tone }}>{data.label}</span>
        <span className='font-mono text-[0.66rem] text-muted-foreground'>{data.count}</span>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className='flex flex-col gap-1.5 rounded-lg border border-border bg-card/90 p-2.5 text-[0.68rem] text-muted-foreground backdrop-blur-sm'>
      <span className='flex items-center gap-2'><span className='size-3 rounded-sm border-2 border-success/80' /> In your library</span>
      <span className='flex items-center gap-2'><span className='size-3 rounded-sm border border-dashed border-muted-foreground/60' /> Not in your library</span>
      <span className='flex items-center gap-2'><span className='size-3 rounded-sm shadow-[0_0_0_2px_var(--color-cyan-400)]' /> In this collection</span>
      <span className='flex items-center gap-2'><span className='h-1.5 w-3 rounded-sm bg-cyan-400' /> Anime <span className='h-1.5 w-3 rounded-sm bg-orange-400' /> Manga <span className='h-1.5 w-3 rounded-sm bg-violet-400' /> Light novel</span>
    </div>
  );
}

const nodeTypes = { card: GraphCard, band: GraphBand };

const TONE = { anime: 'var(--color-cyan-400)', manga: 'var(--color-orange-400)', novel: 'var(--color-violet-400)' };
const toneOf = (node: MangaGraphNode) => (node.type === 'ANIME' ? TONE.anime : node.format === 'NOVEL' ? TONE.novel : TONE.manga);

function countLabel(items: MangaGraphNode[]): string {
  const kinds = [
    ['anime', items.filter((node) => node.type === 'ANIME').length],
    ['manga', items.filter((node) => node.type === 'MANGA' && node.format !== 'NOVEL').length],
    ['light novel', items.filter((node) => node.format === 'NOVEL').length],
  ] as const;
  return kinds.filter(([, count]) => count > 0).map(([noun, count]) => `${count} ${noun}${count === 1 || noun === 'anime' || noun === 'manga' ? '' : 's'}`).join(' · ');
}

/**
 * A series' collection and what AniList relates its entries to -- the same from each of them, as
 * the anime library's: the entries left to right in their order (framed), the one open now ringed;
 * what relates to them in bands, one per entry and relation (anime above, manga and light novels
 * below), each joined to its entry by one line and named once (Nov 2026: a line and a label per card
 * crossed each other and ran behind the cards). A manga card is picked with a click (Shift adds to what is
 * picked) when ``onPick`` is given; otherwise, and for anime, a click opens it.
 */
export function MangaGraph({ graph, onOpen, onPick, selected = [] }: {
  graph: MangaDetail['graph'];
  onOpen: (node: MangaGraphNode) => void;
  onPick?: (node: MangaGraphNode, add: boolean) => void;
  selected?: number[];
}) {
  const { nodes, edges } = useMemo(() => {
    const byId = new Map(graph.nodes.map((node) => [node.anilist_id, node]));
    // The collection's entries left to right in their order, the same from each of them.
    const line = (graph.members ?? [graph.root]).map((member) => byId.get(member)).filter((node): node is MangaGraphNode => Boolean(node));
    const lineIndex = new Map(line.map((node, index) => [node.anilist_id, index]));
    const id = (node: MangaGraphNode) => node.type + '-' + node.anilist_id;
    const cardNode = (node: MangaGraphNode, x: number, y: number): Node<CardData, 'card'> => ({
      id: id(node),
      type: 'card',
      position: { x, y },
      data: { node, root: node.anilist_id === graph.root, selected: selected.includes(node.anilist_id) },
      draggable: false,
    });
    const laid: Node[] = line.map((node, index) => cardNode(node, index * (CARD_W + GAP_X), 0));
    const lines: Edge[] = [];

    // Between the collection's entries: only neighbours, along the row (the order says the rest).
    for (const link of graph.edges) {
      const a = lineIndex.get(link.from);
      const b = lineIndex.get(link.to);
      if (a === undefined || b === undefined || Math.abs(a - b) !== 1) continue;
      const [left, right] = a < b ? [line[a], line[b]] : [line[b], line[a]];
      if (lines.some((edge) => edge.id === id(left) + '>' + id(right))) continue;
      lines.push({ id: id(left) + '>' + id(right), source: id(left), target: id(right), sourceHandle: 'right-out', targetHandle: 'left-in',
        style: { stroke: 'var(--color-cyan-400)', strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--color-cyan-400)' } });
    }

    // Everything else in bands: per entry of the row and relation, anime above, manga and novels below.
    type Band = { key: string; anchor: number; relation: string; above: boolean; items: MangaGraphNode[] };
    const bands = new Map<string, Band>();
    const placed = new Set(line.map((node) => node.anilist_id));
    const join = (anchor: number, other: MangaGraphNode, relation: string) => {
      if (placed.has(other.anilist_id)) return;
      placed.add(other.anilist_id);
      const above = other.type === 'ANIME';
      const key = `${anchor}:${relation}:${above}`;
      const band = bands.get(key) ?? { key, anchor, relation, above, items: [] };
      band.items.push(other);
      bands.set(key, band);
    };
    // Links to the entry open now first, then along the row.
    const links = [...graph.edges].sort((a, b) => {
      const at = (link: typeof a) => Math.min(link.from === graph.root || link.to === graph.root ? -1 : 99, lineIndex.get(link.from) ?? 99, lineIndex.get(link.to) ?? 99);
      return at(a) - at(b);
    });
    for (const link of links) {
      const fromLine = lineIndex.has(link.from);
      const toLine = lineIndex.has(link.to);
      if (fromLine === toLine) continue;
      const anchor = fromLine ? link.from : link.to;
      const other = byId.get(fromLine ? link.to : link.from);
      if (other) join(anchor, other, link.label);
    }
    // Two steps away (related to a related entry): with the entry open now, as "Related".
    for (const node of graph.nodes) join(graph.root, node, 'OTHER');

    const rank = (node: MangaGraphNode) => (has(node) ? 0 : node.followed ? 1 : 2);
    for (const side of [true, false]) {
      const mine = [...bands.values()].filter((band) => band.above === side)
        .sort((a, b) => (lineIndex.get(a.anchor) ?? 0) - (lineIndex.get(b.anchor) ?? 0) || Number(!FOLLOWED_BY.has(a.relation)) - Number(!FOLLOWED_BY.has(b.relation)));
      let right = -Infinity;
      for (const band of mine) {
        band.items.sort((a, b) => rank(a) - rank(b) || (a.year ?? 9999) - (b.year ?? 9999));
        const cols = Math.min(BAND_COLS, band.items.length);
        const rows = Math.ceil(band.items.length / BAND_COLS);
        const width = cols * CARD_W + (cols - 1) * BAND_GAP + 2 * BAND_PAD;
        const height = BAND_HEAD + rows * CARD_H + (rows - 1) * BAND_GAP + BAND_PAD;
        const anchorX = (lineIndex.get(band.anchor) ?? 0) * (CARD_W + GAP_X) + CARD_W / 2;
        const x = Math.max(anchorX - width / 2, right + BANDS_APART);
        right = x + width;
        const y = side ? -ROW_TO_BANDS - height : CARD_H + ROW_TO_BANDS;
        const faint = !band.items.some(has);
        const tone = toneOf(band.items[0]);
        laid.push({ id: 'band-' + band.key, type: 'band', position: { x, y }, zIndex: -1, draggable: false, selectable: false,
          data: { label: RELATIONS[band.relation] ?? band.relation, count: countLabel(band.items), width, height, tone, faint } });
        band.items.forEach((node, index) => {
          laid.push(cardNode(node, x + BAND_PAD + (index % BAND_COLS) * (CARD_W + BAND_GAP), y + BAND_HEAD + Math.floor(index / BAND_COLS) * (CARD_H + BAND_GAP)));
        });
        const anchorNode = byId.get(band.anchor)!;
        lines.push({
          id: id(anchorNode) + '>band-' + band.key,
          source: id(anchorNode),
          target: 'band-' + band.key,
          sourceHandle: side ? 'top-out' : 'bottom-out',
          targetHandle: side ? 'bottom-in' : 'top-in',
          type: 'smoothstep',
          style: FOLLOWED_BY.has(band.relation)
            ? { stroke: tone, strokeWidth: 2.5, opacity: faint ? 0.4 : 1 }
            : { stroke: tone, strokeWidth: 1.5, strokeDasharray: '6 5', opacity: faint ? 0.4 : 1 },
          markerEnd: { type: MarkerType.ArrowClosed, color: tone },
        });
      }
    }
    return { nodes: laid, edges: lines };
  }, [graph, selected]);

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(event, node) => {
          if (node.type !== 'card') return;
          const card = (node.data as CardData).node;
          if (onPick && card.type === 'MANGA') onPick(card, event.shiftKey || event.metaKey || event.ctrlKey);
          else onOpen(card);
        }}
        nodesConnectable={false}
        elementsSelectable={false}
        colorMode='dark'
        fitView
        fitViewOptions={{ padding: 0.1, maxZoom: 1 }}
        minZoom={0.1}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
        <Panel position='top-left'><Legend /></Panel>
      </ReactFlow>
    </ReactFlowProvider>
  );
}
