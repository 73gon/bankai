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
  { label: string; description: string; kind: 'number' | 'quality'; min?: number; step?: number; suffix?: string }
> = {
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

export default function Settings() {
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const r = await api.settings();
      setRows(r.settings);
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
      const s = sectionOf(row.key);
      if (!map.has(s)) map.set(s, []);
      map.get(s)!.push(row);
    }
    const selectorRows = map.get('selector');
    if (selectorRows) {
      selectorRows.sort((a, b) => SELECTOR_ORDER.indexOf(a.key) - SELECTOR_ORDER.indexOf(b.key));
    }
    return Array.from(map.entries());
  }, [rows]);

  function valueForKey(key: string): any {
    const row = rows.find((r) => r.key === key);
    return row ? currentValue(row) : undefined;
  }

  const minSize = Number(valueForKey('selector.min_size_gib'));
  const maxSize = Number(valueForKey('selector.max_size_gib'));
  const minSeeders = Number(valueForKey('selector.min_seeders'));
  const validationError =
    !Number.isFinite(minSize) || minSize < 0
      ? 'Minimum torrent size must be zero or greater.'
      : !Number.isFinite(maxSize) || maxSize <= 0
        ? 'Maximum torrent size must be greater than zero.'
        : minSize > maxSize
          ? 'Minimum torrent size cannot exceed the maximum.'
          : !Number.isInteger(minSeeders) || minSeeders < 0
            ? 'Minimum seeders must be a whole number of zero or greater.'
            : null;

  return (
    <div className='mx-auto max-w-3xl space-y-8 pb-24'>
      <header className='flex flex-wrap items-end justify-between gap-4'>
        <div className='flex items-baseline gap-2'>
          <h1 className='text-2xl font-semibold'>Settings</h1>
          <span className='text-sm text-muted-foreground'>— Edit safe configuration keys, then save all at once.</span>
        </div>
        <div className='flex items-center gap-2'>
          {dirtyCount > 0 && (
            <Button variant='ghost' size='sm' onClick={() => setEdits({})} disabled={saving}>
              <RotateCcw className='h-4 w-4' /> Discard
            </Button>
          )}
          <Button onClick={saveAll} disabled={saving || dirtyCount === 0 || Boolean(validationError)}>
            {saving ? <Loader2 className='h-4 w-4 animate-spin' /> : <Save className='h-4 w-4' />}
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
        <div className='space-y-10'>
          {validationError && <p className='rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300'>{validationError}</p>}
          {groups.map(([section, sectionRows]) => (
            <section key={section}>
              {/* Group divider + title */}
              <div className='mb-4 flex items-center gap-3 border-b border-border pb-2'>
                <h2 className='text-sm font-semibold uppercase tracking-wider text-foreground'>{sectionLabel(section)}</h2>
              </div>
              <div className='divide-y divide-border/60'>
                {sectionRows.map((row) => {
                  const isBool = typeof row.value === 'boolean';
                  const val = currentValue(row);
                  const original = row.value ?? '';
                  const dirty = row.key in edits;
                  const ui = SETTING_UI[row.key];
                  return (
                    <div key={row.key} className='flex flex-wrap items-start justify-between gap-4 py-3'>
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
