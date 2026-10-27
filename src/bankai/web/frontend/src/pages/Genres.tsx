import { useEffect, useMemo, useState } from 'react';
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, Plus, RefreshCw, Save, Search, Trash2, Undo2, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type GenreLanguage, type GenrePlan, type GenreRules, type GenreTitle, type GenreWord } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { cn } from '@/zon/lib/cn';
import { Button } from '@/zon/ui/button';
import { Checkbox } from '@/zon/ui/checkbox';
import { Input } from '@/zon/ui/input';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { Popover, PopoverContent, PopoverTrigger } from '@/zon/ui/popover';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/zon/ui/select';
import { Skeleton } from '@/zon/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/zon/ui/toggle-group';

const LANGUAGES: Array<[GenreLanguage, string]> = [['en', 'English'], ['de', 'Deutsch']];

/** A genre id from its English name: "Fairy Tales" -> "fairy_tales". */
function slug(name: string) {
  return name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function nameOf(rules: GenreRules, id: string, language: GenreLanguage) {
  return rules.vocabulary.find((row) => row.id === id)?.[language] ?? id;
}

/** Which genres a raw genre maps onto; none = dropped. */
function GenrePicker({ rules, value, language, onChange }: { rules: GenreRules; value: string[]; language: GenreLanguage; onChange: (ids: string[]) => void }) {
  return (
    <Popover>
      <PopoverTrigger render={<Button size='sm' variant='secondary' className='max-w-72 justify-start' />}>
        <span className='truncate'>{value.length ? value.map((id) => nameOf(rules, id, language)).join(', ') : 'Dropped'}</span>
      </PopoverTrigger>
      <PopoverContent align='start' className='max-h-80 w-64 overflow-y-auto p-2'>
        <div className='flex flex-col'>
          {rules.vocabulary.map((row) => (
            <label key={row.id} className='flex cursor-pointer items-center gap-2 rounded-item px-2 py-1 text-sm hover:bg-control-hover'>
              <Checkbox checked={value.includes(row.id)} onCheckedChange={(next) => onChange(next ? [...value, row.id] : value.filter((id) => id !== row.id))} />
              {row[language]}
            </label>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** A genre's sources: the raw genres and keyword tags that give it, each removable; one added by typing. */
function SourceChips({ rules, id, caught, onChange }: {
  rules: GenreRules;
  id: string;
  caught: Map<string, string[]>;
  onChange: (update: (rules: GenreRules) => void) => void;
}) {
  const [text, setText] = useState('');
  const sources = [
    ...Object.entries(rules.genres).filter(([, ids]) => ids.includes(id)).map(([raw]) => ({ kind: 'genres' as const, raw })),
    ...Object.entries(rules.tags).filter(([, ids]) => ids.includes(id)).map(([raw]) => ({ kind: 'tags' as const, raw })),
  ];
  const library = Object.entries(rules.libraries).filter(([, ids]) => ids.includes(id)).map(([name]) => name);

  function add() {
    const value = text.trim().toLowerCase();
    if (!value) return;
    const kind = value.startsWith('#') ? 'tags' : 'genres';
    const raw = value.replace(/^#/, '').trim();
    if (!raw) return;
    onChange((draft) => {
      const ids = draft[kind][raw] ?? [];
      if (!ids.includes(id)) draft[kind][raw] = [...ids, id];
    });
    setText('');
  }

  return (
    <div className='flex flex-wrap items-center gap-1'>
      {library.map((name) => <StatusTag key={'library:' + name} variant='outline' title={`Every title of the ${name} library`}>{name} library</StatusTag>)}
      {sources.map(({ kind, raw }) => {
        const titles = caught.get((kind === 'genres' ? 'genre' : 'tag') + ':' + raw) ?? [];
        const label = kind === 'tags' ? '#' + raw : raw;
        return (
          <span
            key={kind + ':' + raw}
            className={cn(
              'inline-flex h-[22px] items-center gap-1 rounded-item border pl-2 pr-0.5 font-mono text-[11px]',
              titles.length ? 'border-field-edge bg-control-surface text-foreground' : 'border-dashed border-field-edge text-muted-foreground',
            )}
            title={titles.length ? `${titles.length} titles: ${titles.join(', ')}` : 'No title has it now; kept for one that brings it'}
          >
            {label}
            {titles.length > 0 && <span className='text-muted-foreground'>{titles.length}</span>}
            <button
              type='button'
              aria-label={`Take ${label} off ${nameOf(rules, id, 'en')}`}
              className='rounded-sm p-0.5 text-muted-foreground hover:bg-control-hover hover:text-foreground focus-ring'
              onClick={() => onChange((draft) => {
                const rest = (draft[kind][raw] ?? []).filter((gid) => gid !== id);
                // A raw genre that gives nothing stays, dropped knowingly; a tag that gives nothing goes.
                if (kind === 'tags' && rest.length === 0) delete draft.tags[raw];
                else draft[kind][raw] = rest;
              })}
            >
              <X className='size-3' />
            </button>
          </span>
        );
      })}
      <Input
        size='sm'
        className='h-[22px] w-32 px-2 text-[11px]'
        placeholder='genre or #tag'
        aria-label={`Add a source to ${nameOf(rules, id, 'en')}`}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); add(); } }}
        onBlur={add}
      />
    </div>
  );
}

/** A vocabulary row: dragged by its grip (or moved with the arrow keys once the grip has focus). */
function VocabularyRow({ row, index, rules, count, caught, onChange, onRemove }: {
  row: GenreWord;
  index: number;
  rules: GenreRules;
  count: number | undefined;
  caught: Map<string, string[]>;
  onChange: (update: (rules: GenreRules) => void) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id: row.id });
  return (
    <TableRow
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(isDragging && 'relative z-10 bg-control-hover shadow-overlay')}
    >
      <TableCell className='whitespace-nowrap'>
        <div className='flex items-center gap-1'>
          <button
            type='button'
            ref={setActivatorNodeRef}
            {...attributes}
            {...listeners}
            aria-label={`Drag ${row.en} to its place; arrow keys move it`}
            className='cursor-grab touch-none rounded-sm p-1 text-muted-foreground hover:text-foreground focus-ring active:cursor-grabbing'
          >
            <GripVertical className='size-4' />
          </button>
          <span className='w-5 text-right font-mono text-xs text-muted-foreground'>{index + 1}</span>
        </div>
      </TableCell>
      <TableCell className='min-w-40'>
        <Input size='sm' aria-label={`English name of ${row.en}`} value={row.en} onChange={(event) => onChange((rules) => { rules.vocabulary[index].en = event.target.value; })} />
      </TableCell>
      <TableCell className='min-w-40'>
        <Input size='sm' aria-label={`German name of ${row.en}`} value={row.de} onChange={(event) => onChange((rules) => { rules.vocabulary[index].de = event.target.value; })} />
      </TableCell>
      <TableCell numeric className='text-xs'>{count ?? '—'}</TableCell>
      <TableCell className='w-full min-w-80'><SourceChips rules={rules} id={row.id} caught={caught} onChange={onChange} /></TableCell>
      <TableCell>
        <Button size='icon-sm' variant='ghost' aria-label={`Remove ${row.en}`} title='Remove this genre and its sources' onClick={onRemove}><Trash2 /></Button>
      </TableCell>
    </TableRow>
  );
}

/** A title's curated genres: each removable, more added from the vocabulary; kept at once as its hand choices. */
function TitleGenres({ title, rules, language, onSaved }: { title: GenreTitle; rules: GenreRules; language: GenreLanguage; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  // Shown at once, before the save and the reload come back; the reload's answer takes over.
  const [shown, setShown] = useState<{ ids: string[]; add: string[]; remove: string[] }>(() => ({
    ids: title.ids, add: title.override?.add ?? [], remove: title.override?.remove ?? [],
  }));
  useEffect(() => setShown({ ids: title.ids, add: title.override?.add ?? [], remove: title.override?.remove ?? [] }), [title]);
  const add = new Set(shown.add);
  const remove = new Set(shown.remove);

  async function keep(next: { add: Set<string>; remove: Set<string> }, ids: string[]) {
    setShown({ ids, add: [...next.add], remove: [...next.remove] });
    setBusy(true);
    try {
      await api.saveGenreOverride(title.key, { add: [...next.add], remove: [...next.remove] });
      onSaved();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  function take(id: string) {
    const nextAdd = new Set(add);
    const nextRemove = new Set(remove);
    if (nextAdd.has(id)) nextAdd.delete(id);
    // Given by the rules as well: taken away by hand.
    if ((title.reasons[id] ?? []).some((why) => why !== 'added')) nextRemove.add(id);
    void keep({ add: nextAdd, remove: nextRemove }, shown.ids.filter((gid) => gid !== id));
  }

  function give(id: string) {
    const nextAdd = new Set(add);
    const nextRemove = new Set(remove);
    if (nextRemove.has(id)) nextRemove.delete(id);
    else nextAdd.add(id);
    setPicking(false);
    const order = rules.vocabulary.map((row) => row.id);
    void keep({ add: nextAdd, remove: nextRemove }, order.filter((gid) => gid === id || shown.ids.includes(gid)));
  }

  // What the title has not got: neither a genre of it, nor one removed by hand (that one is clicked back).
  const missing = rules.vocabulary.filter((row) => !shown.ids.includes(row.id) && !remove.has(row.id));
  return (
    <div className={cn('flex flex-wrap items-center gap-1', busy && 'opacity-60')}>
      {shown.ids.map((id) => {
        const reasons = (title.reasons[id] ?? []).join(', ');
        return (
          <span
            key={id}
            title={add.has(id) ? 'Added by hand' : reasons}
            className={cn(
              'inline-flex h-[22px] items-center gap-1 rounded-item border pl-2 pr-0.5 font-mono text-[11px]',
              add.has(id) ? 'border-info/30 bg-info/15 text-info' : 'border-field-edge bg-control-surface text-foreground',
            )}
          >
            {nameOf(rules, id, language)}
            <button
              type='button'
              disabled={busy}
              aria-label={`Take ${nameOf(rules, id, 'en')} off ${title.name}`}
              className='rounded-sm p-0.5 text-muted-foreground hover:bg-control-hover hover:text-foreground focus-ring'
              onClick={() => take(id)}
            >
              <X className='size-3' />
            </button>
          </span>
        );
      })}
      {title.jellyfin_only.filter((id) => !shown.ids.includes(id)).map((id) => (
        <button
          key={'jellyfin:' + id}
          type='button'
          disabled={busy}
          title="Jellyfin has it now, the source does not give it: it goes when bankai writes. Click to keep it."
          className='inline-flex h-[22px] items-center rounded-item border border-dashed border-warning/50 px-2 font-mono text-[11px] text-warning line-through decoration-warning/60 hover:bg-warning/10 focus-ring'
          onClick={() => give(id)}
        >
          {nameOf(rules, id, language)}
        </button>
      ))}
      {[...remove].map((id) => (
        <button
          key={'removed:' + id}
          type='button'
          disabled={busy}
          title='Removed by hand; click to give it back'
          className='inline-flex h-[22px] items-center rounded-item border border-dashed border-warning/40 px-2 font-mono text-[11px] text-warning line-through hover:bg-warning/10 focus-ring'
          onClick={() => give(id)}
        >
          {nameOf(rules, id, language)}
        </button>
      ))}
      <Popover open={picking} onOpenChange={setPicking}>
        <PopoverTrigger render={<Button size='icon-sm' variant='ghost' aria-label={`Add a genre to ${title.name}`} disabled={busy || missing.length === 0} className='size-[22px]' />}>
          <Plus className='size-3.5' />
        </PopoverTrigger>
        <PopoverContent align='start' className='max-h-80 w-56 overflow-y-auto p-1'>
          <div className='flex flex-col'>
            {missing.map((row) => (
              <button key={row.id} type='button' className='rounded-item px-2 py-1 text-left text-sm hover:bg-control-hover focus-ring' onClick={() => give(row.id)}>
                {row[language]}
              </button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default function Genres() {
  const [plan, setPlan] = useState<GenrePlan | null>(null);
  const [draft, setDraft] = useState<GenreRules | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('vocabulary');
  const [query, setQuery] = useState('');
  const [genre, setGenre] = useState('');
  const [library, setLibrary] = useState('');
  const [onlyChanges, setOnlyChanges] = useState(false);
  const [fresh, setFresh] = useState({ en: '', de: '' });
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  async function load(refresh = false, keepDraft = false) {
    setLoading(true);
    try {
      const answer = await api.genres(undefined, refresh);
      setPlan(answer);
      if (!keepDraft) setDraft(structuredClone(answer.rules));
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, []);

  const dirty = plan !== null && draft !== null && JSON.stringify(plan.rules) !== JSON.stringify(draft);
  const language = draft?.language ?? 'en';

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      await api.saveGenreRules(draft);
      toast.success('Genres kept; the titles show what they change');
      await load();
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  function change(update: (rules: GenreRules) => void) {
    setDraft((current) => {
      if (!current) return current;
      const copy = structuredClone(current);
      update(copy);
      return copy;
    });
  }

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    change((rules) => {
      const from = rules.vocabulary.findIndex((row) => row.id === active.id);
      const to = rules.vocabulary.findIndex((row) => row.id === over.id);
      rules.vocabulary = arrayMove(rules.vocabulary, from, to);
    });
  }

  function remove(id: string) {
    change((rules) => {
      rules.vocabulary = rules.vocabulary.filter((row) => row.id !== id);
      for (const section of ['genres', 'tags', 'libraries', 'yields'] as const) {
        for (const [raw, ids] of Object.entries(rules[section])) rules[section][raw] = ids.filter((gid) => gid !== id);
        delete rules[section][id];
      }
    });
  }

  function add() {
    const id = slug(fresh.en);
    if (!id || !fresh.de.trim()) return;
    change((rules) => { rules.vocabulary.push({ id, en: fresh.en.trim(), de: fresh.de.trim() }); });
    setFresh({ en: '', de: '' });
  }

  const caught = useMemo(() => new Map((plan?.catches ?? []).map((row) => [row.kind + ':' + row.raw, row.titles])), [plan]);
  const counts = useMemo(() => new Map((plan?.vocabulary ?? []).map((row) => [row.id, row.titles])), [plan]);
  const libraries = useMemo(() => [...new Set((plan?.titles ?? []).map((row) => row.library))].sort(), [plan]);
  // The filters' choices; '' is every one.
  const genreItems = useMemo(
    () => [{ value: '', label: 'Every genre' }, ...(plan?.vocabulary ?? []).map((row) => ({ value: row.id, label: `${row[plan!.language]} (${row.titles})` }))],
    [plan],
  );
  const libraryItems = useMemo(
    () => [{ value: '', label: 'Every library' }, ...libraries.map((name) => ({ value: name, label: name === 'anime' ? 'Anime' : name }))],
    [libraries],
  );
  // Dropped knowingly: shown where a title carries it; the rest wait for a title that brings them.
  const carried = new Set((plan?.discarded ?? []).map((row) => row.raw));
  const droppedAll = Object.entries(draft?.genres ?? {}).filter(([, ids]) => ids.length === 0).map(([raw]) => raw).sort();
  const dropped = droppedAll.filter((raw) => carried.has(raw));
  const droppedIdle = droppedAll.filter((raw) => !carried.has(raw));
  const needle = query.trim().toLocaleLowerCase();
  const titles = (plan?.titles ?? []).filter((row) =>
    (!onlyChanges || row.changes)
    && (!genre || row.ids.includes(genre) || (row.override?.remove ?? []).includes(genre))
    && (!library || row.library === library)
    && (!needle || row.name.toLocaleLowerCase().includes(needle)));

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-wrap items-end justify-between gap-3'>
        <div className='flex max-w-3xl flex-col gap-1'>
          <h1 className='font-heading text-3xl font-light'>Genres</h1>
          <p className='text-sm text-muted-foreground'>
            bankai's own genres, written into Jellyfin's normal Genres field so every app shows them. Movies and series get them from
            their TMDB / TVDB genres and keywords, anime from AniList; the order is their priority, specific before broad, since apps
            often show only three. Movies and series are written a few dozen an hour (Genres locked, so Jellyfin keeps them);
            anime through their tvshow.nfo.
          </p>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          {draft && (
            <div className='flex items-center gap-2' title='Jellyfin keeps one name per genre for every user and app: this is the language bankai writes. The Titles tab shows the names in it too.'>
              <span className='text-xs text-muted-foreground'>Names in Jellyfin</span>
              <ToggleGroup<GenreLanguage>
                aria-label='Language of the genre names written to Jellyfin'
                value={[language]}
                onValueChange={(values) => values[0] && change((rules) => { rules.language = values[0]; })}
              >
                {LANGUAGES.map(([value, label]) => <ToggleGroupItem<GenreLanguage> key={value} value={value}>{label}</ToggleGroupItem>)}
              </ToggleGroup>
            </div>
          )}
          <Button variant='secondary' onClick={() => void load(true, dirty)} disabled={loading}>
            <RefreshCw data-icon='inline-start' className={cn(loading && 'animate-spin')} /> Read Jellyfin again
          </Button>
          <Button variant='secondary' onClick={() => plan && setDraft(structuredClone(plan.rules))} disabled={!dirty || saving}>
            <Undo2 data-icon='inline-start' /> Discard
          </Button>
          <Button onClick={() => void save()} disabled={!dirty || saving}><Save data-icon='inline-start' /> Save</Button>
        </div>
      </div>

      {!plan || !draft ? <Skeleton className='h-96 w-full' /> : (
        <>
          <p className='text-xs text-muted-foreground'>
            <span className='font-mono text-foreground'>{plan.summary.titles}</span> titles ({plan.summary.jellyfin} movies and series in
            Jellyfin, {plan.summary.anime} anime) · <span className='font-mono text-foreground'>{plan.summary.changes}</span> differ from Jellyfin
            {' · '}
            {plan.write.movies || plan.write.anime
              ? <span className='text-success'>written to Jellyfin: {plan.summary.written} movies and series{plan.write.anime ? ', anime through their NFO files' : ''}</span>
              : 'not written to Jellyfin yet'}
            {dirty && <span className='text-warning'> · unsaved vocabulary changes: the titles show the rules as saved</span>}
          </p>
          <Tabs value={tab} onValueChange={(value) => setTab(String(value))} className='gap-4'>
            <TabsList>
              <TabsTab value='vocabulary'>Vocabulary <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{draft.vocabulary.length}</span></TabsTab>
              <TabsTab value='titles'>Titles <span className='ml-1.5 font-mono text-[0.68rem] text-muted-foreground'>{plan.summary.titles}</span></TabsTab>
              <TabsTab value='unmapped'>
                Unmapped <span className={cn('ml-1.5 font-mono text-[0.68rem]', plan.unmapped.length ? 'text-warning' : 'text-muted-foreground')}>{plan.unmapped.length}</span>
              </TabsTab>
            </TabsList>

            <TabsPanel value='vocabulary' className='flex flex-col gap-2'>
              <p className='text-xs text-muted-foreground'>
                Drag a genre by its grip to its place. Sources are what give it: a raw genre (TMDB, TVDB, AniList, any language) or a
                #keyword tag; type one and press Enter, × takes it off. A dashed source is one no title carries now. Changes wait for Save.
              </p>
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                <SortableContext items={draft.vocabulary.map((row) => row.id)} strategy={verticalListSortingStrategy}>
                  <Table variant='plain' dense>
                    <TableHeader>
                      <TableRow>
                        <TableHead className='w-16'>Priority</TableHead>
                        <TableHead>English</TableHead>
                        <TableHead>Deutsch</TableHead>
                        <TableHead numeric>Titles</TableHead>
                        <TableHead className='w-full'>Sources</TableHead>
                        <TableHead><span className='sr-only'>Actions</span></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {draft.vocabulary.map((row, index) => (
                        <VocabularyRow
                          key={row.id}
                          row={row}
                          index={index}
                          rules={draft}
                          count={counts.get(row.id)}
                          caught={caught}
                          onChange={change}
                          onRemove={() => remove(row.id)}
                        />
                      ))}
                      <TableRow>
                        <TableCell className='text-xs text-muted-foreground'>New</TableCell>
                        <TableCell><Input size='sm' placeholder='English name' value={fresh.en} onChange={(event) => setFresh((row) => ({ ...row, en: event.target.value }))} /></TableCell>
                        <TableCell><Input size='sm' placeholder='German name' value={fresh.de} onChange={(event) => setFresh((row) => ({ ...row, de: event.target.value }))} /></TableCell>
                        <TableCell />
                        <TableCell className='text-xs text-muted-foreground'>Added last: drag it to its place, then give it its sources.</TableCell>
                        <TableCell>
                          <Button size='icon-sm' variant='secondary' aria-label='Add the genre' disabled={!slug(fresh.en) || !fresh.de.trim() || draft.vocabulary.some((row) => row.id === slug(fresh.en))} onClick={add}><Plus /></Button>
                        </TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </SortableContext>
              </DndContext>
            </TabsPanel>

            <TabsPanel value='titles' className='flex flex-col gap-3'>
              <div className='flex flex-wrap items-center gap-2'>
                <InputGroup className='w-64 shrink-0'>
                  <InputGroupAddon><Search /></InputGroupAddon>
                  <InputGroupInput placeholder='Filter titles' value={query} onChange={(event) => setQuery(event.target.value)} />
                </InputGroup>
                <Select items={genreItems} value={genre} onValueChange={(value) => setGenre(value ?? '')}>
                  <SelectTrigger className='w-56 shrink-0' aria-label='Genre'><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {genreItems.map((item) => <SelectItem key={item.value || 'all'} value={item.value}>{item.label}</SelectItem>)}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <Select items={libraryItems} value={library} onValueChange={(value) => setLibrary(value ?? '')}>
                  <SelectTrigger className='w-44 shrink-0' aria-label='Library'><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {libraryItems.map((item) => <SelectItem key={item.value || 'all'} value={item.value}>{item.label}</SelectItem>)}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <ToggleGroup<'changes' | 'all'> aria-label='Titles shown' value={[onlyChanges ? 'changes' : 'all']} onValueChange={(values) => values[0] && setOnlyChanges(values[0] === 'changes')}>
                  <ToggleGroupItem<'changes' | 'all'> value='all'>All</ToggleGroupItem>
                  <ToggleGroupItem<'changes' | 'all'> value='changes'>Changes</ToggleGroupItem>
                </ToggleGroup>
                <span className='text-xs text-muted-foreground'>{titles.length} shown</span>
              </div>
              <p className='text-xs text-muted-foreground'>
                × takes a genre off a title, + gives it one; kept at once as that title's own choice (blue: added by hand, struck through:
                removed by hand, click to give it back). Hover a genre for why the rules give it.
              </p>
              <Table variant='plain' dense>
                <TableHeader>
                  <TableRow>
                    <TableHead>Title</TableHead>
                    <TableHead>Jellyfin now</TableHead>
                    <TableHead className='w-1/2'>Curated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {titles.map((row) => (
                    <TableRow key={row.key + (row.item_id ?? '')}>
                      <TableCell className='max-w-72'>
                        <p className='truncate text-sm text-foreground' title={row.name}>{row.name}{row.year ? ` (${row.year})` : ''}</p>
                        <p className='font-mono text-[0.66rem] text-muted-foreground'>
                          {row.library === 'anime' ? 'Anime' : row.library} · {row.key} · <span title={`Raw genres: ${row.raw.join(', ') || 'none'}`}>from {row.raw_source}</span>
                        </p>
                      </TableCell>
                      <TableCell className='max-w-80'>
                        <p className='text-xs text-muted-foreground'>{row.before.join(', ') || '—'}</p>
                      </TableCell>
                      <TableCell><TitleGenres title={row} rules={plan.rules} language={plan.language} onSaved={() => void load(false, true)} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TabsPanel>

            <TabsPanel value='unmapped' className='flex flex-col gap-6'>
              <div className='flex flex-col gap-2'>
                <h2 className='font-heading text-lg font-light'>No rule for these</h2>
                {plan.unmapped.length === 0 ? (
                  <p className='text-sm text-muted-foreground'>Every raw genre is mapped, or dropped knowingly below.</p>
                ) : (
                  <Table variant='plain' dense>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Raw genre</TableHead>
                        <TableHead numeric>Titles</TableHead>
                        <TableHead className='w-full'>For example</TableHead>
                        <TableHead>Becomes</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {plan.unmapped.map((row) => (
                        <TableRow key={row.raw}>
                          <TableCell className='whitespace-nowrap font-mono text-xs'>{row.raw}</TableCell>
                          <TableCell numeric className='text-xs'>{row.titles}</TableCell>
                          <TableCell className='max-w-0'><p className='truncate text-xs text-muted-foreground'>{row.examples.join(', ')}</p></TableCell>
                          <TableCell>
                            <GenrePicker rules={draft} value={draft.genres[row.raw.toLowerCase()] ?? []} language={language} onChange={(next) => change((rules) => { rules.genres[row.raw.toLowerCase()] = next; })} />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </div>
              <div className='flex flex-col gap-2'>
                <h2 className='font-heading text-lg font-light'>Dropped knowingly</h2>
                <p className='text-xs text-muted-foreground'>
                  Raw genres that give no genre at all: content flags (Ecchi, Hentai say how explicit a title is, not what kind of story
                  it is), and anything you dropped. The titles stay; they only lose that word. Pick genres to keep it after all.
                </p>
                <Table variant='plain' dense>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Raw genre</TableHead>
                      <TableHead numeric>Titles</TableHead>
                      <TableHead className='w-full'>Carried by</TableHead>
                      <TableHead>Becomes</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {dropped.map((raw) => {
                      const names = plan.discarded.find((row) => row.raw === raw)?.titles ?? [];
                      return (
                        <TableRow key={raw}>
                          <TableCell className='whitespace-nowrap font-mono text-xs'>{raw}</TableCell>
                          <TableCell numeric className='text-xs'>{names.length}</TableCell>
                          <TableCell className='max-w-0'>
                            <p className='text-xs text-muted-foreground'>{names.join(', ')}</p>
                          </TableCell>
                          <TableCell>
                            <GenrePicker rules={draft} value={[]} language={language} onChange={(next) => change((rules) => { rules.genres[raw] = next; })} />
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
                {droppedIdle.length > 0 && (
                  <p className='text-xs text-muted-foreground'>
                    Also dropped, should a title ever bring {droppedIdle.length === 1 ? 'it' : 'them'}: {droppedIdle.join(', ')}.
                  </p>
                )}
              </div>
            </TabsPanel>
          </Tabs>
        </>
      )}
    </div>
  );
}
