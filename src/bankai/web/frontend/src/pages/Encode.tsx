import { useEffect, useState } from 'react';
import { Clock, Loader2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { api, type EncodeScope, type TrackScopeName } from '@/lib/api';
import { StatusTag } from '@/components/status-tag';
import { Button } from '@/zon/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/zon/ui/card';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/zon/ui/dialog';
import { Progress } from '@/zon/ui/progress';
import { Skeleton } from '@/zon/ui/skeleton';
import { Switch } from '@/zon/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/zon/ui/table';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/zon/ui/tabs';

const SCOPES: Array<{ key: TrackScopeName; label: string }> = [
  { key: 'anime', label: 'Anime' },
  { key: 'shows', label: 'Shows' },
  { key: 'movies', label: 'Movies' },
];

const STEPS: Record<string, string> = {
  encode: 'Encoding the picture',
  dolby_vision: 'Adding Dolby Vision',
  mux: 'Putting the tracks together',
  check: 'Checking against the original',
};

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() ?? path;
}

function duration(seconds: number) {
  const minutes = Math.max(0, Math.round(seconds / 60));
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function since(seconds: number) {
  return duration(Date.now() / 1000 - seconds) + ' ago';
}

/** One library's encoding: its switch, the file being encoded, what was done, what comes next. */
function ScopePanel({ library, name, scope, onChange }: {
  library: TrackScopeName;
  name: string;
  scope: EncodeScope;
  onChange: (next: EncodeScope) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const { preview, current } = scope;

  async function setEnabled(enabled: boolean) {
    setBusy(true);
    try {
      const result = await api.setEncode(library, enabled);
      onChange({ ...scope, rules: result.rules });
    } catch (error: any) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  const waiting = Object.entries(scope.waiting);
  const left = Object.entries(scope.left);

  return (
    <div className='flex flex-col gap-4'>
      <Card>
        <CardHeader className='flex flex-row flex-wrap items-start justify-between gap-3'>
          <div className='flex min-w-0 flex-col gap-1'>
            <CardTitle className='text-lg'>Encode this library</CardTitle>
            <CardDescription>
              One file at a time on the graphics card, the one that frees the most first. Each replaces its original only once it is checked.
            </CardDescription>
          </div>
          <Switch
            checked={scope.rules.enabled}
            disabled={busy}
            aria-label={'Encode the ' + name + ' library'}
            onCheckedChange={(value) => { if (value) setConfirming(true); else void setEnabled(false); }}
          />
        </CardHeader>
        <CardContent className='flex flex-col gap-3 text-sm'>
          {scope.read < scope.files && (
            <p className='text-muted-foreground'>
              <Loader2 className='mr-1.5 inline size-3.5 animate-spin' aria-hidden='true' />
              Reading the files: {scope.read.toLocaleString('en-US')} of {scope.files.toLocaleString('en-US')}; the list below fills as it goes.
            </p>
          )}
          <p>
            {preview.files
              ? <><b>{preview.files.toLocaleString('en-US')}</b> file{preview.files === 1 ? '' : 's'} far bigger than needed: <b>{formatBytes(preview.bytes)}</b> now, about <b>{formatBytes(preview.saving)}</b> to free.</>
              : <span className='text-muted-foreground'>
                  {scope.read < scope.files ? 'None found yet' : `No file here is far bigger than it needs to be${scope.done.files ? ' any more' : ''}`}.
                </span>}
            {!scope.rules.enabled && preview.files ? <span className='text-muted-foreground'> Turn the switch on to start.</span> : null}
          </p>
          {current && (
            <div className='flex flex-col gap-1.5 rounded-control border border-field-edge p-3'>
              <p className='flex flex-wrap items-baseline gap-x-2'>
                <span className='truncate font-mono text-xs text-foreground'>{fileName(current.path)}</span>
                <span className='text-xs text-muted-foreground'>{formatBytes(current.size)} · as {current.codec === 'h264' ? 'H.264' : 'HEVC'}</span>
              </p>
              <Progress
                value={current.step === 'encode' && current.fraction != null ? Math.round(current.fraction * 100) : null}
                label={STEPS[current.step] ?? 'Encoding'}
                showValue={current.step === 'encode' && current.fraction != null}
              />
              <p className='text-xs text-muted-foreground'>
                Started {since(current.started)}{current.eta != null ? `, about ${duration(current.eta)} left for the picture` : ''}.
              </p>
            </div>
          )}
          {scope.done.files > 0 && (
            <p>
              Done: <b>{scope.done.files.toLocaleString('en-US')}</b> file{scope.done.files === 1 ? '' : 's'}, <b>{formatBytes(scope.done.bytes)}</b> freed.
              {scope.last && (
                <span className='text-muted-foreground'>
                  {' '}Last: {fileName(scope.last.path)}, {formatBytes(scope.last.before)} → {formatBytes(scope.last.after)}, SSIM {scope.last.ssim.toFixed(3)}, {since(scope.last.at)}.
                </span>
              )}
            </p>
          )}
          {scope.rules.enabled && waiting.length > 0 && (
            <p className='flex items-center gap-1.5 text-muted-foreground'>
              <Clock className='size-3.5' aria-hidden='true' />
              Waiting: {waiting.map(([reason, value]) => `${value} ${reason}`).join(' · ')}
            </p>
          )}
          {left.length > 0 && (
            <p className='text-xs text-muted-foreground'>Left as they are: {left.map(([reason, value]) => `${value} ${reason}`).join(' · ')}</p>
          )}
          {scope.failed.length > 0 && (
            <details className='text-xs'>
              <summary className='cursor-pointer text-warning'>{scope.failed.length} file{scope.failed.length === 1 ? '' : 's'} failed; each is tried again after a day</summary>
              <ul className='mt-1.5 flex flex-col gap-1'>
                {scope.failed.map((row) => (
                  <li key={row.path} className='break-all font-mono text-[0.68rem] text-muted-foreground'>{fileName(row.path)}: {row.error}</li>
                ))}
              </ul>
            </details>
          )}
        </CardContent>
      </Card>

      {scope.next.length > 0 && (
        <Card className='gap-0 overflow-hidden [--card-padding:0]'>
          <CardHeader className='border-b border-border/60 px-4 py-3'>
            <CardTitle className='text-base'>Next</CardTitle>
          </CardHeader>
          <Table variant='plain' dense>
            <TableHeader>
              <TableRow>
                <TableHead className='w-full'>File</TableHead>
                <TableHead numeric>Size</TableHead>
                <TableHead numeric>Picture</TableHead>
                <TableHead numeric>As</TableHead>
                <TableHead numeric>Frees about</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {scope.next.map((row) => (
                <TableRow key={row.path}>
                  <TableCell className='max-w-0'><p className='truncate font-mono text-xs' title={row.path}>{fileName(row.path)}</p></TableCell>
                  <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(row.size)}</TableCell>
                  <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums text-muted-foreground'>{row.mbps} Mbps</TableCell>
                  <TableCell numeric className='text-xs'>{row.codec === 'h264' ? 'H.264' : 'HEVC'}</TableCell>
                  <TableCell numeric className='whitespace-nowrap font-mono text-xs tabular-nums'>{formatBytes(row.estimate)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <p className='text-xs text-muted-foreground'>
        Taken: pictures of 30 Mbps and more in 4K, 22 Mbps for 1080p H.264, 15 Mbps for other 1080p, 10 Mbps below. HEVC stays HEVC and H.264
        stays H.264, so every device that played the original plays the result; audio, subtitles, chapters, HDR10 and Dolby Vision are kept.
        Left as they are: Dolby Vision profile 5, interlaced pictures, files still seeding or changed in the last hour, and results less than a
        fifth smaller.
      </p>

      <Dialog open={confirming} onOpenChange={(open) => { if (!open && !busy) setConfirming(false); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Encode the {name.toLowerCase()} library?</DialogTitle>
            <DialogDescription>
              {preview.files.toLocaleString('en-US')} file{preview.files === 1 ? '' : 's'}, {formatBytes(preview.bytes)} now, about {formatBytes(preview.saving)} freed.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className='text-sm'>
            Each file is encoded again and replaces its original once it is checked: same length, every track, HDR and Dolby Vision, the
            picture compared with the original. The originals are not kept.
          </DialogBody>
          <DialogFooter>
            <Button variant='secondary' onClick={() => setConfirming(false)} disabled={busy}>Cancel</Button>
            <Button onClick={() => { setConfirming(false); void setEnabled(true); }} disabled={busy}>Start</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Videos far bigger than anyone can see, encoded again per library. */
export default function Encode() {
  const [scopes, setScopes] = useState<Record<TrackScopeName, EncodeScope> | null>(null);
  const [tab, setTab] = useState<TrackScopeName>('shows');

  async function load() {
    try {
      setScopes((await api.encode()).scopes);
    } catch (error: any) {
      toast.error(error.message);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className='flex flex-col gap-6'>
      <div className='flex flex-col gap-1'>
        <h1 className='font-heading text-3xl font-light'>Encode</h1>
        <p className='text-sm text-muted-foreground'>
          Videos far bigger than anyone can see, encoded again on the graphics card: about half the size, the same picture, and they play
          wherever the originals did.
        </p>
      </div>
      {scopes === null ? <Skeleton className='h-96 w-full' /> : (
        <Tabs value={tab} onValueChange={(value) => setTab(value as TrackScopeName)} className='gap-4'>
          <TabsList>
            {SCOPES.map((item) => (
              <TabsTab key={item.key} value={item.key}>
                {item.label}
                {scopes[item.key].current
                  ? <StatusTag variant='info' className='ml-1.5'>Encoding</StatusTag>
                  : scopes[item.key].rules.enabled && <StatusTag variant='secondary' className='ml-1.5'>On</StatusTag>}
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
