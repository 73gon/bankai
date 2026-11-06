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
import { cn } from '@/zon/lib/cn';

const CARD_W = 150;
const CARD_H = 296;
const GAP_X = 150;
const GAP_Y = 32;
const GAP_COLUMN = 36;
// Cards a column holds before the next column out: a tall column zooms the graph out to nothing.
const ROWS = 3;

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

type CardData = { node: MangaGraphNode; root: boolean; side: 'left' | 'right' | 'root'; selected: boolean };

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

const nodeTypes = { card: GraphCard };

/**
 * A series' collection and what AniList relates its entries to -- the same from each of them, as
 * the anime library's: the entries left to right in their order (framed), the one open now ringed;
 * their anime on the left, other manga and light novels on the right. The lines are AniList's links,
 * the collection's own among them. A manga card is picked with a click (Shift adds to what is
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
    // The main line: the collection's entries left to right in their order, the same from each of them.
    const line = (graph.members ?? [graph.root]).map((member) => byId.get(member)).filter((node): node is MangaGraphNode => Boolean(node));
    const onLine = new Set(line);
    const others = graph.nodes.filter((node) => !onLine.has(node));
    // What the library has nearest the middle, then what is followed, then the rest; each by year.
    const rank = (node: MangaGraphNode) => (has(node) ? 0 : node.followed ? 1 : 2);
    const order = (a: MangaGraphNode, b: MangaGraphNode) => rank(a) - rank(b) || (a.year ?? 9999) - (b.year ?? 9999);
    const anime = others.filter((node) => node.type === 'ANIME').sort(order);
    const manga = others.filter((node) => node.type === 'MANGA').sort(order);
    const id = (node: MangaGraphNode) => node.type + '-' + node.anilist_id;
    const lineEnd = Math.max(0, line.length - 1) * (CARD_W + GAP_X);
    const columns = (items: MangaGraphNode[], side: 'left' | 'right'): Node<CardData, 'card'>[] => items.map((node, index) => {
      const column = Math.floor(index / ROWS);
      const inColumn = Math.min(ROWS, items.length - column * ROWS);
      const top = -((inColumn * (CARD_H + GAP_Y) - GAP_Y) - CARD_H) / 2;
      const out = CARD_W + GAP_X + column * (CARD_W + GAP_COLUMN);
      return {
        id: id(node),
        type: 'card',
        position: { x: side === 'left' ? -out : lineEnd + out, y: top + (index % ROWS) * (CARD_H + GAP_Y) },
        data: { node, root: false, side, selected: selected.includes(node.anilist_id) },
        draggable: false,
      };
    });
    const laid: Node<CardData, 'card'>[] = [
      ...line.map((node, index): Node<CardData, 'card'> => ({
        id: id(node),
        type: 'card',
        position: { x: index * (CARD_W + GAP_X), y: 0 },
        // The entry open now stands out; the graph is the collection's.
        data: { node, root: node.anilist_id === graph.root, side: 'root', selected: selected.includes(node.anilist_id) },
        draggable: false,
      })),
      ...columns(anime, 'left'),
      ...columns(manga, 'right'),
    ];
    const root = byId.get(graph.root);
    const placed = new Map(laid.map((item) => [item.data.node.anilist_id, item]));
    const lines: Edge[] = graph.edges.flatMap((link) => {
      const from = placed.get(link.from);
      const to = placed.get(link.to);
      if (!from || !to) return [];
      const node = to.data.node === root ? from.data.node : to.data.node;
      const followedBy = FOLLOWED_BY.has(link.label);
      const color = node.type === 'ANIME' ? 'var(--color-cyan-400)' : node.format === 'NOVEL' ? 'var(--color-violet-400)' : 'var(--color-orange-400)';
      // What the library does not have is drawn faint, line and all.
      const faint = has(from.data.node) && has(to.data.node) ? 1 : 0.35;
      const rightward = from.position.x <= to.position.x;
      return [{
        id: from.id + '>' + to.id,
        source: from.id,
        target: to.id,
        sourceHandle: rightward ? 'right-out' : 'left-out',
        targetHandle: rightward ? 'left-in' : 'right-in',
        label: RELATIONS[link.label] ?? link.label,
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: followedBy
          ? { stroke: color, strokeWidth: 2.5, opacity: faint }
          : { stroke: color, strokeWidth: 1.5, strokeDasharray: '6 5', opacity: faint },
        labelStyle: { fill: 'var(--foreground-soft)', fontSize: 11 },
        labelBgStyle: { fill: 'var(--card)' },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 6,
      }];
    });
    return { nodes: laid, edges: lines };
  }, [graph, selected]);

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(event, node) => {
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
