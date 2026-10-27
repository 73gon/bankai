import { useEffect, useState } from 'react';
import { Plus, Save, Sparkles, Trash2, Undo2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type AnimeArc, type AnimeArcSeason } from '@/lib/api';
import { Button } from '@/zon/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Input } from '@/zon/ui/input';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';

/** An entry's arcs as rows: each arc's first episode and name; its last is the next one's first - 1. */
function Rows({ arcs, total, onChange }: { arcs: AnimeArc[]; total: number; onChange: (arcs: AnimeArc[]) => void }) {
  function set(index: number, change: Partial<AnimeArc>) {
    onChange(arcs.map((arc, at) => (at === index ? { ...arc, ...change } : arc)));
  }
  return (
    <Table variant='plain' dense>
      <TableHeader>
        <TableRow>
          <TableHead className='w-16'>Season</TableHead>
          <TableHead className='w-28'>From episode</TableHead>
          <TableHead>Episodes</TableHead>
          <TableHead className='w-full'>Name</TableHead>
          <TableHead><span className='sr-only'>Actions</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {arcs.map((arc, index) => {
          const last = index + 1 < arcs.length ? arcs[index + 1].from - 1 : total;
          return (
            <TableRow key={index}>
              <TableCell className='font-mono text-xs text-muted-foreground'>+{index}</TableCell>
              <TableCell>
                <Input
                  size='sm'
                  type='number'
                  min={1}
                  aria-label={`First episode of arc ${index + 1}`}
                  value={arc.from}
                  disabled={index === 0}
                  onChange={(event) => set(index, { from: Number(event.target.value) || 1 })}
                />
              </TableCell>
              <TableCell className='whitespace-nowrap font-mono text-xs text-muted-foreground'>
                {arc.from}–{last || '…'} · {last ? Math.max(0, last - arc.from + 1) : '?'}
              </TableCell>
              <TableCell>
                <Input size='sm' aria-label={`Name of arc ${index + 1}`} placeholder='Arc name' value={arc.name} onChange={(event) => set(index, { name: event.target.value })} />
              </TableCell>
              <TableCell>
                <Button size='icon-sm' variant='ghost' aria-label={`Remove arc ${index + 1}`} disabled={index === 0} onClick={() => onChange(arcs.filter((_, at) => at !== index))}>
                  <Trash2 />
                </Button>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/**
 * A show's seasons split into arcs by hand: an entry that is one long season (Bleach's 366
 * episodes) becomes a season per arc, named, its episodes keeping their numbers (S02E21).
 */
export function SeasonArcsDialog({ collectionId, title, onClose, onChanged }: {
  collectionId: number | null;
  title: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [seasons, setSeasons] = useState<AnimeArcSeason[] | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [draft, setDraft] = useState<AnimeArc[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (collectionId === null) return;
    setSeasons(null);
    api.animeArcs(collectionId)
      .then((answer) => {
        setSeasons(answer.seasons);
        const first = answer.seasons.find((season) => season.arcs.length || season.suggested.length) ?? answer.seasons[0];
        setPicked(first?.anilist_id ?? null);
      })
      .catch((error: any) => toast.error(error.message));
  }, [collectionId]);

  const season = seasons?.find((row) => row.anilist_id === picked) ?? null;
  useEffect(() => {
    if (season) setDraft(season.arcs.length ? season.arcs : [{ from: 1, name: '' }]);
  }, [season]);

  const sorted = [...draft].sort((a, b) => a.from - b.from);
  const problem = draft.length > 1 || draft[0]?.name
    ? draft.some((arc) => !arc.name.trim())
      ? 'Every arc needs a name.'
      : draft.some((arc) => arc.name.includes('/'))
        ? 'A name cannot hold /.'
        : new Set(draft.map((arc) => arc.from)).size !== draft.length
          ? 'Two arcs start at the same episode.'
          : null
    : null;

  async function save(arcs: AnimeArc[]) {
    if (collectionId === null || !season) return;
    setSaving(true);
    try {
      const answer = await api.saveAnimeArcs(collectionId, season.anilist_id, arcs);
      setSeasons(answer.seasons);
      toast.success(arcs.length ? `${arcs.length} seasons; ${answer.moved} files moved into them` : `One season again; ${answer.moved} files moved`);
      onChanged();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={collectionId !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent size='xl' className='max-h-[90dvh]'>
        <DialogHeader>
          <DialogTitle>Seasons of {title}</DialogTitle>
          <DialogDescription>
            Split a long season into arcs: each becomes a season of its own with its name, its episodes keeping their numbers
            (episode 21 becomes S02E21). The seasons after it move up. Saving moves the files at once, and Jellyfin may forget which of
            the moved episodes were watched.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className='flex flex-col gap-3'>
          {seasons === null ? <Skeleton className='h-48 w-full' /> : seasons.length === 0 ? (
            <p className='text-sm text-muted-foreground'>No season of this show is a single entry that could be split.</p>
          ) : (
            <>
              {seasons.length > 1 && (
                <ToggleGroup<string> aria-label='Season to split' value={[String(picked)]} onValueChange={(values) => values[0] && setPicked(Number(values[0]))}>
                  {seasons.map((row) => (
                    <ToggleGroupItem<string> key={row.anilist_id} value={String(row.anilist_id)}>Season {row.season} · {row.title}</ToggleGroupItem>
                  ))}
                </ToggleGroup>
              )}
              {season && (
                <>
                  <p className='text-xs text-muted-foreground'>
                    {season.title}: {season.episodes} episodes, {season.on_disk} on disk; now season {season.season}
                    {season.arcs.length ? `, split into ${season.arcs.length} arcs` : ', one season'}.
                  </p>
                  <Rows arcs={draft} total={season.episodes} onChange={setDraft} />
                  <div className='flex flex-wrap items-center gap-2'>
                    <Button size='sm' variant='secondary' onClick={() => setDraft([...sorted, { from: (sorted[sorted.length - 1]?.from ?? 0) + 1, name: '' }])}>
                      <Plus data-icon='inline-start' /> Add arc
                    </Button>
                    {season.suggested.length > 0 && (
                      <Button size='sm' variant='secondary' onClick={() => setDraft(season.suggested)} title='The arcs of the official season split, to check and adjust'>
                        <Sparkles data-icon='inline-start' /> Fill in {season.suggested.length} known arcs
                      </Button>
                    )}
                    {problem && <span className='text-xs text-destructive'>{problem}</span>}
                  </div>
                </>
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          {season && season.arcs.length > 0 && (
            <Button variant='ghost' className='mr-auto' disabled={saving} onClick={() => void save([])}>
              <Undo2 data-icon='inline-start' /> One season again
            </Button>
          )}
          <Button variant='secondary' onClick={onClose}>Close</Button>
          <Button disabled={!season || saving || Boolean(problem) || !draft[0]?.name.trim()} onClick={() => void save(sorted)}>
            <Save data-icon='inline-start' /> {saving ? 'Moving files…' : 'Save and move files'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
