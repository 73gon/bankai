import { useEffect, useMemo, useState } from 'react';
import { Settings as SettingsIcon, Save, Loader2, Eye, EyeOff, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';
import { api, type SettingRow } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';

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
  const dot = key.indexOf('.');
  return labelize(dot > 0 ? key.slice(dot + 1) : key);
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
      if (value === original || (value === '' && (original === undefined || original === null || original === ''))) {
        delete n[key];
      } else {
        n[key] = value;
      }
      return n;
    });
  }

  async function saveAll() {
    if (dirtyCount === 0) return;
    setSaving(true);
    let ok = 0;
    try {
      for (const key of dirtyKeys) {
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
    return Array.from(map.entries());
  }, [rows]);

  return (
    <div className='mx-auto max-w-3xl space-y-8 pb-24'>
      <header className='flex flex-wrap items-end justify-between gap-4'>
        <div>
          <h1 className='text-2xl font-semibold'>Settings</h1>
          <p className='mt-1 text-sm text-muted-foreground'>Edit safe configuration keys, then save all at once.</p>
        </div>
        <div className='flex items-center gap-2'>
          {dirtyCount > 0 && (
            <Button variant='ghost' size='sm' onClick={() => setEdits({})} disabled={saving}>
              <RotateCcw className='h-4 w-4' /> Discard
            </Button>
          )}
          <Button onClick={saveAll} disabled={saving || dirtyCount === 0}>
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
          {groups.map(([section, sectionRows]) => (
            <section key={section}>
              {/* Group divider + title */}
              <div className='mb-4 flex items-center gap-3 border-b border-border pb-2'>
                <h2 className='text-sm font-semibold uppercase tracking-wider text-foreground'>{labelize(section)}</h2>
              </div>
              <div className='divide-y divide-border/60'>
                {sectionRows.map((row) => {
                  const isBool = typeof row.value === 'boolean';
                  const val = currentValue(row);
                  const original = row.value ?? '';
                  const dirty = row.key in edits;
                  return (
                    <div key={row.key} className='flex flex-wrap items-center justify-between gap-4 py-3'>
                      <div className='flex min-w-0 items-center gap-2'>
                        <span className='text-foreground'>{fieldLabel(row.key)}</span>
                        {dirty && <span className='h-1.5 w-1.5 rounded-full bg-primary' title='Unsaved change' />}
                        {row.secret && (row.is_set ? <Badge variant='success'>Set</Badge> : <Badge variant='muted'>Unset</Badge>)}
                      </div>

                      <div className='flex items-center gap-2'>
                        {isBool ? (
                          <Switch checked={Boolean(val)} onCheckedChange={(v) => setEdit(row.key, v, original)} />
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
