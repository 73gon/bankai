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
import { ExternalLink, Merge, Network, Plus, Scissors, Star } from 'lucide-react';
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
const CARD_H = 340;
const GAP_X = 96;
const GAP_Y = 72;

type EntryData = { node: CollectionNode; selected: boolean };
type EntryFlowNode = Node<EntryData, 'entry'>;

/** Air date, else year, else last: the order a show's seasons came out in. */
function aired(node: CollectionNode) {
  return node.first_aired ?? (node.year ? String(node.year) : '9999');
}

function meta(node: CollectionNode) {
  const parts = [node.type, node.year ? String(node.year) : null];
  if (node.owned) parts.push(`${node.on_disk}/${node.episodes ?? '?'} on disk`);
  else parts.push(node.episodes ? `${node.episodes} eps · not downloaded` : 'not downloaded');
  return parts.filter(Boolean).join(' · ');
}

/**
 * Where each entry goes: the sequel chain the collection is named after runs
 * left to right as the spine, in sequel order; everything else -- side
 * stories, recaps, movies, a sequel chain of their own -- hangs below the
 * spine entry it is related to, whether it is in the collection or not.
 */
function layout(graph: Graph): Map<number, { x: number; y: number }> {
  const ids = graph.nodes.map((node) => node.anidb_id);
  const byId = new Map(graph.nodes.map((node) => [node.anidb_id, node]));
  const sequelsOf = new Map<number, number[]>(ids.map((id) => [id, []]));
  const prequelsOf = new Map<number, number[]>(ids.map((id) => [id, []]));
  const linked = new Map<number, number[]>(ids.map((id) => [id, []]));
  for (const edge of graph.edges) {
    linked.get(edge.from)?.push(edge.to);
    linked.get(edge.to)?.push(edge.from);
    if (edge.label === 'sequel') {
      sequelsOf.get(edge.from)?.push(edge.to);
      prequelsOf.get(edge.to)?.push(edge.from);
    }
  }
  // The spine: the sequel chain holding the title entry.
  const start = graph.title_entry ?? graph.nodes.find((node) => node.in_collection)?.anidb_id ?? ids[0];
  const spine = new Set<number>();
  const walk = [start];
  while (walk.length) {
    const id = walk.pop()!;
    if (spine.has(id) || !byId.has(id)) continue;
    spine.add(id);
    walk.push(...sequelsOf.get(id)!, ...prequelsOf.get(id)!);
  }
  // Columns in sequel order: each entry one after its latest prequel.
  const column = new Map<number, number>();
  const order = [...spine].sort((a, b) => aired(byId.get(a)!).localeCompare(aired(byId.get(b)!)) || a - b);
  for (let pass = 0; pass < order.length + 1; pass += 1) {
    let changed = false;
    for (const id of order) {
      const before = prequelsOf.get(id)!.filter((other) => spine.has(other) && column.has(other));
      const col = before.length ? Math.max(...before.map((other) => column.get(other)!)) + 1 : column.get(id) ?? 0;
      if (column.get(id) !== col) {
        column.set(id, col);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const positions = new Map<number, { x: number; y: number }>();
  const used = new Map<number, number>(); // column -> rows taken below the spine
  const above = new Map<number, number>(); // column -> parallel sequels stacked above
  for (const id of order) {
    const col = column.get(id)!;
    const stacked = above.get(col) ?? 0;
    above.set(col, stacked + 1);
    positions.set(id, { x: col * (CARD_W + GAP_X), y: -stacked * (CARD_H + GAP_Y) });
  }
  // Everything else hangs off the spine entry it is reached from first.
  const anchor = new Map<number, number>();
  const queue = [...order];
  for (const id of order) anchor.set(id, id);
  while (queue.length) {
    const id = queue.shift()!;
    const others = [...linked.get(id)!].sort((a, b) => aired(byId.get(a)!).localeCompare(aired(byId.get(b)!)) || a - b);
    for (const other of others) {
      if (anchor.has(other)) continue;
      anchor.set(other, anchor.get(id)!);
      queue.push(other);
    }
  }
  const lastColumn = Math.max(0, ...column.values());
  let loose = 0;
  for (const node of [...graph.nodes].sort((a, b) => aired(a).localeCompare(aired(b)) || a.anidb_id - b.anidb_id)) {
    if (positions.has(node.anidb_id)) continue;
    const root = anchor.get(node.anidb_id);
    const col = root !== undefined ? column.get(root)! : lastColumn + 1 + loose++;
    const row = (used.get(col) ?? 0) + 1;
    used.set(col, row);
    positions.set(node.anidb_id, { x: col * (CARD_W + GAP_X), y: row * (CARD_H + GAP_Y) });
  }
  return positions;
}

const hidden = '!h-2 !w-2 !min-h-0 !min-w-0 !border-0 !bg-transparent';

function EntryCard({ data }: NodeProps<EntryFlowNode>) {
  const { node, selected } = data;
  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-xl border bg-card p-2 shadow-lg transition-shadow',
        node.in_collection ? 'border-border' : 'border-dashed border-muted-foreground/40 bg-card/70',
        node.title_entry && 'ring-2 ring-primary/70',
        selected && 'ring-2 ring-info',
      )}
      style={{ width: CARD_W, height: CARD_H }}
      title={node.main_title && node.main_title !== node.title ? node.main_title : node.title}
    >
      {(['left', 'right', 'top', 'bottom'] as const).map((side) => (
        <Handle key={side} id={side} type='source' position={Position[side === 'left' ? 'Left' : side === 'right' ? 'Right' : side === 'top' ? 'Top' : 'Bottom']} isConnectable={false} className={hidden} />
      ))}
      <AnimePoster url={node.poster_url} title={node.title} className={cn('w-full rounded-lg', !node.in_collection && 'opacity-70')} />
      <div className='flex min-h-0 flex-1 flex-col gap-1'>
        <span className='line-clamp-2 text-[0.8rem] font-medium leading-tight text-foreground'>
          {node.title_entry && <Star className='mr-1 inline size-3.5 -translate-y-px fill-current text-primary' aria-label='Title entry' />}
          {node.title}
        </span>
        <span className='truncate font-mono text-[0.62rem] text-muted-foreground'>{meta(node)}</span>
        <span className='mt-auto flex flex-wrap gap-1'>
          {!node.in_collection && node.collection_name && <Badge variant='info' className='max-w-full truncate text-[0.6rem]'>In: {node.collection_name}</Badge>}
          {!node.in_collection && !node.collection_name && <Badge variant='muted' className='text-[0.6rem]'>{node.alone ? 'In no collection' : 'Not in this collection'}</Badge>}
        </span>
      </div>
    </div>
  );
}

const nodeTypes = { entry: EntryCard };

/** Which sides of two cards a link joins: side to side within a row, else bottom to top. */
function sides(from: { x: number; y: number }, to: { x: number; y: number }) {
  if (from.y === to.y) return from.x < to.x ? ['right', 'left'] : ['left', 'right'];
  return from.y < to.y ? ['bottom', 'top'] : ['top', 'bottom'];
}

/** The graph itself: laid out once per loaded graph, then free to pan, zoom and drag. */
function Canvas({ graph, selected, onToggle }: { graph: Graph; selected: number[]; onToggle: (id: number) => void }) {
  const positions = useMemo(() => layout(graph), [graph]);
  const [nodes, , onNodesChange] = useNodesState<EntryFlowNode>(
    graph.nodes.map((node) => ({
      id: String(node.anidb_id),
      type: 'entry',
      position: positions.get(node.anidb_id) ?? { x: 0, y: 0 },
      data: { node, selected: false },
    })),
  );
  const shown = useMemo(
    () => nodes.map((item) => ({ ...item, data: { ...item.data, selected: selected.includes(Number(item.id)) } })),
    [nodes, selected],
  );
  const edges = useMemo<Edge[]>(() => graph.edges.map((item) => {
    const from = positions.get(item.from) ?? { x: 0, y: 0 };
    const to = positions.get(item.to) ?? { x: 0, y: 0 };
    const [sourceHandle, targetHandle] = sides(from, to);
    const spine = item.label === 'sequel' && from.y === to.y;
    return {
      id: `${item.from}-${item.to}-${item.label}`,
      source: String(item.from),
      target: String(item.to),
      sourceHandle,
      targetHandle,
      type: spine ? 'straight' : 'smoothstep',
      label: spine ? undefined : item.label,
      markerEnd: item.label === 'sequel' || item.label === 'side story' || item.label === 'summary'
        ? { type: MarkerType.ArrowClosed, width: 18, height: 18, color: spine ? 'var(--foreground-soft)' : 'var(--muted-foreground)' }
        : undefined,
      style: spine
        ? { stroke: 'var(--foreground-soft)', strokeWidth: 2.5 }
        : { stroke: 'var(--muted-foreground)', strokeWidth: 1.5, strokeDasharray: '6 5' },
      labelStyle: { fill: 'var(--foreground-soft)', fontSize: 11 },
      labelBgStyle: { fill: 'var(--card)' },
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 6,
    };
  }), [graph, positions]);

  return (
    <ReactFlow
      nodes={shown}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_event, node) => onToggle(Number(node.id))}
      connectionMode={ConnectionMode.Loose}
      nodesConnectable={false}
      elementsSelectable={false}
      colorMode='dark'
      fitView
      fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
      minZoom={0.15}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
      style={{ background: 'transparent' }}
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color='var(--muted-foreground)' style={{ opacity: 0.25 }} />
      <Controls showInteractive={false} position='bottom-left' />
    </ReactFlow>
  );
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
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  // A merge moves a whole collection: the button asks once more before it does.
  const [armed, setArmed] = useState<number | null>(null);

  async function load(id: number) {
    setLoading(true);
    try {
      setGraph(await api.collectionGraph(id));
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
    if (groupId !== null) void load(groupId);
  }, [groupId]);

  const byId = useMemo(() => new Map((graph?.nodes ?? []).map((node) => [node.anidb_id, node])), [graph]);
  const picked = selected.map((id) => byId.get(id)).filter((node): node is CollectionNode => Boolean(node));
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

  return (
    <Dialog open={groupId !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className='flex h-[90dvh] w-[min(97vw,1600px)] max-w-none flex-col gap-3'>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'><Network className='size-4' /> {graph?.name ?? 'Collection'}</DialogTitle>
          <DialogDescription>
            The sequel chain runs left to right; side stories, recaps and other related entries hang below the entry they belong to,
            dashed when they are not in this collection. Drag to move around, scroll or pinch to zoom, click entries to change them.
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
              <div className='flex flex-col gap-2 text-xs text-muted-foreground'>
                <p className='text-sm text-foreground'>Click entries to change the collection.</p>
                <p><span className='text-foreground'>Solid cards</span> are in this collection: name it after one, or split entries off into a collection of their own.</p>
                <p><span className='text-foreground'>Dashed cards</span> are related but not in it: pull one in, or merge the whole collection it is in.</p>
                <p>The ★ entry names and pictures the collection, here and in Jellyfin.</p>
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
              </>
            )}
          </aside>
        </div>
      </DialogContent>
    </Dialog>
  );
}
