import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Pencil, RefreshCw, Search, Undo2 } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import { api, type AgeRatingPlan, type AgeRatingTitle } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@zonsh/ui';
import { Button } from '@zonsh/ui';
import { Checkbox } from '@zonsh/ui';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@zonsh/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Skeleton } from '@zonsh/ui';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@zonsh/ui';
import { ToggleGroup, ToggleGroupItem } from '@zonsh/ui';

type Kind = 'all' | 'anime' | 'show';
type From = 'all' | 'de' | 'estimate' | 'hand' | 'none' | 'search' | 'due';

const FROM_ITEMS: Array<{ value: From; label: string }> = [
  { value: 'all', label: 'Every source' },
  { value: 'due', label: 'Not written yet' },
  { value: 'de', label: 'German rating' },
  { value: 'estimate', label: 'Estimated' },
  { value: 'hand', label: 'Set by hand' },
  { value: 'none', label: 'No rating' },
  { value: 'search', label: 'Matched by name' },
];

const HAND_ITEMS = [
  { value: '', label: 'As found' },
  ...[0, 6, 12, 16, 18].map((age) => ({ value: String(age), label: `FSK ${age}` })),
];

/** FSK's own colours, as the badges on the box: white 0, yellow 6, green 12, blue 16, red 18. */
const FSK_TINT: Record<number, string> = {
  0: 'border-foreground/30 bg-foreground/10 text-foreground',
  6: 'border-yellow-400/40 bg-yellow-400/15 text-yellow-200',
  12: 'border-green-500/40 bg-green-500/15 text-green-300',
  16: 'border-sky-500/40 bg-sky-500/15 text-sky-300',
  18: 'border-red-500/40 bg-red-500/15 text-red-300',
};

function Fsk({ rating }: { rating: string | null }) {
  const age = rating?.match(/^FSK-(\d+)$/)?.[1];
  if (!rating) return <span className='text-xs text-muted-foreground'>—</span>;
  return (
    <span className={cn('inline-flex h-[22px] items-center rounded-item border px-2 font-mono text-[11px] whitespace-nowrap', age != null ? FSK_TINT[Number(age)] : 'border-field-edge text-foreground-soft')}>
      {age != null ? `FSK ${age}` : rating}
    </span>
  );
}

function Source({ row }: { row: AgeRatingTitle }) {
  if (row.source === 'de') return <StatusTag variant='success' title={`TMDB's German rating: ${row.basis}`}>German</StatusTag>;
  if (row.source === 'hand') return <StatusTag variant='info'>By hand</StatusTag>;
  if (row.source === 'adult') return <StatusTag variant='warning' title='AniList files it under Hentai'>Adult</StatusTag>;
  if (row.source === 'estimate') {
    return <StatusTag variant='warning' title={`No German rating on TMDB; estimated from ${row.basis}`}>Estimate · {row.basis}</StatusTag>;
  }
  const others = Object.entries(row.ratings).map(([country, value]) => `${country} ${value}`).join(', ');
  return <StatusTag variant='destructive' title={others ? `TMDB has only: ${others}` : 'TMDB has no rating'}>{row.tmdb_id ? 'None' : 'Not on TMDB'}</StatusTag>;
}

/** The TMDB show a title was matched to, with a pen to set it by hand. */
function TmdbMatch({ row, onSaved }: { row: AgeRatingTitle; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  async function save(id: number | null) {
    try {
      await api.setAgeRatingTmdb(row.key, id);
      setOpen(false);
      onSaved();
    } catch (error: any) {
      toast.error(error.message);
    }
  }
  const guess = row.match === 'name search';
  return (
    <div className='flex min-w-0 items-center gap-1.5'>
      {row.tmdb_id ? (
        <a className='min-w-0 truncate text-sm hover:underline' href={`https://www.themoviedb.org/tv/${row.tmdb_id}`} target='_blank' rel='noreferrer' title={`TMDB ${row.tmdb_id} · ${row.match ?? ''}`}>
          {row.tmdb_name ?? row.tmdb_id} <ExternalLink className='inline size-3 text-muted-foreground' />
        </a>
      ) : (
        <span className='text-xs text-muted-foreground'>not found</span>
      )}
      {guess && <StatusTag variant='warning' title='Found by its name on TMDB: check it is the right show'>guess</StatusTag>}
      <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next) setValue(row.tmdb_id ? String(row.tmdb_id) : ''); }}>
        <PopoverTrigger render={<Button size='icon-sm' variant='ghost' aria-label={`Set the TMDB show of ${row.name}`} className='size-[22px] shrink-0' />}>
          <Pencil className='size-3' />
        </PopoverTrigger>
        <PopoverContent align='start' className='flex w-64 flex-col gap-2 p-3'>
          <p className='text-xs text-muted-foreground'>The TMDB id of the show (the number in its address on themoviedb.org/tv/…).</p>
          <Input value={value} inputMode='numeric' onChange={(event) => setValue(event.target.value.replace(/\D/g, ''))} onKeyDown={(event) => { if (event.key === 'Enter' && value) void save(Number(value)); }} />
          <div className='flex justify-end gap-2'>
            <Button size='sm' variant='ghost' onClick={() => void save(null)}>Match again</Button>
            <Button size='sm' disabled={!value} onClick={() => void save(Number(value))}>Save</Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}

/**
 * German age ratings of anime and shows: what bankai would write (TMDB's German rating, else an
 * estimate from another country's, else by hand), reviewed here, written only when asked -- anime
 * into tvshow.nfo, shows into Jellyfin.
 */
export function AgeRatingsPanel() {
  const [plan, setPlan] = useState<AgeRatingPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<Kind>('all');
  const [from, setFrom] = useState<From>('all');
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<string[] | null>(null);

  async function load() {
    setLoading(true);
    try {
      setPlan(await api.ageRatings());
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, []);

  const rows = useMemo(() => {
    const words = query.trim().toLowerCase();
    return (plan?.titles ?? []).filter((row) =>
      (kind === 'all' || row.kind === kind)
      && (from === 'all'
        || (from === 'none' ? !row.rating : from === 'search' ? row.match === 'name search' : from === 'due' ? row.due : row.source === from))
      && (!words || row.name.toLowerCase().includes(words) || (row.tmdb_name ?? '').toLowerCase().includes(words)),
    );
  }, [plan, kind, from, query]);

  const titles = plan?.titles ?? [];
  const count = (test: (row: AgeRatingTitle) => boolean) => titles.filter(test).length;
  const due = titles.filter((row) => row.due);
  const pickedRows = titles.filter((row) => picked.has(row.key));

  async function setHand(row: AgeRatingTitle, value: string) {
    try {
      await api.setAgeRating(row.key, value === '' ? null : Number(value));
      await load();
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  async function write(keys: string[]) {
    // The confirmation closes at once; a toast follows the writing.
    setConfirm(null);
    setPicked(new Set());
    void inBackground(api.applyAgeRatings(keys), {
      loading: `Writing ${keys.length} age rating${keys.length === 1 ? '' : 's'}…`,
      success: (result) => `${result.anime} anime and ${result.shows} show${result.shows === 1 ? '' : 's'} written`
        + (result.failed.length ? ` · failed: ${result.failed.join(', ')}` : ''),
    }).then(() => load(), () => undefined);
  }

  async function undo(keys: string[]) {
    setBusy(true);
    try {
      const result = await api.undoAgeRatings(keys);
      toast.success(`${result.undone} taken back`);
      setPicked(new Set());
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  if (!plan) {
    return loading ? <Skeleton className='h-64 w-full' /> : null;
  }
  const allShown = rows.length > 0 && rows.every((row) => picked.has(row.key));
  return (
    <div className='flex flex-col gap-3'>
      <p className='text-xs text-muted-foreground'>
        FSK ratings for every anime and show, as Jellyfin shows German films ("FSK-16"). From TMDB's German rating where it has one;
        else estimated from another country's (US, then AU, BR, KR) and marked so; set by hand where neither fits. Nothing is written
        until you write it: anime into their tvshow.nfo, shows into Jellyfin (locked, so a refresh keeps it).{' '}
        <span className='font-mono tabular-nums text-foreground'>{count((row) => row.source === 'de')}</span> German,{' '}
        <span className='font-mono tabular-nums text-foreground'>{count((row) => row.source === 'estimate' || row.source === 'adult')}</span> estimated,{' '}
        <span className='font-mono tabular-nums text-foreground'>{count((row) => row.source === 'hand')}</span> by hand,{' '}
        <span className={cn('font-mono tabular-nums', count((row) => !row.rating) ? 'text-warning' : 'text-foreground')}>{count((row) => !row.rating)}</span> without;{' '}
        <span className='font-mono tabular-nums text-foreground'>{count((row) => !!row.written)}</span> written.
      </p>
      <div className='flex flex-wrap items-center gap-2'>
        <InputGroup className='w-64'>
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput placeholder='Filter titles…' value={query} onChange={(event) => setQuery(event.target.value)} />
        </InputGroup>
        <ToggleGroup<Kind> aria-label='Library' value={[kind]} onValueChange={(values) => values[0] && setKind(values[0])}>
          <ToggleGroupItem<Kind> value='all'>All</ToggleGroupItem>
          <ToggleGroupItem<Kind> value='anime'>Anime</ToggleGroupItem>
          <ToggleGroupItem<Kind> value='show'>Shows</ToggleGroupItem>
        </ToggleGroup>
        <Select items={FROM_ITEMS} value={from} onValueChange={(value) => setFrom((value ?? 'all') as From)}>
          <SelectTrigger className='w-48 shrink-0' aria-label='Source'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {FROM_ITEMS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
        <span className='text-xs text-muted-foreground'>{rows.length} shown</span>
        <div className='ml-auto flex items-center gap-2'>
          <Button size='sm' variant='ghost' disabled={loading} onClick={() => void load()}><RefreshCw className={cn(loading && 'animate-spin')} /> Reload</Button>
          {pickedRows.some((row) => row.written) && (
            <Button size='sm' variant='secondary' disabled={busy} onClick={() => void undo(pickedRows.filter((row) => row.written).map((row) => row.key))}>
              <Undo2 /> Take back {pickedRows.filter((row) => row.written).length}
            </Button>
          )}
          <Button size='sm' variant='secondary' disabled={busy || !pickedRows.some((row) => row.rating)} onClick={() => setConfirm(pickedRows.filter((row) => row.rating).map((row) => row.key))}>
            Write {pickedRows.filter((row) => row.rating).length} ticked
          </Button>
          <Button size='sm' disabled={busy || due.length === 0} onClick={() => setConfirm(due.map((row) => row.key))}>
            Write all {due.length}
          </Button>
        </div>
      </div>
      <Table variant='plain' dense>
        <TableHeader>
          <TableRow>
            <TableHead className='w-8'>
              <Checkbox checked={allShown} aria-label='Tick every title shown' onCheckedChange={(checked) => setPicked(checked ? new Set([...picked, ...rows.map((row) => row.key)]) : new Set([...picked].filter((key) => !rows.some((row) => row.key === key))))} />
            </TableHead>
            <TableHead>Title</TableHead>
            <TableHead>TMDB</TableHead>
            <TableHead>Rating</TableHead>
            <TableHead>From</TableHead>
            <TableHead>Active</TableHead>
            <TableHead>By hand</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.key}>
              <TableCell className='w-8'>
                <Checkbox checked={picked.has(row.key)} aria-label={`Tick ${row.name}`} onCheckedChange={(checked) => {
                  const next = new Set(picked);
                  if (checked) next.add(row.key); else next.delete(row.key);
                  setPicked(next);
                }} />
              </TableCell>
              <TableCell>
                <div className='flex min-w-0 flex-col'>
                  <span className='truncate text-sm'>{row.name}</span>
                  <span className='font-mono text-[0.68rem] text-muted-foreground'>{row.kind === 'anime' ? 'Anime' : 'Show'}{row.year ? ` · ${row.year}` : ''}</span>
                </div>
              </TableCell>
              <TableCell className='max-w-72'><TmdbMatch row={row} onSaved={() => void load()} /></TableCell>
              <TableCell><Fsk rating={row.rating} /></TableCell>
              <TableCell><Source row={row} /></TableCell>
              <TableCell>
                <span className={cn('font-mono text-xs', row.current && row.current === row.rating ? 'text-muted-foreground' : 'text-foreground-soft')} title={row.kind === 'anime' ? 'Active now: what bankai wrote into tvshow.nfo' : 'Active now: the rating Jellyfin shows'}>
                  {row.current ?? '—'}
                </span>
              </TableCell>
              <TableCell>
                <Select items={HAND_ITEMS} value={row.source === 'hand' && row.age != null ? String(row.age) : ''} onValueChange={(value) => void setHand(row, value ?? '')}>
                  <SelectTrigger className='h-7 w-28' aria-label={`Rating of ${row.name} by hand`}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {HAND_ITEMS.map((item) => <SelectItem key={item.value || 'found'} value={item.value}>{item.label}</SelectItem>)}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={confirm !== null} onOpenChange={(open) => { if (!open) setConfirm(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Write {confirm?.length ?? 0} age rating{confirm?.length === 1 ? '' : 's'}?</DialogTitle>
            <DialogDescription>
              {(() => {
                const chosen = titles.filter((row) => confirm?.includes(row.key));
                const anime = chosen.filter((row) => row.kind === 'anime').length;
                const estimated = chosen.filter((row) => row.source === 'estimate' || row.source === 'adult').length;
                return `${anime} anime get the rating in their tvshow.nfo, ${chosen.length - anime} show${chosen.length - anime === 1 ? '' : 's'} in Jellyfin (locked). ${estimated} of them are estimates. Each can be taken back.`;
              })()}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant='ghost' onClick={() => setConfirm(null)}>Cancel</Button>
            <Button disabled={busy} onClick={() => confirm && void write(confirm)}>Write</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
