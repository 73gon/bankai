import { useEffect, useState } from 'react';
import { AudioLines, Captions, Clock, Loader2, Lock } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type TrackLanguage, type TrackRules, type TrackScope, type TrackScopeName } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Skeleton } from '@/zon/ui/skeleton';
import { Switch } from '@/zon/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';

const SCOPES: Array<{ key: TrackScopeName; label: string }> = [
  { key: 'anime', label: 'Anime' },
  { key: 'shows', label: 'Shows' },
  { key: 'movies', label: 'Movies' },
];

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function count(value: number) {
  return value.toLocaleString('en-US');
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() ?? path;
}

function since(seconds: number) {
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
}

/** One kind of track in a library: each language, how much of it there is, kept or taken out. */
function LanguageTable({ title, icon: Icon, rows, off, locked, onToggle, busy }: {
  title: string;
  icon: typeof AudioLines;
  rows: TrackLanguage[];
  off: string[];
  /** Always kept: shown without a switch. */
  locked: string[];
  onToggle: (code: string, keep: boolean) => void;
  busy: boolean;
}) {
  return (
    <Card className='gap-0 overflow-hidden [--card-padding:0]'>
      <CardHeader className='flex flex-row items-center gap-2 border-b border-border/60 px-4 py-3'>
        <Icon className='size-4 text-muted-foreground' aria-hidden='true' />
        <CardTitle className='text-base'>{title}</CardTitle>
        <span className='ml-auto text-xs text-muted-foreground'>{rows.length} language{rows.length === 1 ? '' : 's'}</span>
      </CardHeader>
      {rows.length ? (
        <Table variant='plain' dense containerProps={{ className: 'max-h-[60dvh] overflow-auto' }}>
          <TableHeader className='sticky top-0 z-10 bg-(--control-surface)'>
            <TableRow>
              <TableHead className='w-full'>Language</TableHead>
              <TableHead numeric>Files</TableHead>
              <TableHead numeric>Size</TableHead>
              <TableHead numeric>Keep</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const keep = !off.includes(row.code);
              return (
                <TableRow key={row.code} data-selected={keep ? undefined : ''}>
                  <TableCell>
                    <span className={keep ? 'text-foreground' : 'text-muted-foreground line-through'}>{row.name}</span>
                    <span className='ml-2 font-mono text-[0.65rem] text-muted-foreground'>{row.code}</span>
                  </TableCell>
                  <TableCell numeric className='font-mono text-xs tabular-nums' title={count(row.tracks) + ' tracks'}>{count(row.files)}</TableCell>
                  <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground'>{formatBytes(row.bytes)}</TableCell>
                  <TableCell numeric>
                    {locked.includes(row.code) ? (
                      <span className='inline-flex items-center gap-1 text-xs text-muted-foreground' title={row.name + ' is always kept'}>
                        <Lock className='size-3.5' aria-hidden='true' /> Always
                      </span>
                    ) : (
                      <Switch
                        checked={keep}
                        disabled={busy}
                        aria-label={(keep ? 'Take out ' : 'Keep ') + row.name + ' ' + title.toLowerCase()}
                        onCheckedChange={(value) => onToggle(row.code, value)}
                      />
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : <p className='px-4 py-6 text-sm text-muted-foreground'>No tracks read yet.</p>}
    </Card>
  );
}

/** One library's cleanup: on or off, what the switched-off languages change, what was done. */
function ScopePanel({ library, name, scope, onChange }: {
  library: TrackScopeName;
  name: string;
  scope: TrackScope;
  onChange: (next: TrackScope) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const { rules, preview } = scope;
  const reading = scope.read < scope.files;
  const off = [...rules.audio, ...rules.subtitles];

  async function save(changes: Partial<TrackRules>) {
    setBusy(true);
    const before = scope;
    onChange({ ...scope, rules: { ...rules, ...changes } });
    try {
      const result = await api.setTrackRules(library, changes);
      onChange({ ...before, rules: result.rules, preview: result.preview });
    } catch (error: any) {
      onChange(before);
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  function toggle(kind: 'audio' | 'subtitles', code: string, keep: boolean) {
    const current = rules[kind];
    void save({ [kind]: keep ? current.filter((item) => item !== code) : [...current, code] });
  }

  const nameOf = (code: string) =>
    [...scope.languages.audio, ...scope.languages.subtitles].find((row) => row.code === code)?.name
    ?? ({ ger: 'German', eng: 'English', jpn: 'Japanese' } as Record<string, string>)[code] ?? code;
  const waiting = Object.entries(scope.waiting);

  return (
    <div className='flex flex-col gap-4'>
      <Card>
        <CardHeader className='flex flex-row flex-wrap items-start justify-between gap-3'>
          <div className='flex min-w-0 flex-col gap-1'>
            <CardTitle className='text-lg'>Take out the languages switched off</CardTitle>
            <CardDescription>
              bankai writes one file at a time anew without them, losslessly: every other track is copied as it is.
              {preview.convert ? ` Files in another container are put into MKV (${preview.convert} here).` : ''}
            </CardDescription>
          </div>
          <Switch
            checked={rules.enabled}
            disabled={busy}
            aria-label={'Clean up the ' + name + ' library'}
            onCheckedChange={(value) => { if (value) setConfirming(true); else void save({ enabled: false }); }}
          />
        </CardHeader>
        <CardContent className='flex flex-col gap-2 text-sm'>
          <p className='text-muted-foreground'>
            {reading
              ? <><Loader2 className='mr-1.5 inline size-3.5 animate-spin' aria-hidden='true' />Tracks read for {count(scope.read)} of {count(scope.files)} files; the counts below grow as it goes.</>
              : <>Tracks of all {count(scope.files)} files read.</>}
          </p>
          <p>
            {preview.files
              ? <>Switched off: <b>{count(preview.tracks)}</b> track{preview.tracks === 1 ? '' : 's'} in <b>{count(preview.files)}</b> file{preview.files === 1 ? '' : 's'}{preview.bytes ? <> · about <b>{formatBytes(preview.bytes)}</b></> : null}.</>
              : <span className='text-muted-foreground'>Nothing switched off in this library{off.length ? ' that is in any file' : ''}.</span>}
            {!rules.enabled && preview.files ? <span className='text-muted-foreground'> Turn the switch on to start.</span> : null}
          </p>
          {scope.current && (
            <p className='flex items-center gap-1.5'>
              <Loader2 className='size-3.5 animate-spin text-info' aria-hidden='true' />
              Now: <span className='truncate font-mono text-xs'>{fileName(scope.current.path)}</span>
              <span className='text-muted-foreground'>({formatBytes(scope.current.size)}, started {since(scope.current.started)})</span>
            </p>
          )}
          {scope.done.files > 0 && (
            <p>
              Done: <b>{count(scope.done.files)}</b> file{scope.done.files === 1 ? '' : 's'}, <b>{formatBytes(scope.done.bytes)}</b> freed.
              {scope.last && <span className='text-muted-foreground'> Last: {fileName(scope.last.path)}, {formatBytes(scope.last.saved)}, {since(scope.last.at)}.</span>}
            </p>
          )}
          {rules.enabled && waiting.length > 0 && (
            <p className='flex items-center gap-1.5 text-muted-foreground'>
              <Clock className='size-3.5' aria-hidden='true' />
              Waiting: {waiting.map(([reason, value]) => `${count(value)} ${reason}`).join(' · ')}
            </p>
          )}
          {scope.failed.length > 0 && (
            <details className='text-xs'>
              <summary className='cursor-pointer text-warning'>{scope.failed.length} file{scope.failed.length === 1 ? '' : 's'} could not be written anew; each is tried again after a day</summary>
              <ul className='mt-1.5 flex flex-col gap-1'>
                {scope.failed.map((row) => (
                  <li key={row.path} className='break-all font-mono text-[0.68rem] text-muted-foreground'>{fileName(row.path)}: {row.error}</li>
                ))}
              </ul>
            </details>
          )}
        </CardContent>
      </Card>

      <div className='grid gap-4 lg:grid-cols-2'>
        <LanguageTable title='Audio' icon={AudioLines} rows={scope.languages.audio} off={rules.audio} locked={scope.protected} busy={busy} onToggle={(code, keep) => toggle('audio', code, keep)} />
        <LanguageTable title='Subtitles' icon={Captions} rows={scope.languages.subtitles} off={rules.subtitles} locked={scope.protected} busy={busy} onToggle={(code, keep) => toggle('subtitles', code, keep)} />
      </div>
      <p className='text-xs text-muted-foreground'>
        {scope.protected.map(nameOf).join(', ').replace(/, ([^,]*)$/, ' and $1')} are always kept. A file never loses its last audio track. Files still seeding in qBittorrent wait until the torrent is gone (written anew, their space
        would be taken twice), and so do files changed in the last hour. Jellyfin reads the new tracks on its own.
      </p>

      <Dialog open={confirming} onOpenChange={(open) => { if (!open && !busy) setConfirming(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Clean up the {name.toLowerCase()} library?</DialogTitle>
            <DialogDescription>
              {count(preview.files)} file{preview.files === 1 ? '' : 's'} will be written anew, one at a time. The tracks taken out are gone for good.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className='flex flex-col gap-2 text-sm'>
            <p><span className='text-muted-foreground'>Audio taken out:</span> {rules.audio.length ? rules.audio.map(nameOf).join(', ') : 'none'}</p>
            <p><span className='text-muted-foreground'>Subtitles taken out:</span> {rules.subtitles.length ? rules.subtitles.map(nameOf).join(', ') : 'none'}</p>
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setConfirming(false)} disabled={busy}>Cancel</Button>
            <Button onClick={() => { setConfirming(false); void save({ enabled: true }); }} disabled={busy}>Start</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Audio and subtitle languages of the three libraries, each switched off on its own. */
export default function Tracks() {
  const [scopes, setScopes] = useState<Record<TrackScopeName, TrackScope> | null>(null);
  const [tab, setTab] = useState<TrackScopeName>('anime');

  async function load() {
    try {
      setScopes((await api.tracks()).scopes);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    // Reading the tracks and cleaning go on in the background: the counts follow.
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>Tracks</h1>
        <p className='text-sm text-muted-foreground'>
          Every audio and subtitle language in the libraries. Switch off what you never need; each library keeps its own choice.
        </p>
      </div>
      {scopes === null ? <Skeleton className='h-96 w-full' /> : (
        <Tabs value={tab} onValueChange={(value) => setTab(value as TrackScopeName)} className='gap-4'>
          <TabsList>
            {SCOPES.map((item) => (
              <TabsTab key={item.key} value={item.key}>
                {item.label}
                {scopes[item.key].rules.enabled && <StatusTag variant='info' className='ml-1.5'>On</StatusTag>}
              </TabsTab>
            ))}
          </TabsList>
          {SCOPES.map((item) => (
            <TabsPanel key={item.key} value={item.key}>
              <ScopePanel
                library={item.key}
                name={item.label}
                scope={scopes[item.key]}
                onChange={(next) => setScopes((current) => (current ? { ...current, [item.key]: next } : current))}
              />
            </TabsPanel>
          ))}
        </Tabs>
      )}
    </div>
  );
}
