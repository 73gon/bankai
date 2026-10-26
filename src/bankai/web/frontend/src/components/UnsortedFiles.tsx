import { useEffect, useRef, useState } from 'react';
import { ExternalLink, FileQuestion, Link2, Search, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type AniListAnime, type UnsortedFile, type UnsortedGroup, type UnsortedSuggestion } from '@/lib/api';
import { anilistName, anilistUrl, formatLabel } from '@/lib/anilist';
import { AniListPickDialog } from '@/components/AniListPickDialog';
import { AnimePoster } from '@/components/AnimePoster';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Card } from '@/zon/ui/card';
import { Checkbox } from '@/zon/ui/checkbox';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Input } from '@/zon/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 2 : 0)} ${units[index]}`;
}

function marker(file: UnsortedFile) {
  if (file.episode == null) return null;
  return file.season != null
    ? 'S' + String(file.season).padStart(2, '0') + 'E' + String(file.episode).padStart(2, '0')
    : 'E' + file.episode;
}

/** The numbers files get when an entry is picked: their own, where they fit it; else 1, 2, 3… */
function defaultNumbers(files: UnsortedFile[], entry: UnsortedSuggestion | null): string[] {
  const own = files.map((file) => file.episode);
  const fits = own.every((number) => number != null && number > 0)
    && new Set(own).size === own.length
    && (!entry?.episodes || own.every((number) => number! <= entry.episodes!));
  return files.map((file, index) => String(fits ? file.episode : index + 1));
}

type Matching = { group: UnsortedGroup; files: UnsortedFile[] };

/**
 * Files of downloads bankai could not tell the episode of -- OVAs and a film in a
 * show's batch, extras with no number -- matched by hand as in Shoko: tick files,
 * pick the AniList entry, adjust the numbers.
 */
export function UnsortedFiles({ onChanged, onCount }: { onChanged?: () => void; onCount?: (files: number) => void }) {
  const [groups, setGroups] = useState<UnsortedGroup[]>([]);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [matching, setMatching] = useState<Matching | null>(null);
  const [entries, setEntries] = useState<UnsortedSuggestion[]>([]);
  const [entry, setEntry] = useState<UnsortedSuggestion | null>(null);
  const [first, setFirst] = useState('1');
  const [numbers, setNumbers] = useState<string[]>([]);
  // Episodes the library has already, once asked: matching then replaces them.
  const [taken, setTaken] = useState<number[] | null>(null);
  const [searchFor, setSearchFor] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState<Matching | null>(null);
  // The last box ticked, for shift-click ranges.
  const anchor = useRef<string | null>(null);
  const shift = useRef(false);

  async function load() {
    try {
      const result = await api.animeUnsorted();
      setGroups(result.groups);
      onCount?.(result.groups.reduce((sum, group) => sum + group.files.length, 0));
      const ids = new Set(result.groups.flatMap((group) => group.files.map((file) => file.id)));
      setPicked((current) => new Set([...current].filter((id) => ids.has(id))));
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => { void load(); }, []);

  function toggle(group: UnsortedGroup, file: UnsortedFile, on: boolean) {
    const ids = group.files.map((item) => item.id);
    let range = [file.id];
    if (shift.current && anchor.current && ids.includes(anchor.current)) {
      const [from, to] = [ids.indexOf(anchor.current), ids.indexOf(file.id)].sort((a, b) => a - b);
      range = ids.slice(from, to + 1);
    }
    anchor.current = file.id;
    setPicked((current) => {
      const next = new Set(current);
      for (const id of range) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function chosen(group: UnsortedGroup) {
    return group.files.filter((file) => picked.has(file.id));
  }

  function pickEntry(next: UnsortedSuggestion, files = matching?.files ?? []) {
    setEntry(next);
    setTaken(null);
    const defaults = defaultNumbers(files, next);
    setNumbers(defaults);
    setFirst(defaults[0] ?? '1');
  }

  function openMatch(group: UnsortedGroup) {
    const files = chosen(group);
    if (!files.length) return;
    setMatching({ group, files });
    setEntries(group.suggestions);
    setEntry(null);
    setTaken(null);
    const defaults = defaultNumbers(files, null);
    setNumbers(defaults);
    setFirst(defaults[0] ?? '1');
  }

  /** A new first episode numbers every file on from it. */
  function changeFirst(value: string) {
    setFirst(value);
    setTaken(null);
    const start = Number(value);
    if (!matching || !value.trim() || !Number.isInteger(start) || start < 0) return;
    setNumbers(matching.files.map((_, index) => String(start + index)));
  }

  async function searched(anime: AniListAnime) {
    const found: UnsortedSuggestion = {
      anilist_id: anime.anilist_id,
      title: anilistName(anime),
      format: anime.format,
      episodes: anime.episodes,
      year: anime.year,
      cover: anime.cover,
      relation: 'Searched',
    };
    setEntries((current) => [found, ...current.filter((item) => item.anilist_id !== found.anilist_id)]);
    pickEntry(found);
    setSearchFor(null);
  }

  async function match() {
    const target = matching;
    if (!target || !entry) return;
    const values = numbers.map((value) => Number(value));
    if (values.some((value, index) => !numbers[index].trim() || !Number.isInteger(value) || value < 0)) {
      toast.error('Every file needs a whole episode number');
      return;
    }
    if (new Set(values).size !== values.length) {
      toast.error('Two files cannot be the same episode');
      return;
    }
    setBusy(true);
    try {
      if (taken === null) {
        const check = await api.checkUnsorted(entry.anilist_id, values);
        if (check.taken.length) {
          // Asked once: the button now says it replaces them.
          setTaken(check.taken);
          return;
        }
      }
      const ids = target.files.map((file) => file.id);
      const result = await api.matchUnsorted(ids, entry.anilist_id, values, Boolean(taken?.length), `Match ${ids.length} files to ${entry.title}`);
      toast.success(
        result.filed + ' file' + (result.filed === 1 ? '' : 's') + ' matched to ' + entry.title
        + (result.replaced ? ' · ' + result.replaced + ' replaced' : '') + '; they are in the library now',
      );
      setMatching(null);
      await load();
      onChanged?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    const target = discarding;
    if (!target) return;
    setBusy(true);
    try {
      const result = await api.discardUnsorted(target.files.map((file) => file.id));
      toast.success(result.deleted + ' file' + (result.deleted === 1 ? '' : 's') + ' deleted');
      setDiscarding(null);
      await load();
      onChanged?.();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  if (!groups.length) return null;
  return (
    <section className='flex flex-col gap-3'>
      <div className='flex flex-col gap-1'>
        <h2 className='font-heading text-xl font-light'>Files to match</h2>
        <p className='text-sm text-muted-foreground'>
          Files of a download that are no episode bankai could tell: specials, films, extras. Tick files (shift-click for a range),
          pick the AniList entry, adjust the numbers; they go into the library from there.
        </p>
      </div>
      {groups.map((group) => {
        const selected = chosen(group);
        const ids = group.files.map((file) => file.id);
        const ticked = ids.filter((id) => picked.has(id)).length;
        return (
          <Card key={group.key} className='gap-0 overflow-hidden [--card-padding:0]'>
            <div className='flex flex-wrap items-start justify-between gap-3 border-b border-border/60 px-4 py-3'>
              <div className='min-w-0'>
                <p className='break-words font-mono text-xs text-foreground'>{group.release_title}</p>
                <p className='mt-0.5 text-xs text-muted-foreground'>
                  {group.files.length} file{group.files.length === 1 ? '' : 's'} · {formatBytes(group.size)}
                  {group.anilist_title ? ' · downloaded as ' + group.anilist_title : ''}
                </p>
              </div>
              <div className='flex flex-wrap items-center gap-1.5'>
                {group.detail_url && (
                  <Button size='sm' variant='ghost' nativeButton={false} render={<a href={group.detail_url} target='_blank' rel='noreferrer' />}>
                    <ExternalLink data-icon='inline-start' /> Nyaa
                  </Button>
                )}
                <Button size='sm' variant='secondary' disabled={!selected.length || busy} onClick={() => setDiscarding({ group, files: selected })}>
                  <Trash2 data-icon='inline-start' /> Delete
                </Button>
                <Button size='sm' disabled={!selected.length || busy} onClick={() => openMatch(group)}>
                  <Link2 data-icon='inline-start' /> {selected.length ? `Match ${selected.length} file${selected.length === 1 ? '' : 's'}…` : 'Match…'}
                </Button>
              </div>
            </div>
            <Table variant='plain' dense containerProps={{ className: 'max-h-96 overflow-auto' }}>
              <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
                <TableRow>
                  <TableHead className='w-7 pr-2'>
                    <Checkbox
                      nativeButton
                      render={<button type='button' />}
                      className='align-middle'
                      aria-label='Select every file of this download'
                      checked={ticked === ids.length}
                      indeterminate={ticked > 0 && ticked < ids.length}
                      onCheckedChange={(on) => setPicked((current) => {
                        const next = new Set(current);
                        for (const id of ids) {
                          if (on) next.add(id);
                          else next.delete(id);
                        }
                        return next;
                      })}
                    />
                  </TableHead>
                  <TableHead className='w-full'>File</TableHead>
                  <TableHead>Name says</TableHead>
                  <TableHead>Why it waits</TableHead>
                  <TableHead numeric>Size</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.files.map((file) => (
                  <TableRow key={file.id} data-selected={picked.has(file.id) ? '' : undefined}>
                    <TableCell className='w-7 pr-2'>
                      <Checkbox
                        nativeButton
                        render={<button type='button' onClickCapture={(event) => { shift.current = event.shiftKey; }} />}
                        className='align-middle'
                        aria-label={'Select ' + file.name}
                        checked={picked.has(file.id)}
                        onCheckedChange={(on) => toggle(group, file, on)}
                      />
                    </TableCell>
                    <TableCell className='max-w-0'>
                      <p className='truncate font-mono text-[0.7rem] text-foreground' title={file.name}>{file.name}</p>
                      {file.folder && <p className='truncate text-[0.65rem] text-muted-foreground'>{file.folder}/</p>}
                    </TableCell>
                    <TableCell className='font-mono text-xs tabular-nums text-muted-foreground'>{marker(file) ?? '—'}</TableCell>
                    <TableCell className='whitespace-nowrap text-xs text-muted-foreground'>{file.reason}</TableCell>
                    <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(file.size)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        );
      })}

      <Dialog open={matching !== null} onOpenChange={(open) => { if (!open && !busy) setMatching(null); }}>
        <DialogContent size='xl' className='max-h-[90dvh]'>
          <DialogHeader>
            <DialogTitle>Match {matching?.files.length} file{matching?.files.length === 1 ? '' : 's'}</DialogTitle>
            <DialogDescription>
              Pick the AniList entry they are, then check each file&apos;s episode. An entry that is in no show yet, an OVA or a film,
              joins the show this download was of, in its Specials.
            </DialogDescription>
          </DialogHeader>
          {matching && (
            <DialogBody className='grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]'>
              <section className='flex min-w-0 flex-col gap-2'>
                <span className='eyebrow'>AniList entry</span>
                {entries.length ? (
                  <ul className='flex flex-col gap-1.5' role='radiogroup' aria-label='AniList entry'>
                    {entries.map((item) => {
                      const on = entry?.anilist_id === item.anilist_id;
                      return (
                        <li key={item.anilist_id}>
                          <button
                            type='button'
                            role='radio'
                            aria-checked={on}
                            onClick={() => pickEntry(item)}
                            className={cn(
                              'flex w-full cursor-pointer items-center gap-2.5 rounded-control border px-2.5 py-2 text-left text-sm transition-colors focus-ring',
                              on ? 'border-foreground/60 bg-control-hover text-foreground' : 'border-field-edge text-foreground-soft hover:bg-control-hover/60',
                            )}
                          >
                            <AnimePoster url={item.cover} title={item.title} className='w-8 shrink-0 rounded' />
                            <span className='min-w-0 flex-1'>
                              <span className='block break-words'>{item.title}</span>
                              <span className='block text-[0.68rem] text-muted-foreground'>
                                {[formatLabel(item.format), item.episodes ? item.episodes + ' ep' : null, item.year].filter(Boolean).join(' · ')}
                              </span>
                            </span>
                            <span className='flex shrink-0 flex-col items-end gap-1'>
                              <StatusTag variant={item.relation === 'This release' ? 'info' : 'secondary'}>{item.relation}</StatusTag>
                              <a
                                className='link font-mono text-[0.62rem]'
                                href={anilistUrl(item.anilist_id)}
                                target='_blank'
                                rel='noreferrer'
                                onClick={(event) => event.stopPropagation()}
                              >
                                id {item.anilist_id}
                              </a>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : <p className='text-sm text-muted-foreground'>No entry to suggest; search AniList for it.</p>}
                <Button
                  size='sm'
                  variant='secondary'
                  className='self-start'
                  onClick={() => setSearchFor(matching.group.anilist_title || matching.group.release_title || '')}
                >
                  <Search data-icon='inline-start' /> Search AniList…
                </Button>
              </section>
              <section className='flex min-w-0 flex-col gap-2'>
                <div className='flex flex-wrap items-center justify-between gap-2'>
                  <span className='eyebrow'>Episodes</span>
                  <label className='flex items-center gap-2 text-xs text-muted-foreground'>
                    First episode
                    <Input
                      type='number'
                      min={0}
                      inputMode='numeric'
                      aria-label='First episode'
                      value={first}
                      onChange={(event) => changeFirst(event.target.value)}
                      size='sm'
                      className='w-20 font-mono tabular-nums'
                    />
                  </label>
                </div>
                <p className='text-xs text-muted-foreground'>
                  Each file&apos;s own number where it fits the entry, else counted from 1. Changing the first episode numbers them all on from it.
                </p>
                <ul className='flex flex-col divide-y divide-border/60 rounded-control border border-field-edge'>
                  {matching.files.map((file, index) => {
                    const number = Number(numbers[index]);
                    const over = entry?.episodes != null && Number.isInteger(number) && number > entry.episodes;
                    return (
                      <li key={file.id} className='flex items-center gap-3 px-3 py-2'>
                        <div className='min-w-0 flex-1'>
                          <p className='break-all font-mono text-[0.7rem] text-foreground'>{file.name}</p>
                          <p className='text-[0.65rem] text-muted-foreground'>
                            {[file.folder ? file.folder + '/' : null, marker(file) ? 'name says ' + marker(file) : null].filter(Boolean).join(' · ')}
                            {over ? <span className='text-warning'> · past the entry&apos;s {entry!.episodes} episodes</span> : null}
                            {taken?.includes(number) ? <span className='text-warning'> · replaces the library&apos;s file</span> : null}
                          </p>
                        </div>
                        <Input
                          type='number'
                          min={0}
                          inputMode='numeric'
                          aria-label={'Episode of ' + file.name}
                          value={numbers[index] ?? ''}
                          onChange={(event) => {
                            const value = event.target.value;
                            setTaken(null);
                            setNumbers((current) => current.map((old, at) => (at === index ? value : old)));
                          }}
                          size='sm'
                          className='w-20 shrink-0 font-mono tabular-nums'
                        />
                      </li>
                    );
                  })}
                </ul>
                {taken?.length ? (
                  <p className='text-xs text-warning'>
                    The library has episode{taken.length === 1 ? '' : 's'} {taken.join(', ')} of this entry already. Matching replaces {taken.length === 1 ? 'that file' : 'those files'}.
                  </p>
                ) : null}
              </section>
            </DialogBody>
          )}
          <DialogFooter>
            <Button variant='secondary' onClick={() => setMatching(null)} disabled={busy}>Cancel</Button>
            <Button onClick={() => void match()} disabled={busy || !entry}>
              <Link2 data-icon='inline-start' />
              {busy ? 'Matching…' : !entry ? 'Pick an entry' : taken?.length ? 'Replace and match' : 'Match to ' + entry.title}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={discarding !== null} onOpenChange={(open) => { if (!open && !busy) setDiscarding(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {discarding?.files.length} file{discarding?.files.length === 1 ? '' : 's'}?</DialogTitle>
            <DialogDescription>They are removed from disk and do not go into the library.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ul className='flex max-h-60 flex-col gap-1 overflow-auto'>
              {discarding?.files.map((file) => (
                <li key={file.id} className='flex items-center gap-2 font-mono text-[0.7rem] text-foreground'>
                  <FileQuestion className='size-3.5 shrink-0 text-muted-foreground' aria-hidden='true' />
                  <span className='break-all'>{file.name}</span>
                </li>
              ))}
            </ul>
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setDiscarding(null)} disabled={busy}>Cancel</Button>
            <Button variant='destructive' onClick={() => void discard()} disabled={busy}>
              <Trash2 data-icon='inline-start' /> {busy ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AniListPickDialog
        query={searchFor}
        onClose={() => setSearchFor(null)}
        onPick={searched}
        title='Choose the AniList entry'
        description='The entry these files are: an OVA, a special, a film.'
        action='Choose'
      />
    </section>
  );
}
