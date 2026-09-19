import { useEffect, useMemo, useState } from 'react';
import { Settings as SettingsIcon, Save, Loader2, Eye, EyeOff, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { api, type SettingRow } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const SETTING_UI: Record<
  string,
  { label: string; description: string; kind: 'number' | 'quality' | 'paths'; min?: number; step?: number; suffix?: string }
> = {
  'web.server_movie_dirs': {
    label: 'Movie directories',
    description: 'Roots the Movies & Shows library scans for films. One per line.',
    kind: 'paths',
  },
  'web.server_show_dirs': {
    label: 'Show directories',
    description: 'Roots the Movies & Shows library scans for series. One per line.',
    kind: 'paths',
  },
  'selector.preferred_resolutions': {
    label: 'Preferred quality',
    description: 'Try this resolution first, then fall back to the other HQ option.',
    kind: 'quality',
  },
  'selector.min_size_gib': {
    label: 'Minimum torrent size',
    description: 'Ignore suspiciously small HQ releases.',
    kind: 'number',
    min: 0,
    step: 0.5,
    suffix: 'GiB',
  },
  'selector.max_size_gib': {
    label: 'Maximum torrent size',
    description: 'Do not download releases larger than this.',
    kind: 'number',
    min: 0.5,
    step: 0.5,
    suffix: 'GiB',
  },
  'selector.min_seeders': {
    label: 'Minimum seeders',
    description: 'Only consider torrents with at least this many seeders.',
    kind: 'number',
    min: 0,
    step: 1,
  },
};

const SELECTOR_ORDER = [
  'selector.preferred_resolutions',
  'selector.min_size_gib',
  'selector.max_size_gib',
  'selector.min_seeders',
];

function labelize(key: string) {
  return key
    .split(/[._]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

// The section is the config table the key belongs to, e.g. "metadata.tvdb_api_key" -> "metadata".
function sectionOf(key: string): string {
  const dot = key.indexOf('.');
  return dot > 0 ? key.slice(0, dot) : 'general';
}

// Show just the field name inside a section (drop the section prefix).
function fieldLabel(key: string): string {
  if (SETTING_UI[key]) return SETTING_UI[key].label;
  const dot = key.indexOf('.');
  return labelize(dot > 0 ? key.slice(dot + 1) : key);
}

function sectionLabel(section: string): string {
  return section === 'selector' ? 'HQ torrent downloads' : labelize(section);
}

function valuesEqual(a: any, b: any): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return JSON.stringify(a) === JSON.stringify(b);
  return false;
}

/** Which half of the settings a page shows.
 *
 *  There is one config behind two pages: the Movies & Shows page carries what
 *  belongs to that library, and the global page carries the rest. Anime keeps
 *  its own page and its keys are left on the global one rather than hidden,
 *  so nothing becomes unreachable if that page does not cover all of them.
 */
export type SettingsScope = 'mas' | 'global' | 'all';

const MAS_SECTIONS = new Set(['scraper', 'selector']);
const MAS_KEYS = new Set(['web.server_movie_dirs', 'web.server_show_dirs']);

function inScope(key: string, scope: SettingsScope): boolean {
  if (scope === 'all') return true;
  const mas = MAS_SECTIONS.has(sectionOf(key)) || MAS_KEYS.has(key);
  return scope === 'mas' ? mas : !mas;
}

export default function Settings({ scope = 'all' }: { scope?: SettingsScope } = {}) {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await api.settings();
      setRows(r.settings.filter((row) => !row.key.startsWith('anime.') && row.key !== 'transfer.anime_shows_dir'));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  const dirtyKeys = Object.keys(edits);
  const dirtyCount = dirtyKeys.length;

  function currentValue(row: SettingRow) {
    return row.key in edits ? edits[row.key] : (row.value ?? '');
  }

  function setEdit(key: string, value: any, original: any) {
    setEdits((e) => {
      const n = { ...e };
      // Drop the edit if it matches the original value again.
      if (valuesEqual(value, original) || (value === '' && (original === undefined || original === null || original === ''))) {
        delete n[key];
      } else {
        n[key] = value;
      }
      return n;
    });
  }

  async function saveAll() {
    if (dirtyCount === 0) return;
    if (validationError) {
      toast.error(validationError);
      return;
    }
    setSaving(true);
    let ok = 0;
    const keysToSave = [...dirtyKeys];
    // Each API write is validated independently. When both size bounds move
    // past an old bound, save the outward bound first so the intermediate
    // config is valid too (for example 0.5–80 GiB -> 90–100 GiB).
    const minKey = 'selector.min_size_gib';
    const maxKey = 'selector.max_size_gib';
    if (keysToSave.includes(minKey) && keysToSave.includes(maxKey)) {
      const originalMin = Number(rows.find((r) => r.key === minKey)?.value);
      const originalMax = Number(rows.find((r) => r.key === maxKey)?.value);
      const minIndex = keysToSave.indexOf(minKey);
      const maxIndex = keysToSave.indexOf(maxKey);
      if (minSize > originalMax && maxIndex > minIndex) {
        [keysToSave[minIndex], keysToSave[maxIndex]] = [keysToSave[maxIndex], keysToSave[minIndex]];
      } else if (maxSize < originalMin && minIndex > maxIndex) {
        [keysToSave[minIndex], keysToSave[maxIndex]] = [keysToSave[maxIndex], keysToSave[minIndex]];
      }
    }
    try {
      for (const key of keysToSave) {
        await api.setSetting(key, edits[key]);
        ok++;
      }
      toast.success(`Saved ${ok} change${ok === 1 ? '' : 's'}`);
      setEdits({});
      await load();
    } catch (e: any) {
      toast.error(`${e.message}${ok ? ` (${ok} saved before error)` : ''}`);
      await load();
    } finally {
      setSaving(false);
    }
  }

  // Group rows by section, preserving first-seen order.
  const groups = useMemo(() => {
    const map = new Map<string, SettingRow[]>();
    for (const row of rows) {
      if (!inScope(row.key, scope)) continue;
      const s = sectionOf(row.key);
      if (!map.has(s)) map.set(s, []);
      map.get(s)!.push(row);
    }
    const selectorRows = map.get('selector');
    if (selectorRows) {
      selectorRows.sort((a, b) => SELECTOR_ORDER.indexOf(a.key) - SELECTOR_ORDER.indexOf(b.key));
    }
    return Array.from(map.entries());
  }, [rows, scope]);

  function valueForKey(key: string): any {
    const row = rows.find((r) => r.key === key);
    return row ? currentValue(row) : undefined;
  }

  // Only judge fields this page is showing. The torrent selector lives on
  // the Movies & Shows page, and a size complaint raised here would name
  // settings that are not on screen while disabling a Save button that
  // cannot reach them.
  const judgesSelector = groups.some(([section]) => section === 'selector');
  const minSize = Number(valueForKey('selector.min_size_gib'));
  const maxSize = Number(valueForKey('selector.max_size_gib'));
  const minSeeders = Number(valueForKey('selector.min_seeders'));
  const validationError = !judgesSelector
    ? null
    : !Number.isFinite(minSize) || minSize < 0
      ? 'Minimum torrent size must be zero or greater.'
      : !Number.isFinite(maxSize) || maxSize <= 0
        ? 'Maximum torrent size must be greater than zero.'
        : minSize > maxSize
          ? 'Minimum torrent size cannot exceed the maximum.'
          : !Number.isInteger(minSeeders) || minSeeders < 0
            ? 'Minimum seeders must be a whole number of zero or greater.'
            : null;

  return (
    <div className='mx-auto max-w-5xl pb-16'>
      <header className='flex flex-wrap items-end justify-between gap-4 pb-2'>
        <div className='flex flex-col gap-1'>
          <p className='label-mono'>{scope === 'mas' ? 'Movies & Shows' : 'System'}</p>
          <h1 className='font-serif text-3xl'>Settings</h1>
          <p className='text-sm text-muted-foreground'>Edit safe configuration keys, then save them all at once.</p>
        </div>
        <div className='flex items-center gap-2'>
          {dirtyCount > 0 && (
            <Button variant='ghost' onClick={() => setEdits({})} disabled={saving}>
              <RotateCcw data-icon='inline-start' /> Discard
            </Button>
          )}
          <Button onClick={saveAll} disabled={saving || dirtyCount === 0 || Boolean(validationError)}>
            {saving ? <Loader2 data-icon='inline-start' className='animate-spin' /> : <Save data-icon='inline-start' />}
            {dirtyCount > 0 ? `Save ${dirtyCount} change${dirtyCount === 1 ? '' : 's'}` : 'Saved'}
          </Button>
        </div>
      </header>

      {loading ? (
        <div className='space-y-3'>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className='h-10 w-full' />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className='flex flex-col items-center py-16 text-muted-foreground'>
          <SettingsIcon className='mb-3 h-8 w-8' />
          No editable settings.
        </div>
      ) : (
        <div>
          {validationError && (
            <p className='mb-4 border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive-foreground'>
              {validationError}
            </p>
          )}
          {groups.map(([section, sectionRows]) => (
            <section
              key={section}
              className='grid gap-x-10 gap-y-6 border-t border-border/60 py-8 last:border-b md:grid-cols-[168px_1fr]'
            >
              <h2 className='label-mono md:pt-1.5'>{sectionLabel(section)}</h2>
              <div className='flex flex-col gap-6'>
                {sectionRows.map((row) => {
                  const isBool = typeof row.value === 'boolean';
                  const val = currentValue(row);
                  const original = row.value ?? '';
                  const dirty = row.key in edits;
                  const ui = SETTING_UI[row.key];
                  return (
                    <div key={row.key} className='flex flex-wrap items-start justify-between gap-x-6 gap-y-2'>
                      <div className='min-w-0 space-y-1'>
                        <div className='flex items-center gap-2'>
                          <span className='text-foreground'>{fieldLabel(row.key)}</span>
                          {dirty && (
                            <Tooltip>
                              <TooltipTrigger asChild><span className='h-1.5 w-1.5 rounded-full bg-primary' /></TooltipTrigger>
                              <TooltipContent>Unsaved change</TooltipContent>
                            </Tooltip>
                          )}
                          {row.secret && (row.is_set ? <Badge variant='success'>Set</Badge> : <Badge variant='muted'>Unset</Badge>)}
                        </div>
                        {ui?.description && <p className='max-w-md text-xs text-muted-foreground'>{ui.description}</p>}
                      </div>

                      <div className='flex items-center gap-2'>
                        {isBool ? (
                          <Switch checked={Boolean(val)} onCheckedChange={(v) => setEdit(row.key, v, original)} />
                        ) : ui?.kind === 'quality' ? (
                          <Select
                            value={Array.isArray(val) && val[0] === '1080p' ? '1080p' : '2160p'}
                            onValueChange={(v) => setEdit(row.key, [v, v === '2160p' ? '1080p' : '2160p'], original)}
                          >
                            <SelectTrigger className='w-72'>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectGroup>
                                <SelectItem value='1080p'>1080p</SelectItem>
                                <SelectItem value='2160p'>2160p (4K)</SelectItem>
                              </SelectGroup>
                            </SelectContent>
                          </Select>
                        ) : ui?.kind === 'paths' ? (
                          <textarea
                            data-slot='textarea'
                            rows={Math.max(2, String(Array.isArray(val) ? val.join('\n') : (val ?? '')).split('\n').length)}
                            value={Array.isArray(val) ? val.join('\n') : (val ?? '')}
                            spellCheck={false}
                            onChange={(e) => setEdit(row.key, e.target.value.split('\n'), original)}
                            className='w-96 resize-y px-3 py-2 font-mono text-xs'
                          />
                        ) : ui?.kind === 'number' ? (
                          <div className='flex items-center gap-2'>
                            <Input
                              type='number'
                              min={ui.min}
                              step={ui.step}
                              value={val ?? ''}
                              onChange={(e) => setEdit(row.key, e.target.value === '' ? '' : Number(e.target.value), original)}
                              className='w-60 text-right'
                            />
                            {ui.suffix && <span className='w-10 text-xs text-muted-foreground'>{ui.suffix}</span>}
                          </div>
                        ) : (
                          <div className='relative'>
                            <Input
                              type={row.secret && !reveal[row.key] ? 'password' : 'text'}
                              value={val ?? ''}
                              placeholder={row.secret ? 'not set' : ''}
                              onChange={(e) => setEdit(row.key, e.target.value, original)}
                              className='w-72 pr-9'
                            />
                            {row.secret && (
                              <button
                                type='button'
                                onClick={() => setReveal((r) => ({ ...r, [row.key]: !r[row.key] }))}
                                className='absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground'
                              >
                                {reveal[row.key] ? <EyeOff className='h-4 w-4' /> : <Eye className='h-4 w-4' />}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
