import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, FolderInput, ListOrdered } from 'lucide-react';
import { inBackground, toast } from '@/lib/toast';
import { api, type MangaBookKind, type MangaMovePlan } from '@/lib/api';
import { anilistName } from '@/lib/anilist';
import { MangaPickDialog } from '@/components/MangaPickDialog';
import { Button } from '@zonsh/ui';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@zonsh/ui';
import { Input } from '@zonsh/ui';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@zonsh/ui';
import { Spinner } from '@zonsh/ui';
import { cn } from '@zonsh/ui';

type Choice = MangaBookKind | 'keep';
type Row = { kind: Choice; number: string };

const CHOICES: { value: Choice; label: string }[] = [
  { value: 'volume', label: 'Volume' },
  { value: 'chapter', label: 'Chapter' },
  { value: 'keep', label: 'Name as is' },
];

/** The library's name for a book, as the backend's `target_name`: "Vol 01.cbz", "Chapter 0001.cbz", "Chapter 0168.1.cbz". */
function bookName(kind: MangaBookKind, number: number, extension: string): string {
  const whole = Math.trunc(number);
  const fraction = number === whole ? '' : String(number).slice(String(whole).length);
  return (kind === 'volume' ? 'Vol ' + String(whole).padStart(2, '0') : 'Chapter ' + String(whole).padStart(4, '0')) + fraction + extension;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot) : '';
}

/** "1–4, 7, 9–12" from sorted numbers. */
function runs(values: number[]): string {
  const parts: string[] = [];
  for (let index = 0; index < values.length; ) {
    let end = index;
    while (end + 1 < values.length && values[end + 1] === values[end] + 1) end += 1;
    parts.push(end === index ? String(values[index]) : `${values[index]}–${values[end]}`);
    index = end + 1;
  }
  return parts.join(', ');
}

type Verdict = { name: string; tone: 'good' | 'warn' | 'bad' | 'quiet'; text: string };

/** Each row's new name and what it would be in the series: filling a gap, past AniList's count, taken. */
function verdicts(plan: MangaMovePlan, rows: Record<string, Row>): Map<string, Verdict> {
  const out = new Map<string, Verdict>();
  const present = new Set(plan.names);
  const counted = new Map<string, number>();
  const named = new Map<string, Verdict>();
  for (const file of plan.files) {
    const row = rows[file.name];
    if (!row) continue;
    let verdict: Verdict;
    if (row.kind === 'keep') {
      verdict = { name: file.name, tone: 'quiet', text: plan.same ? 'unchanged' : 'keeps its name' };
    } else {
      const number = Number(row.number);
      if (row.number.trim() === '' || !Number.isFinite(number) || number < 0) {
        out.set(file.name, { name: '', tone: 'bad', text: 'needs a number' });
        continue;
      }
      const name = bookName(row.kind, number, extensionOf(file.suggested.name));
      const count = plan.counts[row.kind];
      const noun = row.kind === 'volume' ? 'volume' : 'chapter';
      if (plan.same && name === file.name) verdict = { name, tone: 'quiet', text: 'unchanged' };
      else if (plan.missing[row.kind].includes(number)) verdict = { name, tone: 'good', text: `fills missing ${noun} ${number}` };
      else if (count && number > count) verdict = { name, tone: 'warn', text: `past AniList's ${count} ${noun}${count === 1 ? '' : 's'}` };
      else if (number !== Math.trunc(number)) verdict = { name, tone: 'warn', text: 'between whole numbers' };
      else if (number < 1) verdict = { name, tone: 'warn', text: `before ${noun} 1` };
      else if (plan.missing[row.kind].length < 500 && (count || plan.missing[row.kind].length)) verdict = { name, tone: 'warn', text: `the series has ${noun} ${number} already` };
      else verdict = { name, tone: 'quiet', text: '' };
    }
    if (present.has(verdict.name)) verdict = { ...verdict, tone: 'bad', text: `${verdict.name} is there already` };
    counted.set(verdict.name, (counted.get(verdict.name) ?? 0) + 1);
    named.set(file.name, verdict);
  }
  for (const [file, verdict] of named) {
    out.set(file, (counted.get(verdict.name) ?? 0) > 1 ? { ...verdict, tone: 'bad', text: 'two files would get this name' } : verdict);
  }
  return out;
}

/** Books of one series that are another's (Jujutsu Kaisen's "Vol 00" is Jujutsu Kaisen 0), or renumbered in their own
 *  (its bonus chapter filed as "Chapter 0168.1" is chapter 1): pick the series, then each book's volume or chapter there,
 *  suggested from what that series lacks. */
export function MoveMangaFiles({
  from,
  files,
  renumber = false,
  onClose,
  onMoved,
}: {
  from: { id: number; name: string };
  /** The books to move; null closes. */
  files: string[] | null;
  /** Renumber them in their own series: no series to pick. */
  renumber?: boolean;
  onClose: () => void;
  onMoved: () => void;
}) {
  const [target, setTarget] = useState<{ id: number; name: string } | null>(null);
  const [plan, setPlan] = useState<MangaMovePlan | null>(null);
  const [rows, setRows] = useState<Record<string, Row>>({});

  useEffect(() => {
    setTarget(files && renumber ? from : null);
    setPlan(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a new set of books starts over; `from` follows them
  }, [files, renumber]);

  useEffect(() => {
    if (!files || !target) return;
    let live = true;
    setPlan(null);
    api.mangaMovePlan(from.id, files, target.id).then((next) => {
      if (!live) return;
      setPlan(next);
      setRows(Object.fromEntries(next.files.map((file) => [file.name, { kind: file.suggested.kind, number: String(file.suggested.number) }])));
    }).catch((error: Error) => {
      if (!live) return;
      toast.error(error.message);
      if (renumber) onClose();
      else setTarget(null);
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- asked again only for other books or another series
  }, [files, target]);

  const judged = useMemo(() => (plan ? verdicts(plan, rows) : new Map<string, Verdict>()), [plan, rows]);
  const blocked = [...judged.values()].some((verdict) => verdict.tone === 'bad');
  const changes = plan ? plan.files.filter((file) => judged.get(file.name)?.name !== file.name).length : 0;

  function move() {
    if (!files || !target || !plan || blocked) return;
    const names = Object.fromEntries(plan.files.map((file) => [file.name, judged.get(file.name)?.name ?? file.name]));
    const same = plan.same;
    // The dialog closes at once; a toast follows the renames.
    onClose();
    void inBackground(api.moveMangaFiles(from.id, files, target.id, names), {
      loading: same ? `Renumbering ${files.length} file${files.length === 1 ? '' : 's'}…` : `Moving ${files.length} file${files.length === 1 ? '' : 's'} to ${target.name}…`,
      success: (result) => same
        ? `${result.moved} file${result.moved === 1 ? '' : 's'} renumbered`
        : `${result.moved} file${result.moved === 1 ? '' : 's'} moved to ${result.folder}`,
    }).then(onMoved, () => undefined);
  }

  const missing = plan
    ? (['volume', 'chapter'] as const)
      .filter((kind) => plan.missing[kind].length)
      .map((kind) => `${kind}${plan.missing[kind].length === 1 ? '' : 's'} ${runs(plan.missing[kind])}${plan.missing[kind].length >= 500 ? ', …' : ''}`)
      .join(' · ')
    : '';
  const counts = plan
    ? [plan.counts.volume && `${plan.counts.volume} volume${plan.counts.volume === 1 ? '' : 's'}`, plan.counts.chapter && `${plan.counts.chapter} chapter${plan.counts.chapter === 1 ? '' : 's'}`].filter(Boolean).join(', ')
    : '';

  return (
    <>
      <MangaPickDialog
        query={files && !target && !renumber ? from.name : null}
        title={files ? `Which series ${files.length === 1 ? 'is ' + files[0] : 'are these ' + files.length + ' files'}?` : ''}
        description='They move into its folder, made if it has none; each one is numbered in it next.'
        action='Choose'
        onClose={onClose}
        onPick={async (entry) => setTarget({ id: entry.anilist_id, name: anilistName(entry) })}
      />
      <Dialog open={Boolean(files && target)} onOpenChange={(open) => { if (!open) onClose(); }}>
        <DialogContent size='lg'>
          <DialogHeader>
            <DialogTitle>{renumber ? `Renumber in ${from.name}` : `Move to ${target?.name ?? ''}`}</DialogTitle>
            <DialogDescription>
              {counts ? `AniList counts ${counts}. ` : 'AniList gives no count. '}
              {missing ? `Missing: ${missing}.` : 'Nothing is missing.'} Each book is suggested the first number it lacks when its own does not fit.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {!plan && <div className='flex justify-center py-6'><Spinner /></div>}
            {plan && (
              <ul className='flex flex-col gap-3'>
                {plan.files.map((file) => {
                  const row = rows[file.name] ?? { kind: file.suggested.kind, number: String(file.suggested.number) };
                  const verdict = judged.get(file.name);
                  const set = (next: Partial<Row>) => setRows((current) => ({ ...current, [file.name]: { ...row, ...next } }));
                  return (
                    <li key={file.name} className='flex flex-col gap-1.5'>
                      <div className='flex flex-wrap items-center gap-2'>
                        <span className='w-44 shrink-0 truncate font-mono text-xs text-muted-foreground' title={file.name}>{file.name}</span>
                        <ArrowRight className='size-3.5 shrink-0 text-muted-foreground' />
                        <Select items={CHOICES} value={row.kind} onValueChange={(value) => value && set({ kind: value as Choice })}>
                          <SelectTrigger size='sm' className='w-32' aria-label={'Volume or chapter for ' + file.name}><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectGroup>
                              {CHOICES.map((choice) => <SelectItem key={choice.value} value={choice.value}>{choice.label}</SelectItem>)}
                            </SelectGroup>
                          </SelectContent>
                        </Select>
                        {row.kind !== 'keep' && (
                          <Input
                            size='sm'
                            type='number'
                            inputMode='decimal'
                            min={0}
                            step='any'
                            className='w-20 font-mono tabular-nums'
                            value={row.number}
                            onChange={(event) => set({ number: event.target.value })}
                            aria-label={'Number for ' + file.name}
                          />
                        )}
                      </div>
                      {verdict && (
                        <p className='pl-0 text-xs sm:pl-[12.5rem]'>
                          {verdict.name && <span className='font-mono text-foreground'>{verdict.name}</span>}
                          {verdict.text && (
                            <span className={cn(
                              verdict.name && 'ml-2',
                              verdict.tone === 'good' && 'text-success',
                              verdict.tone === 'warn' && 'text-warning',
                              verdict.tone === 'bad' && 'text-destructive',
                              verdict.tone === 'quiet' && 'text-muted-foreground',
                            )}>
                              {verdict.text}
                            </span>
                          )}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </DialogBody>
          <DialogFooter>
            {!renumber && <Button variant='secondary' onClick={() => setTarget(null)}>Another series</Button>}
            <Button onClick={() => move()} disabled={!plan || blocked || (plan.same && changes === 0)}>
              {renumber ? <ListOrdered data-icon='inline-start' /> : <FolderInput data-icon='inline-start' />}
              {renumber
                ? `Renumber ${changes} file${changes === 1 ? '' : 's'}`
                : `Move ${files?.length ?? 0} file${files?.length === 1 ? '' : 's'}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
